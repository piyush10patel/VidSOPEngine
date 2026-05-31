"""Pipeline worker tasks for video processing."""
import logging
from typing import Optional

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings
from app.core.errors import VideoNotFoundError, TranscriptNotFoundError, PipelineFailedError
from app.models.video import Video, VideoStatus
from app.models.transcript import Transcript
from app.models.sop import SOP


logger = logging.getLogger(__name__)


def get_sync_db_url() -> str:
    """Convert async database URL to sync URL."""
    url = settings.database_url
    if "sqlite+aiosqlite" in url:
        return url.replace("sqlite+aiosqlite", "sqlite")
    if "postgresql+asyncpg" in url:
        return url.replace("postgresql+asyncpg", "postgresql")
    return url


def get_sync_session() -> Session:
    """Create a synchronous database session for worker tasks."""
    engine = create_engine(get_sync_db_url(), echo=False)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def get_video(session: Session, video_id: str) -> Video:
    """
    Get a video by ID.

    Args:
        session: Database session
        video_id: The video ID

    Returns:
        The Video record

    Raises:
        VideoNotFoundError: If video not found
    """
    video = session.execute(
        select(Video).where(Video.id == video_id)
    ).scalar_one_or_none()

    if not video:
        raise VideoNotFoundError(video_id)

    return video


def update_video_status(session: Session, video_id: str, status: VideoStatus) -> Video:
    """
    Update video processing status.

    Args:
        session: Database session
        video_id: The video ID
        status: The new status

    Returns:
        The updated Video record
    """
    video = get_video(session, video_id)
    video.status = status.value if isinstance(status, VideoStatus) else status
    session.commit()
    session.refresh(video)
    logger.info(f"Updated video {video_id} status to {status.value if isinstance(status, VideoStatus) else status}")
    return video


def has_transcript(session: Session, video_id: str) -> bool:
    """Check if a video has a transcript."""
    result = session.execute(
        select(Transcript.id).where(Transcript.video_id == video_id)
    ).scalar_one_or_none()
    return result is not None


def get_transcript(session: Session, video_id: str) -> Transcript:
    """Get transcript for a video."""
    transcript = session.execute(
        select(Transcript).where(Transcript.video_id == video_id)
    ).scalar_one_or_none()

    if not transcript:
        raise TranscriptNotFoundError(video_id)

    return transcript


def transcribe_video(video_id: str, model_size: str = "base") -> dict:
    """
    Transcription-only task.

    Args:
        video_id: The video ID to transcribe
        model_size: Whisper model size (base, small, medium)

    Returns:
        Dict with transcript info
    """
    logger.info(f"Starting transcription for video {video_id} with model {model_size}")

    session = get_sync_session()
    try:
        # Check video exists
        video = get_video(session, video_id)

        # Update status to transcribing
        update_video_status(session, video_id, VideoStatus.TRANSCRIBING)

        # Transcribe via the configured LLM provider. video.file_path may
        # be a real local path (legacy / dev) or a storage key (e.g.
        # 'videos/abc.mp4'); the storage abstraction normalises both.
        import os
        import subprocess
        from app.services.llm import get_provider
        from app.services.storage import get_storage, is_remote_storage

        provider = get_provider()
        storage = get_storage()
        cleanup_local = False
        cleanup_audio = False

        if os.path.exists(video.file_path):
            file_path = video.file_path
        else:
            suffix = os.path.splitext(video.file_path)[1] or ".mp4"
            file_path = storage.download_to_temp(video.file_path, suffix=suffix)
            cleanup_local = True

        try:
            if os.path.getsize(file_path) > 24 * 1024 * 1024:
                audio_path = file_path.rsplit(".", 1)[0] + "_audio.mp3"
                try:
                    subprocess.run(
                        ["ffmpeg", "-i", file_path, "-vn", "-acodec", "mp3",
                         "-ab", "64k", "-ar", "16000", "-y", audio_path],
                        check=True, capture_output=True, timeout=120,
                    )
                    target = audio_path
                    cleanup_audio = True
                except Exception:
                    target = file_path
            else:
                target = file_path

            try:
                resp = provider.transcribe(target, timeout=60)
                transcript_text = resp.text
            finally:
                if cleanup_audio and os.path.exists(target) and target != file_path:
                    os.remove(target)
        finally:
            if cleanup_local and is_remote_storage() and os.path.exists(file_path):
                os.remove(file_path)

        # Save transcript
        from uuid import uuid4
        transcript = Transcript(
            id=str(uuid4()),
            video_id=video_id,
            text=transcript_text,
            model_name=model_size,
            status="completed"
        )
        session.add(transcript)
        session.commit()

        logger.info(f"Transcription completed for video {video_id}")

        return {
            "video_id": video_id,
            "transcript_id": transcript.id,
            "status": "completed"
        }

    except VideoNotFoundError:
        logger.error(f"Video {video_id} not found")
        raise
    except Exception as e:
        logger.error(f"Transcription failed for video {video_id}: {e}")
        try:
            update_video_status(session, video_id, VideoStatus.FAILED)
        except Exception:
            pass
        raise PipelineFailedError(video_id, "transcription", str(e))
    finally:
        session.close()


def generate_sop(video_id: str, model_name: Optional[str] = None, use_vision: bool = True) -> dict:
    """
    SOP generation task using the same modern service path as inline mode.

    Older versions of this worker task synthesized the SOP directly through
    the Groq provider with settings.llm_model. Keep this wrapper for RQ
    compatibility, but delegate to SOPGeneratorService so task model routing
    (Together/Qwen for SOP, OpenRouter/Qwen for vision, Groq for
    transcription) is identical across inline and worker execution.

    Args:
        video_id: The video ID
        model_name: Task model override
        use_vision: Whether to attempt vision-assisted SOP generation

    Returns:
        Dict with SOP info
    """
    import asyncio

    selected_model = model_name or settings.sop_synthesis_model
    logger.info(
        "[pipeline] Starting SOP generation for video %s via SOPGeneratorService "
        "(model=%s, vision=%s)",
        video_id,
        selected_model,
        use_vision,
    )

    async def _run() -> dict:
        from app.models.base import get_async_session_factory
        from app.services.sop_generator_service import SOPGeneratorService

        AsyncSessionLocal = get_async_session_factory()
        async with AsyncSessionLocal() as db:
            service = SOPGeneratorService(db, model_name=selected_model)
            sop = await service.generate_sop_for_video(video_id)
            return {
                "video_id": video_id,
                "sop_id": sop.id,
                "status": "completed",
                "model": service.model_name,
            }

    try:
        return asyncio.run(_run())
    except Exception as e:
        logger.error(f"SOP generation failed for video {video_id}: {e}")
        session = get_sync_session()
        try:
            update_video_status(session, video_id, VideoStatus.FAILED)
        except Exception:
            pass
        finally:
            session.close()
        raise PipelineFailedError(video_id, "sop_generation", str(e))



def _generate_sop_with_vision(provider, vision_model: str, text_model: str,
                              transcript: str, frames: list, extractor, video_id: str) -> dict:
    """Vision-per-frame + text synthesis via the LLM provider abstraction."""
    import os

    # Step 1: Analyze each frame
    frame_analyses = []
    for i, frame_path in enumerate(frames):
        try:
            frame_b64 = extractor.frame_to_base64(frame_path)
            frame_prompt = f"""You are analyzing frame {i+1} of {len(frames)} from a procedural/instructional video.

Describe ONLY what you can directly observe in this image. Do not guess, infer, or assume anything not clearly visible.

1. PRIMARY ACTION: What action is being performed right now? (If unclear, say "unclear")
2. TOOLS/MATERIALS: List only tools or materials you can clearly see.
3. HAND POSITIONS: Where are the hands and what are they holding? (Only if visible)
4. STAGE: Is this early, middle, or late in the procedure?

If something is not clearly visible, omit it. Do not invent details."""

            resp = provider.vision(
                frame_prompt, frame_b64, model=vision_model, timeout=30,
            )
            frame_filename = os.path.basename(frame_path)
            frame_analyses.append({
                "frame_num": i + 1,
                "description": resp.text,
                "image_url": f"/videos/{video_id}/frames/{frame_filename}"
            })
            logger.info(f"Analyzed frame {i+1}/{len(frames)}")
        except Exception as e:
            logger.warning(f"Failed to analyze frame {i+1}: {e}")

    if not frame_analyses:
        raise ValueError("No frames could be analyzed")

    # Step 2: Build combined visual context
    visual_context = "\n\n".join(
        f"=== FRAME {fa['frame_num']}/{len(frames)} ===\n{fa['description']}"
        for fa in frame_analyses
    )

    # Step 3: Synthesize final SOP
    combined_prompt = _build_detailed_sop_prompt(transcript, visual_context, len(frames))
    resp = provider.chat(
        combined_prompt, model=text_model,
        response_format={"type": "json_object"}, timeout=30,
    )
    sop_data = _parse_sop_response(resp.text)

    # Step 4: Assign frame images using linear interpolation -
    # first step -> first frame, last step -> last frame.
    steps = sop_data.get("steps", [])
    if steps and frame_analyses:
        n_steps = len(steps)
        n_frames = len(frame_analyses)
        for i, step in enumerate(steps):
            if n_steps == 1:
                frame_idx = 0
            else:
                frame_idx = round(i * (n_frames - 1) / (n_steps - 1))
            step["image_url"] = frame_analyses[frame_idx]["image_url"]

    return sop_data


def process_video_pipeline(video_id: str) -> dict:
    """
    Full pipeline task: transcription -> SOP generation.

    Worker entrypoint. Runs each stage with explicit timing and an isolated
    /tmp scratch dir so a crash in one job can't pollute another.

    This task:
      1. Checks if video exists
      2. Runs transcription if no transcript exists
      3. Runs SOP generation after transcription
      4. Cleans up media artifacts (R2 + local /tmp)
    """
    import gc
    import tempfile
    import shutil
    from app.observability.timing import StageTimings, stage_timer

    logger.info(f"[pipeline] Starting pipeline for video {video_id}")

    timings = StageTimings()

    # Per-job isolated scratch dir - guarantees a failed run can't leak
    # state to the next job on the same worker. Cleaned up unconditionally
    # in the finally block.
    job_tmp = tempfile.mkdtemp(prefix=f"clarity_{video_id}_")
    logger.info(f"[pipeline] job tmp dir: {job_tmp}")

    session = get_sync_session()
    try:
        with stage_timer(timings, "check_video"):
            video = get_video(session, video_id)
            current_status = (
                video.status.value if isinstance(video.status, VideoStatus) else video.status
            )
            logger.info(f"[pipeline] Video {video_id} found, current status: {current_status}")

        if not has_transcript(session, video_id):
            session.close()
            with stage_timer(timings, "transcription"):
                transcribe_video(video_id, settings.whisper_model_size)
            session = get_sync_session()
        else:
            logger.info(f"[pipeline] Transcript already exists for video {video_id}")

        session.close()
        gc.collect()  # release transcription buffers before the heavier SOP stage

        with stage_timer(timings, "sop_generation"):
            result = generate_sop(video_id, settings.sop_synthesis_model)

        logger.info(f"[pipeline] Pipeline completed for video {video_id}")

        with stage_timer(timings, "cleanup"):
            logger.info(
                "[pipeline] cleanup deferred for %s until workflows, checklists, "
                "and training all succeed",
                video_id,
            )

        logger.info(
            f"[pipeline] [timing] video={video_id} total={timings.total_seconds():.2f}s "
            f"{timings.summary()}"
        )

        return {
            "video_id": video_id,
            "status": "completed",
            "sop_id": result.get("sop_id"),
            "stage_timings": dict(timings),
        }

    except VideoNotFoundError:
        logger.error(f"[pipeline] Failed: Video {video_id} not found")
        raise
    except PipelineFailedError:
        logger.error(
            f"[pipeline] Failed for video {video_id} {timings.summary()}"
        )
        raise
    except Exception as e:
        logger.error(
            f"[pipeline] Failed for video {video_id}: {e} {timings.summary()}"
        )
        try:
            session = get_sync_session()
            update_video_status(session, video_id, VideoStatus.FAILED)
            session.close()
        except Exception:
            pass
        raise PipelineFailedError(video_id, "pipeline", str(e))
    finally:
        # ALWAYS remove the per-job tmp dir, regardless of success/failure.
        # Cleanup at the storage level happens in the cleanup stage above;
        # this just removes the isolated scratch space.
        try:
            shutil.rmtree(job_tmp, ignore_errors=True)
        except Exception:
            pass
        gc.collect()


def _build_sop_prompt(transcript: str) -> str:
    """Build the LLM prompt for text-only SOP generation."""
    return f"""You are an expert process documentation system.

Your task is to generate a Standard Operating Procedure (SOP) strictly from the transcript below.

RULES (CRITICAL):
1. USE ONLY PROVIDED TRANSCRIPT - Do NOT invent steps, tools, or actions not mentioned.
2. MAINTAIN TEMPORAL ORDER - Steps must follow the exact sequence described.
3. GROUP LOGICALLY - Combine closely related actions into one step; do not split unnecessarily.
4. NO HALLUCINATION - If an action is ambiguous, write "unclear action" rather than guessing.
5. BE PRECISE - Each step must be actionable and concise.
6. CONFIDENCE SCORING - Assign each step a confidence score (0.0-1.0) based on how clearly the action is described.

Transcript:
{transcript}

Output ONLY valid JSON:
{{
    "title": "Specific title matching the procedure",
    "summary": "One sentence describing what this procedure accomplishes",
    "sop": [
        {{
            "step_number": 1,
            "title": "2-4 word action title",
            "instruction": "Precise actionable instruction for this step",
            "objects": ["tools or materials used"],
            "checks": ["verification check if mentioned"],
            "evidence": ["transcript reference"],
            "confidence": 0.9,
            "notes": "clarification or 'unclear' if ambiguous"
        }}
    ],
    "overall_confidence": 0.9,
    "warnings": ["list any missing, unclear, or low-confidence steps"]
}}"""


def _build_detailed_sop_prompt(transcript: str, visual_context: str, num_frames: int = 8) -> str:
    """Build event-based SOP prompt using frame observations as structured events."""
    return f"""You are an expert process documentation system.

Your task is to generate a Standard Operating Procedure (SOP) strictly from the structured video observations below.

INPUT - treat each frame as an ordered event:
{visual_context}

TRANSCRIPT (secondary source for narration/context):
{transcript}

RULES (CRITICAL):
1. USE ONLY PROVIDED EVENTS - Do NOT invent steps, tools, or actions not visible in the frames or transcript.
2. MAINTAIN TEMPORAL ORDER - Steps must follow the frame sequence exactly.
3. GROUP LOGICALLY - Combine closely related frame observations into one step when appropriate.
4. NO HALLUCINATION - If a frame action is ambiguous, write "unclear action" rather than guessing.
5. BE PRECISE - Each instruction must be actionable and concise.
6. INCLUDE EVIDENCE - Every step must reference the frame number(s) it is based on.
7. CONFIDENCE SCORING - Score each step 0.0-1.0 based on clarity of the frame observation.

QUALITY CHECK BEFORE OUTPUT:
- Are all steps backed by a frame reference?
- Did you avoid adding external knowledge?
- Are unclear steps explicitly marked?

Output ONLY valid JSON:
{{
    "title": "Specific title matching the procedure",
    "summary": "One sentence describing what this procedure accomplishes",
    "sop": [
        {{
            "step_number": 1,
            "title": "2-4 word action title",
            "instruction": "Precise actionable instruction for this step",
            "objects": ["tools or materials clearly visible in this step"],
            "checks": ["observable sign this step is complete"],
            "evidence": ["Frame X/{num_frames}"],
            "confidence": 0.9,
            "notes": "clarification or 'unclear' if ambiguous"
        }}
    ],
    "overall_confidence": 0.9,
    "warnings": ["list any missing, unclear, or low-confidence steps"]
}}

Output ONLY the JSON."""


def _parse_sop_response(response: str) -> dict:
    """Parse LLM response into SOP dict, handling both old and new output formats."""
    import json
    import re

    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
    if json_match:
        json_str = json_match.group(1).strip()
    else:
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            json_str = json_match.group(0).strip()
        else:
            json_str = response.strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in response: {e}")

    # Normalise new event-based format -> legacy dict format
    if "sop" in data and isinstance(data["sop"], list):
        normalised_steps = []
        for item in data["sop"]:
            normalised_steps.append({
                "step_number": item.get("step_number", 0),
                "title": item.get("title", ""),
                "description": item.get("instruction", item.get("description", "")),
                "tools": item.get("objects", item.get("tools", [])),
                "checks": item.get("checks", []),
                "evidence": item.get("evidence", []),
                "confidence": item.get("confidence", 1.0),
                "notes": item.get("notes"),
            })
        data = {
            "title": data.get("title", "Untitled SOP"),
            "description": data.get("summary", data.get("description", "")),
            "steps": normalised_steps,
            "notes": data.get("warnings", data.get("notes", [])),
            "overall_confidence": data.get("overall_confidence", 1.0),
            "warnings": data.get("warnings", []),
        }

    # Ensure required fields
    if "title" not in data:
        raise ValueError("Missing required field: title")
    if "steps" not in data or not isinstance(data["steps"], list):
        raise ValueError("Missing or invalid field: steps")

    for step in data["steps"]:
        step.setdefault("tools", [])
        step.setdefault("checks", [])
        step.setdefault("evidence", [])
        step.setdefault("confidence", 1.0)
    data.setdefault("notes", [])
    data.setdefault("description", "")
    data.setdefault("overall_confidence", 1.0)
    data.setdefault("warnings", [])

    return data
