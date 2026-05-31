"""Managed SOP library endpoints."""
import logging
import os
import tempfile
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.base import get_db
from app.models.user import User
from app.routers.auth import get_current_user, require_manager_or_admin
from app.schemas.sop import (
    SOPCreateRequest,
    SOPFolderCreateRequest,
    SOPFolderListResponse,
    SOPFolderResponse,
    SOPFolderUpdateRequest,
    SOPListResponse,
    SOPResponse,
    SOPUpdateRequest,
)
from app.services.sop_management_service import SOPManagementService

logger = logging.getLogger(__name__)


router = APIRouter(tags=["sops"])


_ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
_MAX_STEP_IMAGE_BYTES = 8 * 1024 * 1024  # 8MB


def get_sop_management_service(
    db: AsyncSession = Depends(get_db),
) -> SOPManagementService:
    return SOPManagementService(db)


@router.get("/sops", response_model=SOPListResponse)
async def list_sops(
    search: Optional[str] = None,
    tag: Optional[str] = None,
    category: Optional[str] = None,
    folder_id: Optional[str] = None,
    archived: bool = False,
    service: SOPManagementService = Depends(get_sop_management_service),
    current_user: User = Depends(get_current_user),
) -> SOPListResponse:
    rows = await service.list_sops(
        current_user,
        search=search,
        tag=tag,
        category=category,
        folder_id=folder_id,
        archived=archived,
    )
    return SOPListResponse(
        sops=[await service.to_response(row, current_user) for row in rows],
        total=len(rows),
    )


@router.post("/sops", response_model=SOPResponse, status_code=status.HTTP_201_CREATED)
async def create_sop(
    request: SOPCreateRequest,
    service: SOPManagementService = Depends(get_sop_management_service),
    current_user: User = Depends(require_manager_or_admin()),
) -> SOPResponse:
    sop = await service.create_sop(request, current_user)
    return await service.to_response(sop, current_user)


@router.get("/sops/{sop_id}", response_model=SOPResponse)
async def get_sop(
    sop_id: str,
    service: SOPManagementService = Depends(get_sop_management_service),
    current_user: User = Depends(get_current_user),
) -> SOPResponse:
    sop = await service.get_sop(sop_id, current_user)
    return await service.to_response(sop, current_user)


@router.put("/sops/{sop_id}", response_model=SOPResponse)
async def update_sop(
    sop_id: str,
    request: SOPUpdateRequest,
    service: SOPManagementService = Depends(get_sop_management_service),
    current_user: User = Depends(require_manager_or_admin()),
) -> SOPResponse:
    sop = await service.update_sop(sop_id, request, current_user)
    return await service.to_response(sop, current_user)


@router.post("/sops/{sop_id}/publish", response_model=SOPResponse)
async def publish_sop(
    sop_id: str,
    service: SOPManagementService = Depends(get_sop_management_service),
    current_user: User = Depends(require_manager_or_admin()),
) -> SOPResponse:
    request = SOPUpdateRequest(status="published", archived=False)
    sop = await service.update_sop(sop_id, request, current_user)
    return await service.to_response(sop, current_user)


@router.delete("/sops/{sop_id}", status_code=status.HTTP_204_NO_CONTENT)
async def archive_sop(
    sop_id: str,
    service: SOPManagementService = Depends(get_sop_management_service),
    current_user: User = Depends(require_manager_or_admin()),
) -> None:
    await service.archive_sop(sop_id, current_user)


@router.get("/sop-folders", response_model=SOPFolderListResponse)
async def list_sop_folders(
    service: SOPManagementService = Depends(get_sop_management_service),
    current_user: User = Depends(get_current_user),
) -> SOPFolderListResponse:
    folders = await service.list_folders(current_user.id)
    return SOPFolderListResponse(
        folders=[SOPFolderResponse.model_validate(folder) for folder in folders],
        total=len(folders),
    )


@router.post(
    "/sop-folders",
    response_model=SOPFolderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_sop_folder(
    request: SOPFolderCreateRequest,
    service: SOPManagementService = Depends(get_sop_management_service),
    current_user: User = Depends(require_manager_or_admin()),
) -> SOPFolderResponse:
    folder = await service.create_folder(current_user.id, request.name, request.parent_id)
    return SOPFolderResponse.model_validate(folder)


@router.put("/sop-folders/{folder_id}", response_model=SOPFolderResponse)
async def update_sop_folder(
    folder_id: str,
    request: SOPFolderUpdateRequest,
    service: SOPManagementService = Depends(get_sop_management_service),
    current_user: User = Depends(require_manager_or_admin()),
) -> SOPFolderResponse:
    folder = await service.update_folder(
        folder_id,
        current_user.id,
        request.name,
        request.parent_id,
    )
    return SOPFolderResponse.model_validate(folder)


@router.delete("/sop-folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sop_folder(
    folder_id: str,
    service: SOPManagementService = Depends(get_sop_management_service),
    current_user: User = Depends(require_manager_or_admin()),
) -> None:
    await service.delete_folder(folder_id, current_user.id)


@router.post("/sops/step-images", status_code=status.HTTP_201_CREATED)
async def upload_step_image(
    file: UploadFile = File(...),
    current_user: User = Depends(require_manager_or_admin()),
):
    """
    Upload an image for a manually-created SOP step.

    Accepts image/jpeg, image/png, image/webp, image/heic, image/heif.
    Returns a relative URL the frontend stores on SOPStep.image_url.
    The image is served back via GET /sops/step-images/{filename} so
    cross-origin requests stay on the same API origin.
    """
    content_type = (file.content_type or "").lower()
    if content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported image type '{content_type}'. Allowed: {sorted(_ALLOWED_IMAGE_TYPES)}",
        )
    ext = _ALLOWED_IMAGE_TYPES[content_type]
    filename = f"{uuid.uuid4().hex}{ext}"

    from app.services.storage import get_storage, sop_step_image_key

    # Buffer to a tempfile so we can both size-check and let the storage
    # backend stream from disk. The 8MB cap matches the documented mobile
    # capture flow — phones routinely emit ~5MB HEIC images.
    fd, tmp_path = tempfile.mkstemp(suffix=ext)
    os.close(fd)
    total = 0
    try:
        with open(tmp_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 64)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_STEP_IMAGE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Image too large (max {_MAX_STEP_IMAGE_BYTES // (1024 * 1024)}MB)",
                    )
                out.write(chunk)

        key = sop_step_image_key(filename)
        storage = get_storage()
        storage.upload_file(tmp_path, key, content_type=content_type)
        logger.info(
            f"[sops] step-image uploaded user={current_user.id} key={key} bytes={total}"
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return {"image_url": f"/sops/step-images/{filename}", "filename": filename}


@router.get("/sops/step-images/{filename}")
async def get_step_image(filename: str):
    """
    Serve a step image that was uploaded via POST /sops/step-images.

    Unauthenticated by design — the filename is a 32-char UUID that is
    effectively unguessable, and the image_url only appears inside an SOP
    that the viewer must already have access to. Matches the existing
    /videos/{id}/frames/{name} pattern so PDF export (html2canvas with
    crossOrigin="anonymous") can pull the asset without auth headers.
    """
    # Path-traversal guard
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    from app.services.storage import get_storage, is_remote_storage, sop_step_image_key

    key = sop_step_image_key(filename)
    storage = get_storage()

    if is_remote_storage():
        try:
            body = storage.open_read(key)
        except Exception:
            raise HTTPException(status_code=404, detail="Image not found")
        ext = filename.rsplit(".", 1)[-1].lower()
        media_type = {
            "png": "image/png",
            "webp": "image/webp",
        }.get(ext, "image/jpeg")
        return StreamingResponse(
            body,
            media_type=media_type,
            headers={"Cache-Control": "public, max-age=3600"},
        )

    # Local-disk fallback
    from pathlib import Path

    local_path = Path(settings.upload_dir).parent / key
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(str(local_path))
