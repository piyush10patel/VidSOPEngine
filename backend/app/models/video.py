"""Video model."""
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.transcript import Transcript
    from app.models.sop import SOP


class VideoStatus(str, Enum):
    """Video processing status states."""
    UPLOADED = "uploaded"
    TRANSCRIBING = "transcribing"
    SOP_GENERATING = "sop_generating"
    COMPLETED = "completed"
    FAILED = "failed"


class VideoType(str, Enum):
    """Source video kind - determines which generation pipeline runs.

    UI       - software workflow / screen recording (deterministic, label-grounded)
    PHYSICAL - real-world process (probabilistic, vision-grounded)
    """
    UI = "ui"
    PHYSICAL = "physical"


class PipelineComplexity(str, Enum):
    """Routing within the physical pipeline.

    PROCEDURAL_COMPLEX - multi-step operational process; abstraction-friendly.
    ATOMIC_SIMPLE      - short single-actor task; needs micro-action decomposition.
    AUTO               - system classifies at SOP-generation time.
    """
    PROCEDURAL_COMPLEX = "procedural_complex"
    ATOMIC_SIMPLE = "atomic_simple"
    AUTO = "auto"


class Video(Base):
    """Video model for uploaded video files."""

    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255))
    filename: Mapped[str] = mapped_column(String(255))
    file_path: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(20), default=VideoStatus.UPLOADED.value)
    video_type: Mapped[str] = mapped_column(
        String(20), default=VideoType.PHYSICAL.value, server_default=VideoType.PHYSICAL.value
    )
    pipeline_complexity: Mapped[str] = mapped_column(
        String(30),
        default=PipelineComplexity.AUTO.value,
        server_default=PipelineComplexity.AUTO.value,
    )
    pipeline_complexity_confidence: Mapped[Optional[float]] = mapped_column(nullable=True, default=None)

    # SHA-256 of the uploaded file content. Idempotency key: a duplicate
    # upload from the same user returns the existing video instead of
    # regenerating. Nullable - older rows pre-date this column.
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, default=None)

    # Downstream artifact + media cleanup orchestration. Media is only removed
    # after SOP, workflows, checklists, and training have all succeeded.
    artifact_generation_status: Mapped[dict] = mapped_column(JSON, default=dict)
    cleanup_eligible: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    cleanup_completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True, default=None)
    cleanup_last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    cleanup_attempts: Mapped[int] = mapped_column(default=0, server_default="0")

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        default=None, onupdate=datetime.utcnow
    )

    # Relationships
    user: Mapped[Optional["User"]] = relationship(back_populates="videos")
    transcript: Mapped[Optional["Transcript"]] = relationship(
        back_populates="video", uselist=False
    )
    sop: Mapped[Optional["SOP"]] = relationship(back_populates="video", uselist=False)
