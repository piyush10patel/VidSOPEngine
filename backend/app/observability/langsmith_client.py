"""LangSmith setup — sets env vars so @traceable decorators activate.

Lazy / safe-by-default: if LANGSMITH_API_KEY is not set, every traced
function just runs normally with no spans created.

Trace structure for an SOP generation:

  generate_sop                                       (root span)
  ├── analyze_frame   {frame_num: 1}                 (vision call)
  ├── analyze_frame   {frame_num: 2}
  ├── ...
  ├── structure_event {frame_num: 1, action: "..."}  (DSPy)
  ├── structure_event {frame_num: 2, ...}
  ├── ...
  ├── synthesize_sop  {n_events: 8}                  (DSPy)
  └── diagnose_hallucination                          (only if score < 1)

Each span captures input, output, and timing. Search by metadata in the
LangSmith UI: e.g. all `analyze_frame` spans where frame_num=3 across runs.
"""
import logging
import os

from app.core.config import settings

logger = logging.getLogger(__name__)

_initialized = False


def configure() -> bool:
    """Configure LangSmith env vars. Returns True if tracing is enabled."""
    global _initialized
    if _initialized:
        return bool(os.environ.get("LANGSMITH_API_KEY"))
    _initialized = True

    if not getattr(settings, "langsmith_api_key", None):
        logger.info("LangSmith tracing disabled (no LANGSMITH_API_KEY)")
        return False

    # The @traceable decorator reads these env vars on each call
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key
    os.environ["LANGSMITH_PROJECT"] = getattr(settings, "langsmith_project", "VidSOPEngine")
    logger.info(f"LangSmith tracing enabled → project={os.environ['LANGSMITH_PROJECT']}")
    return True


def traceable(*args, **kwargs):
    """Wrapper around langsmith.traceable that's safe when langsmith isn't installed."""
    try:
        from langsmith import traceable as _traceable
        return _traceable(*args, **kwargs)
    except ImportError:
        # No-op decorator if langsmith isn't available
        def decorator(fn):
            return fn
        if len(args) == 1 and callable(args[0]):
            return args[0]
        return decorator
