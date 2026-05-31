"""Adaptive granularity classifier — routes a video to the right pipeline.

Two-stage:
  1. Cheap deterministic heuristics on the transcript (no LLM call).
  2. If ambiguous, fall back to an LLM call with the transcript + a
     small frame-text sample.

Returns {pipeline_type: 'atomic_simple' | 'procedural_complex',
         confidence: 0..1, reason: str}.
"""
import logging
import re
from typing import List, Optional

from app.core.config import settings
from app.observability.langsmith_client import (
    configure as configure_langsmith,
    traceable,
)
from app.services.llm.dspy_executor import run_dspy_sync

logger = logging.getLogger(__name__)


# Words that strongly signal a procedural / multi-step process.
_PROCEDURAL_MARKERS = re.compile(
    r"\b(step|first|second|third|next|then|after|finally|"
    r"workflow|process|procedure|tool|machine|system|"
    r"click|select|navigate|configure|install|deploy|setup|"
    r"check that|verify|ensure|inspect|maintain)\b",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", text or "") if w.strip()])


def _heuristic_classify(transcript: str) -> dict:
    """Fast heuristic. Returns a result OR None when ambiguous."""
    wc = _word_count(transcript)
    has_markers = bool(_PROCEDURAL_MARKERS.search(transcript or ""))

    # Strong signals
    if wc < 25 and not has_markers:
        return {
            "pipeline_type": "atomic_simple",
            "confidence": 0.85,
            "reason": f"Transcript is very short ({wc} words) with no procedural markers",
            "stage": "heuristic",
        }
    if wc > 120 or (wc > 50 and has_markers):
        return {
            "pipeline_type": "procedural_complex",
            "confidence": 0.85,
            "reason": (
                f"Transcript has {wc} words"
                + (" with procedural language" if has_markers else "")
            ),
            "stage": "heuristic",
        }
    return {"pipeline_type": None, "confidence": 0.0, "reason": "ambiguous", "stage": "heuristic"}


def _llm_classify(transcript: str, frame_sample: List[str]) -> dict:
    """LLM fallback when heuristics are ambiguous."""
    from app.dspy_modules.pipeline import VideoComplexityClassifier
    from app.services.llm.dspy_config import configure_dspy

    configure_dspy(task="sop")
    classifier = VideoComplexityClassifier(strategy="predict")
    result = classifier(
        transcript=(transcript or "")[:2000],
        frame_sample="\n".join((frame_sample or [])[:4])[:2000],
    )
    result["stage"] = "llm"
    return result


@traceable(name="classify_video_complexity", run_type="chain")
def classify_video_complexity(
    transcript: str,
    frame_sample: Optional[List[str]] = None,
    threshold: Optional[float] = None,
) -> dict:
    """Decide which pipeline this video should run through.

    Production behaviour (when settings.disable_auto_complexity_classifier
    is True): skip the LLM stage entirely and default to procedural_complex.
    The user is expected to pick the type explicitly at upload time.

    Otherwise (legacy behaviour): cheap heuristic on transcript first,
    LLM fallback if ambiguous, default to procedural if both fail.
    """
    configure_langsmith()
    threshold = threshold if threshold is not None else settings.atomic_simple_classifier_threshold

    if settings.disable_auto_complexity_classifier:
        return {
            "pipeline_type": "procedural_complex",
            "confidence": 0.5,
            "reason": (
                "Auto-classifier disabled in production — defaulted to procedural. "
                "User should pick atomic_simple / procedural_complex / ui at upload."
            ),
            "stage": "disabled",
        }

    h = _heuristic_classify(transcript or "")
    if h["pipeline_type"] is not None and h["confidence"] >= threshold:
        return h

    # Ambiguous → ask the LLM
    try:
        llm = run_dspy_sync(_llm_classify, transcript or "", frame_sample or [])
        if llm.get("pipeline_type") in ("atomic_simple", "procedural_complex"):
            return llm
    except Exception as e:
        logger.warning(f"complexity_classifier LLM stage failed: {e}")

    # Fallback: trust the existing strong path
    return {
        "pipeline_type": "procedural_complex",
        "confidence": 0.5,
        "reason": "Defaulted to procedural (heuristic ambiguous, LLM unavailable)",
        "stage": "default",
    }
