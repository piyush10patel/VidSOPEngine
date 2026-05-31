"""Append-only JSONL store for SOP failure cases.

Why JSONL:
- New failures are appended (no full-file rewrite, safe for concurrent writers)
- DSPy / pandas / jq read it natively
- Each line is a self-contained record — easy to inspect, share, version

Storage location:
- Defaults to {settings.upload_dir}/../failures.jsonl
- On Render this resolves to /opt/render/project/src/data/failures.jsonl
  which lives on the persistent disk and survives deploys.
"""
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional
from uuid import uuid4

from app.core.config import settings
from app.schemas.failure import FailureCase, FailureInput, FailureType, Severity


def _dataset_path() -> Path:
    """Resolve the JSONL file path based on current settings."""
    base = Path(settings.upload_dir).parent
    return base / "failures.jsonl"


def append_case(
    *,
    transcript: str,
    frame_observations: list,
    actual_output: dict,
    expected_output: dict,
    failure_type: FailureType,
    severity: Severity = Severity.MEDIUM,
    notes: Optional[str] = None,
    video_id: Optional[str] = None,
    video_type: str = "physical",
) -> FailureCase:
    """Append a single failure case to the dataset."""
    case = FailureCase(
        id=f"case-{uuid4().hex[:12]}",
        video_id=video_id,
        video_type=video_type,
        created_at=datetime.now(timezone.utc),
        input=FailureInput(
            transcript=transcript,
            frame_observations=frame_observations or [],
        ),
        actual_output=actual_output,
        expected_output=expected_output,
        failure_type=failure_type,
        severity=severity,
        notes=notes,
    )

    path = _dataset_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(case.model_dump_json() + "\n")

    return case


def iter_cases() -> Iterable[FailureCase]:
    """Yield each case from the JSONL file. Skips malformed lines."""
    path = _dataset_path()
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield FailureCase.model_validate_json(line)
            except Exception:
                continue


def load_all() -> List[FailureCase]:
    return list(iter_cases())


def count_by_type() -> dict:
    return dict(Counter(c.failure_type.value for c in iter_cases()))
