"""Atomic-simple physical pipeline — fine-grained action decomposition.

For short, single-actor, low-complexity tasks (filling a bottle, opening a
package, folding cloth). The procedural pipeline over-abstracts these; this
one preserves every visible state transition as its own step.

ACTION TIMELINE ARCHITECTURE
----------------------------
The pipeline does NOT synthesise an SOP directly from summarised events.
Instead it builds a temporally-ordered action timeline first, then formats
the timeline into the final SOP. Stages:

  frames
    → per-frame literal vision descriptions
    → 3-frame sliding window transition analysis (FrameWindowPipeline)
    → action timeline build (ActionTimelineBuilder)
    → object state tracking (ObjectStateTracker)
    → verb-first normalisation
    → confidence-aware micro-action expansion
    → SOP formatting (TimelineFormatter — formatter, NOT inventor)

The procedural pipeline (PhysicalPipeline) is unaffected.

Vision model routes through the shared LLM provider. By default,
settings.atomic_action_model == "qwen" uses settings.vision_model, which
is configured for OpenRouter-hosted Qwen VL.
"""
import gc
import json
import logging
import os
import re
from typing import List, Optional

from app.core.config import settings
from app.dspy_modules.pipeline import (
    ActionTimelineBuilder,
    AtomicActionPipeline,
    AtomicSOPGenerator,
    FrameWindowPipeline,
    ObjectStateTracker,
    SelfCheckPipeline,
    TimelineFormatter,
)
from app.observability.langsmith_client import (
    configure as configure_langsmith,
    traceable,
)
from app.schemas.sop import SOPSchema
from app.services.frame_extractor import FrameExtractor
from app.services.sop_pipelines.base import (
    assign_frame_images_linear,
    parse_sop_response,
)

logger = logging.getLogger(__name__)


_ATOMIC_FRAME_PROMPT = (
    "You are looking at frame {frame_num} of {total} from a SHORT, SIMPLE task.\n\n"
    "Describe exact visible OBJECT INTERACTIONS. Focus on:\n"
    "  - which hand (left / right) and which finger(s) are visible\n"
    "  - which object the hand is currently in contact with\n"
    "  - the manipulation: rotating / pressing / lifting / lowering / sliding /\n"
    "    pulling / pushing / pouring / wiping / inserting / extracting\n"
    "  - motion direction when visible (clockwise, counterclockwise, upward,\n"
    "    downward, leftward, rightward, forward, backward)\n"
    "  - object orientation (upright / inverted / tilted, open / closed, on / off)\n"
    "  - any visible state change in this frame (cap loosening, container filling,\n"
    "    drawer opening, switch flipping)\n\n"
    "HARD RULES:\n"
    "  - Do NOT summarise intent. Do NOT say 'prepares to', 'attempts to',\n"
    "    'appears to', or 'gets ready to' — describe ONLY what is visible.\n"
    "  - Do NOT describe the whole scene generally — focus on the hand-object\n"
    "    interaction.\n"
    "  - If multiple objects are involved, list the primary manipulated object\n"
    "    first and the secondary contact object second.\n"
    "  - Be terse: 2–3 short sentences.\n"
)


class AtomicSimplePipeline:
    """Fine-grained pipeline for short single-actor tasks."""

    def __init__(self, model_name: str):
        self.model_name = model_name

    def _configure_dspy(self) -> None:
        from app.services.llm.dspy_config import configure_dspy
        self.model_name = configure_dspy(task="sop", model_name=self.model_name)

    def _get_vision_call(self):
        """Return a (callable, label) for vision frame analysis.

        Callable shape: ``(prompt: str, image_b64: str) -> str``.

        Routes through ``app.services.llm`` using settings.vision_model.
        The provider abstraction owns timeout, retry, cache, and routing.
        ``atomic_action_model='qwen'`` uses settings.vision_model through
        the model-aware provider router. Missing Together configuration
        fails visibly instead of silently using Groq. The LLMProvider
        interface owns provider selection.
        """
        from app.services.llm import get_provider

        choice = (settings.atomic_action_model or "qwen").lower()
        provider = get_provider()
        if choice in {"qwen", "together"}:
            vision_model = settings.vision_model

            def _together_vision(prompt: str, image_b64: str) -> str:
                resp = provider.vision(
                    prompt,
                    image_b64,
                    model=vision_model,
                    timeout=30,
                )
                return resp.text

            return _together_vision, vision_model

        def _groq(prompt: str, image_b64: str) -> str:
            resp = provider.vision(
                prompt, image_b64,
                model=settings.groq_vision_model, timeout=30,
            )
            return resp.text

        return _groq, settings.groq_vision_model

    def generate_sync(self, transcript: str, video_path: str, video_id: str) -> SOPSchema:
        configure_langsmith()

        @traceable(
            name="generate_sop_atomic_simple",
            run_type="chain",
            metadata={"video_id": video_id, "pipeline": "atomic_simple"},
        )
        def _generate():
            return self._generate_inner(transcript, video_path, video_id)

        return _generate()

    def _generate_inner(self, transcript: str, video_path: str, video_id: str) -> SOPSchema:
        extractor = FrameExtractor()
        n_frames = max(8, min(24, settings.atomic_simple_dense_frames or 16))
        if settings.atomic_use_adaptive_fps:
            # Continuous low-FPS extraction captures cap removals, quick
            # presses, and rapid hand transitions that sparse keyframes miss.
            frames = extractor.extract_adaptive_fps_frames(
                video_path, video_id,
                fps=settings.atomic_adaptive_fps,
                max_frames=settings.atomic_adaptive_max_frames,
            )
            if not frames:
                # Fall back to transition-aware dense if ffmpeg returned nothing.
                frames = extract_transition_frames(extractor, video_path, video_id, num_frames=n_frames)
        elif settings.atomic_use_transition_sampling:
            frames = extract_transition_frames(extractor, video_path, video_id, num_frames=n_frames)
        else:
            frames = extractor.extract_dense_frames(video_path, video_id, num_frames=n_frames)
        if not frames:
            raise ValueError("No frames extracted")

        vision_call, vision_label = self._get_vision_call()

        @traceable(
            name="analyze_frame_atomic",
            run_type="llm",
            metadata={"vision_model": vision_label},
        )
        def _analyze_frame(frame_num: int, total: int, frame_b64: str) -> str:
            prompt = _ATOMIC_FRAME_PROMPT.format(frame_num=frame_num, total=total)
            return vision_call(prompt, frame_b64)

        # Stage 1 — per-frame visual descriptions
        frame_observations: List[dict] = []
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
                    "image_url": f"/videos/{video_id}/frames/{os.path.basename(frame_path)}",
                })
            except Exception as e:
                logger.warning(f"[atomic] Frame {i+1} vision call failed: {e}")

        if not frame_observations:
            raise ValueError("No frames could be analysed")

        # Master switch — when off, fall back to the legacy event-based path.
        if not settings.atomic_use_timeline_pipeline:
            return self._legacy_event_pipeline(
                transcript=transcript,
                frame_observations=frame_observations,
                vision_label=vision_label,
                video_id=video_id,
            )

        # Stage 2 — sliding 3-frame window transition analysis (DSPy)
        window_observations = self._run_window_analysis(frame_observations)
        gc.collect()  # release DSPy intermediate tensors before stage 2.5

        # Stage 2.5 — visual-grounding derivations from windows (deterministic)
        window_observations = preserve_micro_action_chain(window_observations)
        object_interactions = analyze_object_interactions(window_observations)
        contact_events = detect_contact_events(window_observations)
        motion_records = analyze_motion_direction(window_observations)
        # Operational tracking — per-object trajectories + scene graph.
        # Both are pure restructuring of the window observations; no extra
        # LLM cost. Exposed on the SOP for observability and downstream UX.
        object_trajectories = aggregate_object_trajectories(window_observations)
        scene_graph = extract_operational_scene_graph(window_observations)

        # Stage 3 — build temporally-ordered action timeline (DSPy)
        timeline = self._run_timeline_build(window_observations, len(frame_observations))
        gc.collect()  # release window-analysis buffers

        # Stage 4 — object state-change tracking (DSPy)
        state_changes = self._run_state_tracking(timeline)
        gc.collect()

        # Stage 5 — vocabulary enforcement (deterministic).
        # Strict mode rejects inferred-intent and generic verbs entirely;
        # otherwise we fall back to the lighter-touch normaliser.
        if settings.atomic_strict_vocabulary:
            timeline = enforce_atomic_action_vocabulary(timeline)
        else:
            timeline = normalize_atomic_actions(timeline)

        # Stage 5.5 — action boundary segmentation (deterministic)
        action_boundaries = segment_action_boundaries(timeline)

        # Stage 6 — confidence-aware micro-action expansion
        if settings.preserve_micro_actions:
            timeline = self._expand_low_confidence(
                timeline,
                threshold=settings.atomic_confidence_expand_threshold,
                frame_observations=frame_observations,
            )

        # If the timeline is empty (DSPy parse failures / model regression),
        # fall back to the legacy event path so we don't break production.
        if not timeline:
            logger.warning("[atomic] timeline empty — falling back to legacy event path")
            return self._legacy_event_pipeline(
                transcript=transcript,
                frame_observations=frame_observations,
                vision_label=vision_label,
                video_id=video_id,
            )

        # Stage 7 — SOP formatting (formatter, NOT inventor)
        result = self._run_timeline_formatter(timeline, state_changes)

        raw = json.dumps({
            "title": result["title"],
            "summary": result["summary"],
            "sop": result["steps"],
            "overall_confidence": result["overall_confidence"],
            "warnings": result["warnings"],
            "needs_review": False,
        })
        sop = parse_sop_response(raw, video_type="physical")
        assign_frame_images_linear(sop.steps, frame_observations)

        # Observability — timeline-architecture metrics
        n_frames_obs = len(frame_observations)
        timeline_count = len(timeline)
        step_count = len(sop.steps)
        merge_count = max(0, timeline_count - step_count)
        confident_entries = sum(
            1 for t in timeline if (t.get("confidence") or 0) >= 0.5
        )
        # Visual-grounding derived metrics
        protected_count = sum(1 for w in window_observations if w.get("_protected"))
        motion_window_count = sum(1 for w in window_observations if w.get("motion"))

        sop._diagnoses = []                  # type: ignore[attr-defined]
        sop._frame_observations = frame_observations  # type: ignore[attr-defined]
        sop._transcript = transcript          # type: ignore[attr-defined]
        sop._action_timeline = timeline       # type: ignore[attr-defined]
        sop._state_changes = state_changes    # type: ignore[attr-defined]
        sop._object_interactions = object_interactions  # type: ignore[attr-defined]
        sop._contact_events = contact_events            # type: ignore[attr-defined]
        sop._motion_records = motion_records            # type: ignore[attr-defined]
        sop._action_boundaries = action_boundaries      # type: ignore[attr-defined]
        sop._object_trajectories = object_trajectories  # type: ignore[attr-defined]
        sop._scene_graph = scene_graph                  # type: ignore[attr-defined]
        metrics = {
            "pipeline": "atomic_simple",
            "architecture": "timeline",
            "vision_model": vision_label,
            "synthesis_model": self.model_name,
            "frame_count": n_frames_obs,
            "atomic_action_count": timeline_count,
            "step_count": step_count,
            "merge_count": merge_count,
            "event_merge_ratio": (merge_count / timeline_count) if timeline_count else 0.0,
            "object_state_changes": len(state_changes),
            # Timeline-arch metrics
            "micro_action_density": (timeline_count / n_frames_obs) if n_frames_obs else 0.0,
            "action_merge_ratio": (merge_count / timeline_count) if timeline_count else 0.0,
            "timeline_completeness": (
                confident_entries / timeline_count if timeline_count else 0.0
            ),
            "transition_detection_rate": (
                len(state_changes) / timeline_count if timeline_count else 0.0
            ),
            # Visual-grounding metrics
            "action_boundary_count": len(action_boundaries),
            "contact_event_count": len(contact_events),
            "object_interaction_count": len(object_interactions),
            "object_interaction_density": (
                len(object_interactions) / n_frames_obs if n_frames_obs else 0.0
            ),
            "motion_direction_detection_rate": (
                motion_window_count / len(window_observations)
                if window_observations else 0.0
            ),
            "micro_action_preservation_rate": (
                protected_count / len(window_observations)
                if window_observations else 0.0
            ),
            # Operational structure metrics
            "object_trajectory_count": len(object_trajectories),
            "scene_graph_record_count": len(scene_graph),
        }
        sop.generation_metadata = {
            "pipeline": "atomic_simple",
            "architecture": "timeline",
            "synthesis_model": self.model_name,
            "vision_model": vision_label,
            "verification_model": None,
            "self_check_enabled": False,
            "frame_count": n_frames_obs,
        }
        sop._adaptive_metrics = metrics  # type: ignore[attr-defined]
        logger.info(
            "[adaptive] video=%s pipeline=atomic_simple arch=timeline vision=%s "
            "frames=%d timeline=%d boundaries=%d contacts=%d interactions=%d "
            "states=%d steps=%d density=%.2f merge_ratio=%.2f motion_rate=%.2f "
            "preservation_rate=%.2f",
            video_id,
            vision_label,
            metrics["frame_count"],
            metrics["atomic_action_count"],
            metrics["action_boundary_count"],
            metrics["contact_event_count"],
            metrics["object_interaction_count"],
            metrics["object_state_changes"],
            metrics["step_count"],
            metrics["micro_action_density"],
            metrics["action_merge_ratio"],
            metrics["motion_direction_detection_rate"],
            metrics["micro_action_preservation_rate"],
        )
        return sop

    # ------------------------------------------------------------------
    # Timeline-architecture stage helpers (atomic_simple only)
    # ------------------------------------------------------------------

    def _run_window_analysis(self, frame_observations: List[dict]) -> List[dict]:
        """Stage 2 — 3-frame sliding window transition analysis."""

        @traceable(
            name="analyze_frame_windows",
            run_type="chain",
            metadata={"n_frames": len(frame_observations)},
        )
        def _run() -> List[dict]:
            self._configure_dspy()
            return analyze_frame_window(frame_observations, model_name=self.model_name)

        return _run()

    def _run_timeline_build(
        self, window_observations: List[dict], total_frames: int
    ) -> List[dict]:
        """Stage 3 — synthesise the action timeline from window observations."""

        @traceable(
            name="build_action_timeline",
            run_type="chain",
            metadata={"n_windows": len(window_observations)},
        )
        def _run() -> List[dict]:
            self._configure_dspy()
            return build_action_timeline(
                window_observations,
                total_frames=total_frames,
                model_name=self.model_name,
            )

        return _run()

    def _run_state_tracking(self, timeline: List[dict]) -> List[dict]:
        """Stage 4 — object state-change tracking."""

        @traceable(
            name="track_object_state",
            run_type="chain",
            metadata={"n_timeline": len(timeline)},
        )
        def _run() -> List[dict]:
            self._configure_dspy()
            return track_object_state_changes(timeline, model_name=self.model_name)

        return _run()

    def _run_timeline_formatter(
        self, timeline: List[dict], state_changes: List[dict]
    ) -> dict:
        """Stage 7 — pure formatter; does NOT invent steps."""

        @traceable(
            name="format_timeline_to_sop",
            run_type="chain",
            metadata={"n_timeline": len(timeline)},
        )
        def _run() -> dict:
            self._configure_dspy()
            formatter = TimelineFormatter(strategy="predict")
            return formatter(timeline=timeline, state_changes=state_changes)

        return _run()

    def _expand_low_confidence(
        self,
        timeline: List[dict],
        threshold: float,
        frame_observations: List[dict],
    ) -> List[dict]:
        """Stage 6 — when a timeline entry's confidence is low, request a finer split.

        Avoids globally re-prompting; only acts on entries below threshold.
        Falls back to keeping the entry as-is on any failure.
        """
        if threshold <= 0 or not timeline:
            return timeline

        # Map frame_num → raw observation for context lookup.
        obs_by_frame = {o["frame_num"]: o for o in frame_observations}

        expanded: List[dict] = []
        for entry in timeline:
            conf = float(entry.get("confidence") or 1.0)
            if conf >= threshold:
                expanded.append(entry)
                continue

            # Pull surrounding observations for context.
            f = entry.get("frame_num") or 0
            context_frames = [
                obs_by_frame[fn].get("description", "")
                for fn in (f - 1, f, f + 1)
                if fn in obs_by_frame
            ]
            try:
                self._configure_dspy()
                window = FrameWindowPipeline(strategy="predict")
                refined = window(
                    frame_num=f,
                    total_frames=len(frame_observations),
                    prior_frame=context_frames[0] if len(context_frames) > 1 else "",
                    current_frame=context_frames[1] if len(context_frames) > 2 else context_frames[0] if context_frames else "",
                    next_frame=context_frames[2] if len(context_frames) > 2 else "",
                )
                if refined.get("primary_action") and refined["primary_action"] != entry.get("action"):
                    expanded.append({
                        **entry,
                        "action": refined["primary_action"],
                        "objects": refined.get("objects") or entry.get("objects", []),
                        "confidence": max(conf, refined.get("confidence", conf)),
                        "notes": "expanded from low-confidence entry",
                    })
                    continue
            except Exception as e:  # pragma: no cover — fall through gracefully
                logger.warning(f"[atomic] confidence-expand failed @ frame {f}: {e}")

            expanded.append(entry)
        return expanded

    def _legacy_event_pipeline(
        self,
        *,
        transcript: str,
        frame_observations: List[dict],
        vision_label: str,
        video_id: str,
    ) -> SOPSchema:
        """Fallback: original AtomicActionPipeline + AtomicSOPGenerator path."""

        @traceable(
            name="extract_atomic_events_legacy",
            run_type="chain",
            metadata={"n_frames": len(frame_observations)},
        )
        def _structure_events() -> List[dict]:
            self._configure_dspy()
            extractor_module = AtomicActionPipeline(strategy="predict")
            events: List[dict] = []
            prior_action = ""
            for obs in frame_observations:
                try:
                    ev = extractor_module(
                        frame_num=obs["frame_num"],
                        total_frames=len(frame_observations),
                        prior_action=prior_action,
                        observation=obs["description"],
                    )
                    ev["image_url"] = obs.get("image_url", "")
                    events.append(ev)
                    if ev.get("is_transition") and ev.get("primary_action"):
                        prior_action = ev["primary_action"]
                except Exception as e:
                    logger.warning(
                        f"[atomic] legacy event-extract failed @ frame {obs['frame_num']}: {e}"
                    )
            return events

        events = _structure_events()
        transitions = [e for e in events if e.get("is_transition")]
        events_for_sop = transitions if len(transitions) >= 2 else events

        @traceable(
            name="synthesize_sop_atomic_legacy",
            run_type="chain",
            metadata={"n_events": len(events_for_sop)},
        )
        def _synthesize() -> dict:
            self._configure_dspy()
            base = AtomicSOPGenerator(strategy="predict")
            if settings.sop_self_check_enabled and transcript.strip():
                pipeline = SelfCheckPipeline(
                    base=_AtomicAdapter(base),
                    confidence_threshold=settings.sop_confidence_threshold,
                )
                return pipeline(
                    transcript=transcript,
                    frame_observations=frame_observations,
                    events=events_for_sop,
                )
            return base(events=events_for_sop)

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

        action_count = len(events_for_sop)
        merge_count = max(0, action_count - len(sop.steps))
        sop._diagnoses = []                  # type: ignore[attr-defined]
        sop._frame_observations = frame_observations  # type: ignore[attr-defined]
        sop._transcript = transcript          # type: ignore[attr-defined]
        sop.generation_metadata = {
            "pipeline": "atomic_simple",
            "architecture": "legacy_event",
            "synthesis_model": self.model_name,
            "vision_model": vision_label,
            "verification_model": settings.sop_verification_model if settings.sop_self_check_enabled else None,
            "self_check_enabled": settings.sop_self_check_enabled,
            "frame_count": len(frame_observations),
        }
        sop._adaptive_metrics = {  # type: ignore[attr-defined]
            "pipeline": "atomic_simple",
            "architecture": "legacy_event",
            "vision_model": vision_label,
            "synthesis_model": self.model_name,
            "verification_model": settings.sop_verification_model if settings.sop_self_check_enabled else None,
            "frame_count": len(frame_observations),
            "atomic_action_count": action_count,
            "step_count": len(sop.steps),
            "merge_count": merge_count,
            "event_merge_ratio": (merge_count / action_count) if action_count else 0.0,
        }
        logger.info(
            "[adaptive] video=%s pipeline=atomic_simple arch=legacy_event vision=%s "
            "frames=%d events=%d steps=%d merge_ratio=%.2f",
            video_id,
            vision_label,
            len(frame_observations),
            action_count,
            len(sop.steps),
            (merge_count / action_count) if action_count else 0.0,
        )
        return sop


class _AtomicAdapter:
    """Bridge AtomicSOPGenerator (events-only) into SelfCheckPipeline.forward.

    SelfCheckPipeline.forward signature is `(transcript, **kwargs)`; we forward
    the explicit `events` kwarg into AtomicSOPGenerator.
    """

    def __init__(self, base: AtomicSOPGenerator):
        self.base = base

    def __call__(self, transcript: str, **kwargs) -> dict:  # noqa: ARG002
        events = kwargs.get("events") or []
        return self.base(events=events)


# ----------------------------------------------------------------------
# Spec-named module-level convenience APIs
#
# These are thin wrappers around the DSPy modules and the AtomicSimplePipeline
# so callers (tests, eval scripts, future routes) can use the names from the
# adaptive-granularity spec directly.
# ----------------------------------------------------------------------


def analyze_atomic_actions(
    frame_observations: List[dict],
    model_name: Optional[str] = None,
) -> List[dict]:
    """Run AtomicActionPipeline over a list of frame observations.

    Each observation: {"frame_num": int, "description": str, ...}
    Returns a list of atomic event dicts: {frame_num, primary_action,
    state_changes, is_transition, image_url}.
    """
    from app.services.llm.dspy_config import configure_dspy
    configure_dspy(task="sop", model_name=model_name)
    extractor = AtomicActionPipeline(strategy="predict")

    events: List[dict] = []
    prior_action = ""
    for obs in frame_observations:
        try:
            ev = extractor(
                frame_num=obs["frame_num"],
                total_frames=len(frame_observations),
                prior_action=prior_action,
                observation=obs.get("description", ""),
            )
            ev["image_url"] = obs.get("image_url", "")
            events.append(ev)
            if ev.get("is_transition") and ev.get("primary_action"):
                prior_action = ev["primary_action"]
        except Exception as e:
            logger.warning(
                "[atomic] analyze_atomic_actions failed @ frame %s: %s",
                obs.get("frame_num"), e,
            )
    return events


def detect_action_transitions(
    frame_observations: List[dict],
    model_name: Optional[str] = None,
) -> List[dict]:
    """Return only the events flagged as physical state transitions.

    Wraps analyze_atomic_actions and filters to is_transition=True. Useful
    for callers that just want the discrete action sequence.
    """
    return [
        ev for ev in analyze_atomic_actions(frame_observations, model_name=model_name)
        if ev.get("is_transition")
    ]


# ----------------------------------------------------------------------
# Action-timeline architecture — module-level functions
#
# These mirror the spec's API surface (extract_transition_frames,
# analyze_frame_window, analyze_temporal_transition, build_action_timeline,
# track_object_state_changes, normalize_atomic_actions). The
# AtomicSimplePipeline uses them internally; callers (tests, eval scripts,
# future routes) can import them directly.
# ----------------------------------------------------------------------


def _configure_dspy_lm(model_name: Optional[str] = None) -> None:
    from app.services.llm.dspy_config import configure_dspy
    configure_dspy(task="sop", model_name=model_name)


def extract_transition_frames(
    extractor: FrameExtractor,
    video_path: str,
    video_id: str,
    num_frames: Optional[int] = None,
) -> List[str]:
    """Transition-aware frame extraction.

    Prefers scene-boundary frames (PySceneDetect already used in
    FrameExtractor) over fixed-interval extraction. For atomic_simple, we
    bias slightly toward MORE scenes — short single-actor tasks have
    subtle but frequent state changes.
    """
    n = num_frames if num_frames is not None else (settings.atomic_simple_dense_frames or 16)
    n = max(12, min(24, n))
    return extractor.extract_dense_frames(video_path, video_id, num_frames=n)


def analyze_temporal_transition(
    prior_frame: dict,
    current_frame: dict,
    *,
    model_name: Optional[str] = None,
    total_frames: Optional[int] = None,
) -> dict:
    """Pair-wise frame transition analysis.

    Compares Frame N and Frame N+1 (descriptions only — vision step has
    already produced the per-frame text). Returns a single window observation
    describing only what physically changed between them.
    """
    _configure_dspy_lm(model_name)
    pipeline = FrameWindowPipeline(strategy="predict")
    cur_num = current_frame.get("frame_num") or 0
    return pipeline(
        frame_num=cur_num,
        total_frames=total_frames or cur_num,
        prior_frame=prior_frame.get("description", "") if prior_frame else "",
        current_frame=current_frame.get("description", ""),
        next_frame="",
    )


def analyze_frame_window(
    frame_observations: List[dict],
    *,
    model_name: Optional[str] = None,
) -> List[dict]:
    """Run 3-frame sliding-window transition analysis over the observation list.

    For each frame N, sends (N-1, N, N+1) to FrameWindowPipeline and emits a
    transition observation. Boundary frames receive '' for the missing side.
    Filters out 'no change' / very-low-confidence windows.
    """
    if not frame_observations:
        return []

    _configure_dspy_lm(model_name)
    pipeline = FrameWindowPipeline(strategy="predict")

    windows: List[dict] = []
    n = len(frame_observations)
    for i, obs in enumerate(frame_observations):
        prior = frame_observations[i - 1].get("description", "") if i > 0 else ""
        nxt = frame_observations[i + 1].get("description", "") if i < n - 1 else ""
        try:
            w = pipeline(
                frame_num=obs.get("frame_num", i + 1),
                total_frames=n,
                prior_frame=prior,
                current_frame=obs.get("description", ""),
                next_frame=nxt,
            )
            action = (w.get("primary_action") or "").lower()
            if action and action != "no change" and (w.get("confidence") or 0) >= 0.25:
                w["image_url"] = obs.get("image_url", "")
                windows.append(w)
        except Exception as e:
            logger.warning(
                "[atomic] analyze_frame_window failed @ frame %s: %s",
                obs.get("frame_num"), e,
            )
    return windows


def build_action_timeline(
    window_observations: List[dict],
    *,
    total_frames: int,
    model_name: Optional[str] = None,
) -> List[dict]:
    """Synthesise a temporally-ordered action timeline from window observations.

    Output shape per entry:
        {"timestamp": "00:01", "action": "pick up bottle",
         "objects": ["bottle"], "confidence": 0.94, "frame_num": 1}
    """
    if not window_observations:
        return []
    _configure_dspy_lm(model_name)
    builder = ActionTimelineBuilder(strategy="predict")
    timeline = builder(window_observations=window_observations, total_frames=total_frames)
    # Best-effort sort by frame_num if model didn't preserve order
    timeline.sort(key=lambda t: t.get("frame_num") or 0)
    return timeline


def track_object_state_changes(
    timeline: List[dict],
    *,
    model_name: Optional[str] = None,
) -> List[dict]:
    """Extract explicit object state-change records from a timeline.

    Output shape per record:
        {"object": "bottle_cap", "previous_state": "attached",
         "new_state": "removed", "timestamp": "00:04", "frame_num": 4}
    """
    if not timeline:
        return []
    _configure_dspy_lm(model_name)
    tracker = ObjectStateTracker(strategy="predict")
    return tracker(timeline=timeline)


# Verb-first vocabulary for normalisation. The list is intentionally short
# and operational — the goal is to nudge wording toward concrete actions, not
# to enforce a closed vocabulary.
_ATOMIC_VERB_PREFIXES = (
    "pick up", "put down", "place", "lift", "lower", "rotate", "remove",
    "press", "tap", "hold", "grip", "open", "close", "pour", "fill",
    "empty", "turn on", "turn off", "switch on", "switch off", "wipe",
    "fold", "unfold", "insert", "extract", "push", "pull", "slide",
    "rotate", "twist", "unscrew", "screw", "stack", "align", "scan",
    "attach", "detach",
)

# Phrases that signal inferred intent — must not appear in atomic actions.
_ABSTRACT_PHRASE_RE = re.compile(
    r"\b(?:prepares? to|attempts? to|tries? to|appears? to|seems? to|"
    r"intends? to|wants? to|plans? to|gets? ready to|in order to|"
    r"handles?\s+(?:the\s+|a\s+|an\s+)?\w+)\b",
    re.IGNORECASE,
)

# Tokens that indicate the wording is too generic to be useful.
_GENERIC_VERB_RE = re.compile(
    r"^\s*(?:handle|interact with|work with|deal with|manage|process|"
    r"continue|continues|use|operate|do|perform)\b",
    re.IGNORECASE,
)

# Phrases that show up MID-sentence and indicate filler / non-operational
# step content. Step is dropped when the cleaned action collapses to one
# of these (e.g. "the process", "the item", "the object").
_FILLER_OBJECT_RE = re.compile(
    r"^\s*(?:the\s+)?(?:process|item|object|thing|task)\s*$",
    re.IGNORECASE,
)

_INFERENCE_PHRASE_RE = re.compile(
    r"\b(?:in order to|prepares? to|intends? to|wants? to|tries? to|"
    r"plans? to|attempts? to|appears? to|seems? to|gets? ready to)\b",
    re.IGNORECASE,
)


def _verbify(action: str) -> str:
    """Strip leading articles/subjects so the action reads verb-first."""
    a = (action or "").strip()
    if not a:
        return a
    # Drop "The person ", "Person ", "Someone " pronoun lead-ins.
    a = re.sub(r"^(the\s+person|a\s+person|the\s+user|someone)\s+", "", a, flags=re.IGNORECASE)
    # Drop trailing period.
    a = a.rstrip(".")
    return a.strip()


def normalize_atomic_actions(timeline: List[dict]) -> List[dict]:
    """Verb-first vocabulary + literal-action normalisation.

    Deterministic (no LLM call). Applies three lightweight passes:
      1. Strip pronoun lead-ins ("The person picks up X" → "picks up X")
      2. Flag entries containing inference phrases ("prepares to drink") so
         the formatter can warn rather than silently render them as steps
      3. De-duplicate consecutive identical actions (visual-repetition collapse
         is the ONLY merge allowed under preserve_micro_actions).
    """
    out: List[dict] = []
    last_action = None
    for entry in timeline:
        action = _verbify(entry.get("action", ""))
        notes = entry.get("notes")
        if _INFERENCE_PHRASE_RE.search(action):
            notes = (notes + "; " if notes else "") + "inferred-intent phrasing"
        if action and action.lower() == (last_action or "").lower():
            # Visual repetition — collapse into the previous entry.
            continue
        new_entry = {**entry, "action": action}
        if notes:
            new_entry["notes"] = notes
        out.append(new_entry)
        last_action = action
    return out


# ----------------------------------------------------------------------
# Visual-grounding + action-boundary segmentation (atomic_simple only)
# ----------------------------------------------------------------------


def analyze_object_interactions(window_observations: List[dict]) -> List[dict]:
    """Pivot window observations by object → object-interaction records.

    Deterministic. For each window with a populated `objects` list and a
    `primary_action`, emit one record per object with the actor (when
    visible) and timestamp. The primary manipulated object (first in the
    list) gets the verb-first interaction; secondary objects get the
    contact-only interaction.

    Output shape per record:
        {"object": "bottle_cap",
         "interaction": "rotate counterclockwise",
         "actor": "right hand",
         "timestamp": "00:04",
         "frame_num": 6,
         "confidence": 0.91}
    """
    records: List[dict] = []
    for w in window_observations:
        objs = w.get("objects") or []
        action = (w.get("primary_action") or "").strip()
        if not objs or not action or action.lower() == "no change":
            continue
        actor = w.get("actor") or "hand"
        confidence = float(w.get("confidence") or 0.0)
        frame_num = w.get("frame_num")
        timestamp = _timestamp_for_frame(frame_num, total_frames=None)
        for idx, obj in enumerate(objs):
            interaction = action if idx == 0 else f"in contact with {action}"
            records.append({
                "object": obj,
                "interaction": interaction,
                "actor": actor,
                "timestamp": timestamp,
                "frame_num": frame_num,
                "confidence": confidence,
            })
    return records


def detect_contact_events(window_observations: List[dict]) -> List[dict]:
    """Filter window observations into discrete contact-event records.

    Output shape per record:
        {"contact_start": True,
         "object_a": "hand",
         "object_b": "bottle",
         "timestamp": "00:03",
         "frame_num": 3}
    """
    events: List[dict] = []
    for w in window_observations:
        ce = (w.get("contact_event") or "").lower()
        if ce not in ("contact_start", "contact_end"):
            continue
        objs = w.get("objects") or []
        if not objs:
            continue
        actor = w.get("actor") or "hand"
        events.append({
            "contact_start": ce == "contact_start",
            "contact_end": ce == "contact_end",
            "object_a": actor,
            "object_b": objs[0],
            "timestamp": _timestamp_for_frame(w.get("frame_num"), total_frames=None),
            "frame_num": w.get("frame_num"),
        })
    return events


def analyze_motion_direction(window_observations: List[dict]) -> List[dict]:
    """Extract directional-motion records from window observations.

    Output shape per record:
        {"frame_num": 6,
         "motion": "rotate counterclockwise",
         "primary_action": "rotate bottle cap",
         "objects": ["bottle_cap"]}
    """
    out: List[dict] = []
    for w in window_observations:
        motion = (w.get("motion") or "").strip().lower()
        if not motion or motion == "none":
            continue
        out.append({
            "frame_num": w.get("frame_num"),
            "motion": motion,
            "primary_action": w.get("primary_action", ""),
            "objects": w.get("objects") or [],
        })
    return out


def segment_action_boundaries(timeline: List[dict]) -> List[dict]:
    """Group consecutive timeline entries with the same action into spans.

    Deterministic. Each span is one action boundary — preserves the
    micro-action sequence while making it clear where each action starts
    and ends.

    Output shape per span:
        {"start_frame": 12, "end_frame": 18, "action": "rotate bottle cap",
         "objects": ["bottle_cap"], "confidence": 0.92}
    """
    if not timeline:
        return []
    spans: List[dict] = []
    cur: Optional[dict] = None
    for entry in timeline:
        action = (entry.get("action") or "").strip()
        f = entry.get("frame_num") or 0
        if cur is None or cur["action"].lower() != action.lower():
            if cur is not None:
                spans.append(cur)
            cur = {
                "start_frame": f,
                "end_frame": f,
                "action": action,
                "objects": list(entry.get("objects") or []),
                "confidence": float(entry.get("confidence") or 0.0),
            }
        else:
            cur["end_frame"] = f
            # Keep the span confidence as the max across grouped frames so
            # short repeated micro-actions retain their best signal.
            cur["confidence"] = max(cur["confidence"], float(entry.get("confidence") or 0.0))
            for obj in entry.get("objects") or []:
                if obj not in cur["objects"]:
                    cur["objects"].append(obj)
    if cur is not None:
        spans.append(cur)
    return spans


def enforce_atomic_action_vocabulary(timeline: List[dict]) -> List[dict]:
    """Strict verb-first vocab. Removes inference verbs and generic placeholders.

    Stricter than normalize_atomic_actions:
      - Replaces inferred-intent phrasing entirely (action body discarded
        unless it can be salvaged with the matched verb)
      - Drops entries whose verb collapses to a generic placeholder
        ("handle the bottle", "interact with cap", "continue process")
      - Drops entries whose object collapses to filler ("the item",
        "the process", "the object")
    """
    out: List[dict] = []
    for entry in timeline:
        action = (entry.get("action") or "").strip()
        if not action:
            continue
        # Drop generic verb placeholders entirely.
        if _GENERIC_VERB_RE.match(action):
            continue
        # Strip abstract-intent phrasing with a single pass and keep the
        # remainder; if the result is too short, drop the entry.
        cleaned = _ABSTRACT_PHRASE_RE.sub("", action).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned).rstrip(".")
        if len(cleaned.split()) < 2:
            continue
        # Drop entries where the object reduced to filler ("handle the item"
        # → "the item" → drop). The verb-only check below handles cases
        # where the object portion is missing entirely.
        tail = " ".join(cleaned.split()[1:]) if len(cleaned.split()) > 1 else ""
        if _FILLER_OBJECT_RE.match(tail):
            continue
        new_entry = {**entry, "action": cleaned}
        out.append(new_entry)
    return out


# ----------------------------------------------------------------------
# Operational tracking — deterministic structures over existing VLM output
# ----------------------------------------------------------------------


def aggregate_object_trajectories(window_observations: List[dict]) -> List[dict]:
    """Collapse window observations into per-object state machines.

    Deterministic. For each object that appears in any window, builds a
    chronological trajectory of state transitions:

        {
          "object": "bottle",
          "first_frame": 1,
          "last_frame": 14,
          "states": ["closed", "picked up", "filled", "capped", "placed"],
          "interactions": [
            {"frame_num": 1, "action": "pick up bottle", "actor": "right hand"},
            {"frame_num": 4, "action": "open bottle cap", "actor": "right hand"},
            ...
          ],
        }

    No LLM call. Operates entirely on what FrameWindowPipeline already
    extracted (objects, actor, primary_action, state_change). Useful for
    surfaces like 'show me the trajectory of the bottle through this run'
    AND as a memory hint for the formatter that an object is being tracked
    across multiple steps (so don't collapse them).
    """
    by_object: dict = {}
    for w in window_observations:
        objs = w.get("objects") or []
        if not objs:
            continue
        primary = objs[0]
        actor = w.get("actor") or "hand"
        action = (w.get("primary_action") or "").strip()
        frame_num = w.get("frame_num")
        sc = w.get("state_change")  # e.g. 'cap attached→removed'

        traj = by_object.setdefault(primary, {
            "object": primary,
            "first_frame": frame_num,
            "last_frame": frame_num,
            "states": [],
            "interactions": [],
        })
        if frame_num is not None:
            traj["first_frame"] = min(traj["first_frame"] or frame_num, frame_num)
            traj["last_frame"] = max(traj["last_frame"] or frame_num, frame_num)
        if action and action.lower() != "no change":
            traj["interactions"].append({
                "frame_num": frame_num,
                "action": action,
                "actor": actor,
            })
        if sc:
            # Encode as 'previous→new' literal string; the next deterministic
            # pass extracts the new state if the trajectory needs a state list.
            arrow = "→" if "→" in sc else "->"
            if arrow in sc:
                _, new_state = sc.split(arrow, 1)
                new_state = new_state.strip().rstrip(".")
                if new_state and (not traj["states"] or traj["states"][-1] != new_state):
                    traj["states"].append(new_state)
    return list(by_object.values())


def extract_operational_scene_graph(window_observations: List[dict]) -> List[dict]:
    """Restructure window observations into operational scene-graph records.

    Each record:
        {
          "actor": "worker",          # body part / role from the window
          "action": "pouring",        # verb-first
          "object": "water bottle",   # primary manipulated object
          "target": "sink",           # secondary object (when present)
          "state_change": "empty_to_filled",  # snake_case state delta
          "frame_num": 6,
        }

    Deterministic — derived from the same window observations the timeline
    already uses. The 'target' field is populated when a window mentions
    multiple objects (the second is treated as the target/recipient).
    'state_change' is normalised from 'previous→new' arrow form to the
    snake_case 'previous_to_new' shape expected by downstream tooling.
    """
    graph: List[dict] = []
    for w in window_observations:
        action = (w.get("primary_action") or "").strip()
        if not action or action.lower() == "no change":
            continue
        objs = w.get("objects") or []
        if not objs:
            continue

        sc_raw = w.get("state_change") or ""
        state_change = ""
        if sc_raw:
            arrow = "→" if "→" in sc_raw else ("->" if "->" in sc_raw else "")
            if arrow:
                prev, new = sc_raw.split(arrow, 1)
                prev = prev.strip().lower().replace(" ", "_")
                new = new.strip().rstrip(".").lower().replace(" ", "_")
                if prev and new:
                    state_change = f"{prev}_to_{new}"

        record = {
            "actor": (w.get("actor") or "operator").strip() or "operator",
            "action": action,
            "object": objs[0],
            "target": objs[1] if len(objs) > 1 else None,
            "state_change": state_change or None,
            "frame_num": w.get("frame_num"),
        }
        graph.append(record)
    return graph


def preserve_micro_action_chain(window_observations: List[dict]) -> List[dict]:
    """Anti-merge guard. Marks transitions that MUST NOT be collapsed downstream.

    Sets `_protected = True` on windows that:
      - have a contact_event (contact start/end is a hard boundary)
      - change motion direction vs prior window
      - show a state_change ('cap attached→removed')
      - change the primary manipulated object

    The TimelineFormatter signature is already formatter-only, but this flag
    gives downstream code a deterministic signal to refuse collapsing.
    """
    out: List[dict] = []
    prior_motion: Optional[str] = None
    prior_object: Optional[str] = None
    for w in window_observations:
        new_w = dict(w)
        protected = False
        ce = w.get("contact_event")
        if ce in ("contact_start", "contact_end"):
            protected = True
        motion = (w.get("motion") or None)
        if motion and motion != prior_motion:
            protected = True
        if w.get("state_change"):
            protected = True
        objs = w.get("objects") or []
        primary = objs[0] if objs else None
        if primary and primary != prior_object:
            protected = True
        new_w["_protected"] = protected
        out.append(new_w)
        if motion is not None:
            prior_motion = motion
        if primary is not None:
            prior_object = primary
    return out


def _timestamp_for_frame(
    frame_num: Optional[int],
    total_frames: Optional[int],
    duration_seconds: Optional[float] = None,
) -> str:
    """Best-effort timestamp string from a frame number.

    When duration is unknown we fall back to "frame N" — accurate even if
    not human-friendly. The DSPy timeline builder is the primary source of
    pretty timestamps; this is a helper for deterministic emit paths.
    """
    if frame_num is None:
        return ""
    if duration_seconds and total_frames and total_frames > 0:
        seconds = (frame_num / total_frames) * duration_seconds
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"
    return f"frame {frame_num}"
