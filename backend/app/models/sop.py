"""SOP model."""
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from sqlalchemy import JSON, Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.video import Video
    from app.models.transcript import Transcript


class SOP(Base):
    """SOP model for generated Standard Operating Procedures."""

    __tablename__ = "sops"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    video_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("videos.id"), unique=True, nullable=True
    )
    transcript_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("transcripts.id"), nullable=True
    )
    sop_json: Mapped[dict] = mapped_column(JSON)
    simplified_steps_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(20), default="ai_generated", server_default="ai_generated"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="draft", server_default="draft"
    )
    folder_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("sop_folders.id"), nullable=True
    )
    category: Mapped[str] = mapped_column(
        String(100), default="Uncategorized", server_default="Uncategorized"
    )
    tags_json: Mapped[list] = mapped_column(JSON, default=list)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    last_reviewed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    estimated_duration_minutes: Mapped[Optional[int]] = mapped_column(nullable=True)
    visibility_scope: Mapped[str] = mapped_column(
        String(30), default="private", server_default="private"
    )
    allowed_role_min: Mapped[str] = mapped_column(
        String(20), default="manager", server_default="manager"
    )
    shared_with_users_json: Mapped[list] = mapped_column(JSON, default=list)
    is_finalized: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0"
    )
    workflows_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    workflows_generated_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, default=None
    )
    training_module_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    training_generated_at: Mapped[Optional[datetime]] = mapped_column(
        nullable=True, default=None
    )
    # Provenance — captured at generation time so a regressed SOP can be
    # attributed to the exact prompt + model that produced it.
    prompt_version: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    model_used: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        default=None, onupdate=datetime.utcnow
    )

    # Relationships
    video: Mapped[Optional["Video"]] = relationship(back_populates="sop")
    transcript: Mapped[Optional["Transcript"]] = relationship(back_populates="sop")
