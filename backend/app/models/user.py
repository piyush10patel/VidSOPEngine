"""User model."""
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, List, Optional
from uuid import uuid4

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.video import Video


class UserRole(str, Enum):
    """Operational access roles."""

    SUPERADMIN = "superadmin"
    ADMIN = "admin"
    MANAGER = "manager"
    STAFF = "staff"


class User(Base):
    """User model for authentication and video ownership."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    role: Mapped[str] = mapped_column(
        String(20), default=UserRole.ADMIN.value, server_default=UserRole.ADMIN.value
    )
    active: Mapped[bool] = mapped_column(default=True, server_default="1")

    # Domain-specific corrected examples for THIS user. Each item:
    #   {"transcript": str, "expected_output": dict, "label": str|null}
    # During SOP generation, these are merged ahead of the global RAG pool
    # so per-customer style and vocabulary win over generic patterns.
    pinned_examples_json: Mapped[Optional[list]] = mapped_column(
        JSON, default=list, nullable=True
    )

    # Monthly token budget tracking. The counter resets when the calling
    # code observes that we've crossed into a new UTC month (no scheduler
    # required). When TOKEN_BUDGET_MONTHLY_DEFAULT is 0 the budget is
    # treated as unlimited and the counter is informational only.
    tokens_used_this_period: Mapped[int] = mapped_column(default=0, server_default="0")
    period_started_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    videos: Mapped[List["Video"]] = relationship(back_populates="user")
