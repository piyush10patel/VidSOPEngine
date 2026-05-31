"""Video router for upload and management endpoints."""
import os
from typing import Optional

from fastapi import APIRouter, Depends, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import get_db
from app.models.user import User
from app.models.video import Video, VideoStatus, VideoType, PipelineComplexity
from app.models.transcript import Transcript
from app.routers.auth import get_current_user, require_manager_or_admin
from app.services.video_service import VideoService
from app.services.transcription_service import TranscriptionService, WhisperModelSize
from app.services.sop_generator_service import SOPGeneratorService, SOPGenerationFailedError
from app.services.sop_projection_service import (
    build_operator_sop,
    can_view_internal_sop,
    sop_json_for_user,
)
from app.services.pipeline_orchestrator import PipelineOrchestrator
from app.schemas.video import VideoResponse, VideoUploadResponse, StatusResponse, VideoListResponse
from app.schemas.transcript import TranscribeRequest, TranscriptResponse, TranscriptionJobResponse
from app.schemas.sop import (
    SOPABTestRequest,
    SOPABTestResponse,
    SOPABTestVariant,
    SOPResponse,
    SOPSchema,
    SOPGenerationRequest,
    SOPGenerationJobResponse,
    SOPUpdateRequest,
)
from app.schemas.pipeline import PipelineRunRequest, PipelineJobResponse, JobStatusResponse
from app.core.errors import TranscriptionFailedError
from app.core.config import settings


router = APIRouter(prefix="/videos", tags=["videos"])


def sop_response_for_user(sop, current_user: User) -> SOPResponse:
    return SOPResponse(
        id=sop.id,
        video_id=sop.video_id,
        sop=SOPSchema(**sop_json_for_user(sop.sop_json, current_user)),
        operator_sop=build_operator_sop(sop.sop_json),
        can_view_internal=can_view_internal_sop(current_user),
        is_finalized=sop.is_finalized,
        created_at=sop.created_at,
        updated_at=sop.updated_at,
        folder_id=getattr(sop, "folder_id", None),
        category=getattr(sop, "category", None) or "Uncategorized",
        tags=getattr(sop, "tags_json", None) or [],
        archived=bool(getattr(sop, "archived", False)),
        created_by=getattr(sop, "created_by", None),
        updated_by=getattr(sop, "updated_by", None),
        visibility_scope=getattr(sop, "visibility_scope", None) or "private",
        allowed_role_min=getattr(sop, "allowed_role_min", None) or "manager",
        shared_with_users=getattr(sop, "shared_with_users_json", None) or [],
        source_type=getattr(sop, "source_type", None) or "ai_generated",
        status=getattr(sop, "status", None) or "draft",
        last_reviewed_at=getattr(sop, "last_reviewed_at", None),
        estimated_duration_minutes=getattr(sop, "estimated_duration_minutes", None),
    )


def get_video_service(db: AsyncSession = Depends(get_db)) -> VideoService:
    """Dependency to get video service instance."""
    return VideoService(db)


def get_transcription_service(db: AsyncSession = Depends(get_db)) -> TranscriptionService:
    """Dependency to get transcription service instance."""
    return TranscriptionService(db)


def get_sop_generator_service(db: AsyncSession = Depends(get_db)) -> SOPGeneratorService:
    """Dependency to get SOP generator service instance."""
    return SOPGeneratorService(db)


def get_pipeline_orchestrator(db: AsyncSession = Depends(get_db)) -> PipelineOrchestrator:
    """Dependency to get pipeline orchestrator instance."""
    return PipelineOrchestrator(db)


@router.post("/upload", response_model=VideoUploadResponse)
async def upload_video(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    video_type: str = Form("physical", description="'ui' for screen recording, 'physical' for real-world process"),
    pipeline_complexity: str = Form(
        "auto",
        description="'auto' (classifier picks), 'procedural_complex', or 'atomic_simple' - physical videos only",
    ),
    video_service: VideoService = Depends(get_video_service),
    current_user: User = Depends(require_manager_or_admin()),
) -> VideoUploadResponse:
    """
    Upload a video file for processing.

    Accepts: MP4, MOV, AVI, MKV
    Max size: Configurable via environment (default 500MB)
    Pipeline routing:
      video_type=ui       -> UI workflow pipeline
      video_type=physical -> routed by complexity:
                              auto                -> classifier picks at SOP-time
                              procedural_complex  -> existing strong path
                              atomic_simple       -> granularity-preserving path
    """
    try:
        vtype = VideoType(video_type.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"video_type must be 'ui' or 'physical', got '{video_type}'",
        )

    try:
        complexity = PipelineComplexity(pipeline_complexity.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=(
                "pipeline_complexity must be one of "
                "'auto' | 'procedural_complex' | 'atomic_simple'"
            ),
        )

    video = await video_service.upload(
        file=file, title=title, user_id=current_user.id,
        video_type=vtype, pipeline_complexity=complexity,
    )

    return VideoUploadResponse(
        id=video.id,
        title=video.title,
        filename=video.filename,
        status=video.status,
        video_type=vtype,
        pipeline_complexity=complexity,
        message="Video uploaded successfully",
    )


@router.get("", response_model=VideoListResponse)
async def list_videos(
    video_service: VideoService = Depends(get_video_service),
    current_user: User = Depends(get_current_user),
) -> VideoListResponse:
    """List videos belonging to the current user."""
    videos = await video_service.list_all(user_id=current_user.id)

    video_responses = [
        VideoResponse(
            id=v.id,
            title=v.title,
            filename=v.filename,
            status=v.status,
            video_type=v.video_type,
            pipeline_complexity=v.pipeline_complexity,
            pipeline_complexity_confidence=v.pipeline_complexity_confidence,
            created_at=v.created_at,
            has_transcript=v.transcript is not None,
            has_sop=v.sop is not None
        )
        for v in videos
    ]

    return VideoListResponse(
        videos=video_responses,
        total=len(video_responses)
    )


@router.get("/{video_id}", response_model=VideoResponse)
async def get_video(
    video_id: str,
    video_service: VideoService = Depends(get_video_service),
    current_user: User = Depends(get_current_user),
) -> VideoResponse:
    """
    Get video metadata by ID.
    """
    video = await video_service.get_by_id(video_id)

    return VideoResponse(
        id=video.id,
        title=video.title,
        filename=video.filename,
        status=video.status,
        video_type=video.video_type,
        pipeline_complexity=video.pipeline_complexity,
        pipeline_complexity_confidence=video.pipeline_complexity_confidence,
        created_at=video.created_at,
        has_transcript=video.transcript is not None,
        has_sop=video.sop is not None
    )


@router.get("/{video_id}/status", response_model=StatusResponse)
async def get_video_status(
    video_id: str,
    video_service: VideoService = Depends(get_video_service),
    current_user: User = Depends(get_current_user),
) -> StatusResponse:
    """
    Get current processing status of a video.
    Used for polling during pipeline execution.
    """
    video = await video_service.get_by_id(video_id)

    return StatusResponse(
        video_id=video.id,
        status=video.status
    )


async def _run_transcription(
    video_id: str,
    model_size: str,
    db: AsyncSession
):
    """Background task to run transcription."""
    service = TranscriptionService(db, model_size=model_size)
    try:
        await service.transcribe_video(video_id, model_size)
    except Exception as e:
        # Status is already set to failed in transcribe_video
        pass


@router.post("/{video_id}/transcribe", response_model=TranscriptionJobResponse)
async def start_transcription(
    video_id: str,
    request: TranscribeRequest = TranscribeRequest(),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    transcription_service: TranscriptionService = Depends(get_transcription_service),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin()),
) -> TranscriptionJobResponse:
    """
    Start transcription job for a video.

    Model sizes: base, small, medium

    The transcription runs synchronously for MVP (can be moved to background worker later).
    """
    # Verify video exists
    video = await transcription_service.get_video(video_id)

    # Check if already has transcript
    if await transcription_service.has_transcript(video_id):
        return TranscriptionJobResponse(
            video_id=video_id,
            status="completed",
            message="Transcript already exists"
        )

    # Run transcription (synchronously for MVP simplicity)
    try:
        transcript = await transcription_service.transcribe_video(
            video_id,
            model_size=request.model_size.value
        )

        return TranscriptionJobResponse(
            video_id=video_id,
            status="completed",
            message="Transcription completed successfully"
        )
    except Exception as e:
        raise TranscriptionFailedError(video_id, str(e))


@router.get("/{video_id}/transcript", response_model=TranscriptResponse)
async def get_transcript(
    video_id: str,
    transcription_service: TranscriptionService = Depends(get_transcription_service),
    current_user: User = Depends(get_current_user),
) -> TranscriptResponse:
    """
    Get transcript for a video if available.
    """
    transcript = await transcription_service.get_transcript(video_id)

    return TranscriptResponse(
        id=transcript.id,
        video_id=transcript.video_id,
        text=transcript.text,
        model_name=transcript.model_name,
        created_at=transcript.created_at
    )


@router.post("/{video_id}/generate-sop", response_model=SOPGenerationJobResponse)
async def generate_sop(
    video_id: str,
    request: SOPGenerationRequest = SOPGenerationRequest(),
    sop_service: SOPGeneratorService = Depends(get_sop_generator_service),
    current_user: User = Depends(require_manager_or_admin()),
) -> SOPGenerationJobResponse:
    """
    Start SOP generation from transcript.

    Requires transcript to exist for the video.
    Uses open-source LLM (Mistral, Llama3, etc.) via Ollama.

    The generation runs synchronously for MVP (can be moved to background worker later).
    """
    # Verify video exists
    video = await sop_service.get_video(video_id)

    # Check if already has SOP
    if await sop_service.has_sop(video_id):
        return SOPGenerationJobResponse(
            video_id=video_id,
            status="completed",
            message="SOP already exists"
        )

    # Run SOP generation (synchronously for MVP simplicity)
    try:
        from app.services.cleanup_service import mark_artifact_status, maybe_cleanup_video_media
        await mark_artifact_status(sop_service.db, video_id, "sop", "running")
        sop = await sop_service.generate_sop_for_video(
            video_id,
            model_name=request.llm_model
        )
        await maybe_cleanup_video_media(sop_service.db, video_id)

        return SOPGenerationJobResponse(
            video_id=video_id,
            status="completed",
            message="SOP generation completed successfully"
        )
    except SOPGenerationFailedError:
        raise
    except Exception as e:
        raise SOPGenerationFailedError(video_id, str(e))


@router.get("/{video_id}/sop", response_model=SOPResponse)
async def get_sop(
    video_id: str,
    sop_service: SOPGeneratorService = Depends(get_sop_generator_service),
    current_user: User = Depends(get_current_user),
) -> SOPResponse:
    """Get generated SOP for a video if available."""
    sop = await sop_service.get_sop(video_id)
    return sop_response_for_user(sop, current_user)


@router.post("/{video_id}/sop/ab-test", response_model=SOPABTestResponse)
async def ab_test_sop_models(
    video_id: str,
    request: SOPABTestRequest = SOPABTestRequest(),
    sop_service: SOPGeneratorService = Depends(get_sop_generator_service),
    current_user: User = Depends(require_manager_or_admin()),
) -> SOPABTestResponse:
    """Run unsaved SOP variants for model A/B testing."""
    variants = await sop_service.ab_test_sop_for_video(
        video_id=video_id,
        models=request.models,
        pipeline_complexity=request.pipeline_complexity,
    )
    return SOPABTestResponse(
        video_id=video_id,
        variants=[
            SOPABTestVariant(
                model=model,
                sop=sop,
                step_scores=sop.steps,
                overall_confidence=sop.overall_confidence,
                needs_review=sop.needs_review,
                warnings=sop.warnings,
            )
            for model, sop in variants
        ],
    )


@router.post("/{video_id}/sop/translate", response_model=SOPResponse)
async def translate_sop(
    video_id: str,
    target_language: str = "en",
    sop_service: SOPGeneratorService = Depends(get_sop_generator_service),
    current_user: User = Depends(get_current_user),
) -> SOPResponse:
    """Translate this video's SOP into the target language and save it.

    The structure (steps, evidence, image_urls, confidence, links,
    metadata) is preserved. Only user-facing text fields (title,
    description, step titles/descriptions, tools, checks, notes,
    warnings) are translated. Calling with the SOP's current language is
    a no-op. Supports 'en' and 'hi' today.
    """
    from fastapi import HTTPException

    from app.schemas.sop import SOPSchema
    from app.services.sop_translation_service import (
        SOPTranslationError,
        translate_sop_schema,
        normalize_sop_language,
        SUPPORTED_SOP_LANGUAGES,
    )

    target = normalize_sop_language(target_language)
    if target not in SUPPORTED_SOP_LANGUAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported language '{target_language}'. Supported: {sorted(SUPPORTED_SOP_LANGUAGES)}",
        )

    sop_row = await sop_service.get_sop(video_id)
    current_schema = SOPSchema(**sop_row.sop_json)
    try:
        translated = await translate_sop_schema(current_schema, target_language=target)
    except SOPTranslationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    sop_row.sop_json = translated.model_dump(mode="json")
    await sop_service.db.commit()
    await sop_service.db.refresh(sop_row)
    return sop_response_for_user(sop_row, current_user)


@router.put("/{video_id}/sop", response_model=SOPResponse)
async def update_sop(
    video_id: str,
    request: SOPUpdateRequest,
    sop_service: SOPGeneratorService = Depends(get_sop_generator_service),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin()),
) -> SOPResponse:
    """Replace the SOP json with the user's edited version (text + tool selections).

    Rejected if the SOP has already been finalized.
    """
    sop = await sop_service.get_sop(video_id)
    if sop.is_finalized:
        raise HTTPException(status_code=409, detail="SOP is finalized; cannot edit")
    if request.sop is None:
        raise HTTPException(status_code=400, detail="sop is required")

    incoming = request.sop.model_dump(mode="json")
    # Diag line that lets us confirm step images survive the round-trip.
    # Greppable for [sop_update] in logs.
    image_urls = [
        (s.get("step_number"), s.get("image_url")) for s in (incoming.get("steps") or [])
    ]
    import logging as _logging
    _logging.getLogger(__name__).info(
        "[sop_update] video=%s steps=%d images=%s",
        video_id, len(incoming.get("steps") or []), image_urls,
    )
    sop.sop_json = incoming
    sop.updated_by = current_user.id
    await db.commit()
    await db.refresh(sop)

    return sop_response_for_user(sop, current_user)


@router.post("/{video_id}/sop/finalize", response_model=SOPResponse)
async def finalize_sop(
    video_id: str,
    request: SOPUpdateRequest,
    sop_service: SOPGeneratorService = Depends(get_sop_generator_service),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin()),
) -> SOPResponse:
    """Save the user's final edits and lock the SOP - no further changes allowed.

    The finalized SOP is what gets shared / printed / exported.
    """
    sop = await sop_service.get_sop(video_id)
    if sop.is_finalized:
        raise HTTPException(status_code=409, detail="SOP is already finalized")
    if request.sop is None:
        raise HTTPException(status_code=400, detail="sop is required")

    # Finalize is irreversible: refuse to lock a SOP that still contains
    # any step a reviewer flagged as wrong. The reviewer must either edit
    # the step and toggle the flag off, or delete it. Defense in depth on
    # top of the frontend disable — protects against stale bundles or
    # direct API calls.
    wrong_steps = [
        step for step in (request.sop.steps or [])
        if bool(getattr(step, "user_marked_wrong", False))
    ]
    if wrong_steps:
        wrong_numbers = [step.step_number for step in wrong_steps]
        raise HTTPException(
            status_code=400,
            detail=(
                "Cannot finalize: steps "
                f"{wrong_numbers} are still marked wrong. Resolve them first."
            ),
        )

    incoming = request.sop.model_dump(mode="json")
    image_urls = [
        (s.get("step_number"), s.get("image_url")) for s in (incoming.get("steps") or [])
    ]
    import logging as _logging
    _logging.getLogger(__name__).info(
        "[sop_finalize] video=%s steps=%d images=%s",
        video_id, len(incoming.get("steps") or []), image_urls,
    )
    sop.sop_json = incoming
    sop.is_finalized = True
    sop.updated_by = current_user.id
    await db.commit()
    await db.refresh(sop)

    return sop_response_for_user(sop, current_user)


async def _run_real_pipeline(video_id: str) -> None:
    """Run full pipeline as a FastAPI background task.

    Branches on video.video_type:
      - physical -> transcribe (Whisper) -> physical SOP pipeline
      - ui       -> skip transcription, run UI pipeline directly
    """
    import logging as _logging
    import traceback as _traceback
    _log = _logging.getLogger(__name__)
    from app.models.base import get_async_session_factory
    AsyncSessionLocal = get_async_session_factory()
    async with AsyncSessionLocal() as db:
        trans_service = TranscriptionService(db)
        sop_service = SOPGeneratorService(db)
        video_service = VideoService(db)
        try:
            video = await video_service.get_by_id(video_id)
            is_ui = (video.video_type == VideoType.UI.value)

            if not is_ui:
                # Physical: needs transcript
                if not await trans_service.has_transcript(video_id):
                    _log.info(f"[pipeline] Transcribing {video_id} (physical)")
                    await trans_service.transcribe_video(video_id)
                else:
                    _log.info(f"[pipeline] Transcript exists for {video_id}")
            else:
                _log.info(f"[pipeline] Skipping transcription for {video_id} (UI workflow)")

            if not await sop_service.has_sop(video_id):
                _log.info(f"[pipeline] Generating SOP for {video_id} (type={video.video_type})")
                from app.services.cleanup_service import mark_artifact_status
                await mark_artifact_status(db, video_id, "sop", "running")
                await sop_service.generate_sop_for_video(video_id)
            else:
                _log.info(f"[pipeline] SOP exists for {video_id}")
                from app.services.cleanup_service import mark_artifact_status
                await mark_artifact_status(db, video_id, "sop", "success")

            # Media cleanup is delayed until SOP + downstream artifacts complete.
            try:
                from app.services.cleanup_service import maybe_cleanup_video_media
                stats = await maybe_cleanup_video_media(db, video_id)
                _log.info(f"[pipeline] cleanup for {video_id}: {stats}")
            except Exception as ce:
                _log.warning(f"[pipeline] cleanup failed for {video_id}: {ce}")
        except Exception as e:
            _log.error(
                f"[pipeline] FAILED for {video_id}: {e}\n{_traceback.format_exc()}"
            )


@router.post("/{video_id}/pipeline/run", response_model=PipelineJobResponse)
async def run_pipeline(
    video_id: str,
    request: PipelineRunRequest = PipelineRunRequest(),
    pipeline_orchestrator: PipelineOrchestrator = Depends(get_pipeline_orchestrator),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_manager_or_admin()),
) -> PipelineJobResponse:
    """
    Run full pipeline: transcription -> SOP generation.

    Dispatch is governed by settings.pipeline_run_mode:
      - "inline" (default) - schedules a FastAPI BackgroundTask in this
        process. No worker service required. The HTTP response returns
        immediately; the pipeline runs asynchronously after the response.
      - "rq" - pushes a job to Upstash Redis for the vidsopengine-worker
        service to consume. Only useful if you've actually deployed the
        worker (see render.yaml).
    """
    # Verify video exists up-front so we fail loudly here, not silently
    # inside the background task.
    video = await pipeline_orchestrator.get_video(video_id)
    has_transcript = (
        await db.execute(select(Transcript.id).where(Transcript.video_id == video_id))
    ).scalar_one_or_none() is not None
    if video.video_type == VideoType.UI.value or has_transcript:
        video.status = VideoStatus.SOP_GENERATING.value
    else:
        video.status = VideoStatus.TRANSCRIBING.value
    await db.commit()

    mode = (settings.pipeline_run_mode or "inline").lower()

    if mode == "rq":
        # Worker-consumed path
        try:
            job_id = await pipeline_orchestrator.run_pipeline(video_id)
            return PipelineJobResponse(
                job_id=job_id,
                video_id=video_id,
                status="queued",
                message="Pipeline job enqueued successfully (RQ)",
            )
        except Exception as e:
            # If Redis is misconfigured, fall through to inline rather than
            # 500-erroring the user.
            import logging as _l
            _l.getLogger(__name__).warning(
                f"RQ enqueue failed, falling back to inline: {e}"
            )

    # Default inline path
    from uuid import uuid4 as _uuid4
    job_id = f"inline-{_uuid4().hex[:8]}"
    background_tasks.add_task(_run_real_pipeline, video_id)
    return PipelineJobResponse(
        job_id=job_id,
        video_id=video_id,
        status="queued",
        message="Pipeline started inline",
    )


@router.get("/{video_id}/pipeline/status/{job_id}", response_model=JobStatusResponse)
async def get_pipeline_job_status(
    video_id: str,
    job_id: str,
    pipeline_orchestrator: PipelineOrchestrator = Depends(get_pipeline_orchestrator)
) -> JobStatusResponse:
    """
    Get the status of a pipeline job.
    """
    status = pipeline_orchestrator.get_job_status(job_id)
    return JobStatusResponse(**status)



@router.get("/{video_id}/frames/{frame_name}")
async def get_frame(
    video_id: str,
    frame_name: str,
    video_service: VideoService = Depends(get_video_service),
):
    """
    Get a frame image for a video.

    Frames are streamed through the API. This avoids browser CORS failures
    when the SOP viewer and PDF export load R2-backed images.
    """
    # Path-traversal guard
    if ".." in frame_name or "/" in frame_name or "\\" in frame_name:
        raise HTTPException(status_code=400, detail="Invalid frame name")

    # Verify video exists (also gates access)
    await video_service.get_by_id(video_id)

    from app.services.storage import frame_key, get_storage, is_remote_storage

    key = frame_key(video_id, frame_name)
    storage = get_storage()

    if is_remote_storage():
        try:
            body = storage.open_read(key)
        except Exception:
            raise HTTPException(status_code=404, detail="Frame not found")
        return StreamingResponse(
            body,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=3600"},
        )

    # Local-disk fallback - serve the file directly.
    frames_dir = f"{settings.upload_dir}/frames/{video_id}"
    frame_path = os.path.join(frames_dir, frame_name)
    if not os.path.exists(frame_path):
        raise HTTPException(status_code=404, detail="Frame not found")
    return FileResponse(
        frame_path,
        media_type="image/jpeg",
        filename=frame_name,
    )
