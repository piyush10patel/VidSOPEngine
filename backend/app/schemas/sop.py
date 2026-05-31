"""Pydantic schemas for SOP-related API operations."""
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_serializer

from app.schemas._datetime import utc_iso


class SOPStep(BaseModel):
    """Schema for a single SOP step."""

    step_number: int = Field(..., description="Sequential step number")
    title: str = Field(..., description="Brief title for the step")
    description: str = Field(..., description="Detailed description of the step")
    tools: List[str] = Field(default_factory=list, description="Tools required for this step")
    checks: List[str] = Field(default_factory=list, description="Verification checks for this step")
    image_url: Optional[str] = Field(default=None, description="URL to step image from video frame")
    source_frame_num: Optional[int] = Field(
        default=None,
        description=(
            "1-based frame number the synthesis model chose as the single best "
            "visual illustration of this step. Set by the LLM during synthesis "
            "and used to look up image_url deterministically."
        ),
    )
    evidence: List[str] = Field(default_factory=list, description="Frame/timestamp references backing this step")
    confidence: float = Field(default=1.0, description="Confidence score 0-1 for this step")
    notes: Optional[str] = Field(default=None, description="Clarification if action is ambiguous")
    verified: Optional[bool] = Field(default=None, description="Set by self-check: True if supported by source")
    verification_quote: Optional[str] = Field(default=None, description="Source quote backing this step")
    correctness_score: Optional[float] = Field(default=None, description="Per-step correctness score 0-1")
    correctness_label: Optional[str] = Field(default=None, description="supported, partially_supported, or unsupported")
    correctness_reason: Optional[str] = Field(default=None, description="Short reason for per-step correctness score")
    correctness_issue_type: Optional[str] = Field(default=None, description="missing_evidence, wrong_order, hallucinated_action, vague_step, or none")
    user_marked_wrong: bool = Field(default=False, description="True when a reviewer marked this generated step wrong")
    user_correction_note: Optional[str] = Field(default=None, description="Reviewer note explaining what was wrong")
    warning: Optional[str] = None
    estimated_time_minutes: Optional[int] = None
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    linked_documents: List[str] = Field(default_factory=list)
    linked_checklists: List[str] = Field(default_factory=list)
    linked_training: List[str] = Field(default_factory=list)
    linked_workflows: List[str] = Field(default_factory=list)


class SOPSchema(BaseModel):
    """Schema for the complete SOP structure."""

    title: str = Field(..., description="Title of the SOP")
    description: str = Field(..., description="Overview description of the SOP")
    steps: List[SOPStep] = Field(..., description="List of procedure steps")
    notes: List[str] = Field(default_factory=list, description="Safety notes and warnings")
    overall_confidence: float = Field(default=1.0, description="Overall confidence score 0-1")
    warnings: List[str] = Field(default_factory=list, description="Missing, unclear, or low-confidence issues")
    needs_review: bool = Field(default=False, description="True if any step needs human review")
    generation_metadata: Dict[str, Any] = Field(default_factory=dict)
    video_type: Optional[str] = Field(default=None, description="'ui' or 'physical' pipeline")
    tools_materials: List[str] = Field(default_factory=list)
    sections: List[Dict[str, Any]] = Field(default_factory=list)
    attachments: List[Dict[str, Any]] = Field(default_factory=list)


class SimplifiedSOPStep(BaseModel):
    step_number: int
    title: str
    instruction: str
    tools: List[str] = Field(default_factory=list)
    checks: List[str] = Field(default_factory=list)
    image_url: Optional[str] = None
    notes: Optional[str] = None


class SimplifiedSOP(BaseModel):
    title: str
    description: str
    steps: List[SimplifiedSOPStep] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class SOPResponse(BaseModel):
    """Response schema for SOP data."""

    id: str
    video_id: Optional[str] = None
    sop: SOPSchema
    operator_sop: Optional[SimplifiedSOP] = None
    can_view_internal: bool = False
    is_finalized: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None
    folder_id: Optional[str] = None
    category: str = "Uncategorized"
    tags: List[str] = Field(default_factory=list)
    archived: bool = False
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    visibility_scope: str = "private"
    allowed_role_min: str = "manager"
    shared_with_users: List[str] = Field(default_factory=list)
    owner_email: Optional[str] = None
    linked_workflows_count: int = 0
    linked_checklists_count: int = 0
    linked_training_count: int = 0
    source_type: str = "ai_generated"
    status: str = "draft"
    last_reviewed_at: Optional[datetime] = None
    estimated_duration_minutes: Optional[int] = None
    @field_serializer("created_at", "updated_at", "last_reviewed_at")
    def _ser_dt(self, v):
        return utc_iso(v)

    class Config:
        from_attributes = True


class SOPCreateRequest(BaseModel):
    """Create a managed SOP record."""

    sop: SOPSchema
    video_id: Optional[str] = None
    folder_id: Optional[str] = None
    category: str = "Uncategorized"
    tags: List[str] = Field(default_factory=list)
    visibility_scope: str = "private"
    allowed_role_min: str = "manager"
    shared_with_users: List[str] = Field(default_factory=list)
    source_type: str = "manual"
    status: str = "draft"
    estimated_duration_minutes: Optional[int] = None


class SOPUpdateRequest(BaseModel):
    """Edit SOP content or management metadata."""

    sop: Optional[SOPSchema] = None
    folder_id: Optional[str] = None
    category: Optional[str] = None
    tags: Optional[List[str]] = None
    archived: Optional[bool] = None
    visibility_scope: Optional[str] = None
    allowed_role_min: Optional[str] = None
    shared_with_users: Optional[List[str]] = None
    source_type: Optional[str] = None
    status: Optional[str] = None
    last_reviewed_at: Optional[datetime] = None
    estimated_duration_minutes: Optional[int] = None


class SOPListResponse(BaseModel):
    sops: List[SOPResponse]
    total: int


class SOPFolderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    parent_id: Optional[str] = None


class SOPFolderUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    parent_id: Optional[str] = None


class SOPFolderResponse(BaseModel):
    id: str
    name: str
    parent_id: Optional[str] = None
    owner_id: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    @field_serializer("created_at", "updated_at")
    def _ser_dt(self, v):
        return utc_iso(v)

    class Config:
        from_attributes = True


class SOPFolderListResponse(BaseModel):
    folders: List[SOPFolderResponse]
    total: int


class SOPGenerationRequest(BaseModel):
    """Request schema for SOP generation."""

    llm_model: Optional[str] = Field(
        default=None,
        description="LLM model to use for generation (defaults to config)",
    )


class SOPABTestRequest(BaseModel):
    """Run multiple SOP synthesis models against the same video without saving."""

    models: Optional[List[str]] = Field(
        default=None,
        description="Together/Groq model names. Defaults to SOP_AB_TEST_MODELS.",
    )
    pipeline_complexity: Optional[str] = Field(
        default=None,
        description="Override pipeline complexity for the A/B run.",
    )


class SOPABTestVariant(BaseModel):
    model: str
    sop: SOPSchema
    step_scores: List[SOPStep]
    overall_confidence: float
    needs_review: bool
    warnings: List[str] = Field(default_factory=list)


class SOPABTestResponse(BaseModel):
    video_id: str
    variants: List[SOPABTestVariant]


class SOPGenerationJobResponse(BaseModel):
    """Response schema for SOP generation job status."""

    video_id: str
    status: str
    message: str = "SOP generation started"
