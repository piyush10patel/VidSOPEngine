"""Physical process video pipeline (vision-grounded, probabilistic).

Whisper transcript + per-frame vision descriptions → DSPy synthesis →
self-check + few-shot retrieval → SOP. This is the original pipeline that
existed before the UI/physical split.
"""
import json
import logging
import os

from app.core.config import settings
from app.dspy_modules.pipeline import DetailedSOPPipeline, FullSOPPipeline, SelfCheckPipeline
from app.observability.diagnosis import diagnose_hallucination, summarize_diagnoses
from app.observability.langsmith_client import configure as configure_langsmith, traceable
from app.observability.scorers import hallucination_rate
from app.schemas.sop import SOPSchema
from app.services.frame_extractor import FrameExtractor
from app.services.sop_pipelines.base import (
    assign_frame_images_linear,
    parse_sop_response,
)

logger = logging.getLogger(__name__)


class PhysicalPipeline:
    """Generates SOPs from physical-process videos.

    Inputs:  transcript text, video file path, video_id
    Outputs: SOPSchema with video_type='physical'
    """

    def __init__(self, model_name: str, user_id: str | None = None):
        self.model_name = model_name
        self.user_id = user_id

    def _configure_dspy(self) -> None:
        from app.services.llm.dspy_config import configure_dspy
        self.model_name = configure_dspy(task="sop", model_name=self.model_name)

    def generate_sync(self, transcript: str, video_path: str, video_id: str) -> SOPSchema:
        """Synchronous (blocking) generation — call via run_in_executor from async code."""
        configure_langsmith()

        @traceable(name="generate_sop_physical", run_type="chain", metadata={"video_id": video_id})
        def _generate():
            return self._generate_inner(transcript, video_path, video_id)

        return _generate()

    def _generate_inner(self, transcript: str, video_path: str, video_id: str) -> SOPSchema:
        from app.services.llm import get_provider

        provider = get_provider()
        vision_model = settings.vision_model

        extractor = FrameExtractor()
        # Budget frames proportional to video length. Without this a 60s
        # video and a 9s video both got 12 frames, so longer clips
        # collapsed into 5-6 steps that missed half the action. See
        # FrameExtractor.adaptive_frame_count for the schedule.
        n_frames = extractor.adaptive_frame_count(video_path)
        frames = extractor.extract_frames(
            video_path, video_id,
            num_frames=n_frames,
        )
        if not frames:
            raise ValueError("No frames extracted")

        # Heuristic pre-filter: drop near-duplicate frames before the VLM
        # loop. Cheap perceptual diff (~5ms/frame) typically saves 30-50%
        # of vision tokens on talky / static videos. Configurable via
        # settings.prefilter_static_threshold; set to 1.0 to disable.
        threshold = float(getattr(settings, "prefilter_static_threshold", 0.92))
        if threshold < 1.0:
            frames = extractor.filter_static_frames(
                frames, similarity_threshold=threshold,
            )

        @traceable(name="analyze_frame_physical", run_type="llm")
        def _analyze_frame(frame_num: int, total: int, frame_b64: str) -> str:
            # Provider call routes through app/services/llm/ — explicit
            # 30s timeout + 3-attempt retry on transient errors. See CLAUDE.md.
            # Tighter literal-observation prompt — the previous version asked
            # for 'main action' which the VLM tended to summarise into a
            # paraphrase. This version forces concrete observations the
            # synthesiser can quote verbatim.
            prompt = (
                "This video may document physical work, software work, service "
                "work, documentation, planning, training, review, inspection, "
                "or troubleshooting. Treat any repeatable work signal as valid "
                "SOP evidence; do not dismiss it as irrelevant only because it "
                "is not a hand/tool task.\n\n"
                f"You are observing frame {frame_num} of {total} from an "
                f"operational procedure.\n\n"
                "Describe ONLY what is directly visible in this single frame.\n"
                "Your response MUST start with TWO lines in this exact format "
                "and nothing else on those lines:\n"
                "  PHASE: before        (operator is approaching / reaching "
                "                        toward the target; the main action "
                "                        has NOT started yet)\n"
                "  PHASE: during        (operator is actively performing the "
                "                        main action; tool engaged with "
                "                        object; state mid-change; this is "
                "                        the moment you would photograph if "
                "                        you had to pick ONE frame to "
                "                        illustrate the action)\n"
                "  PHASE: after         (the main action just completed; the "
                "                        object is in its new stable state; "
                "                        the operator is releasing or "
                "                        moving away)\n"
                "Pick exactly one. If genuinely unclear, prefer `during`.\n"
                "\n"
                "  CONFIDENCE: 0.0      (you cannot reliably tell what is "
                "                        happening in this frame)\n"
                "  CONFIDENCE: 0.5      (best guess, but plausibly wrong)\n"
                "  CONFIDENCE: 1.0      (you are certain about the PHASE and "
                "                        PRIMARY ACTION)\n"
                "Pick a value in [0.0, 1.0] — be conservative; use 1.0 only "
                "when the action is unambiguous.\n"
                "\n"
                "Then on subsequent lines:\n"
                "1. PRIMARY ACTION (verb-first): the exact physical action "
                "being performed RIGHT NOW (e.g. 'unscrewing the cap', "
                "'pouring water', 'pressing the green button'). If multiple "
                "actions are happening, list them separately. If no action "
                "is visible, say 'no visible physical action, but process "
                "signal: ...' when there is narration, a screen, a document, "
                "a meeting artifact, or a visible work item to document. Only "
                "say 'no process signal' when nothing work-related is present.\n"
                "2. STATE OBSERVATIONS: any visible state CHANGE or "
                "noteworthy state — open/closed, on/off, full/empty, "
                "attached/detached, mid-rotation, partially inserted, light "
                "on, valve open, bolt flush, screen text changed. Be "
                "concrete: 'bolt is now flush with surface', 'cap is "
                "halfway off', 'green LED illuminated'. State observations "
                "give the synthesis model the visual anchor it needs to "
                "tie a step to the right frame.\n"
                "3. TOOLS/MATERIALS VISIBLE: list every tool, part, or "
                "object visible in the frame. Use literal names, not "
                "categories ('phillips screwdriver', not 'tool').\n"
                "4. HAND POSITIONS: where each hand is and what it is "
                "touching. Be precise about contact ('right hand grips "
                "the bottle neck', 'left hand stabilises the base').\n\n"
                "RULES: Use literal action descriptions. Do NOT infer goals "
                "('appears to be preparing'), do NOT use generic verbs "
                "('handles', 'works with'), do NOT summarise the whole "
                "procedure. Describe ONLY this frame."
            )
            resp = provider.vision(prompt, frame_b64, model=vision_model, timeout=30)
            return resp.text

        # Pulled out into helpers so other pipelines can share the
        # parsing rules if we extend phase / action tagging to them.
        import re as _re
        _PHASE_RE = _re.compile(
            r"PHASE\s*[:=]\s*(before|during|after)\b",
            _re.IGNORECASE,
        )
        # Confidence is a float in [0, 1]. Tolerant of "CONFIDENCE: 0.8",
        # "CONFIDENCE=0.85", or "CONFIDENCE: 90%" — we strip a trailing
        # percent and divide.
        _CONFIDENCE_RE = _re.compile(
            r"CONFIDENCE\s*[:=]\s*([0-9]*\.?[0-9]+)\s*(%?)",
            _re.IGNORECASE,
        )
        # PRIMARY ACTION is the first numbered field in the vision
        # response (see prompt above). Captures everything from the
        # colon to end-of-line; tolerant of "1." / "1)" prefixes and of
        # leading text like "1. PRIMARY ACTION (verb-first): ...".
        _PRIMARY_ACTION_RE = _re.compile(
            r"(?:^|\n)\s*\d[\.\)]\s*PRIMARY\s*ACTION[^:\n]*:\s*([^\n]+)",
            _re.IGNORECASE,
        )

        def _extract_phase(description: str) -> str | None:
            if not description:
                return None
            m = _PHASE_RE.search(description)
            return m.group(1).lower() if m else None

        def _extract_confidence(description: str) -> float | None:
            if not description:
                return None
            m = _CONFIDENCE_RE.search(description)
            if not m:
                return None
            try:
                value = float(m.group(1))
            except ValueError:
                return None
            if m.group(2) == "%":
                value = value / 100.0
            # Clamp to [0, 1] in case the model emits 1.5 or 87.
            if value > 1.0:
                value = value / 100.0 if value <= 100.0 else 1.0
            return max(0.0, min(1.0, value))

        def _extract_primary_action(description: str) -> str | None:
            if not description:
                return None
            m = _PRIMARY_ACTION_RE.search(description)
            return m.group(1).strip().lower() if m else None

        frame_observations = []
        for i, frame_path in enumerate(frames):
            try:
                description = _analyze_frame(
                    frame_num=i + 1,
                    total=len(frames),
                    frame_b64=extractor.frame_to_base64(frame_path),
                )
                frame_observations.append({
                    "frame_num": i + 1,
                    "description": description,
                    "phase": _extract_phase(description),
                    # Vision-model self-confidence, [0, 1]. The verb
                    # match swap in base.py skips overriding a chosen
                    # frame whose confidence is high — trust the LLM
                    # when it was sure.
                    "confidence": _extract_confidence(description),
                    # Action text from the PRIMARY ACTION line of the
                    # vision response. Used by the verb-match swap in
                    # base.py to detect when the LLM cited a frame whose
                    # action doesn't actually match the step ("wipe"
                    # frame chosen for an "inspect" step).
                    "primary_action": _extract_primary_action(description),
                    "image_url": f"/videos/{video_id}/frames/{os.path.basename(frame_path)}",
                })
            except Exception as e:
                logger.warning(f"Frame {i+1} vision call failed: {e}")

        if not frame_observations:
            raise ValueError("No frames could be analyzed")

        use_legacy = (settings.physical_sop_synthesis_mode or "").lower() == "legacy_detailed"
        self_check_advisory = use_legacy and settings.sop_self_check_advisory_for_legacy

        @traceable(name="synthesize_sop_physical", run_type="chain", metadata={"n_events": len(frame_observations)})
        def _synthesize():
            self._configure_dspy()
            if use_legacy:
                base = DetailedSOPPipeline(
                    strategy="predict",
                    use_few_shot=settings.sop_few_shot_enabled,
                )
            else:
                base = FullSOPPipeline(
                    event_strategy="predict",
                    sop_strategy="predict",
                    use_few_shot=settings.sop_few_shot_enabled,
                )
            if settings.sop_self_check_enabled:
                pipeline = SelfCheckPipeline(
                    base=base,
                    confidence_threshold=settings.sop_confidence_threshold,
                    advisory=self_check_advisory,
                )
                return pipeline(
                    transcript=transcript,
                    frame_observations=frame_observations,
                    user_id=self.user_id,
                )
            return base(
                transcript=transcript,
                frame_observations=frame_observations,
                user_id=self.user_id,
            )

        result = _synthesize()

        raw = json.dumps({
            "title": result["title"],
            "summary": result["summary"],
            "sop": result["steps"],
            "overall_confidence": result["overall_confidence"],
            "warnings": result["warnings"],
            "needs_review": result.get("needs_review", False),
        })
        sop = parse_sop_response(raw, video_type="physical")
        assign_frame_images_linear(sop.steps, frame_observations)
        sop.generation_metadata = {
            "pipeline": "physical",
            "synthesis_mode": settings.physical_sop_synthesis_mode,
            "synthesis_model": self.model_name,
            "vision_model": vision_model,
            "verification_model": settings.sop_verification_model if settings.sop_self_check_enabled else None,
            "self_check_enabled": settings.sop_self_check_enabled,
            "self_check_advisory": self_check_advisory,
            "frame_count": len(frame_observations),
        }

        # Diagnosis if hallucination detected — preserves existing observability
        sop_dict = sop.model_dump()
        diagnoses = []
        if hallucination_rate(sop_dict, transcript, frame_observations) < 1.0:
            diagnoses = diagnose_hallucination(sop_dict, transcript, frame_observations)
            summary = summarize_diagnoses(diagnoses)
            logger.info(
                f"[physical] Hallucination diagnosis for {video_id}: "
                f"{summary['total_hallucinations']} items, by_cause={summary['by_root_cause']}"
            )

        # Stash for the service-layer auto-capture step
        sop._diagnoses = diagnoses  # type: ignore[attr-defined]
        sop._frame_observations = frame_observations  # type: ignore[attr-defined]
        sop._transcript = transcript  # type: ignore[attr-defined]

        # Adaptive-granularity metric parity — the procedural path emits the
        # same metric keys as atomic_simple so dashboards can compare.
        step_count = len(sop.steps)
        sop._adaptive_metrics = {  # type: ignore[attr-defined]
            "pipeline": "procedural_complex",
            "synthesis_mode": settings.physical_sop_synthesis_mode,
            "vision_model": vision_model,
            "synthesis_model": self.model_name,
            "verification_model": settings.sop_verification_model if settings.sop_self_check_enabled else None,
            "self_check_advisory": self_check_advisory,
            "frame_count": len(frame_observations),
            "atomic_action_count": len(frame_observations),
            "step_count": step_count,
            "event_merge_ratio": (
                max(0, len(frame_observations) - step_count) / len(frame_observations)
                if frame_observations else 0.0
            ),
            "merge_count": max(0, len(frame_observations) - step_count),
        }
        logger.info(
            "[adaptive] video=%s pipeline=procedural_complex frames=%d steps=%d",
            video_id, len(frame_observations), step_count,
        )
        return sop
