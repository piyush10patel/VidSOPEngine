"""Bounded auto-rewriter for SOPs that failed SOPBench scoring.

When the 4-axis SOPBench scorer flags one or more axes as failing, the
runner can optionally hand the SOP, the source, and the failure rationales
to this rewriter. It asks the Groq judge to PATCH only the failing axes,
keeping every other field intact.

Why a separate rewriter instead of re-running synthesis from scratch:

1. Re-running synthesis costs N×vision + 1×synthesis calls. A focused
   text-only rewrite is a single Groq chat call (~$0.001).
2. Re-running can silently change unrelated fields. A targeted rewrite
   preserves step IDs, source_frame_num anchors, evidence citations,
   and image_url mappings — only the offending text changes.
3. The rewriter has access to the FAILURE RATIONALES from the scorer,
   so it can fix the right thing instead of guessing.

The rewriter is bounded: at most ``max_attempts`` rounds, and each round
only fires on still-failing axes (so we don't keep regenerating a step
that is already passing).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _build_judge():
    from evals.offline.metrics.deepeval_metrics import _build_groq_judge

    return _build_groq_judge()


_REWRITE_INSTRUCTIONS = {
    "step_quality": (
        "Rewrite each step's `instruction` field to be a single verb-first "
        "imperative an operator can execute without further interpretation. "
        "Remove vague verbs ('handle', 'process', 'continue'), split multi-"
        "action sentences, replace observational phrasing ('the operator "
        "picks up X') with commands ('Pick up X')."
    ),
    "coherence": (
        "Fix title-vs-step mismatches, remove duplicate steps, restore steps "
        "the source clearly shows but the SOP omitted, and re-order steps to "
        "match execution order. Do NOT invent steps the source never showed."
    ),
    "language": (
        "Ensure every learner-facing string is in the target language. For "
        "Hindi targets, use Devanagari throughout except for brand names, "
        "model numbers, file names, URLs, and numeric measurements. Use "
        "natural Hinglish where it matches industry usage; avoid stilted "
        "formal Hindi."
    ),
    "safety": (
        "Add explicit warning text on any step whose source material mentioned "
        "a caution, hazard, PPE requirement, or risk. Surface the source "
        "wording verbatim where possible — do NOT invent new hazards."
    ),
}


def _build_rewrite_prompt(
    sop: Dict[str, Any],
    source_summary: str,
    failed_axes: List[str],
    rationales: Dict[str, str],
    target_language: str,
) -> str:
    fix_blocks = "\n".join(
        f"- {axis.upper()}: {_REWRITE_INSTRUCTIONS[axis]}\n  "
        f"Judge's complaint on this run: {rationales.get(axis, '(no rationale)')}"
        for axis in failed_axes
    )
    return (
        "You are rewriting a Standard Operating Procedure that just failed "
        f"automated quality scoring on these axes: {', '.join(failed_axes)}.\n\n"
        f"Target language: {target_language}\n\n"
        "FIXES TO APPLY (only these — preserve everything else verbatim):\n"
        f"{fix_blocks}\n\n"
        "SOURCE MATERIAL the SOP was generated from (do NOT contradict it):\n"
        f"{source_summary}\n\n"
        "CURRENT SOP (modify in place, return the SAME JSON shape):\n"
        f"{json.dumps(sop, ensure_ascii=False, indent=2)[:8000]}\n\n"
        "Output the corrected SOP as a single JSON object. Preserve every "
        "field that is NOT directly addressed by the listed fixes (step_number, "
        "source_frame_num, evidence, image_url, confidence, tools, checks, "
        "linked_*). Do NOT add explanatory prose before or after the JSON."
    )


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Tolerate markdown fences and prose preamble around the rewrite output."""
    if not text:
        return None
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def auto_rewrite(
    *,
    fixture: dict,
    initial_sop: Dict[str, Any],
    initial_score: Dict[str, Any],
    rescore: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]],
    max_attempts: int = 2,
) -> Dict[str, Any]:
    """Iteratively patch the failing axes until they pass or attempts run out.

    Args:
        fixture: The fixture dict (provides source summary + target language).
        initial_sop: The first SOP that failed scoring.
        initial_score: The SOPBench scorer output for ``initial_sop``.
        rescore: Callable returning a fresh SOPBench scorer output for a
            given SOP dict. We pass this in instead of calling
            ``run_sopbench`` directly so the runner can decide threshold
            / judge-model overrides.
        max_attempts: Maximum rewrite passes (default 2). Each pass only
            targets the still-failing axes from the previous round.

    Returns::

        {
          "attempts": [
            {"axes_targeted": [...], "score": {...}, "sop": {...},
             "error": Optional[str]},
            ...
          ],
          "final_sop": {...},
          "final_score": {...},
          "improved": bool,
        }
    """
    from evals.offline.metrics.sopbench import _build_source_block

    if not initial_score or initial_score.get("error"):
        return {
            "attempts": [],
            "final_sop": initial_sop,
            "final_score": initial_score,
            "improved": False,
            "note": "initial score unavailable; nothing to rewrite",
        }

    try:
        judge = _build_judge()
    except Exception as exc:  # noqa: BLE001
        return {
            "attempts": [],
            "final_sop": initial_sop,
            "final_score": initial_score,
            "improved": False,
            "error": f"could not build Groq judge: {exc}",
        }

    source = _build_source_block(fixture)
    target_language = initial_score.get("target_language", "English")

    current_sop = initial_sop
    current_score = initial_score
    attempts: List[Dict[str, Any]] = []
    improved = False

    for attempt_idx in range(max_attempts):
        failed_axes = list(current_score.get("failed_axes", []) or [])
        if not failed_axes:
            break

        rationales = {
            axis: (current_score["axes"][axis] or {}).get("rationale", "")
            for axis in failed_axes
            if axis in (current_score.get("axes") or {})
        }
        prompt = _build_rewrite_prompt(
            current_sop, source, failed_axes, rationales, target_language,
        )

        entry: Dict[str, Any] = {
            "axes_targeted": failed_axes,
            "attempt": attempt_idx + 1,
        }
        try:
            raw = judge.generate(prompt, max_tokens=3000)
        except Exception as exc:  # noqa: BLE001
            entry.update(error=f"rewrite call failed: {exc}", sop=current_sop, score=current_score)
            attempts.append(entry)
            break

        new_sop = _extract_json_object(raw)
        if not new_sop or not new_sop.get("steps"):
            entry.update(error="rewrite returned unparseable / empty SOP", sop=current_sop, score=current_score)
            attempts.append(entry)
            break

        # Re-merge unchanged fields conservatively so the rewriter cannot
        # accidentally drop image_url / source_frame_num / evidence. We
        # walk the new steps in order and patch only the textual fields
        # the rewrite is allowed to touch.
        merged = _merge_rewrite(current_sop, new_sop)

        new_score = rescore(merged)
        entry["sop"] = merged
        entry["score"] = new_score
        attempts.append(entry)

        if new_score and new_score.get("overall", 0) > current_score.get("overall", 0):
            improved = True

        current_sop = merged
        current_score = new_score or current_score

        if not (current_score and current_score.get("failed_axes")):
            break

    return {
        "attempts": attempts,
        "final_sop": current_sop,
        "final_score": current_score,
        "improved": improved,
    }


def _merge_rewrite(original: Dict[str, Any], rewritten: Dict[str, Any]) -> Dict[str, Any]:
    """Keep the rewrite's text fields; preserve the original's anchors.

    Fields the rewriter is ALLOWED to change: title, description,
    summary, step.title, step.description / instruction, step.warning,
    step.notes, step.tools, step.checks.

    Fields it is NOT allowed to change (we always restore from the
    original): step.step_number, step.source_frame_num, step.image_url,
    step.evidence, step.confidence, step.linked_*. These carry the
    pipeline's grounding signals and must not be silently rewritten.
    """
    merged = dict(rewritten)
    merged["steps"] = []
    original_steps = original.get("steps", []) or []
    rewritten_steps = rewritten.get("steps", []) or []
    # Index original steps by step_number so the rewriter can reorder
    # without losing anchors. Falls back to positional if the rewriter
    # produced fewer steps than the original.
    by_num: Dict[int, Dict[str, Any]] = {
        int(s.get("step_number", i + 1)): s
        for i, s in enumerate(original_steps)
    }
    for i, rw in enumerate(rewritten_steps):
        step_num = int(rw.get("step_number", i + 1))
        original_step = by_num.get(step_num) or (
            original_steps[i] if i < len(original_steps) else {}
        )
        merged_step = dict(rw)
        for protected in (
            "step_number",
            "source_frame_num",
            "image_url",
            "evidence",
            "confidence",
            "linked_documents",
            "linked_checklists",
            "linked_training",
            "linked_workflows",
            "attachments",
            "verified",
            "verification_quote",
        ):
            if protected in original_step:
                merged_step[protected] = original_step[protected]
        merged["steps"].append(merged_step)
    return merged
