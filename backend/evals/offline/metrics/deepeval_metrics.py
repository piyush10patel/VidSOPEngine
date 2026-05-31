"""DeepEval semantic metrics with a Groq-backed judge.

DeepEval ships custom-LLM hooks; we plug Groq in via litellm's standard
provider routing. The judge model is configurable via ``DEEPEVAL_JUDGE_MODEL``
and defaults to a fast Groq Llama model.

This module degrades gracefully when ``deepeval`` is not installed —
``run_deepeval`` returns ``None`` so the runner can show a "skipped"
row instead of crashing.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

_logger = logging.getLogger(__name__)


def _judge_model_name() -> str:
    return os.getenv("DEEPEVAL_JUDGE_MODEL", "groq/llama-3.3-70b-versatile")


# Groq's free tier caps llama-3.3-70b at 12 000 tokens-per-minute. Each
# SOPBench / DeepEval call sends ~600-1200 input + 200 output tokens,
# so a tight loop will burst through the limit in under 10 seconds.
# Defaults below pace calls just under the cap (~7s between calls,
# ~8 calls/min ~ 12k TPM headroom). Override via env vars when you
# upgrade to Groq Dev/Pro tiers.
_MIN_INTERVAL = float(os.getenv("EVAL_JUDGE_MIN_INTERVAL_SEC", "7.0"))
_MAX_RETRIES = int(os.getenv("EVAL_JUDGE_MAX_RETRIES", "5"))
_BASE_BACKOFF = float(os.getenv("EVAL_JUDGE_BASE_BACKOFF_SEC", "5.0"))


def _is_rate_limit_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate_limit" in msg
        or "ratelimit" in msg
        or "too many requests" in msg
    )


def _parse_retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Pull the 'try again in X' hint Groq embeds in its 429 body."""
    match = re.search(r"try again in\s+([0-9.]+)\s*s", str(exc), re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _build_groq_judge():
    """Build a DeepEval-compatible LLM that talks to Groq via litellm.

    Lazy imports so a missing ``deepeval`` install just means
    ``run_deepeval`` skips. We deliberately do NOT touch settings here so
    the eval harness can be vendored without pulling in the FastAPI app.
    """
    from deepeval.models.base_model import DeepEvalBaseLLM
    import litellm

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not set; export it before running DeepEval metrics."
        )

    model_name = _judge_model_name()

    class GroqJudge(DeepEvalBaseLLM):
        # Class-level so throttling spans every judge instance built in
        # the same process - SOPBench + DeepEval share the same Groq
        # quota and would otherwise race each other.
        _last_call_at: float = 0.0

        def load_model(self):  # pragma: no cover — required by base class
            return None

        def _throttle(self) -> None:
            gap = time.time() - GroqJudge._last_call_at
            if gap < _MIN_INTERVAL:
                time.sleep(_MIN_INTERVAL - gap)

        def generate(self, prompt: str, **kwargs) -> str:
            last_exc: Optional[BaseException] = None
            for attempt in range(_MAX_RETRIES + 1):
                self._throttle()
                try:
                    resp = litellm.completion(
                        model=model_name,
                        messages=[{"role": "user", "content": prompt}],
                        api_key=api_key,
                        temperature=0,
                        max_tokens=kwargs.get("max_tokens", 1024),
                    )
                    GroqJudge._last_call_at = time.time()
                    return resp.choices[0].message.content or ""
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    GroqJudge._last_call_at = time.time()
                    if not _is_rate_limit_error(exc):
                        # Non-rate-limit error -> don't retry, surface
                        # so the caller can mark the axis as failed.
                        raise
                    if attempt >= _MAX_RETRIES:
                        break
                    # Wait at least Groq's suggested Retry-After, with
                    # exponential backoff layered on top for safety.
                    suggested = _parse_retry_after_seconds(exc) or _BASE_BACKOFF
                    wait = max(suggested, _BASE_BACKOFF) * (2 ** attempt)
                    wait = min(wait, 90.0)  # don't sleep forever
                    _logger.warning(
                        "[judge] Groq 429 on attempt %d/%d; sleeping %.1fs (suggested=%s)",
                        attempt + 1, _MAX_RETRIES + 1, wait,
                        f"{suggested:.1f}s" if suggested else "n/a",
                    )
                    time.sleep(wait)
            assert last_exc is not None
            raise last_exc

        async def a_generate(self, prompt: str, **kwargs) -> str:
            return self.generate(prompt, **kwargs)

        def get_model_name(self) -> str:
            return model_name

    return GroqJudge()


def _summarise_test_case(
    fixture_name: str,
    step: dict,
    transcript: str,
    frame_observations: List[dict],
) -> str:
    """Compact context string fed to GEval. Kept short to control judge cost."""
    obs_lines = [
        f"Frame {o.get('frame_num', '?')}: {o.get('description', '')}"
        for o in frame_observations
    ]
    return (
        f"Procedure: {fixture_name}\n"
        f"Transcript excerpt: {transcript[:600]}\n\n"
        f"Frame observations:\n" + "\n".join(obs_lines[:12])
    )


def run_deepeval(
    fixture: dict,
    predicted_sop: dict,
    sample_size: int = 5,
) -> Optional[Dict[str, Any]]:
    """Score a sample of predicted steps for faithfulness + hallucination.

    Returns ``None`` if deepeval is not installed. Otherwise returns::

        {
          "judge_model": "groq/llama-3.3-70b-versatile",
          "n_evaluated": int,
          "faithfulness": {"mean": float, "min": float, "per_step": [...]},
          "hallucination": {"mean": float, "max": float, "per_step": [...]},
          "actionability": {...},
        }

    We sample at most ``sample_size`` steps (default 5) so a 20-step SOP
    doesn't quietly burn a dollar of judge calls per fixture.
    """
    try:
        from deepeval.metrics import GEval
        from deepeval.test_case import LLMTestCase, LLMTestCaseParams
    except ImportError:
        return None

    try:
        judge = _build_groq_judge()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not build Groq judge: {exc}"}

    steps = predicted_sop.get("steps", []) or []
    if not steps:
        return {"n_evaluated": 0, "note": "no steps to evaluate"}

    # Sample evenly across the SOP so we hit early, middle, and late steps.
    indices = (
        list(range(len(steps)))
        if len(steps) <= sample_size
        else [round(i * (len(steps) - 1) / (sample_size - 1)) for i in range(sample_size)]
    )
    sampled = [(i, steps[i]) for i in indices]

    context = _summarise_test_case(
        fixture.get("name", "<unnamed>"),
        steps,
        fixture.get("transcript", ""),
        fixture.get("frame_observations", []),
    )

    faithfulness = GEval(
        name="Faithfulness",
        criteria=(
            "The step description must be supported by the transcript or the "
            "frame observations. Penalise tools, actions, or objects not "
            "present in the source. Score 0-10, higher is better."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
        ],
        model=judge,
    )

    hallucination = GEval(
        name="Hallucination",
        criteria=(
            "Identify any entity, tool, action, or quantity in the step text "
            "that is NOT supported by the source. Score 0-10 where 10 means "
            "ZERO hallucinations and 0 means the step is fully invented."
        ),
        evaluation_params=[
            LLMTestCaseParams.INPUT,
            LLMTestCaseParams.ACTUAL_OUTPUT,
        ],
        model=judge,
    )

    actionability = GEval(
        name="Actionability",
        criteria=(
            "Is the step a single verb-first imperative an operator could "
            "execute without further interpretation? Penalise vague verbs "
            "('handle', 'continue', 'work with'), multi-action sentences, "
            "and steps phrased as observations rather than commands. "
            "Score 0-10."
        ),
        evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT],
        model=judge,
    )

    per_step = []
    for idx, step in sampled:
        step_text = (
            f"{step.get('title', '')}\n"
            f"{step.get('description') or step.get('instruction', '')}"
        ).strip()
        case = LLMTestCase(input=context, actual_output=step_text)
        try:
            faithfulness.measure(case)
            f_score = float(faithfulness.score or 0.0)
            hallucination.measure(case)
            h_score = float(hallucination.score or 0.0)
            actionability.measure(case)
            a_score = float(actionability.score or 0.0)
        except Exception as exc:  # noqa: BLE001
            per_step.append({"step_index": idx, "error": str(exc)})
            continue
        per_step.append({
            "step_index": idx,
            "faithfulness": round(f_score, 3),
            "hallucination": round(h_score, 3),
            "actionability": round(a_score, 3),
        })

    def _agg(field: str) -> Dict[str, float]:
        values = [e[field] for e in per_step if field in e]
        if not values:
            return {"mean": 0.0, "min": 0.0, "max": 0.0, "n": 0}
        return {
            "mean": round(sum(values) / len(values), 3),
            "min": round(min(values), 3),
            "max": round(max(values), 3),
            "n": len(values),
        }

    return {
        "judge_model": _judge_model_name(),
        "n_evaluated": len(per_step),
        "faithfulness": _agg("faithfulness"),
        "hallucination": _agg("hallucination"),
        "actionability": _agg("actionability"),
        "per_step": per_step,
    }
