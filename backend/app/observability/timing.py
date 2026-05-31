"""Lightweight stage-level timing for pipeline observability.

Tiny by design — does not depend on Braintrust / LangSmith / Prometheus.
Every long-running stage in the worker wraps itself in `stage_timer(...)`
to record duration; results live in a per-run `StageTimings` dict that the
caller can stash on the SOPSchema (alongside `_adaptive_metrics`) or log.

Usage:

    timings = StageTimings()

    with stage_timer(timings, "transcription"):
        run_whisper(...)

    with stage_timer(timings, "frame_extraction"):
        extract_frames(...)

    logger.info(f"[timing] video={vid} {timings.summary()}")

The contextmanager guarantees the duration is recorded even on exception,
so a failed stage is still observable.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class StageTimings(dict):
    """Dict-shaped, JSON-serialisable accumulator of stage durations (seconds)."""

    def add(self, stage: str, seconds: float, error: Optional[str] = None) -> None:
        entry = {"seconds": round(seconds, 3)}
        if error:
            entry["error"] = error
        # If a stage runs more than once, sum durations and count occurrences.
        prev = self.get(stage)
        if isinstance(prev, dict):
            entry["seconds"] = round(prev["seconds"] + seconds, 3)
            entry["count"] = prev.get("count", 1) + 1
            if error and "error" not in prev:
                entry["error"] = error
        self[stage] = entry

    def summary(self) -> str:
        """Compact one-line summary suitable for logs."""
        return " ".join(
            f"{name}={info['seconds']:.2f}s"
            + (f"!err" if "error" in info else "")
            for name, info in self.items()
        )

    def total_seconds(self) -> float:
        return round(sum(info.get("seconds", 0.0) for info in self.values()), 3)


@contextmanager
def stage_timer(timings: StageTimings, stage: str):
    """Context manager that records elapsed time on `timings[stage]`.

    Always records — even on exception — so the metric is visible regardless.
    Re-raises the original exception unchanged.
    """
    started = time.monotonic()
    error: Optional[str] = None
    try:
        yield
    except Exception as e:
        error = type(e).__name__
        raise
    finally:
        elapsed = time.monotonic() - started
        timings.add(stage, elapsed, error=error)
