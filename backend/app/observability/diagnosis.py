"""Hallucination root-cause diagnosis.

Triggered when hallucination_rate < 1.0. For each ungrounded item in the SOP,
classifies the failure mode so we know what to fix:

  - action_missing      Pure invention — the action is genuinely not in any source
  - wrong_grouping      Action exists in source but step combines unrelated actions
  - constraint_ignored  Generic filler step added despite "no invention" rule
  - synonym_miss        Action IS in source but with different wording (false positive)

The diagnosis is logged as metadata on the LangSmith span, so the UI shows
both the failed step AND the verdict next to each other.
"""
import json
import logging
from typing import List

from app.observability.langsmith_client import traceable

logger = logging.getLogger(__name__)


_DIAGNOSIS_PROMPT = """You are diagnosing why an AI-generated SOP step contains content not grounded in the source.

STEP UNDER REVIEW:
{step_json}

UNGROUNDED ITEM (tool/object that does not appear in source):
"{ungrounded_item}"

SOURCE TRANSCRIPT:
{transcript}

SOURCE FRAMES (each line is one frame's vision-model description):
{frame_descriptions}

Choose EXACTLY ONE root cause:

1. "action_missing"      — The item is pure invention. No frame or transcript line mentions it (even by synonym).
2. "wrong_grouping"      — The action IS in the source but this step has bundled it with unrelated actions.
3. "constraint_ignored"  — The model added a generic filler step (e.g. "gather materials") despite no source evidence.
4. "synonym_miss"        — The item IS in the source under a different name. (False-positive of the grounding check.)

Reply with JSON only:
{{
  "root_cause": "<one of the four>",
  "evidence": "<short quote or frame ref proving your verdict>",
  "confidence": <0.0-1.0>
}}"""


def _judge(prompt: str, model: str = "llama-3.3-70b-versatile") -> dict:
    """Single LLM JSON-mode call. Returns {} on failure so diagnosis never blocks."""
    try:
        from app.services.llm import get_provider
        resp = get_provider().chat(
            prompt, model=model,
            response_format={"type": "json_object"}, timeout=30,
        )
        return json.loads(resp.text)
    except Exception as e:
        logger.warning(f"Diagnosis judge call failed: {e}")
        return {}


def _find_ungrounded_items(sop: dict, transcript: str, frame_observations: list) -> list:
    """Token-level grounding check. Returns list of (step_idx, item) tuples."""
    corpus = transcript.lower() + " "
    for obs in frame_observations:
        corpus += str(obs.get("description", "")).lower() + " "

    ungrounded = []
    steps = sop.get("steps") or sop.get("sop") or []
    for idx, step in enumerate(steps):
        items = (step.get("tools") or step.get("objects") or [])
        for item in items:
            tokens = [t for t in str(item).lower().split() if len(t) > 2]
            if tokens and not any(tok in corpus for tok in tokens):
                ungrounded.append((idx, item))
    return ungrounded


@traceable(name="diagnose_hallucination", run_type="chain")
def diagnose_hallucination(
    sop: dict,
    transcript: str,
    frame_observations: list,
) -> List[dict]:
    """Diagnose every hallucinated item in the SOP.

    Returns a list of diagnosis dicts, one per ungrounded item:
      {
        "step_index": 2,
        "step_title": "Tighten with wrench",
        "ungrounded_item": "wrench",
        "root_cause": "action_missing",
        "evidence": "...",
        "confidence": 0.92,
      }
    """
    ungrounded = _find_ungrounded_items(sop, transcript, frame_observations)
    if not ungrounded:
        return []

    steps = sop.get("steps") or sop.get("sop") or []
    frame_descriptions = "\n".join(
        f"Frame {obs.get('frame_num', i+1)}: {obs.get('description', '')}"
        for i, obs in enumerate(frame_observations)
    ) or "(no frames — text-only generation)"

    diagnoses = []
    for step_idx, item in ungrounded:
        step = steps[step_idx]
        prompt = _DIAGNOSIS_PROMPT.format(
            step_json=json.dumps(step, indent=2),
            ungrounded_item=item,
            transcript=transcript[:2000],
            frame_descriptions=frame_descriptions[:2000],
        )
        verdict = _judge(prompt)
        diagnoses.append({
            "step_index": step_idx,
            "step_title": step.get("title", ""),
            "ungrounded_item": item,
            "root_cause": verdict.get("root_cause", "unknown"),
            "evidence": verdict.get("evidence", ""),
            "confidence": float(verdict.get("confidence", 0.0)),
        })

    return diagnoses


def summarize_diagnoses(diagnoses: List[dict]) -> dict:
    """Roll up per-item diagnoses into a per-cause histogram for the trace."""
    counts = {}
    for d in diagnoses:
        cause = d.get("root_cause", "unknown")
        counts[cause] = counts.get(cause, 0) + 1
    return {
        "total_hallucinations": len(diagnoses),
        "by_root_cause": counts,
        "details": diagnoses,
    }
