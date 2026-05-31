"""Generic version-history snapshot for any entity (workflow / checklist / training_module).

A single table covers all entities — `entity_type` discriminates and
`snapshot_json` holds the full body of the row at that point in time.
"""
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EntityVersion(Base):
    """Snapshot of an entity at a particular version number.

    Created by the service layer just before any update. Restoring a version
    overwrites the live row with the snapshot's body.
    """

    __tablename__ = "entity_versions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    entity_type: Mapped[str] = mapped_column(String(40))   # workflow | checklist | training_module
    entity_id: Mapped[str] = mapped_column(String(36))     # FK in spirit; not enforced (cross-table)
    version: Mapped[int] = mapped_column()
    snapshot_json: Mapped[dict] = mapped_column(JSON)
    change_summary: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
