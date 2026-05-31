"""SOPBench-inspired 4-axis quality scorer for generated SOPs.

The published SOPBench paper benchmarks whether AGENTS follow SOPs; it
does not expose a pip-installable scorer for the quality of a generated
SOP. This module re-implements the spirit of its rubrics — covering the
four axes the project cares about — using the Groq judge that the rest
of the eval suite already uses.

The four axes are:

  step_quality      verb-first, actionable, unambiguous, no filler
  coherence         title/steps consistent, ordering, no duplicates,
                    no missing critical steps the source clearly showed
  language          Devanagari vs Roman fidelity for Hindi SOPs,
                    natural-Hinglish style, no random English fallbacks
                    for content words; clear operational English otherwise
  safety            warnings present where source warranted them, PPE /
                    cautions surfaced, no silent omission of risky steps

Each axis returns a 0-10 score, plus a short rationale string from the
judge so the auto-rewriter can fix the right thing. The module degrades
to ``None`` when ``deepeval`` isn't installed, matching the rest of the
metric modules.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# Threshold below which an axis is considered failing. Configurable so
# users can tighten or loosen without code changes.
DEFAULT_THRESHOLD = float(os.getenv("SOPBENCH_THRESHOLD", "7.0"))


@dataclass
class AxisScore:
    name: str
    score: float
    rationale: str
    failed: bool


def _judge_model_name() -> str:
    return os.getenv("DEEPEVAL_JUDGE_MODEL", "groq/llama-3.3-70b-versatile")


def _build_judge():
    """Reuse the Groq judge defined in deepeval_metrics for consistency.

    Importing the helper rather than rebuilding it means the model name
    and any provider tweaks stay in one place.
    """
    from evals.offline.metrics.deepeval_metrics import _build_groq_judge

    return _build_groq_judge()


# ─────────────────────────────────────────── rubrics


_RUBRICS: Dict[str, str] = {
    "step_quality": (
        "Score 0-10 the QUALITY of each individual step in this SOP. "
        "High score = every step is verb-first imperative, single action, "
        "unambiguous, no filler. Low score = vague verbs ('handle', "
        "'process'), multi-action sentences, or steps phrased as "
        "observations rather than commands. Be strict — generic phrasing "
        "must not score above 5."
    ),
    "coherence": (
        "Score 0-10 how COHERENT the SOP is end-to-end. High score = the "
        "title accurately describes what the steps accomplish, steps are "
        "in execution order, no duplicates, no missing critical steps the "
        "source clearly showed, no scope creep into unrelated procedures. "
        "Low score = title-vs-steps mismatch, jumbled order, repeated "
        "actions, or omitted setup/closing steps."
    ),
    "language": (
        "Score 0-10 the LANGUAGE quality. The SOP target language is "
        "given below. High score = consistent script (Devanagari "
        "throughout for Hindi, except brand names / model numbers / "
        "measurements that stay verbatim), natural operational phrasing, "
        "Hinglish where it matches industry usage. Low score = random "
        "English fallbacks for content words mid-Hindi sentence, "
        "transliteration instead of translation, or stilted formal Hindi "
        "where Hinglish would read better."
    ),
    "safety": (
        "Score 0-10 the OPERATIONAL SAFETY coverage. High score = every "
        "risk the source mentioned is reflected as a warning, PPE or "
        "safety steps are present where appropriate, the SOP does not "
        "silently omit a risky action. Low score = source mentioned a "
        "caution / hazard that the SOP dropped, or PPE that the source "
        "showed but the SOP did not surface."
    ),
}


def _build_source_block(fixture: dict) -> str:
    transcript = (fixture.get("transcript") or "").strip()
    obs = fixture.get("frame_observations") or []
    obs_lines = "\n".join(
        f"  Frame {o.get('frame_num', '?')}: {o.get('description', '')}"
        for o in obs[:24]
    )
    return f"TRANSCRIPT:\n{transcript[:1200]}\n\nFRAME OBSERVATIONS:\n{obs_lines}"


def _build_sop_block(sop: dict, max_steps: int = 30) -> str:
    title = sop.get("title", "")
    steps = sop.get("steps", []) or []
    lines = [f"TITLE: {title}", f"DESCRIPTION: {sop.get('description', '')}"]
    lines.append("\nSTEPS:")
    for s in steps[:max_steps]:
        warning = s.get("warning") or s.get("note") or ""
        line = f"  {s.get('step_number', '?')}. {s.get('title', '')}: {s.get('description') or s.get('instruction', '')}"
        if warning:
            line += f" [warning: {warning}]"
        lines.append(line)
    if len(steps) > max_steps:
        lines.append(f"  ... ({len(steps) - max_steps} more steps truncated)")
    return "\n".join(lines)


def _parse_judge_response(text: str) -> tuple[float, str]:
    """Pull a 0-10 score and a one-line rationale out of the judge text.

    The judge is asked for the format ``SCORE: <int> ... RATIONALE: <text>``,
    but models drift. We try the structured form first, then fall back to
    the first integer in the response. Anything beyond range clamps to
    [0, 10] so a stray "100" doesn't poison aggregates.
    """
    import re

    score = 0.0
    rationale = text.strip()
    m = re.search(r"SCORE\s*[:=]\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if m:
        try:
            score = float(m.group(1))
        except ValueError:
            score = 0.0
    else:
        m2 = re.search(r"\b([0-9]|10)(?:\.\d+)?\b", text)
        if m2:
            try:
                score = float(m2.group(1))
            except ValueError:
                score = 0.0
    score = max(0.0, min(10.0, score))
    rm = re.search(r"RATIONALE\s*[:=]\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    if rm:
        rationale = rm.group(1).strip().splitlines()[0]
    return score, rationale[:400]


def _score_axis(
    judge,
    axis: str,
    rubric: str,
    source: str,
    sop_block: str,
    target_language: str,
) -> AxisScore:
    prompt = (
        f"You are grading a Standard Operating Procedure on the {axis.upper()} "
        f"axis.\n\n"
        f"Target language: {target_language}\n\n"
        f"RUBRIC:\n{rubric}\n\n"
        f"SOURCE MATERIAL (transcript + frame observations the SOP was "
        f"generated from):\n{source}\n\n"
        f"GENERATED SOP:\n{sop_block}\n\n"
        f"Reply in this exact format on TWO lines:\n"
        f"SCORE: <integer 0-10>\n"
        f"RATIONALE: <one sentence pointing at the worst offender, or "
        f"'no issues' if the score is 10>"
    )
    raw = judge.generate(prompt, max_tokens=200)
    score, rationale = _parse_judge_response(raw)
    return AxisScore(
        name=axis,
        score=score,
        rationale=rationale,
        failed=score < DEFAULT_THRESHOLD,
    )


def run_sopbench(
    fixture: dict,
    predicted_sop: dict,
    threshold: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Score the predicted SOP on all four SOPBench-inspired axes.

    Returns ``None`` if deepeval is not installed. Otherwise returns::

        {
          "threshold": float,
          "target_language": "English" | "Hindi",
          "overall": float,            # mean across axes
          "axes": {
            "step_quality": {"score": float, "rationale": str, "failed": bool},
            "coherence":    {...},
            "language":     {...},
            "safety":       {...},
          },
          "failed_axes": ["language", ...],
          "judge_model": "groq/...",
        }
    """
    try:
        # Just to confirm deepeval is importable before we burn judge calls.
        import deepeval  # noqa: F401
    except ImportError:
        return None

    try:
        judge = _build_judge()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not build Groq judge: {exc}"}

    threshold = float(threshold if threshold is not None else DEFAULT_THRESHOLD)
    target_language = (
        fixture.get("target_language")
        or (predicted_sop.get("generation_metadata") or {}).get("output_language")
        or "English"
    )
    if str(target_language).lower() in ("hi", "hindi"):
        target_language = "Hindi"
    else:
        target_language = "English"

    source = _build_source_block(fixture)
    sop_block = _build_sop_block(predicted_sop)

    axis_results: Dict[str, AxisScore] = {}
    axis_errors: Dict[str, str] = {}
    for axis, rubric in _RUBRICS.items():
        # The safety axis only makes sense if the source mentions risks;
        # skip the call when the transcript is empty AND no observation
        # contains a hazard keyword. Saves ~25% of judge tokens on bland
        # fixtures.
        if axis == "safety" and not _source_has_safety_signal(fixture):
            axis_results[axis] = AxisScore(
                name=axis, score=10.0,
                rationale="source had no explicit safety signal", failed=False,
            )
            continue
        try:
            axis_results[axis] = _score_axis(
                judge, axis, rubric, source, sop_block, target_language,
            )
        except Exception as exc:  # noqa: BLE001
            # A single bad axis call - typically a Groq rate-limit hard
            # failure after retries - should NOT lose the whole fixture's
            # score. Record the error and continue; the report still
            # surfaces the axes that completed successfully.
            axis_errors[axis] = (
                type(exc).__name__ + ": " + str(exc)[:240]
            )
            axis_results[axis] = AxisScore(
                name=axis, score=0.0,
                rationale=f"judge call failed: {type(exc).__name__}",
                failed=True,
            )

    scored = [a for name, a in axis_results.items() if name not in axis_errors]
    overall = (
        sum(a.score for a in scored) / len(scored)
        if scored else 0.0
    )
    failed = [name for name, a in axis_results.items() if a.score < threshold]

    payload: Dict[str, Any] = {
        "threshold": threshold,
        "target_language": target_language,
        "overall": round(overall, 2),
        "axes": {
            name: {
                "score": round(a.score, 2),
                "rationale": a.rationale,
                "failed": bool(a.score < threshold),
            }
            for name, a in axis_results.items()
        },
        "failed_axes": failed,
        "judge_model": _judge_model_name(),
    }
    if axis_errors:
        payload["axis_errors"] = axis_errors
        payload["n_axes_with_errors"] = len(axis_errors)
    return payload


_SAFETY_KEYWORDS = (
    "caution", "warning", "danger", "hazard", "safety", "ppe", "gloves",
    "goggles", "helmet", "burn", "shock", "sharp", "hot", "lock", "isolate",
    "lockout", "tagout", "high voltage", "spill", "fume", "toxic",
)


def _source_has_safety_signal(fixture: dict) -> bool:
    transcript = (fixture.get("transcript") or "").lower()
    obs_text = " ".join(
        (o.get("description") or "").lower()
        for o in fixture.get("frame_observations", [])
    )
    blob = transcript + " " + obs_text
    return any(kw in blob for kw in _SAFETY_KEYWORDS)
