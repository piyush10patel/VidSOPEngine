"""Pipeline orchestrator for coordinating video-to-SOP workflow."""
import logging
from typing import Optional
from uuid import uuid4

try:
    from redis import Redis
    from rq import Queue, Retry
    from rq.job import Job
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    Redis = None
    Queue = None
    Job = None
    Retry = None
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import VideoNotFoundError, PipelineFailedError
from app.models.video import Video, VideoStatus


logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Orchestrator for the video-to-SOP processing pipeline."""
    
    def __init__(
        self, 
        db: AsyncSession,
        redis_conn: Optional[Redis] = None,
        queue_name: str = "pipeline"
    ):
        """
        Initialize pipeline orchestrator.
        
        Args:
            db: Database session
            redis_conn: Redis connection (creates new if None)
            queue_name: Name of the RQ queue to use
        """
        self.db = db
        self._redis_conn = redis_conn
        self._queue_name = queue_name
        self._queue: Optional[Queue] = None
    
    @property
    def redis_conn(self) -> Redis:
        """Get or create Redis connection."""
        if self._redis_conn is None:
            self._redis_conn = Redis.from_url(settings.redis_url)
        return self._redis_conn
    
    @property
    def queue(self) -> Queue:
        """Get or create RQ queue."""
        if self._queue is None:
            self._queue = Queue(self._queue_name, connection=self.redis_conn)
        return self._queue
    
    async def get_video(self, video_id: str) -> Video:
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
            select(Video).where(Video.id == video_id)
        )
        video = result.scalar_one_or_none()
        
        if not video:
            raise VideoNotFoundError(video_id)
        
        return video
    
    async def update_video_status(self, video_id: str, status: VideoStatus) -> Video:
        """
        Update video processing status.
        
        Args:
            video_id: The video ID
            status: The new status
            
        Returns:
            The updated Video record
        """
        video = await self.get_video(video_id)
        video.status = status
        await self.db.commit()
        await self.db.refresh(video)
        return video
    
    def _retry_policy(self):
        """Build an RQ Retry with exponential backoff.

        max_retries=3, intervals = [base, base*2, base*4] seconds.
        Returns None when RQ isn't installed (defensive).
        """
        if Retry is None:
            return None
        base = max(1, settings.pipeline_retry_backoff_seconds)
        max_retries = max(0, settings.pipeline_max_retries)
        if max_retries == 0:
            return None
        intervals = [base * (2 ** i) for i in range(max_retries)]
        return Retry(max=max_retries, interval=intervals)

    def enqueue_pipeline(self, video_id: str) -> str:
        """Enqueue a full pipeline job for a video.

        Includes an RQ Retry policy for transient failures (Groq throttles,
        R2 timeouts, ffmpeg restarts). Worker tasks raise non-retriable
        exceptions for permanent failures (invalid video, schema errors).
        """
        job = self.queue.enqueue(
            "app.tasks.pipeline.process_video_pipeline",
            video_id,
            job_id=f"pipeline-{video_id}-{uuid4().hex[:8]}",
            job_timeout="1h",
            result_ttl=86400,
            failure_ttl=86400,
            retry=self._retry_policy(),
        )

        logger.info(f"Enqueued pipeline job {job.id} for video {video_id}")
        return job.id

    def enqueue_transcription(self, video_id: str, model_size: str = "base") -> str:
        """Enqueue a transcription-only job."""
        job = self.queue.enqueue(
            "app.tasks.pipeline.transcribe_video",
            video_id,
            model_size,
            job_id=f"transcribe-{video_id}-{uuid4().hex[:8]}",
            job_timeout="30m",
            result_ttl=86400,
            failure_ttl=86400,
            retry=self._retry_policy(),
        )

        logger.info(f"Enqueued transcription job {job.id} for video {video_id}")
        return job.id

    def enqueue_sop_generation(self, video_id: str, model_name: Optional[str] = None) -> str:
        """Enqueue an SOP generation job."""
        job = self.queue.enqueue(
            "app.tasks.pipeline.generate_sop",
            video_id,
            model_name or settings.sop_synthesis_model,
            job_id=f"sop-{video_id}-{uuid4().hex[:8]}",
            job_timeout="30m",
            result_ttl=86400,
            failure_ttl=86400,
            retry=self._retry_policy(),
        )

        logger.info(f"Enqueued SOP generation job {job.id} for video {video_id}")
        return job.id
    
    def get_job_status(self, job_id: str) -> dict:
        """
        Get the status of a job.
        
        Args:
            job_id: The job ID
            
        Returns:
            Dict with job status information
        """
        try:
            job = Job.fetch(job_id, connection=self.redis_conn)
            return {
                "job_id": job.id,
                "status": job.get_status(),
                "result": job.result if job.is_finished else None,
                "error": str(job.exc_info) if job.is_failed else None,
                "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "ended_at": job.ended_at.isoformat() if job.ended_at else None,
            }
        except Exception as e:
            return {
                "job_id": job_id,
                "status": "not_found",
                "error": str(e)
            }
    
    async def run_pipeline(self, video_id: str) -> str:
        """
        Start the full pipeline for a video.

        This validates the video exists, then enqueues the pipeline job.

        Args:
            video_id: The video ID

        Returns:
            Job ID for tracking

        Raises:
            VideoNotFoundError: If video not found
        """
        # Verify video exists
        video = await self.get_video(video_id)

        # Enqueue the pipeline job
        job_id = self.enqueue_pipeline(video_id)

        return job_id

    async def run_pipeline_inline(self, video_id: str, background_tasks) -> str:
        """
        Inline (no-worker) variant of run_pipeline.

        Schedules `process_video_pipeline(video_id)` as a FastAPI
        BackgroundTask so the work runs in the API process AFTER the HTTP
        response is sent. FastAPI runs sync background tasks in a thread
        pool, so this does NOT block the event loop.

        Use this when you don't want to run a separate worker service.

        Args:
            video_id: The video ID
            background_tasks: A FastAPI BackgroundTasks instance

        Returns:
            A synthetic job_id — not queryable via get_job_status (no RQ),
            but distinct enough for logging. The frontend polls the video
            status row in the DB anyway, not the job status.

        Raises:
            VideoNotFoundError: If video not found
        """
        # Verify video exists before scheduling the task
        await self.get_video(video_id)

        # Lazy-import to avoid forcing the worker module's import chain
        # on every API request.
        from app.tasks.pipeline import process_video_pipeline

        background_tasks.add_task(process_video_pipeline, video_id)

        job_id = f"inline-{video_id}-{uuid4().hex[:8]}"
        logger.info(f"Scheduled inline pipeline job {job_id} for video {video_id}")
        return job_id
