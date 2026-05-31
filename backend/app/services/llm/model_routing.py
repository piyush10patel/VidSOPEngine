"""Per-stage model routing for admin views and A/B harnesses.

Production pipeline code primarily uses ``app.services.llm.dspy_config``.
This module provides the same routing information in a structured form for
admin endpoints and offline comparisons.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Optional

from app.core.config import settings
from app.services.llm.model_aliases import normalize_model_name

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StageModel:
    """One model assignment for a stage."""
    provider: str  # "groq" | "openrouter" | "together"
    model: str
    label: str


@dataclass(frozen=True)
class StageRouting:
    """The routing decision for a stage: default + optional A/B variants."""
    stage: str
    default: StageModel
    variants: List[StageModel]


_STAGE_MODEL_ATTRS = {
    "vision": "vision_model",
    "sop_synthesis": "sop_synthesis_model",
    "workflows": "workflow_model",
    "checklists": "checklist_model",
    "training": "training_model",
}

_ENV_PREFIX_ALIASES = {
    "workflows": ("WORKFLOWS", "WORKFLOW"),
    "checklists": ("CHECKLISTS", "CHECKLIST"),
}


def _provider_for_model(model_name: str, default_provider: str = "") -> str:
    model_name = normalize_model_name(model_name)
    default_provider = (default_provider or "").lower()
    if model_name.startswith("groq/"):
        return "groq"
    if model_name.startswith("openrouter/"):
        return "openrouter"
    if model_name.startswith("together_ai/"):
        return "together"
    if model_name.startswith(("Qwen/", "mistralai/", "meta-llama/")):
        return "together"
    return default_provider or settings.llm_provider


def _strip_provider_prefix(model_name: str) -> str:
    for prefix in ("groq/", "openrouter/", "together_ai/"):
        if model_name.startswith(prefix):
            return model_name[len(prefix):]
    return model_name


def _label(provider: str, model: str) -> str:
    safe_model = (
        model.split("/")[-1]
        .replace("-Instruct", "")
        .replace("-Turbo", "")
        .replace("_", "-")
    )
    return f"{provider}-{safe_model}".lower()


def _env_override(stage: str) -> tuple[str | None, str | None]:
    prefixes = _ENV_PREFIX_ALIASES.get(stage, (stage.upper(),))
    for prefix in prefixes:
        provider = os.environ.get(f"{prefix}_PROVIDER")
        model = os.environ.get(f"{prefix}_MODEL")
        if provider or model:
            return provider, model
    return None, None


def _configured_stage_model(stage: str) -> StageModel:
    if stage == "transcription":
        return StageModel("groq", "whisper-large-v3", "groq-whisper")
    if stage not in _STAGE_MODEL_ATTRS:
        raise ValueError(f"Unknown stage: {stage}. Known: {all_stages()}")

    model = normalize_model_name(getattr(settings, _STAGE_MODEL_ATTRS[stage]))
    default_provider = settings.vision_provider if stage == "vision" else settings.llm_provider
    provider = _provider_for_model(model, default_provider)

    override_provider, override_model = _env_override(stage)
    if override_model:
        model = normalize_model_name(override_model)
    if override_provider:
        provider = override_provider.lower()
    else:
        provider = _provider_for_model(model, default_provider)

    return StageModel(provider=provider, model=_strip_provider_prefix(model), label=_label(provider, model))


def stage_model(stage: str) -> StageModel:
    """Return the default model assignment for a stage."""
    return _configured_stage_model(stage)


def stage_routing(stage: str) -> StageRouting:
    """Default + A/B variants for a stage."""
    default = stage_model(stage)
    variants: list[StageModel] = []

    if stage == "sop_synthesis":
        for model_name in settings.sop_ab_test_model_list:
            normalized = normalize_model_name(model_name)
            provider = _provider_for_model(normalized, settings.llm_provider)
            raw_model = _strip_provider_prefix(normalized)
            if provider == default.provider and raw_model == default.model:
                continue
            variants.append(
                StageModel(
                    provider=provider,
                    model=raw_model,
                    label=_label(provider, raw_model),
                )
            )

    return StageRouting(stage=stage, default=default, variants=variants)


def all_stages() -> List[str]:
    return ["checklists", "sop_synthesis", "training", "transcription", "vision", "workflows"]


def configure_dspy_for_stage(stage: str, *, model_override: Optional[StageModel] = None) -> None:
    """Configure dspy.LM for a text-generation stage."""
    import dspy

    sm = model_override or stage_model(stage)
    if sm.provider == "groq":
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY is required for Groq calls.")
        lm = dspy.LM(f"groq/{sm.model}", api_key=settings.groq_api_key, temperature=0)
    elif sm.provider == "together":
        if not settings.together_api_key:
            raise RuntimeError("TOGETHER_API_KEY is required for Together AI calls.")
        lm = dspy.LM(
            f"together_ai/{sm.model}",
            api_key=settings.together_api_key,
            temperature=0,
        )
    else:
        raise RuntimeError(f"DSPy stage '{stage}' does not support provider '{sm.provider}'")

    dspy.configure(lm=lm)
    logger.info(
        "[model_routing] stage=%s provider=%s model=%s label=%s",
        stage,
        sm.provider,
        sm.model,
        sm.label,
    )


def call_chat_for_stage(
    prompt: str,
    *,
    stage: str,
    model_override: Optional[StageModel] = None,
    response_format: Optional[dict] = None,
    timeout: int = 30,
):
    """Direct chat call routed by stage for admin/A-B helpers."""
    sm = model_override or stage_model(stage)
    from app.services.llm import get_provider

    provider = get_provider(sm.provider)
    return provider.chat(
        prompt,
        model=sm.model,
        timeout=timeout,
        response_format=response_format,
    )
