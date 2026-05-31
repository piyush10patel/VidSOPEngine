"""UI screen recording pipeline (label-grounded, deterministic).

Frames → vision-model UI inspection → UISOPGenerator (DSPy) → SOP.
No transcript needed: UI workflows are grounded by what's on screen.

Vision prompt is UI-specific: extracts exact button/menu/label text,
identifies interactions (click/type/scroll/navigate), and refuses to
guess unreadable labels.
"""
import json
import logging
import os

from app.core.config import settings
from app.dspy_modules.pipeline import SelfCheckPipeline, UISOPGenerator
from app.observability.langsmith_client import configure as configure_langsmith, traceable
from app.schemas.sop import SOPSchema
from app.services.frame_extractor import FrameExtractor
from app.services.sop_pipelines.base import (
    assign_frame_images_linear,
    parse_sop_response,
)

logger = logging.getLogger(__name__)


# Number of frames for UI videos — denser than physical because UI changes
# can be subtle (button highlight, menu open, modal dismissed).
UI_FRAME_COUNT = 12


class UIPipeline:
    """Generates SOPs from UI screen recording videos.

    Inputs:  video file path, video_id (no transcript)
    Outputs: SOPSchema with video_type='ui', steps that quote exact UI labels
    """

    def __init__(self, model_name: str):
        self.model_name = model_name

    def _configure_dspy(self) -> None:
        from app.services.llm.dspy_config import configure_dspy
        self.model_name = configure_dspy(task="sop", model_name=self.model_name)

    def generate_sync(self, video_path: str, video_id: str) -> SOPSchema:
        configure_langsmith()

        @traceable(name="generate_sop_ui", run_type="chain", metadata={"video_id": video_id})
        def _generate():
            return self._generate_inner(video_path, video_id)

        return _generate()

    def _generate_inner(self, video_path: str, video_id: str) -> SOPSchema:
        from app.services.llm import get_provider

        provider = get_provider()
        vision_model = settings.vision_model

        extractor = FrameExtractor()
        frames = extractor.extract_frames(video_path, video_id, num_frames=UI_FRAME_COUNT)
        if not frames:
            raise ValueError("No frames extracted")

        @traceable(name="analyze_frame_ui", run_type="llm")
        def _analyze_ui_frame(frame_num: int, total: int, frame_b64: str) -> str:
            # UI-specific: prioritise exact label extraction over hand position etc.
            prompt = (
                f"You are analyzing frame {frame_num} of {total} from a software screen recording.\n\n"
                "Extract ONLY what you can read directly from the screen. Use VERBATIM text — "
                "do NOT paraphrase button or menu labels.\n\n"
                "1. SCREEN: What is the title of the current screen, page, or window? "
                "Use exact text from the title bar, breadcrumb, or main heading. "
                "Say 'unclear' if you cannot read it.\n\n"
                "2. INTERACTIVE ELEMENTS: List EVERY clickable element visible (buttons, "
                "tabs, menu items, links, form fields). Use the EXACT text shown.\n\n"
                "3. ACTIVE INTERACTION: Is there a visible cursor, hover state, focus ring, "
                "open menu, or modal? If yes, which exact label is being interacted with?\n\n"
                "4. ACTION TYPE: Based on the visible state, choose ONE: "
                "click | type | scroll | navigate | view | unclear\n\n"
                "5. TEXT BEING TYPED: If the action is 'type', what text is in the active "
                "input field? Empty string otherwise.\n\n"
                "Be strict: if you can't read a label clearly, say 'unclear' for that "
                "field. Do NOT guess."
            )
            resp = provider.vision(prompt, frame_b64, model=vision_model, timeout=30)
            return resp.text

        frame_observations = []
        for i, frame_path in enumerate(frames):
            try:
                description = _analyze_ui_frame(
                    frame_num=i + 1,
                    total=len(frames),
                    frame_b64=extractor.frame_to_base64(frame_path),
                )
                frame_observations.append({
                    "frame_num": i + 1,
                    "description": description,
                    "image_url": f"/videos/{video_id}/frames/{os.path.basename(frame_path)}",
                })
            except Exception as e:
                logger.warning(f"UI frame {i+1} vision call failed: {e}")

        if not frame_observations:
            raise ValueError("No UI frames could be analyzed")

        @traceable(name="synthesize_sop_ui", run_type="chain", metadata={"n_events": len(frame_observations)})
        def _synthesize():
            self._configure_dspy()
            base = UISOPGenerator(event_strategy="predict", sop_strategy="predict")
            if settings.sop_self_check_enabled:
                # Self-check pass verifies each step against the event stream.
                # For UI: the "transcript" arg is the events JSON since UI has no audio source.
                events_text = "\n".join(
                    f"Frame {fo['frame_num']}: {fo['description']}"
                    for fo in frame_observations
                )
                pipeline = SelfCheckPipeline(
                    base=base,
                    confidence_threshold=settings.sop_confidence_threshold,
                )
                return pipeline(transcript=events_text, frame_observations=frame_observations)
            return base(transcript="", frame_observations=frame_observations)

        result = _synthesize()

        raw = json.dumps({
            "title": result["title"],
            "summary": result["summary"],
            "sop": result["steps"],
            "overall_confidence": result["overall_confidence"],
            "warnings": result["warnings"],
            "needs_review": result.get("needs_review", False),
        })
        sop = parse_sop_response(raw, video_type="ui")
        assign_frame_images_linear(sop.steps, frame_observations)
        sop.generation_metadata = {
            "pipeline": "ui",
            "synthesis_model": self.model_name,
            "vision_model": vision_model,
            "verification_model": settings.sop_verification_model if settings.sop_self_check_enabled else None,
            "self_check_enabled": settings.sop_self_check_enabled,
            "frame_count": len(frame_observations),
        }

        # No hallucination diagnosis for UI: ungrounded labels are caught by self-check
        # via the verification quote (must match a label that appeared in the events).
        sop._diagnoses = []  # type: ignore[attr-defined]
        sop._frame_observations = frame_observations  # type: ignore[attr-defined]
        sop._transcript = ""  # type: ignore[attr-defined]
        return sop
