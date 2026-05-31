"""Transcription service using OpenAI Whisper."""
import asyncio
from enum import Enum
from pathlib import Path
from typing import Optional
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import VideoNotFoundError, TranscriptNotFoundError
from app.models.video import Video, VideoStatus
from app.models.transcript import Transcript


class WhisperModelSize(str, Enum):
    """Supported Whisper model sizes."""
    BASE = "base"
    SMALL = "small"
    MEDIUM = "medium"


class TranscriptionService:
    """Service for video transcription using Groq Whisper API."""

    def __init__(self, db: AsyncSession, model_size: Optional[str] = None):
        self.db = db
        self.model_size = model_size or settings.whisper_model_size

    def transcribe_sync(self, video_path_or_key: str) -> str:
        """Transcribe a video using the configured LLM provider.

        `video_path_or_key` can be either a real local path (legacy / dev)
        or a storage key (e.g. 'videos/abc.mp4'). We resolve via the storage
        backend so R2 keys download to /tmp transparently.
        """
        import os
        import subprocess
        from app.services.llm import get_provider
        from app.services.storage import get_storage, is_remote_storage

        provider = get_provider()
        storage = get_storage()

        # Resolve the input to a real local file path. For LocalStorage this
        # is a no-op when given a path; for R2 it downloads to a temp file.
        cleanup_local = False
        if os.path.exists(video_path_or_key):
            file_path = video_path_or_key
        else:
            # Treat as a storage key — download to /tmp.
            suffix = os.path.splitext(video_path_or_key)[1] or ".mp4"
            file_path = storage.download_to_temp(video_path_or_key, suffix=suffix)
            cleanup_local = True

        cleanup_audio = False
        try:
            # Groq Whisper limit is 25 MB — extract compressed audio for larger files
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
                return resp.text
            finally:
                if cleanup_audio and os.path.exists(target) and target != file_path:
                    os.remove(target)
        finally:
            # Only delete the source file when WE downloaded it from remote
            # storage. Never delete a path that the caller still owns.
            if cleanup_local and is_remote_storage() and os.path.exists(file_path):
                os.remove(file_path)
    
    async def transcribe(self, video_path: str) -> str:
        """
        Transcribe a video file using Whisper (async wrapper).
        
        Args:
            video_path: Path to the video file
            
        Returns:
            Transcript text
        """
        # Run Whisper in a thread pool since it's CPU-bound
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.transcribe_sync, video_path)
    
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
    
    async def save_transcript(
        self, 
        video_id: str, 
        text: str, 
        model_name: Optional[str] = None
    ) -> Transcript:
        """
        Save transcript to database.
        
        Args:
            video_id: The video ID
            text: Transcript text
            model_name: Name of the Whisper model used
            
        Returns:
            The created Transcript record
        """
        transcript = Transcript(
            id=str(uuid4()),
            video_id=video_id,
            text=text,
            model_name=model_name or self.model_size,
            status="completed"
        )
        
        self.db.add(transcript)
        await self.db.commit()
        await self.db.refresh(transcript)
        
        return transcript
    
    async def get_transcript(self, video_id: str) -> Transcript:
        """
        Get transcript for a video.
        
        Args:
            video_id: The video ID
            
        Returns:
            The Transcript record
            
        Raises:
            TranscriptNotFoundError: If transcript not found
        """
        result = await self.db.execute(
            select(Transcript).where(Transcript.video_id == video_id)
        )
        transcript = result.scalar_one_or_none()
        
        if not transcript:
            raise TranscriptNotFoundError(video_id)
        
        return transcript
    
    async def has_transcript(self, video_id: str) -> bool:
        """
        Check if a video has a transcript.
        
        Args:
            video_id: The video ID
            
        Returns:
            True if transcript exists, False otherwise
        """
        result = await self.db.execute(
            select(Transcript.id).where(Transcript.video_id == video_id)
        )
        return result.scalar_one_or_none() is not None
    
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
        video.status = status.value if isinstance(status, VideoStatus) else status
        await self.db.commit()
        await self.db.refresh(video)
        return video
    
    async def transcribe_video(
        self, 
        video_id: str,
        model_size: Optional[str] = None
    ) -> Transcript:
        """
        Full transcription workflow: transcribe video and save to database.
        
        Args:
            video_id: The video ID
            model_size: Optional model size override
            
        Returns:
            The created Transcript record
        """
        # Get video
        video = await self.get_video(video_id)
        
        # Update status to transcribing
        await self.update_video_status(video_id, VideoStatus.TRANSCRIBING)
        
        try:
            # Use provided model size or default
            if model_size:
                self.model_size = model_size
            
            # Transcribe
            text = await self.transcribe(video.file_path)
            
            # Save transcript
            transcript = await self.save_transcript(video_id, text, self.model_size)
            
            return transcript
            
        except Exception as e:
            # Update status to failed on error
            await self.update_video_status(video_id, VideoStatus.FAILED)
            raise
