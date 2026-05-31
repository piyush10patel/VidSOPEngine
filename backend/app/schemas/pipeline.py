"""Pipeline-related Pydantic schemas."""
from typing import Optional
from datetime import datetime

from pydantic import BaseModel


class PipelineRunRequest(BaseModel):
    """Request to run the full pipeline."""
    pass  # No parameters needed for MVP


class PipelineJobResponse(BaseModel):
    """Response after enqueueing a pipeline job."""
    job_id: str
    video_id: str
    status: str
    message: Optional[str] = None


class JobStatusResponse(BaseModel):
    """Response for job status query."""
    job_id: str
    status: str
    result: Optional[str] = None
    error: Optional[str] = None
    enqueued_at: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
