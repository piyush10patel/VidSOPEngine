"""Media artifact cleanup - runs AFTER successful SOP generation.

Once a SOP is persisted, the raw media is no longer the source of truth.
This module deletes:
    - the uploaded source video
    - extracted frames (in storage)
    - local /tmp scratch dirs (downloaded videos, frame caches)

while preserving everything operational:
    - SOPs / workflows / checklists / training modules
    - execution runs
    - metrics and version history
    - Postgres rows in general

Cleanup is config-gated, NEVER raises, and tolerates missing files. It runs
only AFTER persistence and observability logging have completed.
"""
from __future__ import annotations

import gc
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from app.core.config import settings
from app.services.storage import frames_prefix, get_storage, video_key

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Delayed media cleanup orchestration
# ----------------------------------------------------------------------


ARTIFACT_STAGES = ("sop", "workflows", "checklists", "training")


def default_artifact_status() -> dict:
    return {
        stage: {"status": "pending", "updated_at": None, "error": None}
        for stage in ARTIFACT_STAGES
    }


def merge_artifact_status(existing: Optional[dict]) -> dict:
    merged = default_artifact_status()
    if isinstance(existing, dict):
        for stage, value in existing.items():
            if stage in merged and isinstance(value, dict):
                merged[stage].update(value)
    return merged


def all_artifacts_successful(statuses: Optional[dict]) -> bool:
    merged = merge_artifact_status(statuses)
    return all(merged[stage].get("status") == "success" for stage in ARTIFACT_STAGES)


async def mark_artifact_status(
    db,
    video_id: str,
    stage: str,
    status: str,
    error: Optional[str] = None,
):
    """Persist generation progress for one downstream artifact stage."""
    if stage not in ARTIFACT_STAGES:
        raise ValueError(f"Unknown artifact stage: {stage}")

    from sqlalchemy import select
    from app.models.video import Video

    result = await db.execute(select(Video).where(Video.id == video_id))
    video = result.scalar_one_or_none()
    if not video:
        return None

    statuses = merge_artifact_status(video.artifact_generation_status)
    statuses[stage] = {
        "status": status,
        "updated_at": datetime.utcnow().isoformat(),
        "error": str(error)[:500] if error else None,
    }
    video.artifact_generation_status = statuses
    video.cleanup_eligible = all_artifacts_successful(statuses)
    if status == "failed":
        video.cleanup_last_error = str(error)[:500] if error else f"{stage} failed"
    await db.commit()
    await db.refresh(video)
    return video


async def refresh_cleanup_eligibility(db, video_id: str):
    from sqlalchemy import select
    from app.models.video import Video

    result = await db.execute(select(Video).where(Video.id == video_id))
    video = result.scalar_one_or_none()
    if not video:
        return None
    video.artifact_generation_status = merge_artifact_status(video.artifact_generation_status)
    video.cleanup_eligible = all_artifacts_successful(video.artifact_generation_status)
    await db.commit()
    await db.refresh(video)
    return video


async def maybe_cleanup_video_media(db, video_id: str) -> dict:
    """Idempotently delete media only after every downstream artifact succeeds."""
    from sqlalchemy import select
    from app.models.video import Video

    result = await db.execute(select(Video).where(Video.id == video_id))
    video = result.scalar_one_or_none()
    if not video:
        return {"skipped": True, "reason": "video_not_found"}

    if video.cleanup_completed_at:
        return {"skipped": True, "reason": "already_completed"}

    statuses = merge_artifact_status(video.artifact_generation_status)
    video.artifact_generation_status = statuses
    video.cleanup_eligible = all_artifacts_successful(statuses)
    if not video.cleanup_eligible:
        await db.commit()
        return {"skipped": True, "reason": "artifacts_not_complete", "statuses": statuses}

    video.cleanup_attempts = int(video.cleanup_attempts or 0) + 1
    await db.commit()

    stats = cleanup_processing_artifacts(video_id, video_file_path=video.file_path)
    if stats.get("errors"):
        video.cleanup_last_error = f"Cleanup completed with {stats['errors']} error(s)"
    else:
        video.cleanup_last_error = None
        video.cleanup_completed_at = datetime.utcnow()
    await db.commit()
    await db.refresh(video)
    return {"skipped": False, "stats": stats}


# ----------------------------------------------------------------------
# Artifact tracking
# ----------------------------------------------------------------------


@dataclass
class ProcessingArtifactRegistry:
    """Tracks artifacts created during a single pipeline run.

    Pipelines that want deterministic cleanup populate this object as they
    work; the cleanup function then walks it. When no registry is provided,
    cleanup_processing_artifacts() falls back to convention-based discovery
    (videos/<id>.*, frames/<id>/*, /tmp scratch).
    """

    video_id: str
    video_storage_key: Optional[str] = None
    frame_storage_keys: List[str] = field(default_factory=list)
    local_video_paths: List[str] = field(default_factory=list)
    local_frame_dirs: List[str] = field(default_factory=list)
    preview_frame_keys: List[str] = field(default_factory=list)

    def register_video(self, *, key: Optional[str] = None, local_path: Optional[str] = None) -> None:
        if key:
            self.video_storage_key = key
        if local_path and local_path not in self.local_video_paths:
            self.local_video_paths.append(local_path)

    def register_frame(self, *, key: Optional[str] = None, local_dir: Optional[str] = None) -> None:
        if key and key not in self.frame_storage_keys:
            self.frame_storage_keys.append(key)
        if local_dir and local_dir not in self.local_frame_dirs:
            self.local_frame_dirs.append(local_dir)

    def mark_preview(self, key: str) -> None:
        if key not in self.preview_frame_keys:
            self.preview_frame_keys.append(key)


# ----------------------------------------------------------------------
# Thumbnails (optional preview retention)
# ----------------------------------------------------------------------


def make_preview_thumbnails(
    local_frame_paths: List[str],
    *,
    max_count: Optional[int] = None,
    max_width: int = 320,
    jpeg_quality: int = 70,
) -> List[str]:
    """Resize first N frames into compressed thumbnails for SOP cards.

    Returns paths to the new thumbnail files (caller is expected to upload
    them to storage). Original frames are NOT deleted by this function.
    Pillow is the only dep; if unavailable we silently skip.
    """
    try:
        from PIL import Image
    except ImportError:
        logger.warning("[cleanup] Pillow not available; skipping thumbnail creation")
        return []

    cap = max_count if max_count is not None else settings.max_retained_preview_frames
    cap = max(0, int(cap))
    thumbs: List[str] = []
    for p in local_frame_paths[:cap]:
        try:
            img = Image.open(p)
            w, h = img.size
            if w > max_width:
                scale = max_width / w
                img = img.resize((max_width, int(h * scale)), Image.LANCZOS)
            thumb_path = p.replace(".jpg", "_thumb.jpg")
            img.save(thumb_path, "JPEG", quality=jpeg_quality, optimize=True)
            thumbs.append(thumb_path)
        except Exception as e:
            logger.warning(f"[cleanup] thumbnail creation failed: {p}: {e}")
    return thumbs


# ----------------------------------------------------------------------
# Per-run cleanup
# ----------------------------------------------------------------------


def _delete_storage_object(storage, key: str, stats: dict) -> None:
    try:
        storage.delete_object(key)
        logger.info(f"[cleanup] Deleted temporary artifact: {key}")
    except Exception as e:
        logger.warning(f"[cleanup] Cleanup failed: {key}: {e}")
        stats["errors"] += 1


def _delete_local_path(path: str, stats: dict) -> bool:
    """Delete a local file or directory. Returns True on success."""
    try:
        if not os.path.exists(path):
            return False
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=False)
        else:
            os.unlink(path)
        logger.info(f"[cleanup] Deleted temporary artifact: {path}")
        return True
    except Exception as e:
        logger.warning(f"[cleanup] Cleanup failed: {path}: {e}")
        stats["errors"] += 1
        return False


def cleanup_processing_artifacts(
    video_id: str,
    *,
    video_file_path: Optional[str] = None,
    registry: Optional[ProcessingArtifactRegistry] = None,
) -> dict:
    """Delete media artifacts after persistence + observability succeed.

    Args:
        video_id: the video this run was for
        video_file_path: the Video.file_path value (a storage key like
            'videos/<id>.mp4', OR a legacy local path). Optional; helpful
            when no registry is supplied.
        registry: optional explicit artifact tracker

    Honours these settings:
        delete_source_video_after_processing
        delete_extracted_frames_after_processing
        delete_temp_processing_dirs
        retain_preview_keyframes
        max_retained_preview_frames
        retain_source_video           (overrides delete when True)

    NEVER raises. Failures are logged and counted in `stats['errors']`.
    """
    stats = {
        "video_deleted": False,
        "frames_deleted": 0,
        "temp_files_deleted": 0,
        "temp_dirs_deleted": 0,
        "errors": 0,
    }
    storage = get_storage()

    # ---------- 1. Source video ----------
    if (
        settings.delete_source_video_after_processing
        and not settings.retain_source_video
    ):
        # Prefer registry -> explicit param -> no-op
        candidate_keys: List[str] = []
        candidate_local: List[str] = []
        if registry and registry.video_storage_key:
            candidate_keys.append(registry.video_storage_key)
        if registry and registry.local_video_paths:
            candidate_local.extend(registry.local_video_paths)
        if video_file_path:
            if os.path.exists(video_file_path):
                candidate_local.append(video_file_path)
            else:
                # Treat as a storage key
                if video_file_path not in candidate_keys:
                    candidate_keys.append(video_file_path)

        for key in candidate_keys:
            _delete_storage_object(storage, key, stats)
            stats["video_deleted"] = True

        for path in candidate_local:
            if _delete_local_path(path, stats):
                stats["temp_files_deleted"] += 1
                stats["video_deleted"] = True

    # ---------- 2. Extracted frames ----------
    if settings.delete_extracted_frames_after_processing:
        prefix = frames_prefix() + f"{video_id}/"
        preview = set(registry.preview_frame_keys) if registry else set()

        if registry and registry.frame_storage_keys and preview:
            # Per-key delete so we can spare previews.
            for k in registry.frame_storage_keys:
                if k in preview:
                    continue
                _delete_storage_object(storage, k, stats)
                stats["frames_deleted"] += 1
        else:
            # Bulk prefix delete - fastest on R2.
            try:
                count = storage.delete_prefix(prefix)
                stats["frames_deleted"] = count
                if count:
                    logger.info(f"[cleanup] Deleted {count} frame(s) under {prefix}")
            except Exception as e:
                logger.warning(f"[cleanup] Cleanup failed: prefix {prefix}: {e}")
                stats["errors"] += 1

    # ---------- 3. Local /tmp scratch ----------
    if settings.delete_temp_processing_dirs:
        local_paths = list(registry.local_video_paths if registry else [])
        local_dirs = list(registry.local_frame_dirs if registry else [])

        # Convention: FrameExtractor writes to {upload_dir.parent}/frames/{id}/
        derived_frames_dir = Path(settings.upload_dir).parent / "frames" / video_id
        if derived_frames_dir.exists() and str(derived_frames_dir) not in local_dirs:
            local_dirs.append(str(derived_frames_dir))

        for path in local_paths:
            if _delete_local_path(path, stats):
                stats["temp_files_deleted"] += 1

        for d in local_dirs:
            if _delete_local_path(d, stats):
                stats["temp_dirs_deleted"] += 1

    # ---------- 4. Memory release hint ----------
    gc.collect()

    return stats


# ----------------------------------------------------------------------
# Stale-file sweep (startup hook)
# ----------------------------------------------------------------------


def cleanup_stale_processing_files() -> dict:
    """Sweep ephemeral scratch dirs for files older than the configured age.

    Catches:
      - downloaded source videos from previously-crashed runs
      - frame dirs from runs that crashed before cleanup finished
      - any orphaned temp files in upload_dir / frames / temp / processing / cache

    Runs on app startup. Gated on `cleanup_stale_processing_files` setting.
    """
    if not settings.cleanup_stale_processing_files:
        return {"skipped": True}

    cutoff = time.time() - max(1, settings.cleanup_stale_file_age_hours) * 3600
    stats = {"files_deleted": 0, "dirs_deleted": 0, "errors": 0}

    upload_root = Path(settings.upload_dir)
    candidate_dirs = [
        upload_root,
        upload_root.parent / "frames",
        upload_root.parent / "temp",
        upload_root.parent / "processing",
        upload_root.parent / "cache",
    ]

    for d in candidate_dirs:
        if not d.exists():
            continue
        # Stale files
        for entry in list(d.rglob("*")):
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    stats["files_deleted"] += 1
            except Exception as e:
                logger.warning(f"[cleanup] stale file removal failed: {entry}: {e}")
                stats["errors"] += 1
        # Empty directories - deepest-first so parents become candidates too.
        for entry in sorted(d.rglob("*"), key=lambda p: -len(p.parts)):
            try:
                if entry.is_dir() and not any(entry.iterdir()):
                    entry.rmdir()
                    stats["dirs_deleted"] += 1
            except Exception:
                pass

    if stats["files_deleted"] or stats["dirs_deleted"]:
        logger.info(
            f"[cleanup] stale sweep removed {stats['files_deleted']} file(s), "
            f"{stats['dirs_deleted']} dir(s)"
        )
    return stats


# ----------------------------------------------------------------------
# Memory cleanup
# ----------------------------------------------------------------------


# Heavy debug attributes the atomic + procedural pipelines stash on SOPSchema
# instances for observability. After Braintrust + auto-capture have logged
# them, they're safe to drop.
_DEBUG_SCHEMA_ATTRS = (
    "_frame_observations",
    "_action_timeline",
    "_state_changes",
    "_object_interactions",
    "_contact_events",
    "_motion_records",
    "_action_boundaries",
    "_transition_windows",
    "_object_trajectories",
    "_scene_graph",
    "_transcript",
    "_diagnoses",
)


def free_pipeline_memory(*objects, drop_metrics: bool = False) -> None:
    """Drop heavy debug attributes from SOPSchema instances and gc.collect().

    Pass any number of objects - typically the SOPSchema returned from a
    pipeline. Each is scanned for known debug attributes and they're removed
    if present. `_adaptive_metrics` is preserved by default (some downstream
    code reads it); pass drop_metrics=True to drop it too.
    """
    attrs: Iterable[str] = _DEBUG_SCHEMA_ATTRS
    if drop_metrics:
        attrs = list(_DEBUG_SCHEMA_ATTRS) + ["_adaptive_metrics"]

    for obj in objects:
        if obj is None:
            continue
        for attr in attrs:
            if hasattr(obj, attr):
                try:
                    delattr(obj, attr)
                except Exception:
                    pass
    gc.collect()
