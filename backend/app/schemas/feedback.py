"""Schemas for human feedback on generated SOPs."""
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.failure import FailureType


class FeedbackRequest(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0, description="Quality score 0.0–1.0")
    comment: Optional[str] = Field(None, description="Free-text feedback")
    failure_type: Optional[FailureType] = Field(
        None, description="If score is low, optionally categorise the failure"
    )


class FeedbackResponse(BaseModel):
    sop_id: str
    video_id: str
    score: float
    logged_to_braintrust: bool
    saved_as_failure_case: bool
