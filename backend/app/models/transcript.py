"""Transcript model."""
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.video import Video
    from app.models.sop import SOP


class Transcript(Base):
    """Transcript model for video transcriptions."""
    
    __tablename__ = "transcripts"
    
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    video_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("videos.id"), unique=True
    )
    text: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="completed")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    
    # Relationships
    video: Mapped["Video"] = relationship(back_populates="transcript")
    sop: Mapped[Optional["SOP"]] = relationship(back_populates="transcript")
