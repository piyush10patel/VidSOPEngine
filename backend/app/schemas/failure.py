"""Pydantic schemas for the SOP failure-case dataset."""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_serializer

from app.schemas._datetime import utc_iso


class FailureType(str, Enum):
    """Categorisation of why an SOP output is considered a failure."""
    HALLUCINATION = "hallucination"          # Steps reference things not in source
    WRONG_ANSWER = "wrong_answer"            # Action described incorrectly
    BAD_FORMATTING = "bad_formatting"        # JSON schema violations, missing fields
    EDGE_CASE = "edge_case"                  # Unusual input (very short, no narration, etc.)
    MISSING_STEP = "missing_step"            # Real step from video was skipped
    WRONG_ORDER = "wrong_order"              # Steps in wrong sequence
    LOW_CONFIDENCE = "low_confidence"        # Model was uncertain (good detection)


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FailureInput(BaseModel):
    """The input that produced the failure — replayable."""
    transcript: str
    frame_observations: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Raw frame text from vision model: [{frame_num, description}, ...]",
    )


class FailureCase(BaseModel):
    """One failure case. Becomes a training example for DSPy optimization."""
    id: str = Field(..., description="Unique case ID")
    video_id: Optional[str] = Field(None, description="Source video for traceability")
    video_type: Optional[str] = Field(default="physical", description="'ui' or 'physical' — pipeline this case is for")
    created_at: datetime
    input: FailureInput
    actual_output: Dict[str, Any] = Field(..., description="The SOP the model generated")
    expected_output: Dict[str, Any] = Field(..., description="The corrected SOP")
    failure_type: FailureType
    severity: Severity = Severity.MEDIUM
    notes: Optional[str] = None

    @field_serializer("created_at")
    def _ser_dt(self, v):
        return utc_iso(v)

class FailureCreateRequest(BaseModel):
    """POST body to record a new failure case."""
    video_id: Optional[str] = Field(None, description="If set, input/actual_output auto-loaded from DB")
    expected_output: Dict[str, Any]
    failure_type: FailureType
    severity: Severity = Severity.MEDIUM
    notes: Optional[str] = None
    # Optional manual overrides if not loading from a video_id:
    transcript: Optional[str] = None
    frame_observations: Optional[List[Dict[str, Any]]] = None
    actual_output: Optional[Dict[str, Any]] = None


class FailureListResponse(BaseModel):
    cases: List[FailureCase]
    total: int
    by_type: Dict[str, int]
