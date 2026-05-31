"""Human feedback on generated SOPs.

POST /sop/{video_id}/feedback
  - Logs the human score to Braintrust as a `human_quality` score
  - If failure_type is provided, also appends a case to the failure dataset
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.datasets import failures as failures_store
from app.models.base import get_db
from app.models.sop import SOP
from app.models.transcript import Transcript
from app.models.user import User
from app.observability.braintrust_client import log_human_feedback
from app.routers.auth import get_current_user
from app.schemas.failure import Severity
from app.schemas.feedback import FeedbackRequest, FeedbackResponse


router = APIRouter(prefix="/sop", tags=["feedback"])


@router.post("/{video_id}/feedback", response_model=FeedbackResponse)
async def submit_feedback(
    video_id: str,
    request: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FeedbackResponse:
    sop_row = (await db.execute(
        select(SOP).where(SOP.video_id == video_id)
    )).scalar_one_or_none()
    if not sop_row:
        raise HTTPException(status_code=404, detail=f"No SOP for video {video_id}")

    # Push to Braintrust as a separate scored event tagged with sop_id
    logged = log_human_feedback(
        sop_id=sop_row.id,
        video_id=video_id,
        score=request.score,
        failure_type=request.failure_type.value if request.failure_type else None,
        comment=request.comment,
        user_id=str(current_user.id),
    )

    # If marked as a failure, also record in the failure dataset for DSPy training
    saved_as_failure = False
    if request.failure_type is not None and request.score < 0.5:
        transcript_row = (await db.execute(
            select(Transcript).where(Transcript.video_id == video_id)
        )).scalar_one_or_none()
        if transcript_row:
            failures_store.append_case(
                transcript=transcript_row.text,
                frame_observations=[],
                actual_output=sop_row.sop_json,
                expected_output=sop_row.sop_json,  # placeholder — user can refine via /failures
                failure_type=request.failure_type,
                severity=Severity.HIGH if request.score < 0.3 else Severity.MEDIUM,
                notes=request.comment,
                video_id=video_id,
            )
            saved_as_failure = True

    return FeedbackResponse(
        sop_id=sop_row.id,
        video_id=video_id,
        score=request.score,
        logged_to_braintrust=logged,
        saved_as_failure_case=saved_as_failure,
    )
