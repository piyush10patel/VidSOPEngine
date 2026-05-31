"""Step-level SOP correction memory."""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SOPCorrectionMemory(Base):
    """A reusable step-level correction captured from human SOP review."""

    __tablename__ = "sop_correction_memories"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    organization_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    video_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True)
    transcript_excerpt: Mapped[str] = mapped_column(String(1200), default="")
    original_step_json: Mapped[dict] = mapped_column(JSON, default=dict)
    corrected_step_json: Mapped[dict] = mapped_column(JSON, default=dict)
    correction_note: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    issue_type: Mapped[str] = mapped_column(String(80), default="wrong_answer", server_default="wrong_answer")
    source: Mapped[str] = mapped_column(String(40), default="human_review", server_default="human_review")
    usage_count: Mapped[int] = mapped_column(default=0, server_default="0")
    last_used_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
