"""Deterministic SOP presentation projections.

The generated SOP remains the source of truth. These helpers only shape the
same data into an operator-safe view for staff-facing screens.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.models.user import User


INTERNAL_SOP_STEP_KEYS = {
    "evidence",
    "confidence",
    "verified",
    "verification_quote",
    "correctness_score",
    "correctness_label",
    "correctness_reason",
    "correctness_issue_type",
}


def can_view_internal_sop(user: User | None) -> bool:
    role = (getattr(user, "role", "staff") or "staff").lower()
    return role in {"manager", "admin", "superadmin"}


def build_operator_sop(sop_json: dict[str, Any] | None) -> dict[str, Any]:
    source = sop_json or {}
    steps = []
    all_tools: list[str] = []

    for index, step in enumerate(source.get("steps") or [], start=1):
        if not isinstance(step, dict):
            continue
        tools = [str(item) for item in (step.get("tools") or []) if str(item).strip()]
        checks = [str(item) for item in (step.get("checks") or []) if str(item).strip()]
        for tool in tools:
            if tool not in all_tools:
                all_tools.append(tool)

        steps.append({
            "step_number": int(step.get("step_number") or index),
            "title": str(step.get("title") or f"Step {index}").strip(),
            "instruction": str(
                step.get("instruction") or step.get("description") or ""
            ).strip(),
            "tools": tools,
            "checks": checks,
            "image_url": step.get("image_url"),
            "notes": step.get("notes") if step.get("notes") not in ("null", "") else None,
        })

    return {
        "title": str(source.get("title") or "Standard operating procedure").strip(),
        "description": str(
            source.get("description") or source.get("summary") or ""
        ).strip(),
        "steps": steps,
        "tools": all_tools,
        "warnings": [str(item) for item in (source.get("warnings") or [])],
        "notes": [str(item) for item in (source.get("notes") or [])],
    }


def sanitize_sop_for_operator(sop_json: dict[str, Any] | None) -> dict[str, Any]:
    source = deepcopy(sop_json or {})
    raw_metadata = source.pop("generation_metadata", None) or {}
    source.pop("overall_confidence", None)
    source["needs_review"] = False

    clean_steps = []
    for index, step in enumerate(source.get("steps") or [], start=1):
        if not isinstance(step, dict):
            continue
        item = {k: v for k, v in step.items() if k not in INTERNAL_SOP_STEP_KEYS}
        item.setdefault("step_number", index)
        item.setdefault("title", f"Step {index}")
        item.setdefault("description", item.get("instruction") or "")
        item.setdefault("tools", [])
        item.setdefault("checks", [])
        item.setdefault("evidence", [])
        item.setdefault("confidence", 1.0)
        item.setdefault("user_marked_wrong", False)
        clean_steps.append(item)
    source["steps"] = clean_steps
    source.setdefault("title", "Standard operating procedure")
    source.setdefault("description", "")
    source.setdefault("notes", [])
    source.setdefault("warnings", [])
    source.setdefault("overall_confidence", 1.0)
    # Preserve presentation-language metadata so the UI knows which language
    # the staff-facing SOP is currently in (drives the "View in हिन्दी /
    # English" toggle). Other internal generation metadata is dropped.
    presentation_metadata: dict[str, Any] = {}
    if isinstance(raw_metadata, dict):
        for key in ("output_language", "translated_to", "translated_from"):
            value = raw_metadata.get(key)
            if value:
                presentation_metadata[key] = value
    source["generation_metadata"] = presentation_metadata
    return source


def sop_json_for_user(sop_json: dict[str, Any] | None, user: User | None) -> dict[str, Any]:
    if can_view_internal_sop(user):
        return sop_json or {}
    return sanitize_sop_for_operator(sop_json)
