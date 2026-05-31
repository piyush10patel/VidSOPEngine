"""Object storage abstraction — Cloudflare R2 in prod, local disk in dev.

The same interface is used by video_service (uploads), frame_extractor
(extracted frames), transcription/pipeline tasks (download for ffmpeg /
Whisper), and the frame-serving HTTP route (presigned URLs).

Selection is automatic: if r2_bucket + r2_endpoint are set, R2 is used;
otherwise we fall back to LocalStorage so local dev keeps working without
any cloud credentials.

Keys are POSIX-style paths inside the bucket:
  videos/{video_id}.{ext}
  frames/{video_id}/{frame_filename}
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class Storage(Protocol):
    """Common interface for video / frame object storage."""

    def upload_file(self, local_path: str, key: str, content_type: Optional[str] = None) -> str: ...

    def download_to_temp(self, key: str, suffix: str = "") -> str: ...

    def presigned_url(self, key: str, expires: Optional[int] = None) -> str: ...

    def delete_object(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int: ...

    def open_read(self, key: str): ...  # returns a binary file-like


# ----------------------------------------------------------------------
# Cloudflare R2 (S3-compatible) implementation
# ----------------------------------------------------------------------


class R2Storage:
    """Cloudflare R2 backend via the boto3 S3 client.

    Lazy-imports boto3 so the dev stack doesn't pay for it when LocalStorage
    is in use.
    """

    def __init__(self) -> None:
        self.bucket = settings.r2_bucket
        self.endpoint = settings.r2_endpoint
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=settings.r2_access_key_id,
                aws_secret_access_key=settings.r2_secret_access_key,
                config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
                region_name="auto",
            )
        return self._client

    def upload_file(self, local_path: str, key: str, content_type: Optional[str] = None) -> str:
        extra = {"ContentType": content_type} if content_type else {}
        self.client.upload_file(local_path, self.bucket, key, ExtraArgs=extra)
        return key

    def download_to_temp(self, key: str, suffix: str = "") -> str:
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        self.client.download_file(self.bucket, key, path)
        return path

    def presigned_url(self, key: str, expires: Optional[int] = None) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires or settings.r2_presigned_ttl_seconds,
        )

    def delete_object(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            logger.warning(f"[storage] delete_object({key}) failed: {e}")

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object whose key starts with `prefix`. Returns count."""
        count = 0
        try:
            paginator = self.client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                contents = page.get("Contents") or []
                if not contents:
                    continue
                self.client.delete_objects(
                    Bucket=self.bucket,
                    Delete={"Objects": [{"Key": obj["Key"]} for obj in contents]},
                )
                count += len(contents)
        except Exception as e:
            logger.warning(f"[storage] delete_prefix({prefix}) failed: {e}")
        return count

    def open_read(self, key: str):
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"]


# ----------------------------------------------------------------------
# Local-disk fallback (dev / when R2 isn't configured)
# ----------------------------------------------------------------------


class LocalStorage:
    """Local-disk storage rooted at settings.upload_dir.

    The full key (e.g. 'videos/abc.mp4') becomes a relative path under
    upload_dir's parent so 'frames/{id}/...' and 'videos/{id}...' coexist
    in the same data dir.
    """

    def __init__(self) -> None:
        # `upload_dir` is e.g. './data/videos' — parent is the data root.
        self.root = Path(settings.upload_dir).parent

    def _resolve(self, key: str) -> Path:
        return self.root / key

    def upload_file(self, local_path: str, key: str, content_type: Optional[str] = None) -> str:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if Path(local_path).resolve() != dest.resolve():
            shutil.copyfile(local_path, dest)
        return key

    def download_to_temp(self, key: str, suffix: str = "") -> str:
        src = self._resolve(key)
        if not src.exists():
            raise FileNotFoundError(f"Local storage key not found: {key}")
        # For local storage the source IS already on disk — just return its
        # path. Callers that delete the temp will fail; document accordingly.
        return str(src)

    def presigned_url(self, key: str, expires: Optional[int] = None) -> str:
        # Local mode — there's no real signed URL. Callers of this URL on
        # the dev frontend hit the FastAPI passthrough route, which reads
        # the file directly from disk.
        return f"/storage/{key}"

    def delete_object(self, key: str) -> None:
        try:
            self._resolve(key).unlink(missing_ok=True)
        except Exception as e:
            logger.warning(f"[storage] local delete_object({key}) failed: {e}")

    def delete_prefix(self, prefix: str) -> int:
        target = self._resolve(prefix)
        if not target.exists():
            return 0
        count = 0
        if target.is_dir():
            for entry in target.rglob("*"):
                if entry.is_file():
                    try:
                        entry.unlink()
                        count += 1
                    except Exception:
                        pass
        else:
            target.unlink(missing_ok=True)
            count = 1
        return count

    def open_read(self, key: str):
        return open(self._resolve(key), "rb")


# ----------------------------------------------------------------------
# Module singleton
# ----------------------------------------------------------------------


_storage: Optional[Storage] = None


def get_storage() -> Storage:
    """Return the active storage backend. R2 when configured, local otherwise."""
    global _storage
    if _storage is None:
        if settings.r2_bucket and settings.r2_endpoint and settings.r2_access_key_id:
            _storage = R2Storage()
            logger.info(f"[storage] using R2 bucket={settings.r2_bucket}")
        else:
            _storage = LocalStorage()
            logger.info(f"[storage] using LocalStorage root={Path(settings.upload_dir).parent}")
    return _storage


def is_remote_storage() -> bool:
    """True when the active backend is R2 (i.e. videos/frames are not on local disk)."""
    return isinstance(get_storage(), R2Storage)


# ----------------------------------------------------------------------
# Key helpers — keep storage layout decisions in one place.
# ----------------------------------------------------------------------


def video_key(video_id: str, ext: str) -> str:
    """Storage key for an uploaded video file."""
    ext = ext if ext.startswith(".") else f".{ext}"
    return f"videos/{video_id}{ext}"


def frame_key(video_id: str, frame_filename: str) -> str:
    """Storage key for an extracted frame."""
    return f"frames/{video_id}/{frame_filename}"


def sop_step_image_key(filename: str) -> str:
    """Storage key for a manually-uploaded SOP step image."""
    return f"sop-step-images/{filename}"


def video_prefix() -> str:
    return "videos/"


def frames_prefix() -> str:
    return "frames/"
