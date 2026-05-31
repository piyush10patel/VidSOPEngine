"""Capture and reuse human SOP corrections."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sop_correction_memory import SOPCorrectionMemory
from app.models.user import User


MAX_PINNED_EXAMPLES = 50


def _step_key(step: dict[str, Any]) -> int:
    try:
        return int(step.get("step_number") or 0)
    except (TypeError, ValueError):
        return 0


def _steps_by_number(sop: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        _step_key(step): step
        for step in (sop or {}).get("steps", [])
        if isinstance(step, dict) and _step_key(step)
    }


def _changed(original: dict[str, Any] | None, corrected: dict[str, Any]) -> bool:
    if corrected.get("user_marked_wrong") or corrected.get("user_correction_note"):
        return True
    if not original:
        return True
    for key in ("title", "description", "tools", "checks", "notes"):
        if original.get(key) != corrected.get(key):
            return True
    return False


def _compact_expected_output(expected_output: dict[str, Any]) -> dict[str, Any]:
    steps = []
    for step in (expected_output or {}).get("steps", []):
        if not isinstance(step, dict):
            continue
        steps.append({
            "step_number": step.get("step_number"),
            "title": step.get("title", ""),
            "description": step.get("description", ""),
            "tools": step.get("tools", []),
            "checks": step.get("checks", []),
        })
    return {
        "title": (expected_output or {}).get("title", ""),
        "description": (expected_output or {}).get("description", ""),
        "steps": steps,
        "notes": (expected_output or {}).get("notes", []),
        "warnings": (expected_output or {}).get("warnings", []),
    }


class SOPCorrectionMemoryService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def record_from_failure(
        self,
        *,
        user: User,
        transcript: str,
        actual_output: dict[str, Any],
        expected_output: dict[str, Any],
        video_id: str | None,
        failure_type: str,
        notes: str | None = None,
    ) -> list[SOPCorrectionMemory]:
        original_steps = _steps_by_number(actual_output or {})
        corrected_steps = _steps_by_number(expected_output or {})
        created: list[SOPCorrectionMemory] = []
        excerpt = (transcript or "")[:1200]

        for number, corrected in corrected_steps.items():
            original = original_steps.get(number)
            if not _changed(original, corrected):
                continue
            memory = SOPCorrectionMemory(
                id=str(uuid4()),
                user_id=user.id,
                organization_id=getattr(user, "organization_id", None),
                video_id=video_id,
                transcript_excerpt=excerpt,
                original_step_json=original or {},
                corrected_step_json=corrected,
                correction_note=corrected.get("user_correction_note") or notes,
                issue_type=failure_type,
                source="human_review",
                created_at=datetime.utcnow(),
            )
            self.db.add(memory)
            created.append(memory)

        if created:
            pins = list(user.pinned_examples_json or [])
            pins.insert(0, {
                "transcript": excerpt,
                "expected_output": _compact_expected_output(expected_output),
                "label": "auto-captured human SOP correction",
                "created_at": datetime.utcnow().isoformat(),
                "video_id": video_id,
                "step_corrections": [
                    {
                        "step_number": item.corrected_step_json.get("step_number"),
                        "before": item.original_step_json,
                        "after": item.corrected_step_json,
                        "note": item.correction_note,
                    }
                    for item in created
                ],
            })
            # Deduplicate by expected output hash while keeping newest first.
            seen: set[str] = set()
            unique_pins = []
            for pin in pins:
                key = json.dumps(pin.get("expected_output", {}), sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                unique_pins.append(pin)
                if len(unique_pins) >= MAX_PINNED_EXAMPLES:
                    break
            user.pinned_examples_json = unique_pins

        await self.db.commit()
        for memory in created:
            await self.db.refresh(memory)
        return created

    async def list_for_user(self, user: User, limit: int = 50) -> list[SOPCorrectionMemory]:
        q = (
            select(SOPCorrectionMemory)
            .where(SOPCorrectionMemory.user_id == user.id)
            .order_by(SOPCorrectionMemory.created_at.desc())
            .limit(limit)
        )
        result = await self.db.execute(q)
        return list(result.scalars().all())
