"""DSPy model routing helpers."""
from __future__ import annotations

import logging

from app.core.config import settings
from app.services.llm.model_aliases import normalize_model_name


logger = logging.getLogger(__name__)

_TASK_MODEL_SETTINGS = {
    "sop": "sop_synthesis_model",
    "workflow": "workflow_model",
    "checklist": "checklist_model",
    "training": "training_model",
    "verification": "sop_verification_model",
}


def task_model(task: str, override: str | None = None) -> str:
    if override:
        return normalize_model_name(override)
    attr = _TASK_MODEL_SETTINGS.get(task)
    if attr:
        value = getattr(settings, attr, "")
        if value:
            return normalize_model_name(value)
    return normalize_model_name(settings.llm_model)


def _provider_for_model(model_name: str, default_provider: str = "") -> str:
    model_name = normalize_model_name(model_name)
    default_provider = (default_provider or "").lower()
    if model_name.startswith("groq/"):
        return "groq"
    if model_name.startswith("openrouter/"):
        return "openrouter"
    if model_name.startswith("together_ai/"):
        return "together"
    if default_provider:
        return default_provider
    if model_name.startswith(("Qwen/", "mistralai/", "meta-llama/")):
        return "together"
    return settings.llm_provider


def _strip_provider_prefix(model_name: str) -> str:
    for prefix in ("groq/", "openrouter/", "together_ai/"):
        if model_name.startswith(prefix):
            return model_name[len(prefix):]
    return model_name


def configure_dspy(
    *,
    task: str,
    model_name: str | None = None,
    default_provider: str = "",
    temperature: float = 0,
) -> str:
    """Configure DSPy for a task and return the raw model name used."""
    import dspy

    selected = task_model(task, override=model_name)
    provider = _provider_for_model(selected, default_provider=default_provider)
    raw_model = _strip_provider_prefix(selected)

    if provider == "together":
        if not settings.together_api_key:
            raise ValueError(
                "TOGETHER_API_KEY is required for Together model "
                f"'{raw_model}'"
            )
        lm = dspy.LM(
            f"together_ai/{raw_model}",
            api_key=settings.together_api_key,
            temperature=temperature,
        )
    elif provider == "groq":
        lm = dspy.LM(
            f"groq/{raw_model}",
            api_key=settings.groq_api_key,
            temperature=temperature,
        )
    else:
        raise ValueError(f"Unsupported LLM provider '{provider}' for model '{selected}'")

    dspy.configure(lm=lm)
    logger.info("[llm.dspy] task=%s provider=%s model=%s", task, provider, raw_model)
    return raw_model
