"""Pydantic schemas for transcript-related API operations."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_serializer

from app.services.transcription_service import WhisperModelSize

from app.schemas._datetime import utc_iso


class TranscribeRequest(BaseModel):
    """Request schema for starting transcription."""
    model_size: WhisperModelSize = Field(
        default=WhisperModelSize.BASE,
        description="Whisper model size to use for transcription"
    )


class TranscriptResponse(BaseModel):
    """Response schema for transcript data."""
    id: str
    video_id: str
    text: str
    model_name: str
    created_at: datetime
    @field_serializer("created_at")
    def _ser_dt(self, v):
        return utc_iso(v)

    class Config:
        from_attributes = True


class TranscriptionJobResponse(BaseModel):
    """Response schema for transcription job status."""
    video_id: str
    status: str
    message: str = "Transcription started"
