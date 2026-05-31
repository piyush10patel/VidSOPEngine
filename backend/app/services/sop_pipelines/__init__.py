"""Pipeline split.

Three pipelines, one router:
  - PhysicalPipeline       — multi-step real-world processes (default for physical videos)
  - AtomicSimplePipeline   — short, single-actor tasks (granularity-preserving)
  - UIPipeline             — software screen recordings (label-grounded)
"""
from app.services.sop_pipelines.atomic_simple import (
    AtomicSimplePipeline,
    aggregate_object_trajectories,
    analyze_atomic_actions,
    analyze_frame_window,
    analyze_motion_direction,
    analyze_object_interactions,
    analyze_temporal_transition,
    build_action_timeline,
    detect_action_transitions,
    detect_contact_events,
    enforce_atomic_action_vocabulary,
    extract_operational_scene_graph,
    extract_transition_frames,
    normalize_atomic_actions,
    preserve_micro_action_chain,
    segment_action_boundaries,
    track_object_state_changes,
)
from app.services.sop_pipelines.physical import PhysicalPipeline
from app.services.sop_pipelines.ui import UIPipeline
from app.services.sop_pipelines.router import get_pipeline

__all__ = [
    "PhysicalPipeline",
    "UIPipeline",
    "AtomicSimplePipeline",
    "get_pipeline",
    # Adaptive-granularity surface
    "analyze_atomic_actions",
    "detect_action_transitions",
    # Action-timeline architecture surface
    "analyze_frame_window",
    "analyze_temporal_transition",
    "build_action_timeline",
    "extract_transition_frames",
    "normalize_atomic_actions",
    "track_object_state_changes",
    # Visual-grounding + boundary segmentation surface
    "analyze_motion_direction",
    "analyze_object_interactions",
    "detect_contact_events",
    "enforce_atomic_action_vocabulary",
    "preserve_micro_action_chain",
    "segment_action_boundaries",
    # Operational tracking surface
    "aggregate_object_trajectories",
    "extract_operational_scene_graph",
]
