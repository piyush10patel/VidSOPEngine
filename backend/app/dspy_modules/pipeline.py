"""DSPy pipeline modules for SOP generation.

To test a variation, change the strategy parameter:
  - "predict"  → dspy.Predict        (direct answer)
  - "cot"      → dspy.ChainOfThought (reasons step-by-step before answering)

Example:
    pipeline = SOPGenerationPipeline(strategy="cot")
    result = pipeline(transcript=t, events=events)
"""
import json
import logging
from typing import Literal

import dspy

from app.dspy_modules.signatures import (
    ActionTimelineSynthesis,
    AtomicActionExtraction,
    AtomicSOPSynthesis,
    ChecklistExtraction,
    DetailedSOPSynthesis,
    FrameEventExtraction,
    FrameWindowTransition,
    ObjectStateChanges,
    SOPSynthesis,
    SOPVerification,
    TextOnlySOPSynthesis,
    TimelineToSOP,
    TrainingModuleGeneration,
    UIEventExtraction,
    UISOPSynthesis,
    VideoComplexityClassification,
    WorkflowExtraction,
)

logger = logging.getLogger(__name__)

Strategy = Literal["predict", "cot"]


def _make_predictor(signature, strategy: Strategy):
    if strategy == "cot":
        return dspy.ChainOfThought(signature)
    return dspy.Predict(signature)


def _parse_float(value: str, default: float = 0.8) -> float:
    try:
        return max(0.0, min(1.0, float(str(value).strip())))
    except (ValueError, TypeError):
        return default


def _strip_code_fence(text: str) -> str:
    """Remove a single surrounding markdown fence if present.

    Models occasionally wrap structured output in ```json ... ``` even when the
    signature asks for raw JSON. Strip one optional fence so downstream
    json.loads has a fighting chance.
    """
    t = text.strip()
    if not t.startswith("```"):
        return t
    # Drop the opening fence (optional language hint) and the trailing fence.
    first_newline = t.find("\n")
    if first_newline == -1:
        return t
    body = t[first_newline + 1 :]
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body.strip()


def _parse_json_list(value: str, default=None) -> list:
    """Parse an LLM output expected to be a JSON array.

    Tolerates: surrounding markdown fences, leading prose ("Here is the
    JSON:"), trailing commentary. Falls back to extracting between the first
    ``[`` and last ``]`` so a chatty model still produces a usable list.
    """
    if default is None:
        default = []
    raw = str(value or "")
    text = _strip_code_fence(raw)
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            # Some models return {"checklists": [...]} or {"workflows": [...]}.
            # Pick the first list-valued field rather than dropping everything.
            for inner in parsed.values():
                if isinstance(inner, list):
                    return inner
    except (json.JSONDecodeError, TypeError):
        pass
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    logger.warning(
        "[dspy] failed to parse JSON list from LLM output (len=%d, preview=%r)",
        len(raw),
        raw[:200],
    )
    return default


def _extract_json_object(value: str) -> dict:
    """Parse an LLM output expected to be a JSON object.

    Same tolerance as :func:`_parse_json_list`: optional markdown fence,
    leading prose, trailing commentary. Returns ``{}`` when nothing parses so
    schema validation surfaces the missing-fields error rather than a
    json.JSONDecodeError.
    """
    raw = str(value or "")
    text = _strip_code_fence(raw)
    stripped = text.strip()
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    logger.warning(
        "[dspy] failed to parse JSON object from LLM output (len=%d, preview=%r)",
        len(raw),
        raw[:200],
    )
    return {}


def _normalise_sop_steps(items: list) -> list:
    steps = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        steps.append({
            "step_number": item.get("step_number", len(steps) + 1),
            "title": item.get("title", ""),
            "instruction": item.get("instruction", item.get("description", "")),
            "objects": item.get("objects", item.get("tools", [])),
            "checks": item.get("checks", []),
            "evidence": item.get("evidence", []),
            "confidence": item.get("confidence", 0.8),
            "notes": item.get("notes"),
        })
    return steps


def _visual_context(frame_observations: list[dict]) -> str:
    blocks = []
    for obs in frame_observations or []:
        frame = obs.get("frame_num", "?")
        image = obs.get("image_url", "")
        desc = obs.get("description", "")
        blocks.append(f"=== FRAME {frame} ===\nimage_url: {image}\n{desc}")
    return "\n\n".join(blocks)


class FrameEventPipeline(dspy.Module):
    """Converts a raw frame text observation into a structured event dict.

    The vision model already ran (multimodal); this module structures its output.
    Swap strategy to test different reasoning approaches on the same observation.
    """

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.extract = _make_predictor(FrameEventExtraction, strategy)

    def forward(self, frame_num: int, total_frames: int, observation: str) -> dict:
        context = f"Frame {frame_num}/{total_frames}. Observation: {observation}"
        result = self.extract(frame_context=context)

        objects = [o.strip() for o in result.objects.split(",") if o.strip()]

        return {
            "frame_num": frame_num,
            "action": result.action,
            "objects": objects,
            "stage": result.stage,
            "confidence": _parse_float(result.confidence),
        }


class SOPGenerationPipeline(dspy.Module):
    """Synthesises a complete SOP from structured frame events and transcript.

    Uses SOPSynthesis signature when events are available, falls back to
    TextOnlySOPSynthesis when there are no frame events.

    If `use_few_shot=True`, retrieves 2 most-similar past corrected SOPs from
    the failure dataset (LlamaIndex BM25) and prepends them to the transcript
    as reference examples — pure RAG over our own corrections.
    """

    def __init__(self, strategy: Strategy = "predict", use_few_shot: bool = True):
        super().__init__()
        self.generate_with_events = _make_predictor(SOPSynthesis, strategy)
        self.generate_text_only = _make_predictor(TextOnlySOPSynthesis, strategy)
        self.use_few_shot = use_few_shot

    def _augment_with_examples(self, transcript: str, user_id: str | None = None) -> str:
        if not self.use_few_shot:
            return transcript
        try:
            from app.dspy_modules.retrieval import (
                retrieve_similar_examples,
                format_examples_for_prompt,
            )
            examples = retrieve_similar_examples(transcript, top_k=2, user_id=user_id)
            block = format_examples_for_prompt(examples)
            if block:
                return f"{block}\n\n--- CURRENT VIDEO TO DOCUMENT ---\n{transcript}"
        except Exception as e:
            logger.warning(f"Few-shot retrieval failed: {e}")
        return transcript

    def forward(
        self,
        transcript: str,
        events: list | None = None,
        user_id: str | None = None,
    ) -> dict:
        augmented = self._augment_with_examples(transcript, user_id=user_id)

        if events:
            events_json = json.dumps(events, indent=2)
            result = self.generate_with_events(
                transcript=augmented,
                events=events_json,
            )
        else:
            result = self.generate_text_only(transcript=augmented)

        steps = _parse_json_list(result.steps_json)
        warnings = _parse_json_list(result.warnings_json)
        overall_confidence = _parse_float(result.overall_confidence)

        return {
            "title": result.title,
            "summary": result.summary,
            "steps": steps,
            "overall_confidence": overall_confidence,
            "warnings": warnings,
        }


class DetailedSOPPipeline(dspy.Module):
    """Transcript-primary, raw-vision SOP generation."""

    def __init__(self, strategy: Strategy = "predict", use_few_shot: bool = True):
        super().__init__()
        self.generate = _make_predictor(DetailedSOPSynthesis, strategy)
        self.use_few_shot = use_few_shot

    def _augment_with_examples(self, transcript: str, user_id: str | None = None) -> str:
        if not self.use_few_shot:
            return transcript
        try:
            from app.dspy_modules.retrieval import (
                retrieve_similar_examples,
                format_examples_for_prompt,
            )
            examples = retrieve_similar_examples(transcript, top_k=3, user_id=user_id)
            block = format_examples_for_prompt(examples)
            if block:
                return f"{block}\n\n--- CURRENT VIDEO TO DOCUMENT ---\n{transcript}"
        except Exception as e:
            logger.warning(f"Detailed few-shot retrieval failed: {e}")
        return transcript

    def forward(
        self,
        transcript: str,
        frame_observations: list[dict],
        user_id: str | None = None,
    ) -> dict:
        result = self.generate(
            transcript=self._augment_with_examples(transcript, user_id=user_id),
            visual_context=_visual_context(frame_observations),
        )
        data = _extract_json_object(result.sop_json)
        steps = _normalise_sop_steps(data.get("sop") or data.get("steps") or [])
        return {
            "title": data.get("title", "Untitled SOP"),
            "summary": data.get("summary", data.get("description", "")),
            "steps": steps,
            "overall_confidence": _parse_float(data.get("overall_confidence"), 0.8),
            "warnings": data.get("warnings", data.get("notes", [])) or [],
            "frame_observations": frame_observations,
        }


class FullSOPPipeline(dspy.Module):
    """End-to-end: frame observations → structured events → SOP.

    The multimodal vision call (frame image → text) is handled upstream
    and passed in as `frame_observations`. This module:
      1. Structures each observation into a typed event (FrameEventPipeline)
      2. Synthesises the final SOP (SOPGenerationPipeline)

    Pass the same strategy to both sub-modules for consistent behaviour,
    or mix strategies to target specific bottlenecks.
    """

    def __init__(
        self,
        event_strategy: Strategy = "predict",
        sop_strategy: Strategy = "predict",
        use_few_shot: bool = True,
    ):
        super().__init__()
        self.event_pipeline = FrameEventPipeline(strategy=event_strategy)
        self.sop_pipeline = SOPGenerationPipeline(strategy=sop_strategy, use_few_shot=use_few_shot)

    def forward(
        self,
        transcript: str,
        frame_observations: list[dict],  # [{"frame_num": int, "description": str, "image_url": str}]
        user_id: str | None = None,
    ) -> dict:
        events = []
        for obs in frame_observations:
            try:
                event = self.event_pipeline(
                    frame_num=obs["frame_num"],
                    total_frames=len(frame_observations),
                    observation=obs["description"],
                )
                event["image_url"] = obs.get("image_url", "")
                events.append(event)
            except Exception as e:
                logger.warning(f"FrameEventPipeline failed for frame {obs['frame_num']}: {e}")

        if not events and frame_observations:
            events = [
                {
                    "frame_num": obs.get("frame_num"),
                    "action": obs.get("description", ""),
                    "objects": [],
                    "stage": "unknown",
                    "confidence": 0.55,
                    "image_url": obs.get("image_url", ""),
                }
                for obs in frame_observations
                if (obs.get("description") or "").strip()
            ]

        sop = self.sop_pipeline(transcript=transcript, events=events, user_id=user_id)
        sop["frame_observations"] = frame_observations  # preserve for image assignment
        return sop


# Public alias — clarifies which generator handles physical videos.
PhysicalSOPGenerator = FullSOPPipeline


class VideoComplexityClassifier(dspy.Module):
    """Single LLM call: returns {pipeline_type, confidence, reason}."""

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.classify = _make_predictor(VideoComplexityClassification, strategy)

    def forward(self, transcript: str, frame_sample: str) -> dict:
        result = self.classify(transcript=transcript, frame_sample=frame_sample)
        ptype = (result.pipeline_type or "").strip().lower()
        if ptype not in ("atomic_simple", "procedural_complex"):
            ptype = "procedural_complex"  # default to existing strong pipeline
        return {
            "pipeline_type": ptype,
            "confidence": _parse_float(result.confidence, default=0.5),
            "reason": (result.reason or "").strip(),
        }


class AtomicActionPipeline(dspy.Module):
    """Convert raw frame text → structured atomic action event.

    Bias: preserve every transition. Do NOT summarise.
    """

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.extract = _make_predictor(AtomicActionExtraction, strategy)

    def forward(
        self,
        frame_num: int,
        total_frames: int,
        prior_action: str,
        observation: str,
    ) -> dict:
        result = self.extract(
            frame_num=frame_num,
            total_frames=total_frames,
            prior_action=prior_action,
            frame_observation=observation,
        )
        is_transition = (result.is_transition or "").strip().lower().startswith("y")
        return {
            "frame_num": frame_num,
            "primary_action": (result.primary_action or "").strip(),
            "state_changes": [
                s.strip() for s in (result.state_changes or "").split(",") if s.strip()
            ],
            "is_transition": is_transition,
        }


class AtomicSOPGenerator(dspy.Module):
    """End-to-end synthesis from atomic events → fine-grained SOP.

    Used by the AtomicSimplePipeline. Lower merge threshold than the
    procedural pipeline — preserves micro-actions deliberately.
    """

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.synthesize = _make_predictor(AtomicSOPSynthesis, strategy)

    def forward(self, events: list) -> dict:
        if not events:
            return {
                "title": "",
                "summary": "",
                "steps": [],
                "overall_confidence": 0.0,
                "warnings": ["No atomic events extracted"],
            }
        result = self.synthesize(events=json.dumps(events, indent=2))
        return {
            "title": result.title,
            "summary": result.summary,
            "steps": _parse_json_list(result.steps_json),
            "overall_confidence": _parse_float(result.overall_confidence),
            "warnings": _parse_json_list(result.warnings_json),
        }


class FrameWindowPipeline(dspy.Module):
    """3-frame sliding-window transition analysis (atomic_simple only).

    Reads prior + current + next frame text descriptions and emits the
    transition between them. Used as the first stage of the action-timeline
    architecture, replacing isolated single-frame analysis for atomic tasks.
    """

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.analyse = _make_predictor(FrameWindowTransition, strategy)

    def forward(
        self,
        frame_num: int,
        total_frames: int,
        prior_frame: str,
        current_frame: str,
        next_frame: str,
    ) -> dict:
        result = self.analyse(
            frame_num=frame_num,
            total_frames=total_frames,
            prior_frame=prior_frame or "",
            current_frame=current_frame or "",
            next_frame=next_frame or "",
        )
        objects = [o.strip() for o in (result.objects or "").split(",") if o.strip()]
        motion = (getattr(result, "motion", "") or "").strip().lower()
        contact = (getattr(result, "contact_event", "") or "").strip().lower()
        state_change = (getattr(result, "state_change", "") or "").strip()
        actor = (getattr(result, "actor", "") or "").strip()
        # Treat "none"/"" sentinels uniformly as None so downstream code can
        # filter on truthiness.
        return {
            "frame_num": frame_num,
            "transition": (result.transition or "").strip(),
            "primary_action": (result.primary_action or "").strip(),
            "objects": objects,
            "actor": actor or None,
            "motion": motion if motion and motion != "none" else None,
            "contact_event": contact if contact and contact != "none" else None,
            "state_change": state_change or None,
            "confidence": _parse_float(result.confidence, default=0.5),
        }


class ActionTimelineBuilder(dspy.Module):
    """Build a temporally-ordered action timeline from window observations."""

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.build = _make_predictor(ActionTimelineSynthesis, strategy)

    def forward(self, window_observations: list, total_frames: int) -> list:
        if not window_observations:
            return []
        result = self.build(
            window_observations=json.dumps(window_observations, indent=2),
            total_frames=total_frames,
        )
        return _parse_json_list(result.timeline_json)


class ObjectStateTracker(dspy.Module):
    """Extract explicit object state-change records from a timeline."""

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.extract = _make_predictor(ObjectStateChanges, strategy)

    def forward(self, timeline: list) -> list:
        if not timeline:
            return []
        result = self.extract(timeline_json=json.dumps(timeline, indent=2))
        return _parse_json_list(result.state_changes_json)


class TimelineFormatter(dspy.Module):
    """Format a finalised timeline into an SOP. Pure formatter — does NOT invent."""

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.format = _make_predictor(TimelineToSOP, strategy)

    def forward(self, timeline: list, state_changes: list) -> dict:
        if not timeline:
            return {
                "title": "",
                "summary": "",
                "steps": [],
                "overall_confidence": 0.0,
                "warnings": ["No timeline entries to format"],
            }
        result = self.format(
            timeline_json=json.dumps(timeline, indent=2),
            state_changes_json=json.dumps(state_changes or [], indent=2),
        )
        return {
            "title": (result.title or "").strip(),
            "summary": (result.summary or "").strip(),
            "steps": _parse_json_list(result.steps_json),
            "overall_confidence": _parse_float(result.overall_confidence),
            "warnings": _parse_json_list(result.warnings_json),
        }


class WorkflowExtractor(dspy.Module):
    """Convert a finalized/draft SOP → structured workflows.

    Stand-alone (does not chain into other pipelines): callers pass an SOP dict,
    receive a list of workflow dicts ready for the API.
    """

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.extract = _make_predictor(WorkflowExtraction, strategy)

    def forward(self, sop: dict, target_language: str = "English") -> list:
        result = self.extract(
            sop_json=json.dumps(sop, indent=2),
            target_language=target_language,
        )
        return _parse_json_list(result.workflows_json)


class ChecklistExtractor(dspy.Module):
    """Convert an SOP → operationally-usable verification checklists, grouped
    by context (opening / execution / closing)."""

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.extract = _make_predictor(ChecklistExtraction, strategy)

    def forward(self, sop: dict, target_language: str = "English") -> list:
        result = self.extract(
            sop_json=json.dumps(sop, indent=2),
            target_language=target_language,
        )
        return _parse_json_list(result.checklists_json)


class TrainingModuleGenerator(dspy.Module):
    """Convert (SOP + workflows + checklists) → learner-facing training module.

    Token-efficient: takes only the structured artifacts — no transcript or
    frame data. The signature enforces the strict output schema; this module
    parses the raw JSON string into a dict.
    """

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.generate = _make_predictor(TrainingModuleGeneration, strategy)

    def forward(
        self,
        sop: dict,
        workflows: list,
        checklists: list,
        target_language: str = "English",
    ) -> dict:
        result = self.generate(
            sop_json=json.dumps(sop, indent=2),
            workflows_json=json.dumps(workflows, indent=2),
            checklists_json=json.dumps(checklists),
            target_language=target_language,
        )
        # Hindi-mode runs occasionally wrap the payload in ```json ... ``` or
        # add a leading "Here is the module:" sentence. _extract_json_object
        # tolerates both and falls back to the outermost { ... } slice.
        return _extract_json_object(getattr(result, "training_module_json", ""))


class UIEventPipeline(dspy.Module):
    """Convert a raw UI frame description → structured interaction event.

    Hard-grounded by exact UI labels (no paraphrasing). Wraps UIEventExtraction.
    """

    def __init__(self, strategy: Strategy = "predict"):
        super().__init__()
        self.extract = _make_predictor(UIEventExtraction, strategy)

    def forward(self, frame_num: int, total_frames: int, observation: str) -> dict:
        result = self.extract(
            frame_num=frame_num,
            total_frames=total_frames,
            frame_observation=observation,
        )
        labels = [label.strip() for label in result.visible_labels.split(",") if label.strip()]
        return {
            "frame_num": frame_num,
            "screen": result.screen,
            "action_type": result.action_type,
            "target_label": result.target_label,
            "visible_labels": labels,
            "typed_text": result.typed_text,
            "confidence": _parse_float(result.confidence),
        }


class UISOPGenerator(dspy.Module):
    """End-to-end pipeline for UI screen recording videos.

    UI workflows are deterministic: every step references an exact label that
    appeared on screen. This pipeline keeps that grounding explicit:
      1. UIEventPipeline — structures each frame description into a typed event
      2. UISOPSynthesis  — synthesises the SOP from the event sequence

    Does NOT take a transcript — UI tutorials may have voice-over but the
    ground truth for interactions is on the screen, not in the audio.
    """

    def __init__(
        self,
        event_strategy: Strategy = "predict",
        sop_strategy: Strategy = "predict",
    ):
        super().__init__()
        self.event_pipeline = UIEventPipeline(strategy=event_strategy)
        self.synthesizer = _make_predictor(UISOPSynthesis, sop_strategy)

    def forward(self, transcript: str = "", frame_observations: list | None = None) -> dict:
        # `transcript` accepted for SelfCheckPipeline compatibility but ignored
        observations = frame_observations or []
        events = []
        for obs in observations:
            try:
                event = self.event_pipeline(
                    frame_num=obs["frame_num"],
                    total_frames=len(observations),
                    observation=obs["description"],
                )
                event["image_url"] = obs.get("image_url", "")
                events.append(event)
            except Exception as e:
                logger.warning(f"UIEventPipeline failed for frame {obs['frame_num']}: {e}")

        if not events:
            return {
                "title": "",
                "summary": "",
                "steps": [],
                "overall_confidence": 0.0,
                "warnings": ["No UI events could be extracted"],
                "frame_observations": observations,
            }

        result = self.synthesizer(events=json.dumps(events, indent=2))
        return {
            "title": result.title,
            "summary": result.summary,
            "steps": _parse_json_list(result.steps_json),
            "overall_confidence": _parse_float(result.overall_confidence),
            "warnings": _parse_json_list(result.warnings_json),
            "frame_observations": observations,
        }


class SelfCheckPipeline(dspy.Module):
    """Wraps a base SOP pipeline with a verification pass + threshold enforcement.

    Two-pass system:
      1. Generate SOP (delegated to base pipeline)
      2. Verify each step is supported by the source
         - unsupported steps get verified=False, confidence halved, added to warnings
         - if any step is unverified OR overall_confidence < threshold → needs_review=True

    Pass `confidence_threshold` to control rejection sensitivity.
    """

    def __init__(
        self,
        base: dspy.Module,
        confidence_threshold: float = 0.5,
        verify_strategy: Strategy = "predict",
        advisory: bool = False,
    ):
        super().__init__()
        self.base = base
        self.threshold = confidence_threshold
        self.advisory = advisory
        self.verify = _make_predictor(SOPVerification, verify_strategy)

    def _run_verification(self, transcript: str, events: list, steps: list) -> dict:
        """Returns {step_number: {"supported": bool, "quote": str}}."""
        if not steps:
            return {}
        steps_payload = [
            {
                "step_number": s.get("step_number"),
                "title": s.get("title", ""),
                "description": s.get("instruction", s.get("description", "")),
            }
            for s in steps
        ]
        try:
            from app.services.llm.dspy_config import configure_dspy
            configure_dspy(task="verification")
            result = self.verify(
                transcript=transcript,
                events=json.dumps(events) if events else "[]",
                steps_to_verify=json.dumps(steps_payload),
            )
            verifications = _parse_json_list(result.verifications_json)
        except Exception as e:
            logger.warning(f"Verification call failed: {e}")
            return {}
        return {v.get("step_number"): v for v in verifications if isinstance(v, dict)}

    def forward(self, transcript: str, **kwargs) -> dict:
        sop = self.base(transcript=transcript, **kwargs)
        steps = sop.get("steps", [])
        events = kwargs.get("events") or sop.get("events") or []
        if not events and "frame_observations" in sop:
            events = [
                {"frame_num": o.get("frame_num"), "action": o.get("description", "")}
                for o in sop["frame_observations"]
            ]

        verifications = self._run_verification(transcript, events, steps)

        unverified_count = 0
        warnings = list(sop.get("warnings", []))
        for s in steps:
            sn = s.get("step_number")
            v = verifications.get(sn)
            if v is None:
                # No verdict — leave verified unset, treat as below threshold by lowering confidence
                s["verified"] = None
                s["correctness_score"] = None
                s["correctness_label"] = "not_evaluated"
                s["correctness_reason"] = "Verification model did not return a verdict for this step."
                s["correctness_issue_type"] = "missing_evidence"
                continue
            label = str(v.get("correctness_label") or "").strip().lower()
            supported = bool(v.get("supported")) or label in {"supported", "partially_supported"}
            score = _parse_float(
                v.get("correctness_score"),
                default=1.0 if supported else 0.0,
            )
            score = max(0.0, min(1.0, score))
            s["verified"] = supported
            s["verification_quote"] = v.get("quote", "")
            s["correctness_score"] = score
            s["correctness_label"] = label or ("supported" if supported else "unsupported")
            s["correctness_reason"] = v.get("reason", "")
            s["correctness_issue_type"] = v.get(
                "issue_type",
                "none" if supported else "missing_evidence",
            )
            if not supported or score < self.threshold:
                unverified_count += 1
                if not self.advisory:
                    s["confidence"] = max(0.1, _parse_float(s.get("confidence"), 0.5) * max(0.3, score))
                warnings.append(
                    f"Step {sn} ({s.get('title', '')}) not supported by source — confidence reduced"
                )

        # Recompute overall confidence as average of per-step (after verification penalty)
        if steps:
            avg = sum(_parse_float(s.get("confidence"), 0.5) for s in steps) / len(steps)
            sop["overall_confidence"] = avg
        else:
            sop["overall_confidence"] = 0.0

        below_threshold = sop["overall_confidence"] < self.threshold
        sop["needs_review"] = bool(below_threshold if self.advisory else (unverified_count or below_threshold))
        sop["warnings"] = warnings
        return sop
