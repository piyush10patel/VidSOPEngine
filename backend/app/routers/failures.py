"""API endpoints for the SOP failure-case dataset.

POST   /failures            Record a new failure case
GET    /failures            List all failure cases
GET    /failures/stats      Counts by failure_type
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.datasets import failures as failures_store
from app.models.base import get_db
from app.models.sop import SOP
from app.models.transcript import Transcript
from app.models.user import User
from app.routers.auth import get_current_user
from app.schemas.failure import (
    FailureCase,
    FailureCreateRequest,
    FailureListResponse,
)
from app.services.sop_correction_memory_service import SOPCorrectionMemoryService


router = APIRouter(prefix="/failures", tags=["failures"])


@router.post("", response_model=FailureCase)
async def record_failure(
    request: FailureCreateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FailureCase:
    """Record a new failure case.

    Two ways to provide input:
    1. Pass `video_id` — transcript and actual_output are loaded from the DB.
    2. Pass `transcript`, `frame_observations`, `actual_output` directly.

    `expected_output` and `failure_type` are always required.
    """
    transcript = request.transcript
    actual_output = request.actual_output
    frame_observations = request.frame_observations or []

    if request.video_id:
        # Load actual SOP and transcript from DB
        sop_row = (await db.execute(
            select(SOP).where(SOP.video_id == request.video_id)
        )).scalar_one_or_none()
        transcript_row = (await db.execute(
            select(Transcript).where(Transcript.video_id == request.video_id)
        )).scalar_one_or_none()

        if not sop_row or not transcript_row:
            raise HTTPException(
                status_code=404,
                detail=f"No SOP/transcript found for video {request.video_id}",
            )

        if actual_output is None:
            actual_output = sop_row.sop_json
        if transcript is None:
            transcript = transcript_row.text

    if transcript is None or actual_output is None:
        raise HTTPException(
            status_code=400,
            detail="Must provide either video_id or (transcript + actual_output)",
        )

    case = failures_store.append_case(
        transcript=transcript,
        frame_observations=frame_observations,
        actual_output=actual_output,
        expected_output=request.expected_output,
        failure_type=request.failure_type,
        severity=request.severity,
        notes=request.notes,
        video_id=request.video_id,
    )
    try:
        await SOPCorrectionMemoryService(db).record_from_failure(
            user=current_user,
            transcript=transcript,
            actual_output=actual_output,
            expected_output=request.expected_output,
            video_id=request.video_id,
            failure_type=request.failure_type.value,
            notes=request.notes,
        )
    except Exception:
        # Failure dataset persistence is the primary contract of this endpoint.
        # Memory capture is best-effort and should never block a correction.
        pass
    return case


@router.get("/correction-memory")
async def list_correction_memory(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    memories = await SOPCorrectionMemoryService(db).list_for_user(current_user)
    return {
        "memories": [
            {
                "id": memory.id,
                "video_id": memory.video_id,
                "original_step": memory.original_step_json,
                "corrected_step": memory.corrected_step_json,
                "correction_note": memory.correction_note,
                "issue_type": memory.issue_type,
                "created_at": memory.created_at,
            }
            for memory in memories
        ],
        "total": len(memories),
    }


@router.get("", response_model=FailureListResponse)
async def list_failures(
    current_user: User = Depends(get_current_user),
) -> FailureListResponse:
    cases = failures_store.load_all()
    return FailureListResponse(
        cases=cases,
        total=len(cases),
        by_type=failures_store.count_by_type(),
    )


@router.get("/stats")
async def failure_stats(
    current_user: User = Depends(get_current_user),
) -> dict:
    from collections import Counter
    cases = failures_store.load_all()
    by_video_type = Counter((c.video_type or "physical") for c in cases)
    return {
        "total": len(cases),
        "by_type": failures_store.count_by_type(),
        "by_video_type": dict(by_video_type),
        "target_minimum": 20,
        "target_recommended": 50,
        "ready_for_dspy_optimization_per_type": {
            vtype: count >= 20 for vtype, count in by_video_type.items()
        },
    }
