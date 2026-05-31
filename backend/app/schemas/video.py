"""Pydantic schemas for video-related API operations."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_serializer

from app.models.video import VideoStatus, VideoType, PipelineComplexity

from app.schemas._datetime import utc_iso


class VideoResponse(BaseModel):
    """Response schema for video metadata."""
    id: str
    title: str
    filename: str
    status: VideoStatus
    video_type: VideoType = VideoType.PHYSICAL
    pipeline_complexity: PipelineComplexity = PipelineComplexity.AUTO
    pipeline_complexity_confidence: Optional[float] = None
    created_at: datetime
    has_transcript: bool = False
    has_sop: bool = False
    @field_serializer("created_at")
    def _ser_dt(self, v):
        return utc_iso(v)

    class Config:
        from_attributes = True


class VideoUploadResponse(BaseModel):
    """Response schema for successful video upload."""
    id: str
    title: str
    filename: str
    status: VideoStatus
    video_type: VideoType
    pipeline_complexity: PipelineComplexity = PipelineComplexity.AUTO
    message: str = "Video uploaded successfully"


class StatusResponse(BaseModel):
    """Response schema for video status polling."""
    video_id: str
    status: VideoStatus
    message: Optional[str] = None


class VideoListResponse(BaseModel):
    """Response schema for video listing."""
    videos: list[VideoResponse]
    total: int
