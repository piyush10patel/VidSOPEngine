"""Video service for file validation, upload, and management."""
import hashlib
import os
import tempfile
import aiofiles
from pathlib import Path
from typing import List, Optional, Tuple
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.errors import InvalidFileFormatError, FileTooLargeError, VideoNotFoundError
from app.models.video import Video, VideoStatus, VideoType, PipelineComplexity
from app.services.storage import get_storage, video_key


class VideoService:
    """Service for video file operations."""

    # Allowed video file extensions
    ALLOWED_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv'}

    def __init__(self, db: AsyncSession):
        """Initialize video service with database session."""
        self.db = db

    @classmethod
    def validate_file_format(cls, filename: str) -> Tuple[bool, Optional[str]]:
        """
        Validate that the file has an allowed video format.

        Args:
            filename: The name of the file to validate

        Returns:
            Tuple of (is_valid, extension) where extension is lowercase if valid
        """
        if not filename:
            return False, None

        # Get the file extension (lowercase)
        ext = Path(filename).suffix.lower()

        if ext in cls.ALLOWED_EXTENSIONS:
            return True, ext
        return False, ext

    @classmethod
    def validate_file_size(cls, file_size: int, max_size_bytes: Optional[int] = None) -> bool:
        """
        Validate that the file size is within the configured limit.

        Args:
            file_size: Size of the file in bytes
            max_size_bytes: Maximum allowed size in bytes (uses config default if None)

        Returns:
            True if file size is valid, False otherwise
        """
        if max_size_bytes is None:
            max_size_bytes = settings.max_file_size_bytes

        return file_size <= max_size_bytes

    @classmethod
    def validate_file(
        cls,
        filename: str,
        file_size: int,
        max_size_bytes: Optional[int] = None
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Validate both file format and size.

        Args:
            filename: The name of the file to validate
            file_size: Size of the file in bytes
            max_size_bytes: Maximum allowed size in bytes (uses config default if None)

        Returns:
            Tuple of (is_valid, error_message, extension)
        """
        # Validate format first
        format_valid, ext = cls.validate_file_format(filename)
        if not format_valid:
            return False, f"Invalid file format '{ext}'. Allowed: {', '.join(sorted(cls.ALLOWED_EXTENSIONS))}", ext

        # Validate size
        if max_size_bytes is None:
            max_size_bytes = settings.max_file_size_bytes

        if not cls.validate_file_size(file_size, max_size_bytes):
            return False, f"File size ({file_size} bytes) exceeds maximum ({max_size_bytes} bytes)", ext

        return True, "", ext

    async def upload(
        self,
        file: UploadFile,
        title: Optional[str] = None,
        user_id: Optional[str] = None,
        video_type: VideoType = VideoType.PHYSICAL,
        pipeline_complexity: PipelineComplexity = PipelineComplexity.AUTO,
    ) -> Video:
        """
        Upload a video file and create database record.

        Streams the file to disk in 1 MB chunks to avoid loading large
        files into RAM. Rejects files that exceed MAX_FILE_SIZE_MB mid-stream.

        Raises:
            InvalidFileFormatError: If file format is not allowed
            FileTooLargeError: If file exceeds size limit
        """
        # Validate format before touching disk
        format_valid, ext = self.validate_file_format(file.filename or "")
        if not format_valid:
            raise InvalidFileFormatError(
                filename=file.filename or "",
                allowed_formats=list(self.ALLOWED_EXTENSIONS)
            )

        video_id = str(uuid4())
        # Stream to a temp file first so we can enforce the size cap without
        # buffering in memory. Then upload to the active storage backend
        # (R2 in prod, LocalStorage in dev - same interface). The DB stores
        # the storage KEY in `file_path`; downstream code resolves that to
        # either an R2 object or a local file via storage.download_to_temp().
        os.makedirs(settings.upload_dir, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext, dir=settings.upload_dir)
        os.close(tmp_fd)

        max_bytes = settings.max_file_size_bytes
        file_size = 0
        sha = hashlib.sha256()

        try:
            async with aiofiles.open(tmp_path, 'wb') as f:
                while True:
                    chunk = await file.read(1024 * 1024)  # 1 MB
                    if not chunk:
                        break
                    file_size += len(chunk)
                    if file_size > max_bytes:
                        await f.close()
                        os.remove(tmp_path)
                        raise FileTooLargeError(
                            file_size=file_size,
                            max_size=max_bytes
                        )
                    sha.update(chunk)
                    await f.write(chunk)
        except FileTooLargeError:
            raise
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        content_hash = sha.hexdigest()

        # Idempotency: if THIS user already uploaded a file with the same
        # content, return that video instead of double-storing. INV-17.
        if user_id:
            existing = await self.db.execute(
                select(Video).where(
                    Video.user_id == user_id,
                    Video.content_hash == content_hash,
                )
            )
            dup = existing.scalar_one_or_none()
            if dup:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass
                return dup

        # Hand off to the storage backend.
        storage = get_storage()
        key = video_key(video_id, ext)
        try:
            storage.upload_file(tmp_path, key, content_type="video/mp4")
        finally:
            # On R2 the upload copied the file - we can drop the temp.
            # On LocalStorage upload_file already moved/copied it; if the
            # upload pointed somewhere else we still want to clean the temp.
            if os.path.exists(tmp_path):
                # Local storage may have its destination = tmp_path's parent
                # if the resolved root coincides; only delete if distinct.
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

        # Create database record - file_path stores the STORAGE KEY now.
        from app.services.cleanup_service import default_artifact_status

        video = Video(
            id=video_id,
            user_id=user_id,
            title=title or file.filename or "Untitled",
            filename=file.filename or "unknown",
            file_path=key,
            status=VideoStatus.UPLOADED.value,
            video_type=video_type.value if isinstance(video_type, VideoType) else video_type,
            pipeline_complexity=(
                pipeline_complexity.value
                if isinstance(pipeline_complexity, PipelineComplexity)
                else pipeline_complexity
            ),
            content_hash=content_hash,
            artifact_generation_status=default_artifact_status(),
            cleanup_eligible=False,
        )

        self.db.add(video)
        await self.db.commit()
        await self.db.refresh(video)

        return video

    async def get_by_id(self, video_id: str) -> Video:
        """
        Get a video by ID.

        Args:
            video_id: The video ID

        Returns:
            The Video record

        Raises:
            VideoNotFoundError: If video not found
        """
        result = await self.db.execute(
            select(Video).options(
                selectinload(Video.transcript),
                selectinload(Video.sop)
            ).where(Video.id == video_id)
        )
        video = result.scalar_one_or_none()

        if not video:
            raise VideoNotFoundError(video_id)

        return video

    async def list_all(self, user_id: Optional[str] = None) -> List[Video]:
        """
        List all videos, optionally filtered by user.

        Args:
            user_id: Optional user ID to filter by

        Returns:
            List of Video records
        """
        query = select(Video).options(
            selectinload(Video.transcript),
            selectinload(Video.sop)
        ).order_by(Video.created_at.desc())

        if user_id:
            query = query.where(Video.user_id == user_id)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    async def update_status(self, video_id: str, status: VideoStatus) -> Video:
        """
        Update video processing status.

        Args:
            video_id: The video ID
            status: The new status

        Returns:
            The updated Video record

        Raises:
            VideoNotFoundError: If video not found
        """
        video = await self.get_by_id(video_id)
        video.status = status.value if isinstance(status, VideoStatus) else status
        await self.db.commit()
        await self.db.refresh(video)
        return video
