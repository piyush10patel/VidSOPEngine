"""Pydantic schemas for the EntityVersion history table."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, field_serializer

from app.schemas._datetime import utc_iso


class EntityVersionResponse(BaseModel):
    id: str
    entity_type: str
    entity_id: str
    version: int
    snapshot_json: dict
    change_summary: Optional[str] = None
    user_id: Optional[str] = None
    created_at: datetime
    @field_serializer("created_at")
    def _ser_dt(self, v):
        return utc_iso(v)

    class Config:
        from_attributes = True


class EntityVersionListResponse(BaseModel):
    versions: List[EntityVersionResponse]
    total: int
    current_version: int
