"""Frame extraction service using PySceneDetect + FFmpeg."""
import os
import subprocess
import base64
import logging
import time
from pathlib import Path
from typing import List, Optional

from app.core.config import settings
from app.services.storage import frame_key as storage_frame_key, get_storage

logger = logging.getLogger(__name__)

# Max dimension to resize frames before sending to vision API.
# Reduces base64 payload without losing procedural detail.
MAX_FRAME_DIM = 1280


class FrameExtractor:
    """Extract key frames from videos.

    Primary strategy: PySceneDetect — picks one frame per scene boundary
    so every frame represents an actual action transition.
    Fallback: evenly-spaced FFmpeg extraction when scene detection fails
    or returns too few frames.
    """

    def __init__(self, frames_dir: Optional[str] = None):
        self.frames_dir = frames_dir or f"{settings.upload_dir}/frames"
        os.makedirs(self.frames_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Duration-aware frame budgeting
    # ------------------------------------------------------------------

    def probe_duration_seconds(self, video_path: str) -> Optional[float]:
        """Return the video's duration via ffprobe, or None if unreadable.

        Resolves storage keys to a local tempfile first so this works
        identically in dev (local disk) and prod (R2). Failures are
        non-fatal — the caller falls back to the static frame count when
        the probe can't read the duration.
        """
        local_video_path, cleanup = self._resolve_to_local(video_path)
        try:
            try:
                result = subprocess.run(
                    [
                        "ffprobe", "-v", "error",
                        "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1",
                        local_video_path,
                    ],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode != 0:
                    logger.warning(
                        "ffprobe failed for %s: %s", video_path, result.stderr.strip()[:200],
                    )
                    return None
                value = (result.stdout or "").strip()
                if not value:
                    return None
                return float(value)
            except (subprocess.TimeoutExpired, ValueError) as e:
                logger.warning("ffprobe could not read duration of %s: %s", video_path, e)
                return None
        finally:
            if cleanup and os.path.exists(local_video_path):
                try:
                    os.remove(local_video_path)
                except Exception:
                    pass

    def adaptive_frame_count(self, video_path: str) -> int:
        """Pick a frame count proportional to video duration.

        Returns ``settings.procedural_frame_count`` when adaptive sampling
        is disabled or the duration probe fails — preserves the legacy
        behaviour as a safe fallback.

        The default formula is ``round(duration / seconds_per_frame)``
        clamped to ``[procedural_frame_min, procedural_frame_max]``. With
        the default 3.5s/frame budget:

          9s  video  -> max(8, round(9/3.5))   = 8 frames
          30s video  -> max(8, round(30/3.5))  = 9 frames
          60s video  -> max(8, round(60/3.5))  = 17 frames
          120s video -> max(8, round(120/3.5)) = 34 frames
          300s video -> min(40, round(...))    = 40 frames (capped)
        """
        if not settings.procedural_adaptive_frames:
            return settings.procedural_frame_count
        duration = self.probe_duration_seconds(video_path)
        if duration is None or duration <= 0:
            logger.info(
                "[frames] duration probe failed; falling back to static count=%d",
                settings.procedural_frame_count,
            )
            return settings.procedural_frame_count
        target = round(duration / max(0.5, settings.procedural_seconds_per_frame))
        budget = max(settings.procedural_frame_min, min(settings.procedural_frame_max, target))
        logger.info(
            "[frames] adaptive count: duration=%.1fs target=%d clamped=%d (sec/frame=%.1f)",
            duration, target, budget, settings.procedural_seconds_per_frame,
        )
        return budget

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_dense_frames(
        self,
        video_path: str,
        video_id: str,
        num_frames: Optional[int] = None,
    ) -> List[str]:
        """Dense extraction (12–24 frames) for atomic_simple tasks.

        Thin wrapper around extract_frames with the count clamped to the
        adaptive-granularity range. Defaults to settings.atomic_simple_dense_frames.
        Procedural videos should keep using extract_frames(num_frames=8).
        """
        n = num_frames if num_frames is not None else (settings.atomic_simple_dense_frames or 16)
        n = max(12, min(24, n))
        return self.extract_frames(video_path, video_id, num_frames=n)

    def extract_adaptive_fps_frames(
        self,
        video_path: str,
        video_id: str,
        fps: float = 1.5,
        max_frames: int = 32,
    ) -> List[str]:
        """Continuous low-FPS extraction for atomic_simple tasks.

        Captures micro-actions that sparse keyframes miss (cap removal,
        quick presses, rapid hand transitions). Uses a fixed FPS rather than
        scene detection. Hard-capped at `max_frames` to keep token usage
        bounded — for tasks longer than (max_frames / fps) seconds we
        truncate to the most recent frames after extraction.
        """
        fps = max(0.25, min(4.0, float(fps)))
        max_frames = max(12, min(48, int(max_frames)))
        output_dir = f"{self.frames_dir}/{video_id}"
        os.makedirs(output_dir, exist_ok=True)
        self._clear_existing_frames(output_dir)
        local_video_path, _cleanup_video = self._resolve_to_local(video_path)
        try:
            frames = self._extract_interval_frames(
                local_video_path, output_dir, num_frames=max_frames, fps=fps,
            )
            if len(frames) > max_frames:
                # ffmpeg may have written more — trim to the cap, evenly sampled.
                step = len(frames) / max_frames
                frames = [frames[int(i * step)] for i in range(max_frames)]
            self._mirror_frames_to_storage(video_id, frames)
            logger.info(
                "Adaptive-FPS extraction gave %d frames @ %.1f fps for %s",
                len(frames), fps, video_id,
            )
            return frames
        finally:
            if _cleanup_video and os.path.exists(local_video_path):
                try:
                    os.remove(local_video_path)
                except Exception:
                    pass

    def extract_frames(
        self,
        video_path: str,
        video_id: str,
        num_frames: int = 12,
        fps: Optional[float] = None,
    ) -> List[str]:
        """Extract key frames, preferring scene-boundary detection.

        `video_path` may be a local path OR a storage key. When it's a key
        we download to a /tmp file, run ffmpeg/scenedetect on the local
        copy, then mirror each extracted frame to the storage backend so
        it can be served via a presigned URL later.
        """
        output_dir = f"{self.frames_dir}/{video_id}"
        os.makedirs(output_dir, exist_ok=True)
        self._clear_existing_frames(output_dir)

        # Resolve video_path → local file (download from storage if needed).
        local_video_path, _cleanup_video = self._resolve_to_local(video_path)

        try:
            # 1. Try scene-based extraction
            frames: List[str] = []
            # If scene detection returns fewer than this many frames AND the
            # caller asked for at least twice that, treat it as "scene
            # detection failed to find the action density we need" and fall
            # back to interval. The old threshold of >=3 was way too low —
            # a 60-second video with the camera barely moving would get 5
            # scene-detected frames, the function would happily return 5
            # without trying interval, and the synthesis model only ever saw
            # 5 events. Interval extraction in that case would have given
            # us the 17 frames we actually asked for.
            scene_min_acceptable = max(3, num_frames // 2 if num_frames > 0 else 3)
            if not fps:
                try:
                    frames = self._extract_scene_frames(local_video_path, output_dir, num_frames)
                    if len(frames) >= scene_min_acceptable:
                        logger.info(f"Scene detection gave {len(frames)} frames for {video_id}")
                    else:
                        logger.info(
                            "Scene detection gave only %d frames (wanted ~%d, need >=%d), "
                            "falling back to interval extraction",
                            len(frames), num_frames, scene_min_acceptable,
                        )
                        frames = []
                except Exception as e:
                    logger.warning(f"Scene detection failed for {video_id}: {e}")
                    frames = []

            # 2. Interval-based fallback
            if not frames:
                frames = self._extract_interval_frames(
                    local_video_path, output_dir, num_frames, fps
                )
                logger.info(f"Interval extraction gave {len(frames)} frames for {video_id}")

            # Mirror each frame into the storage backend so the API can
            # serve them via presigned URL. Local-mode is a no-op copy.
            self._mirror_frames_to_storage(video_id, frames)
            return frames
        finally:
            if _cleanup_video and os.path.exists(local_video_path):
                try:
                    os.remove(local_video_path)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _resolve_to_local(self, video_path: str) -> tuple[str, bool]:
        """Return (local_path, was_downloaded)."""
        if os.path.exists(video_path):
            return video_path, False
        from app.services.storage import is_remote_storage
        storage = get_storage()
        suffix = os.path.splitext(video_path)[1] or ".mp4"
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                local = storage.download_to_temp(video_path, suffix=suffix)
                if os.path.exists(local) and os.path.getsize(local) > 0:
                    return local, is_remote_storage()
                raise FileNotFoundError(f"Downloaded video is empty: {video_path}")
            except Exception as e:
                last_error = e
                logger.warning(
                    "Video download not ready for frame extraction "
                    "(attempt %s/3, key=%s): %s",
                    attempt,
                    video_path,
                    e,
                )
                time.sleep(0.5 * attempt)
        raise last_error or FileNotFoundError(video_path)

    def _mirror_frames_to_storage(self, video_id: str, local_paths: List[str]) -> None:
        """Upload extracted frames to the storage backend (R2 in prod)."""
        storage = get_storage()
        for p in local_paths:
            try:
                key = storage_frame_key(video_id, os.path.basename(p))
                storage.upload_file(p, key, content_type="image/jpeg")
            except Exception as e:
                logger.warning(f"Frame upload to storage failed ({p}): {e}")

    def frame_to_base64(self, frame_path: str) -> str:
        """Return base64 string of a frame, resized to MAX_FRAME_DIM if needed."""
        resized = self._resize_frame(frame_path)
        with open(resized, "rb") as f:
            data = base64.b64encode(f.read()).decode("utf-8")
        if resized != frame_path and os.path.exists(resized):
            os.remove(resized)
        return data

    def get_frames_as_base64(self, frame_paths: List[str]) -> List[str]:
        return [self.frame_to_base64(p) for p in frame_paths]

    def cleanup_frames(self, video_id: str) -> None:
        import shutil
        output_dir = f"{self.frames_dir}/{video_id}"
        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)
            logger.info(f"Cleaned up frames for video {video_id}")

    # ------------------------------------------------------------------
    # Scene-boundary extraction (PySceneDetect)
    # ------------------------------------------------------------------

    def _clear_existing_frames(self, output_dir: str) -> None:
        """Remove stale JPGs before a new extraction run."""
        for name in os.listdir(output_dir):
            if not name.lower().endswith(".jpg"):
                continue
            try:
                os.remove(os.path.join(output_dir, name))
            except Exception as e:
                logger.warning("Could not remove stale frame %s: %s", name, e)

    def _extract_scene_frames(
        self, video_path: str, output_dir: str, max_frames: int
    ) -> List[str]:
        """Pick one representative frame per detected scene boundary."""
        from scenedetect import detect, ContentDetector

        # threshold 27 balances sensitivity — lower = more scenes detected
        scene_list = detect(video_path, ContentDetector(threshold=27.0))

        if not scene_list:
            return []

        # If too many scenes, sample evenly across them
        if len(scene_list) > max_frames:
            step = len(scene_list) / max_frames
            scene_list = [scene_list[int(i * step)] for i in range(max_frames)]

        frames = []
        for i, (start_time, _end_time) in enumerate(scene_list):
            # Jump 0.5 s into the scene so we land after the cut flash
            timestamp = max(0.0, start_time.get_seconds() + 0.5)
            out_path = f"{output_dir}/scene_{i+1:04d}.jpg"

            cmd = [
                "ffmpeg", "-ss", str(timestamp),
                "-i", video_path,
                "-frames:v", "1",
                "-q:v", "2",
                "-y", out_path,
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0 and os.path.exists(out_path):
                frames.append(out_path)

        return sorted(frames)

    # ------------------------------------------------------------------
    # Interval-based extraction (FFmpeg fallback)
    # ------------------------------------------------------------------

    def _extract_interval_frames(
        self,
        video_path: str,
        output_dir: str,
        num_frames: int,
        fps: Optional[float],
    ) -> List[str]:
        output_pattern = f"{output_dir}/frame_%04d.jpg"

        if fps:
            cmd = [
                "ffmpeg", "-i", video_path,
                "-vf", f"fps={fps}",
                "-q:v", "2", "-y", output_pattern,
            ]
        else:
            duration = self._get_video_duration(video_path)
            if duration <= 0:
                duration = 60
            interval = duration / (num_frames + 1)
            cmd = [
                "ffmpeg", "-i", video_path,
                "-vf", f"fps=1/{interval}",
                "-frames:v", str(num_frames),
                "-q:v", "2", "-y", output_pattern,
            ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.warning(
                    "Interval frame extraction failed with code %s: %s",
                    result.returncode,
                    (result.stderr or "")[-500:],
                )
                return []
        except subprocess.TimeoutExpired:
            logger.error("Interval frame extraction timed out")
            return []

        return sorted(
            f"{output_dir}/{f}"
            for f in os.listdir(output_dir)
            if f.endswith(".jpg")
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_video_duration(self, video_path: str) -> float:
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return float(result.stdout.strip())
        except Exception:
            return 0

    def _resize_frame(self, frame_path: str) -> str:
        """Resize frame so its longest side is at most MAX_FRAME_DIM.

        Returns the original path if no resize is needed or Pillow is missing.
        Returns a new temp path for the resized image (caller must clean up).
        """
        try:
            from PIL import Image

            img = Image.open(frame_path)
            w, h = img.size
            if max(w, h) <= MAX_FRAME_DIM:
                return frame_path

            scale = MAX_FRAME_DIM / max(w, h)
            new_size = (int(w * scale), int(h * scale))
            img = img.resize(new_size, Image.LANCZOS)

            resized_path = frame_path.replace(".jpg", "_resized.jpg")
            img.save(resized_path, "JPEG", quality=85)
            return resized_path
        except Exception:
            return frame_path

    # ------------------------------------------------------------------
    # Phase-A pre-filter: drop near-duplicate frames before VLM analysis.
    #
    # Adjacent frames in a static scene (operator pausing, talking head,
    # idle screen) are nearly identical and waste VLM tokens. A perceptual
    # diff on a downsampled greyscale version is cheap (~5ms/frame) and
    # gives 30-50% cost savings on talky / static videos without any
    # change to the synthesis path. The first frame of every static run
    # is kept so we still see what the scene contained.
    # ------------------------------------------------------------------

    def filter_static_frames(
        self,
        frame_paths: List[str],
        similarity_threshold: float = 0.92,
    ) -> List[str]:
        """Drop near-duplicate frames from a chronologically ordered list.

        Returns the kept subset in order. A frame is "near-duplicate" of
        the previous KEPT frame when the perceptual-similarity score is
        above ``similarity_threshold`` (default 0.92 — quite aggressive;
        only drops frames that look essentially identical).

        Never drops the first or last frame — those bookend the action
        and the synthesiser uses them for before/after evidence.

        If Pillow can't open a frame for any reason, that frame is
        always kept and used as the new comparison anchor — failure
        mode here must be "send too much to VLM," never "skip a real
        action frame."
        """
        if len(frame_paths) <= 2:
            return frame_paths

        try:
            from PIL import Image
        except Exception:
            logger.warning("[prefilter] PIL not available; skipping static-frame filter")
            return frame_paths

        def _signature(path: str):
            """Tiny greyscale thumbnail flattened to a list of 0..255 ints."""
            try:
                img = Image.open(path).convert("L").resize((16, 16))
                return list(img.getdata())
            except Exception:
                return None

        def _similarity(a, b) -> float:
            """1.0 == identical, 0.0 == maximally different.

            Uses mean absolute pixel difference normalised to 255.
            Cheap and stable; sensitive to motion and on-screen text
            changes while ignoring small JPEG noise.
            """
            if not a or not b or len(a) != len(b):
                return 0.0
            diff = sum(abs(int(x) - int(y)) for x, y in zip(a, b)) / (len(a) * 255.0)
            return 1.0 - diff

        kept: List[str] = [frame_paths[0]]
        last_sig = _signature(frame_paths[0])
        dropped = 0
        for path in frame_paths[1:-1]:
            sig = _signature(path)
            if sig is None or last_sig is None:
                kept.append(path)
                last_sig = sig if sig is not None else last_sig
                continue
            sim = _similarity(sig, last_sig)
            if sim >= similarity_threshold:
                dropped += 1
                continue
            kept.append(path)
            last_sig = sig
        kept.append(frame_paths[-1])

        if dropped:
            logger.info(
                "[prefilter] kept %d/%d frames (dropped %d near-duplicates, threshold=%.2f)",
                len(kept), len(frame_paths), dropped, similarity_threshold,
            )
        return kept
