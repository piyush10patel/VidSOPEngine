"""SOP Generator service - uses DSPy pipeline for modular, testable prompting."""
import json
import os
import re
from typing import Optional
from uuid import uuid4


def _current_prompt_version() -> str:
    """Identifier for the prompt set baked into this deploy.

    Prompts are frozen at deploy time (CLAUDE.md INV-12), so the prompt
    version is the deploy commit SHA. Render and Vercel both set their
    respective env vars automatically; fall back to 'dev' for local runs.
    """
    sha = (
        os.environ.get("RENDER_GIT_COMMIT")
        or os.environ.get("VERCEL_GIT_COMMIT_SHA")
        or os.environ.get("GIT_COMMIT_SHA")
    )
    return (sha[:12] if sha else "dev")

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.errors import (
    VideoNotFoundError,
    TranscriptNotFoundError,
    SOPNotFoundError,
    AppException,
    ErrorCodes
)
from app.models.video import Video, VideoStatus
from app.models.transcript import Transcript
from app.models.sop import SOP
from app.schemas.sop import SOPSchema, SOPStep
from app.services.llm.dspy_executor import run_dspy_async
from app.services.llm.model_aliases import normalize_model_name


class SOPGenerationFailedError(AppException):
    """Raised when SOP generation fails."""

    def __init__(self, video_id: str, reason: str = "Unknown error"):
        super().__init__(
            error_code=ErrorCodes.SOP_GENERATION_FAILED,
            message=f"SOP generation failed for video '{video_id}': {reason}",
            status_code=500,
            details={"video_id": video_id, "reason": reason}
        )


# Prompt template for SOP generation (text-only fallback)
SOP_GENERATION_PROMPT = """You are an expert process documentation system.

Your task is to generate a Standard Operating Procedure (SOP) strictly from the transcript below.

General work coverage: narrated processes, meetings, reviews, customer/service
tasks, documentation, planning, software work, training, inspection, and
troubleshooting can all become SOPs when they describe repeatable actions. Do
not reject process-like work just because it is not a physical tool task.

RULES (CRITICAL):
1. USE ONLY PROVIDED TRANSCRIPT - Do NOT invent steps, tools, or actions not mentioned.
2. MAINTAIN TEMPORAL ORDER - Steps must follow the exact sequence described.
3. GROUP LOGICALLY - Combine closely related actions into one step; do not split unnecessarily.
4. NO HALLUCINATION - If an action is ambiguous, write "unclear action" rather than guessing.
5. BE PRECISE - Each step must be actionable and concise.
6. CONFIDENCE SCORING - Assign each step a confidence score (0.0-1.0) based on how clearly the action is described in the transcript.

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
            "objects": ["tools or materials used in this step"],
            "checks": ["verification check if mentioned"],
            "evidence": ["transcript reference or timestamp if available"],
            "confidence": 0.9,
            "notes": "clarification or 'unclear' if ambiguous"
        }}
    ],
    "overall_confidence": 0.9,
    "warnings": ["list any missing, unclear, or low-confidence steps"]
}}"""


class SOPGeneratorService:
    """Service for generating SOPs from transcripts using LLMs."""

    # Maximum retries for JSON parsing
    MAX_RETRIES = 3

    def __init__(self, db: AsyncSession, model_name: Optional[str] = None):
        """
        Initialize SOP generator service.

        Args:
            db: Database session
            model_name: LLM model name (e.g., 'mistral', 'llama3', 'mixtral')
        """
        self.db = db
        self.model_name = normalize_model_name(model_name or settings.sop_synthesis_model)

    def _build_prompt(self, transcript: str) -> str:
        """
        Build the LLM prompt for SOP generation.

        Args:
            transcript: The transcript text

        Returns:
            Formatted prompt string
        """
        return SOP_GENERATION_PROMPT.format(transcript=transcript)

    def _extract_json_from_response(self, response: str) -> str:
        """
        Extract JSON from LLM response, handling potential markdown formatting.

        Args:
            response: Raw LLM response

        Returns:
            Extracted JSON string
        """
        # Try to find JSON in code blocks first
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
        if json_match:
            return json_match.group(1).strip()

        # Try to find raw JSON object
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            return json_match.group(0).strip()

        # Return as-is if no patterns found
        return response.strip()

    def _parse_sop_response(self, response: str) -> SOPSchema:
        """Parse LLM response into SOPSchema, handling both old and new output formats."""
        json_str = self._extract_json_from_response(response)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in response: {e}")

        # Normalise new event-based format -> SOPSchema fields
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
                    "verified": item.get("verified"),
                    "verification_quote": item.get("verification_quote"),
                    "correctness_score": item.get("correctness_score"),
                    "correctness_label": item.get("correctness_label"),
                    "correctness_reason": item.get("correctness_reason"),
                    "correctness_issue_type": item.get("correctness_issue_type"),
                    "user_marked_wrong": item.get("user_marked_wrong", False),
                    "user_correction_note": item.get("user_correction_note"),
                })
            data = {
                "title": data.get("title", "Untitled SOP"),
                "description": data.get("summary", data.get("description", "")),
                "steps": normalised_steps,
                "notes": data.get("warnings", data.get("notes", [])),
                "overall_confidence": data.get("overall_confidence", 1.0),
                "warnings": data.get("warnings", []),
                "needs_review": data.get("needs_review", False),
                "generation_metadata": data.get("generation_metadata", {}),
            }

        try:
            sop = SOPSchema(**data)
            return sop
        except Exception as e:
            raise ValueError(f"Response does not match SOP schema: {e}")

    def _configure_dspy(self) -> None:
        """Configure DSPy LM once per call (idempotent - DSPy caches internally)."""
        from app.services.llm.dspy_config import configure_dspy
        self.model_name = configure_dspy(task="sop", model_name=self.model_name)

    def _generate_sync(self, transcript: str) -> str:
        """Text-only SOP generation via DSPy + self-check + threshold enforcement."""
        from app.dspy_modules.pipeline import SOPGenerationPipeline, SelfCheckPipeline

        self._configure_dspy()
        base = SOPGenerationPipeline(
            strategy="predict",
            use_few_shot=settings.sop_few_shot_enabled,
        )
        if settings.sop_self_check_enabled:
            pipeline = SelfCheckPipeline(
                base=base,
                confidence_threshold=settings.sop_confidence_threshold,
            )
        else:
            pipeline = base

        result = pipeline(transcript=transcript, events=None)

        return json.dumps({
            "title": result["title"],
            "summary": result["summary"],
            "sop": result["steps"],
            "overall_confidence": result["overall_confidence"],
            "warnings": result["warnings"],
            "needs_review": result.get("needs_review", False),
            "generation_metadata": {
                "pipeline": "text_only",
                "synthesis_model": self.model_name,
                "verification_model": settings.sop_verification_model if settings.sop_self_check_enabled else None,
                "self_check_enabled": settings.sop_self_check_enabled,
                "self_check_advisory": False,
            },
        })

    # Vision-assisted generation now lives in app/services/sop_pipelines/physical.py
    # (PhysicalPipeline). This service dispatches via get_pipeline() in
    # generate_sop_for_video().

    def _auto_capture_failure(
        self,
        video_id: str,
        transcript: str,
        frame_observations: list,
        sop: SOPSchema,
        diagnoses: list,
        video_type: str = "physical",
    ) -> None:
        """If the generation tripped quality guards, append it to failures.jsonl.

        Records `video_type` so the dataset can be filtered per-pipeline for
        independent eval and DSPy optimization runs.
        """
        if not sop.needs_review:
            return

        from app.datasets import failures as failures_store
        from app.schemas.failure import FailureType, Severity

        causes = [d.get("root_cause", "") for d in diagnoses]
        if causes and causes.count("wrong_grouping") > len(causes) / 2:
            failure_type = FailureType.WRONG_ORDER
        elif any(c in ("action_missing", "constraint_ignored") for c in causes):
            failure_type = FailureType.HALLUCINATION
        else:
            failure_type = FailureType.LOW_CONFIDENCE

        severity = Severity.HIGH if sop.overall_confidence < 0.3 else Severity.MEDIUM

        failures_store.append_case(
            transcript=transcript,
            frame_observations=frame_observations,
            actual_output=sop.model_dump(mode="json"),
            expected_output=sop.model_dump(mode="json"),
            failure_type=failure_type,
            severity=severity,
            notes=(
                f"Auto-captured ({video_type}): needs_review=True, "
                f"overall_confidence={sop.overall_confidence:.2f}, "
                f"{len(diagnoses)} hallucinations diagnosed"
            ),
            video_id=video_id,
            video_type=video_type,
        )

    def _log_diagnostic(self, video_id: str, sop_schema: SOPSchema) -> None:
        """Single greppable summary line per SOP generation.

        Helps answer 'is the new code path actually running for this
        video?' without redeploying. Grep production logs with:

            [diag-sop]

        The line records: pipeline used, frame count, step count, how
        many steps got an LLM-emitted source_frame_num (vs filled by
        fallback), and the detected output language. Anything missing
        or unexpected here narrows the bug instantly.
        """
        import logging as _log
        metadata = sop_schema.generation_metadata or {}
        adaptive = getattr(sop_schema, "_adaptive_metrics", {}) or {}
        frame_obs = getattr(sop_schema, "_frame_observations", []) or []
        steps = sop_schema.steps or []
        explicit = sum(
            1
            for s in steps
            if getattr(s, "source_frame_num", None) is not None
        )
        unique_frames = len({
            s.source_frame_num
            for s in steps
            if getattr(s, "source_frame_num", None) is not None
        })
        _log.getLogger(__name__).info(
            "[diag-sop] video=%s pipeline=%s frames=%d steps=%d explicit_frame_anchors=%d/%d "
            "unique_anchors=%d output_language=%s lang_source=%s",
            video_id,
            metadata.get("pipeline") or adaptive.get("pipeline") or "?",
            len(frame_obs),
            len(steps),
            explicit, len(steps),
            unique_frames,
            metadata.get("output_language", "?"),
            metadata.get("output_language_source", "preset"),
        )

    def _detect_and_stamp_language(self, sop_schema: SOPSchema) -> None:
        """Set ``generation_metadata.output_language`` based on the synthesised
        step content. Critical for downstream extractors.

        The synthesis prompts are written in English but the LLM mirrors the
        source language — a Hindi transcript produces Hindi step titles and
        descriptions. Without an explicit ``output_language`` stamp, the
        workflow/checklist/training extractors read the metadata default
        ``"en"`` and synthesise English output even when the SOP itself is
        clearly in Hindi. That was the user-reported "training stays in
        English even after re-generating from a Hindi SOP" symptom.

        Detection rule: if at least 30% of step.title characters across the
        whole SOP are in the Devanagari Unicode range, stamp ``"hi"``.
        Otherwise leave the existing value (which preserves explicit
        translations done via the UI). Devanagari is the only non-English
        script we currently support, so a single-script check is enough.
        """
        import re as _re
        if not sop_schema or not sop_schema.steps:
            return
        existing = (sop_schema.generation_metadata or {}).get("output_language")
        if existing in ("hi", "en"):
            # Trust an explicit upstream stamp (e.g. from the translation
            # service). Only fill in when nothing is set.
            return
        devanagari = _re.compile(r"[ऀ-ॿ]")
        total_chars = 0
        devanagari_chars = 0
        for step in sop_schema.steps:
            for txt in (step.title or "", step.description or ""):
                if not txt:
                    continue
                total_chars += len(txt)
                devanagari_chars += len(devanagari.findall(txt))
        if total_chars == 0:
            return
        ratio = devanagari_chars / total_chars
        metadata = dict(sop_schema.generation_metadata or {})
        if ratio >= 0.30:
            metadata["output_language"] = "hi"
            metadata["output_language_source"] = "auto_detected_devanagari"
        else:
            metadata["output_language"] = "en"
            metadata["output_language_source"] = "auto_detected_default"
        sop_schema.generation_metadata = metadata
        import logging as _log
        _log.getLogger(__name__).info(
            "[lang] auto-detected SOP language=%s (devanagari_ratio=%.2f, total_chars=%d)",
            metadata["output_language"], ratio, total_chars,
        )

    def _attach_fallback_frame_images(
        self,
        video_id: str,
        video_path: str,
        sop: SOPSchema,
    ) -> None:
        """Attach representative frame URLs when text-only fallback produced the SOP."""
        if not sop.steps or any(step.image_url for step in sop.steps):
            return

        try:
            import os
            from app.services.frame_extractor import FrameExtractor
            from app.services.sop_pipelines.base import assign_frame_images_linear

            extractor = FrameExtractor()
            frames = extractor.extract_frames(
                video_path,
                video_id,
                num_frames=settings.procedural_frame_count,
            )
            frame_observations = [
                {
                    "frame_num": i + 1,
                    "description": "Fallback representative video frame",
                    "image_url": f"/videos/{video_id}/frames/{os.path.basename(path)}",
                }
                for i, path in enumerate(frames)
            ]
            assign_frame_images_linear(sop.steps, frame_observations)
            sop._frame_observations = frame_observations  # type: ignore[attr-defined]
            metadata = dict(sop.generation_metadata or {})
            metadata["image_source"] = "fallback_frame_extraction"
            metadata["fallback_frame_count"] = len(frame_observations)
            sop.generation_metadata = metadata
        except Exception as e:
            import logging as _log
            _log.getLogger(__name__).warning(
                "Fallback frame attachment failed for %s: %s",
                video_id,
                e,
            )

    async def generate(self, transcript: str) -> SOPSchema:
        """
        Generate SOP from transcript text with retry logic.

        Args:
            transcript: The transcript text

        Returns:
            Validated SOPSchema

        Raises:
            SOPGenerationFailedError: If generation fails after retries
        """
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                # DSPy settings are process-global; keep DSPy calls on one
                # dedicated thread instead of the default thread pool.
                response = await run_dspy_async(self._generate_sync, transcript)

                # Parse and validate response
                sop = self._parse_sop_response(response)
                return sop

            except Exception as e:
                last_error = e
                # Continue to retry

        raise ValueError(f"Failed to generate valid SOP after {self.MAX_RETRIES} attempts: {last_error}")

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

    async def save_sop(
        self,
        video_id: str,
        transcript_id: str | None,
        sop_data: SOPSchema
    ) -> SOP:
        """
        Save SOP to database.

        Args:
            video_id: The video ID
            transcript_id: The transcript ID
            sop_data: The validated SOP schema

        Returns:
            The created SOP record
        """
        video = await self.get_video(video_id)
        sop = SOP(
            id=str(uuid4()),
            video_id=video_id,
            transcript_id=transcript_id,
            sop_json=sop_data.model_dump(),
            created_by=video.user_id,
            updated_by=video.user_id,
            category="Uncategorized",
            tags_json=[],
            visibility_scope="private",
            allowed_role_min="manager",
            shared_with_users_json=[],
            # MLOps audit trail — attribute the output to the deploy that
            # produced it and the synthesis model that ran the pipeline.
            prompt_version=_current_prompt_version(),
            model_used=settings.sop_synthesis_model,
        )

        self.db.add(sop)
        await self.db.commit()
        await self.db.refresh(sop)
        try:
            from app.services.cleanup_service import mark_artifact_status
            await mark_artifact_status(self.db, video_id, "sop", "success")
        except Exception as e:
            import logging as _l
            _l.getLogger(__name__).warning(f"SOP artifact status update failed: {e}")

        # Charge the user's monthly budget. Estimate is fine — the goal
        # is operator-level cost visibility, not exact accounting. Failures
        # here must not block the SOP from being returned.
        try:
            from app.services.budget_service import record_tokens, ESTIMATED_TOKENS_PER_GENERATION
            from sqlalchemy import select
            from app.models.user import User
            result = await self.db.execute(select(User).where(User.id == video.user_id))
            user = result.scalar_one_or_none()
            if user is not None:
                await record_tokens(self.db, user, ESTIMATED_TOKENS_PER_GENERATION)
        except Exception as e:
            import logging as _l
            _l.getLogger(__name__).warning(f"budget record_tokens failed: {e}")

        return sop

    async def get_sop(self, video_id: str) -> SOP:
        """
        Get SOP for a video.

        Args:
            video_id: The video ID

        Returns:
            The SOP record

        Raises:
            SOPNotFoundError: If SOP not found
        """
        result = await self.db.execute(
            select(SOP).where(SOP.video_id == video_id)
        )
        sop = result.scalar_one_or_none()

        if not sop:
            raise SOPNotFoundError(video_id)

        return sop

    async def has_sop(self, video_id: str) -> bool:
        """
        Check if a video has an SOP.

        Args:
            video_id: The video ID

        Returns:
            True if SOP exists, False otherwise
        """
        result = await self.db.execute(
            select(SOP.id).where(SOP.video_id == video_id)
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

    async def generate_sop_for_video(
        self,
        video_id: str,
        model_name: Optional[str] = None
    ) -> SOP:
        """Dispatch to the right pipeline based on video.video_type.

        UI videos       -> UIPipeline (no transcript, label-grounded)
        Physical videos -> PhysicalPipeline (transcript + vision, with text fallback)

        Cross-cutting concerns (DB save, status updates, Braintrust logging,
        auto-failure-capture) live here so both pipelines share them.
        """
        from app.services.sop_pipelines import get_pipeline

        video = await self.get_video(video_id)
        await self.update_video_status(video_id, VideoStatus.SOP_GENERATING)

        if model_name:
            self.model_name = normalize_model_name(model_name)

        is_ui = (video.video_type == "ui")
        transcript = None
        if not is_ui:
            transcript = await self.get_transcript(video_id)

        # Adaptive granularity - classify if the user chose auto, persist the result.
        complexity = video.pipeline_complexity or "auto"
        if not is_ui and complexity == "auto":
            from app.models.video import PipelineComplexity
            from app.services.complexity_classifier import classify_video_complexity
            try:
                verdict = classify_video_complexity(transcript.text if transcript else "")
                complexity = verdict.get("pipeline_type") or PipelineComplexity.PROCEDURAL_COMPLEX.value
                video.pipeline_complexity = complexity
                video.pipeline_complexity_confidence = float(verdict.get("confidence", 0.0))
                await self.db.commit()
                import logging as _log
                _log.getLogger(__name__).info(
                    f"[adaptive] video {video_id} -> {complexity} "
                    f"(conf={verdict.get('confidence')}, stage={verdict.get('stage')}, "
                    f"reason={verdict.get('reason')})"
                )
            except Exception as e:
                import logging as _log
                _log.getLogger(__name__).warning(
                    f"[adaptive] classifier failed, defaulting to procedural: {e}"
                )
                complexity = "procedural_complex"

        try:
            pipeline = get_pipeline(
                video.video_type,
                self.model_name,
                complexity=complexity,
                user_id=video.user_id,
            )
            sop_schema: Optional[SOPSchema] = None
            try:
                if is_ui:
                    sop_schema = await run_dspy_async(
                        pipeline.generate_sync,
                        video.file_path,
                        video_id,
                    )
                else:
                    sop_schema = await run_dspy_async(
                        pipeline.generate_sync,
                        transcript.text,
                        video.file_path,
                        video_id,
                    )
            except Exception as e:
                import logging as _log
                _log.getLogger(__name__).warning(
                    f"{video.video_type} pipeline failed for {video_id}: {e}"
                )
                # Text-only fallback only applies to physical (UI has no transcript)
                if not is_ui:
                    sop_schema = await self.generate(transcript.text)
                    sop_schema.video_type = "physical"
                    self._attach_fallback_frame_images(
                        video_id,
                        video.file_path,
                        sop_schema,
                    )

            if sop_schema is None:
                raise SOPGenerationFailedError(video_id, "All pipelines failed")

            # Stamp the detected output language onto the SOP BEFORE saving
            # so the downstream workflow / checklist / training extractors
            # read the right target_language when they run. Without this,
            # a Hindi-narration video produces Hindi step text but stays
            # marked output_language=en, so training generates English
            # content even on a re-run — exactly the bug the user hit.
            self._detect_and_stamp_language(sop_schema)

            # Operator-visible diagnostic dump. Greppable in Render logs as
            # ``[diag-sop]``. Lets us answer "did this video actually use
            # the new code paths?" without redeploying.
            self._log_diagnostic(video_id, sop_schema)

            transcript_id = transcript.id if transcript else None
            sop = await self.save_sop(video_id, transcript_id, sop_schema)
            await self.update_video_status(video_id, VideoStatus.COMPLETED)

            # Unified routing observability - single line per video, regardless
            # of which pipeline ran. Picks up _adaptive_metrics that each pipeline
            # stashes on the SOPSchema.
            adaptive_metrics = getattr(sop_schema, "_adaptive_metrics", {}) or {}
            if adaptive_metrics:
                import logging as _log
                _log.getLogger(__name__).info(
                    "[adaptive] routed video=%s video_type=%s pipeline=%s "
                    "complexity=%s confidence=%.2f frames=%s actions=%s steps=%s "
                    "merge_count=%s merge_ratio=%.2f",
                    video_id,
                    video.video_type,
                    adaptive_metrics.get("pipeline"),
                    complexity if not is_ui else "n/a",
                    float(video.pipeline_complexity_confidence or 0.0),
                    adaptive_metrics.get("frame_count"),
                    adaptive_metrics.get("atomic_action_count"),
                    adaptive_metrics.get("step_count"),
                    adaptive_metrics.get("merge_count"),
                    float(adaptive_metrics.get("event_merge_ratio") or 0.0),
                )

            # Braintrust online scoring (no-op if BRAINTRUST_API_KEY unset)
            try:
                from app.observability.braintrust_client import log_sop_generation
                from app.observability.scorers import online_scores
                obs = getattr(sop_schema, "_frame_observations", [])
                tx = getattr(sop_schema, "_transcript", transcript.text if transcript else "")
                output_dict = sop_schema.model_dump()
                bt_metadata = {
                    "model": self.model_name,
                    "video_type": video.video_type,
                    **{f"adaptive_{k}": v for k, v in adaptive_metrics.items()},
                }
                log_sop_generation(
                    sop_id=sop.id,
                    video_id=video_id,
                    transcript=tx,
                    frame_observations=obs,
                    output=output_dict,
                    scores=online_scores(output_dict, tx, obs),
                    metadata=bt_metadata,
                )
            except Exception as bt_err:
                import logging as _l
                _l.getLogger(__name__).warning(f"Braintrust logging failed: {bt_err}")

            # Auto-capture failure case if quality threshold tripped
            try:
                self._auto_capture_failure(
                    video_id=video_id,
                    transcript=getattr(sop_schema, "_transcript", transcript.text if transcript else ""),
                    frame_observations=getattr(sop_schema, "_frame_observations", []),
                    sop=sop_schema,
                    diagnoses=getattr(sop_schema, "_diagnoses", []),
                    video_type=video.video_type,
                )
            except Exception as e:
                import logging as _l
                _l.getLogger(__name__).warning(f"Auto-capture failed: {e}")

            # Free heavy debug attributes from the SOPSchema. Braintrust +
            # auto_capture have already consumed them; keeping them around
            # would pin frame text + timeline buffers in memory until the
            # request returns. Storage / video-file cleanup is the caller's
            # responsibility (see cleanup_service.cleanup_processing_artifacts).
            try:
                from app.services.cleanup_service import free_pipeline_memory
                free_pipeline_memory(sop_schema)
            except Exception as e:
                import logging as _l
                _l.getLogger(__name__).warning(f"Memory free failed: {e}")

            return sop

        except Exception as e:
            await self.update_video_status(video_id, VideoStatus.FAILED)
            try:
                from app.services.cleanup_service import mark_artifact_status
                await mark_artifact_status(self.db, video_id, "sop", "failed", str(e))
            except Exception:
                pass
            raise SOPGenerationFailedError(video_id, str(e))

    async def ab_test_sop_for_video(
        self,
        video_id: str,
        models: list[str] | None = None,
        pipeline_complexity: str | None = None,
    ) -> list[tuple[str, SOPSchema]]:
        """Generate unsaved SOP variants for model A/B testing.

        This uses the same generation path as production but does not save,
        finalize, update video status, or run media cleanup.
        """
        from app.services.sop_pipelines import get_pipeline

        video = await self.get_video(video_id)
        is_ui = video.video_type == "ui"
        transcript = None if is_ui else await self.get_transcript(video_id)
        selected = models or [
            normalize_model_name(m.strip())
            for m in settings.sop_ab_test_models.split(",")
            if m.strip()
        ]
        selected = [normalize_model_name(model) for model in selected]
        complexity = pipeline_complexity or video.pipeline_complexity or "procedural_complex"

        variants: list[tuple[str, SOPSchema]] = []
        for model in selected:
            pipeline = get_pipeline(
                video.video_type,
                model,
                complexity=complexity,
                user_id=video.user_id,
            )
            if is_ui:
                sop_schema = await run_dspy_async(
                    pipeline.generate_sync,
                    video.file_path,
                    video_id,
                )
            else:
                sop_schema = await run_dspy_async(
                    pipeline.generate_sync,
                    transcript.text,
                    video.file_path,
                    video_id,
                )
            variants.append((model, sop_schema))
        return variants
