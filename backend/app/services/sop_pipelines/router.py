"""Pipeline router — picks the right pipeline for a video.

Routing dimensions:
  - video_type:        UI         → UIPipeline
                       PHYSICAL   → routed by complexity below
  - pipeline_complexity (physical only):
                       ATOMIC_SIMPLE      → AtomicSimplePipeline
                       PROCEDURAL_COMPLEX → PhysicalPipeline (existing strong path)
                       AUTO               → caller is expected to classify first
                                            and pass the resolved value here

Kill-switch: settings.force_procedural_only_for_physical, when True, makes
every physical video route to PhysicalPipeline regardless of complexity.
Use it when atomic_simple misbehaves — the procedural path is unaffected.
"""
import logging
from typing import Union

from app.core.config import settings
from app.models.video import PipelineComplexity, VideoType
from app.services.sop_pipelines.atomic_simple import AtomicSimplePipeline
from app.services.sop_pipelines.physical import PhysicalPipeline
from app.services.sop_pipelines.ui import UIPipeline


logger = logging.getLogger(__name__)
PipelineInstance = Union[PhysicalPipeline, AtomicSimplePipeline, UIPipeline]


def get_pipeline(
    video_type: str,
    model_name: str,
    complexity: str = PipelineComplexity.PROCEDURAL_COMPLEX.value,
    user_id: str | None = None,
) -> PipelineInstance:
    """Return the pipeline instance for the given video_type + complexity.

    user_id (optional) is forwarded into PhysicalPipeline so per-user pinned
    examples can win over the global RAG pool. Atomic + UI pipelines don't
    use few-shot retrieval today.
    """
    if video_type == VideoType.UI.value:
        return UIPipeline(model_name=model_name)

    # Kill-switch — physical videos always use PhysicalPipeline.
    if settings.force_procedural_only_for_physical:
        if complexity == PipelineComplexity.ATOMIC_SIMPLE.value:
            logger.info(
                "[router] force_procedural_only_for_physical=True — "
                "rerouting atomic_simple → PhysicalPipeline"
            )
        return PhysicalPipeline(model_name=model_name, user_id=user_id)

    if complexity == PipelineComplexity.ATOMIC_SIMPLE.value:
        return AtomicSimplePipeline(model_name=model_name)
    # Default and PROCEDURAL_COMPLEX both go through the existing strong path.
    return PhysicalPipeline(model_name=model_name, user_id=user_id)
