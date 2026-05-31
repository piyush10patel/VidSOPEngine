"""Braintrust client — logs every SOP generation with scores and accepts human feedback.

Lazy / safe-by-default: if BRAINTRUST_API_KEY is not set, every function in
this module is a no-op so the app still runs in local dev without configuration.
"""
import logging
from typing import Any, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_braintrust_logger = None
_initialized = False


def get_logger():
    """Return a Braintrust logger, initialising on first call. None if disabled."""
    global _braintrust_logger, _initialized
    if _initialized:
        return _braintrust_logger
    _initialized = True

    if not getattr(settings, "braintrust_api_key", None):
        logger.info("Braintrust disabled (no BRAINTRUST_API_KEY)")
        return None

    try:
        import braintrust
        _braintrust_logger = braintrust.init_logger(
            project=getattr(settings, "braintrust_project", "VidSOPEngine"),
            api_key=settings.braintrust_api_key,
        )
        logger.info("Braintrust logger initialised")
    except Exception as e:
        logger.warning(f"Braintrust init failed: {e}")
        _braintrust_logger = None
    return _braintrust_logger


def log_sop_generation(
    *,
    sop_id: str,
    video_id: str,
    transcript: str,
    frame_observations: list,
    output: dict,
    scores: dict,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Log an SOP generation to Braintrust. Returns the span id (or None if disabled)."""
    bt = get_logger()
    if bt is None:
        return None

    try:
        span = bt.log(
            input={
                "transcript": transcript,
                "frame_observations": frame_observations,
            },
            output=output,
            scores=scores,
            metadata={
                "sop_id": sop_id,
                "video_id": video_id,
                **(metadata or {}),
            },
        )
        return str(span) if span else None
    except Exception as e:
        logger.warning(f"Braintrust log_sop_generation failed: {e}")
        return None


def log_human_feedback(
    *,
    sop_id: str,
    video_id: str,
    score: float,
    failure_type: Optional[str] = None,
    comment: Optional[str] = None,
    user_id: Optional[str] = None,
) -> bool:
    """Log human feedback as a scored event tagged with the SOP id."""
    bt = get_logger()
    if bt is None:
        return False

    try:
        bt.log(
            input={"sop_id": sop_id, "video_id": video_id},
            output={"comment": comment} if comment else {},
            scores={"human_quality": float(score)},
            metadata={
                "sop_id": sop_id,
                "video_id": video_id,
                "kind": "human_feedback",
                "failure_type": failure_type,
                "user_id": user_id,
            },
        )
        return True
    except Exception as e:
        logger.warning(f"Braintrust log_human_feedback failed: {e}")
        return False
