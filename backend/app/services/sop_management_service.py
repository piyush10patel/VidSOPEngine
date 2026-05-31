"""SOP management service: folders, metadata, access, and linked assets."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

from app.core.errors import AppException, ErrorCodes
from app.models.sop import SOP
from app.models.sop_folder import SOPFolder
from app.models.user import User
from app.models.video import Video
from app.schemas.sop import SOPCreateRequest, SOPResponse, SOPUpdateRequest
from app.services.sop_projection_service import (
    build_operator_sop,
    can_view_internal_sop,
    sop_json_for_user,
)


ROLE_RANK = {"staff": 1, "manager": 2, "admin": 3, "superadmin": 4}


class SOPManagementError(AppException):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(
            error_code=ErrorCodes.VALIDATION_ERROR,
            message=message,
            status_code=status_code,
        )


class ManagedSOPNotFoundError(AppException):
    def __init__(self, sop_id: str):
        super().__init__(
            error_code=ErrorCodes.SOP_NOT_FOUND,
            message=f"SOP '{sop_id}' not found",
            status_code=404,
            details={"sop_id": sop_id},
        )


def _role_at_least(user_role: str, minimum: str) -> bool:
    return ROLE_RANK.get(user_role or "staff", 1) >= ROLE_RANK.get(minimum or "staff", 1)


def _created_by_or_owner(sop: SOP, user: User) -> bool:
    if sop.created_by == user.id:
        return True
    if sop.video and sop.video.user_id == user.id:
        return True
    return False


def can_access_sop(sop: SOP, user: User) -> bool:
    role = (getattr(user, "role", "staff") or "staff").lower()
    if role == "superadmin":
        return True
    if _created_by_or_owner(sop, user):
        return True
    if user.id in (sop.shared_with_users_json or []):
        return True
    return False


def can_edit_sop(sop: SOP, user: User) -> bool:
    role = (getattr(user, "role", "staff") or "staff").lower()
    if role in {"admin", "superadmin"}:
        return True
    if role == "manager" and _created_by_or_owner(sop, user):
        return True
    return False


class SOPManagementService:
    MAX_FOLDER_DEPTH = 3

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _can_access_sop(self, sop: SOP, user: User) -> bool:
        role = (getattr(user, "role", "staff") or "staff").lower()
        if role in {"admin", "superadmin"}:
            return True
        if _created_by_or_owner(sop, user):
            return True
        if user.id in (sop.shared_with_users_json or []):
            return True
        return False

    async def _folder_depth(self, folder_id: Optional[str], owner_id: str) -> int:
        if not folder_id:
            return 0
        depth = 0
        current = folder_id
        seen: set[str] = set()
        while current:
            if current in seen:
                raise SOPManagementError("Folder cannot contain itself")
            seen.add(current)
            folder = await self.get_folder(current, owner_id)
            depth += 1
            current = folder.parent_id
        return depth

    async def list_folders(self, owner_id: str) -> list[SOPFolder]:
        result = await self.db.execute(
            select(SOPFolder)
            .where(SOPFolder.owner_id == owner_id)
            .order_by(SOPFolder.parent_id.is_not(None), SOPFolder.name.asc())
        )
        return list(result.scalars().all())

    async def get_folder(self, folder_id: str, owner_id: str) -> SOPFolder:
        result = await self.db.execute(
            select(SOPFolder).where(
                SOPFolder.id == folder_id,
                SOPFolder.owner_id == owner_id,
            )
        )
        folder = result.scalar_one_or_none()
        if not folder:
            raise SOPManagementError("Folder not found", status_code=404)
        return folder

    async def create_folder(self, owner_id: str, name: str, parent_id: Optional[str]) -> SOPFolder:
        if await self._folder_depth(parent_id, owner_id) >= self.MAX_FOLDER_DEPTH:
            raise SOPManagementError("Folder nesting is limited to 3 levels")
        folder = SOPFolder(
            id=str(uuid4()),
            name=name.strip(),
            parent_id=parent_id,
            owner_id=owner_id,
        )
        self.db.add(folder)
        await self.db.commit()
        await self.db.refresh(folder)
        return folder

    async def update_folder(
        self, folder_id: str, owner_id: str, name: Optional[str], parent_id: Optional[str]
    ) -> SOPFolder:
        folder = await self.get_folder(folder_id, owner_id)
        if parent_id == folder_id:
            raise SOPManagementError("Folder cannot contain itself")
        if parent_id is not None and await self._folder_depth(parent_id, owner_id) >= self.MAX_FOLDER_DEPTH:
            raise SOPManagementError("Folder nesting is limited to 3 levels")
        if name is not None:
            folder.name = name.strip()
        if parent_id is not None:
            folder.parent_id = parent_id or None
        folder.updated_at = datetime.utcnow()
        await self.db.commit()
        await self.db.refresh(folder)
        return folder

    async def delete_folder(self, folder_id: str, owner_id: str) -> None:
        folder = await self.get_folder(folder_id, owner_id)
        result = await self.db.execute(
            select(SOP).where(SOP.folder_id == folder.id, SOP.created_by == owner_id)
        )
        for sop in result.scalars().all():
            sop.folder_id = None
        await self.db.delete(folder)
        await self.db.commit()

    async def list_sops(
        self,
        user: User,
        search: Optional[str] = None,
        tag: Optional[str] = None,
        category: Optional[str] = None,
        folder_id: Optional[str] = None,
        archived: bool = False,
    ) -> list[SOP]:
        role = (getattr(user, "role", "staff") or "staff").lower()
        q = (
            select(SOP)
            .options(selectinload(SOP.video))
            .outerjoin(Video, SOP.video_id == Video.id)
            .where(SOP.archived == archived)
        )

        if role not in {"admin", "superadmin"}:
            q = q.where(
                or_(
                    SOP.created_by == user.id,
                    Video.user_id == user.id,
                    SOP.visibility_scope.in_(["role", "team", "organization"]),
                )
            )
        if category:
            q = q.where(SOP.category == category)
        if folder_id:
            q = q.where(SOP.folder_id == folder_id)
        q = q.order_by(SOP.updated_at.desc().nullslast(), SOP.created_at.desc())
        result = await self.db.execute(q)
        rows = []
        for row in result.scalars().unique().all():
            if await self._can_access_sop(row, user):
                rows.append(row)
        if search:
            needle = search.lower()
            rows = [
                row for row in rows
                if needle in (row.category or "").lower()
                or needle in str((row.sop_json or {}).get("title", "")).lower()
                or needle in str((row.sop_json or {}).get("description", "")).lower()
                or needle in str((row.sop_json or {}).get("summary", "")).lower()
            ]
        if tag:
            rows = [row for row in rows if tag in (row.tags_json or [])]
        return rows

    async def get_sop(self, sop_id: str, user: User) -> SOP:
        result = await self.db.execute(
            select(SOP)
            .options(selectinload(SOP.video))
            .outerjoin(Video, SOP.video_id == Video.id)
            .where(SOP.id == sop_id)
        )
        sop = result.scalar_one_or_none()
        if not sop or not await self._can_access_sop(sop, user):
            raise ManagedSOPNotFoundError(sop_id)
        return sop

    async def create_sop(self, request: SOPCreateRequest, user: User) -> SOP:
        if request.folder_id:
            await self.get_folder(request.folder_id, user.id)
        if request.video_id:
            result = await self.db.execute(select(Video).where(Video.id == request.video_id))
            video = result.scalar_one_or_none()
            if not video:
                raise SOPManagementError("Video not found", status_code=404)
            role = (getattr(user, "role", "staff") or "staff").lower()
            if role not in {"admin", "superadmin"} and video.user_id != user.id:
                raise SOPManagementError("You do not have access to this video", status_code=403)
        sop = SOP(
            id=str(uuid4()),
            video_id=request.video_id,
            transcript_id=None,
            sop_json=request.sop.model_dump(mode="json"),
            folder_id=request.folder_id,
            category=request.category or "Uncategorized",
            tags_json=request.tags or [],
            created_by=user.id,
            updated_by=user.id,
            visibility_scope=request.visibility_scope or "private",
            allowed_role_min=request.allowed_role_min or "manager",
            shared_with_users_json=request.shared_with_users or [],
            source_type=request.source_type or "manual",
            status=request.status or "draft",
            is_finalized=request.status == "published",
            last_reviewed_at=datetime.utcnow() if request.status == "published" else None,
            estimated_duration_minutes=request.estimated_duration_minutes,
        )
        self.db.add(sop)
        await self.db.commit()
        await self.db.refresh(sop)
        return sop

    async def update_sop(self, sop_id: str, request: SOPUpdateRequest, user: User) -> SOP:
        sop = await self.get_sop(sop_id, user)
        if not can_edit_sop(sop, user):
            raise SOPManagementError("You do not have permission to edit this SOP", status_code=403)
        fields_set = getattr(request, "model_fields_set", set())
        if request.sop is not None:
            if sop.is_finalized and (sop.source_type or "ai_generated") == "ai_generated":
                raise SOPManagementError("SOP is finalized; cannot edit content", status_code=409)
            incoming = request.sop.model_dump(mode="json")
            image_urls = [
                (s.get("step_number"), s.get("image_url")) for s in (incoming.get("steps") or [])
            ]
            logger.info(
                "[sop_managed_update] id=%s steps=%d images=%s",
                sop_id, len(incoming.get("steps") or []), image_urls,
            )
            sop.sop_json = incoming
        if "folder_id" in fields_set:
            if request.folder_id:
                await self.get_folder(request.folder_id, user.id)
            sop.folder_id = request.folder_id or None
        if request.category is not None:
            sop.category = request.category or "Uncategorized"
        if request.tags is not None:
            sop.tags_json = request.tags
        if request.archived is not None:
            sop.archived = request.archived
            if request.archived:
                sop.status = "archived"
            elif sop.status == "archived":
                sop.status = "published" if sop.is_finalized else "draft"
        if request.visibility_scope is not None:
            sop.visibility_scope = request.visibility_scope
        if request.allowed_role_min is not None:
            sop.allowed_role_min = request.allowed_role_min
        if request.shared_with_users is not None:
            sop.shared_with_users_json = request.shared_with_users
        if request.source_type is not None:
            sop.source_type = request.source_type
        if request.status is not None:
            sop.status = "archived" if request.archived is True else request.status
            if request.status == "published":
                sop.last_reviewed_at = datetime.utcnow()
                sop.is_finalized = True
        if "last_reviewed_at" in fields_set:
            sop.last_reviewed_at = request.last_reviewed_at
        if "estimated_duration_minutes" in fields_set:
            sop.estimated_duration_minutes = request.estimated_duration_minutes
        sop.updated_by = user.id
        sop.updated_at = datetime.utcnow()
        await self.db.commit()
        await self.db.refresh(sop)
        return sop

    async def archive_sop(self, sop_id: str, user: User) -> None:
        sop = await self.get_sop(sop_id, user)
        if not can_edit_sop(sop, user):
            raise SOPManagementError("You do not have permission to archive this SOP", status_code=403)
        sop.archived = True
        sop.status = "archived"
        sop.updated_by = user.id
        sop.updated_at = datetime.utcnow()
        await self.db.commit()

    async def to_response(self, sop: SOP, user: User | None = None) -> SOPResponse:
        actor = user
        owner_email = None
        if sop.created_by:
            owner = await self.db.get(User, sop.created_by)
            owner_email = owner.email if owner else None
        if not owner_email and sop.video and sop.video.user_id:
            owner = await self.db.get(User, sop.video.user_id)
            owner_email = owner.email if owner else None

        return SOPResponse(
            id=sop.id,
            video_id=sop.video_id,
            sop=sop_json_for_user(sop.sop_json, actor),
            operator_sop=build_operator_sop(sop.sop_json),
            can_view_internal=can_view_internal_sop(actor),
            is_finalized=sop.is_finalized,
            created_at=sop.created_at,
            updated_at=sop.updated_at,
            folder_id=sop.folder_id,
            category=sop.category or "Uncategorized",
            tags=sop.tags_json or [],
            archived=bool(sop.archived),
            created_by=sop.created_by,
            updated_by=sop.updated_by,
            visibility_scope=sop.visibility_scope or "private",
            allowed_role_min=sop.allowed_role_min or "manager",
            shared_with_users=sop.shared_with_users_json or [],
            owner_email=owner_email,
            linked_workflows_count=0,
            linked_checklists_count=0,
            linked_training_count=0,
            source_type=sop.source_type or "ai_generated",
            status="archived" if sop.archived else ("published" if sop.is_finalized else (sop.status or "draft")),
            last_reviewed_at=sop.last_reviewed_at,
            estimated_duration_minutes=sop.estimated_duration_minutes,
        )
