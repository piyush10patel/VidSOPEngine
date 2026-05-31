"""Shared versioning helpers — snapshot before update, list, restore.

Single history table covers all entity types via entity_type + entity_id.
Live row's `version` column is the current version; bumped by 1 on each
update or restore. Snapshot contains the FULL pre-change state.
"""
import logging
from typing import Any, List, Optional
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entity_version import EntityVersion

logger = logging.getLogger(__name__)


async def snapshot_entity(
    db: AsyncSession,
    *,
    entity_type: str,
    entity_id: str,
    version: int,
    snapshot: dict,
    user_id: Optional[str] = None,
    change_summary: Optional[str] = None,
) -> EntityVersion:
    """Persist the entity's state under the given version number."""
    row = EntityVersion(
        id=str(uuid4()),
        entity_type=entity_type,
        entity_id=entity_id,
        version=version,
        snapshot_json=snapshot,
        change_summary=change_summary,
        user_id=user_id,
    )
    db.add(row)
    return row


async def list_versions(
    db: AsyncSession, *, entity_type: str, entity_id: str,
) -> List[EntityVersion]:
    result = await db.execute(
        select(EntityVersion)
        .where(
            EntityVersion.entity_type == entity_type,
            EntityVersion.entity_id == entity_id,
        )
        .order_by(EntityVersion.version.desc())
    )
    return list(result.scalars().all())


async def find_version(
    db: AsyncSession, *, entity_type: str, entity_id: str, version: int,
) -> Optional[EntityVersion]:
    result = await db.execute(
        select(EntityVersion).where(
            EntityVersion.entity_type == entity_type,
            EntityVersion.entity_id == entity_id,
            EntityVersion.version == version,
        )
    )
    return result.scalar_one_or_none()


def to_snapshot(row: Any, exclude: tuple = ("created_at", "updated_at")) -> dict:
    """Serialise a SQLAlchemy row's column values into a JSON-safe dict.

    Datetime values are coerced to isoformat strings.
    """
    from sqlalchemy import inspect as sa_inspect

    snap = {}
    for col in sa_inspect(row).mapper.column_attrs:
        if col.key in exclude:
            continue
        v = getattr(row, col.key)
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        snap[col.key] = v
    return snap
