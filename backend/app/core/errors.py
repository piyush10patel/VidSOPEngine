"""Centralized error handling and error response schema."""
from datetime import datetime
from typing import Optional, Dict, Any

from pydantic import BaseModel


class ErrorCodes:
    """Machine-readable error codes."""
    INVALID_FILE_FORMAT = "INVALID_FILE_FORMAT"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    VIDEO_NOT_FOUND = "VIDEO_NOT_FOUND"
    TRANSCRIPT_NOT_FOUND = "TRANSCRIPT_NOT_FOUND"
    SOP_NOT_FOUND = "SOP_NOT_FOUND"
    WORKFLOW_NOT_FOUND = "WORKFLOW_NOT_FOUND"
    CHECKLIST_NOT_FOUND = "CHECKLIST_NOT_FOUND"
    TRAINING_NOT_FOUND = "TRAINING_NOT_FOUND"
    TRANSCRIPTION_FAILED = "TRANSCRIPTION_FAILED"
    SOP_GENERATION_FAILED = "SOP_GENERATION_FAILED"
    PIPELINE_FAILED = "PIPELINE_FAILED"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    INVALID_TOKEN = "INVALID_TOKEN"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"


class ErrorResponse(BaseModel):
    """Consistent error response schema for all API errors."""
    error_code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    timestamp: datetime

    class Config:
        json_schema_extra = {
            "example": {
                "error_code": "VIDEO_NOT_FOUND",
                "message": "Video with the specified ID was not found",
                "details": {"video_id": "123e4567-e89b-12d3-a456-426614174000"},
                "timestamp": "2026-01-08T12:00:00Z"
            }
        }


class AppException(Exception):
    """Base application exception."""
    
    def __init__(
        self,
        error_code: str,
        message: str,
        status_code: int = 400,
        details: Optional[Dict[str, Any]] = None
    ):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)
    
    def to_response(self) -> ErrorResponse:
        """Convert exception to ErrorResponse."""
        return ErrorResponse(
            error_code=self.error_code,
            message=self.message,
            details=self.details,
            timestamp=datetime.utcnow()
        )


class VideoNotFoundError(AppException):
    """Raised when a video is not found."""
    
    def __init__(self, video_id: str):
        super().__init__(
            error_code=ErrorCodes.VIDEO_NOT_FOUND,
            message=f"Video with ID '{video_id}' was not found",
            status_code=404,
            details={"video_id": video_id}
        )


class TranscriptNotFoundError(AppException):
    """Raised when a transcript is not found."""
    
    def __init__(self, video_id: str):
        super().__init__(
            error_code=ErrorCodes.TRANSCRIPT_NOT_FOUND,
            message=f"Transcript for video '{video_id}' was not found",
            status_code=404,
            details={"video_id": video_id}
        )


class SOPNotFoundError(AppException):
    """Raised when an SOP is not found."""
    
    def __init__(self, video_id: str):
        super().__init__(
            error_code=ErrorCodes.SOP_NOT_FOUND,
            message=f"SOP for video '{video_id}' was not found",
            status_code=404,
            details={"video_id": video_id}
        )


class InvalidFileFormatError(AppException):
    """Raised when an invalid file format is uploaded."""
    
    def __init__(self, filename: str, allowed_formats: list):
        super().__init__(
            error_code=ErrorCodes.INVALID_FILE_FORMAT,
            message=f"Invalid file format. Allowed formats: {', '.join(allowed_formats)}",
            status_code=400,
            details={"filename": filename, "allowed_formats": allowed_formats}
        )


class FileTooLargeError(AppException):
    """Raised when a file exceeds the size limit."""
    
    def __init__(self, file_size: int, max_size: int):
        super().__init__(
            error_code=ErrorCodes.FILE_TOO_LARGE,
            message=f"File size ({file_size} bytes) exceeds maximum allowed ({max_size} bytes)",
            status_code=413,
            details={"file_size": file_size, "max_size": max_size}
        )


class AuthenticationError(AppException):
    """Raised when authentication fails."""
    
    def __init__(self, message: str = "Authentication required"):
        super().__init__(
            error_code=ErrorCodes.AUTHENTICATION_REQUIRED,
            message=message,
            status_code=401
        )


class InvalidCredentialsError(AppException):
    """Raised when credentials are invalid."""
    
    def __init__(self):
        super().__init__(
            error_code=ErrorCodes.INVALID_CREDENTIALS,
            message="Invalid email or password",
            status_code=401
        )


class InvalidTokenError(AppException):
    """Raised when a token is invalid or expired."""
    
    def __init__(self, message: str = "Invalid or expired token"):
        super().__init__(
            error_code=ErrorCodes.INVALID_TOKEN,
            message=message,
            status_code=401
        )


class TranscriptionFailedError(AppException):
    """Raised when transcription fails."""
    
    def __init__(self, video_id: str, reason: str = "Unknown error"):
        super().__init__(
            error_code=ErrorCodes.TRANSCRIPTION_FAILED,
            message=f"Transcription failed for video '{video_id}': {reason}",
            status_code=500,
            details={"video_id": video_id, "reason": reason}
        )


class PipelineFailedError(AppException):
    """Raised when pipeline execution fails."""
    
    def __init__(self, video_id: str, stage: str, reason: str = "Unknown error"):
        super().__init__(
            error_code=ErrorCodes.PIPELINE_FAILED,
            message=f"Pipeline failed for video '{video_id}' at stage '{stage}': {reason}",
            status_code=500,
            details={"video_id": video_id, "stage": stage, "reason": reason}
        )
