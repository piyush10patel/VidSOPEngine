"""One-time-code rows for forgot-password recovery.

Stored as a hash, not the raw digits, so a DB leak doesn't hand
attackers a list of in-flight reset codes. Expires after 10 minutes
and is consumed atomically when the user verifies it.
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PasswordResetOtp(Base):
    __tablename__ = "password_reset_otps"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    # bcrypt hash of the 6-digit OTP. Never store the raw code.
    code_hash: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column()
    # Failed verify attempts. Locked after 5 to defeat online brute-force.
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    consumed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    request_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
