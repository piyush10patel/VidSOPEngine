"""SOP folder model."""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class SOPFolder(Base):
    """User-owned folder for organizing SOPs.

    parent_id supports shallow nesting. The service enforces max depth so the
    product stays simple for non-technical operators.
    """

    __tablename__ = "sop_folders"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("sop_folders.id"), nullable=True
    )
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        default=None, onupdate=datetime.utcnow
    )
