"""Model-aware provider router.

Keeps Groq available for Whisper transcription, routes OpenRouter vision
models to OpenRouter, and routes Together text models to Together.
"""
from __future__ import annotations

from typing import Optional

from app.core.config import settings
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.model_aliases import normalize_model_name
from app.services.llm.openrouter_provider import OpenRouterProvider
from app.services.llm.provider import LLMResponse
from app.services.llm.together_provider import TogetherProvider


def _provider_for_model(model_name: str, default_provider: str) -> str:
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
    return default_provider


def _strip_provider_prefix(model_name: str) -> str:
    for prefix in ("groq/", "openrouter/", "together_ai/"):
        if model_name.startswith(prefix):
            return model_name[len(prefix):]
    return model_name


class RoutingProvider:
    """Route each operation to the provider implied by its model."""

    def __init__(self) -> None:
        self.groq = GroqProvider()
        self.openrouter = OpenRouterProvider()
        self.together = TogetherProvider()

    def transcribe(
        self,
        audio_path: str,
        *,
        model: Optional[str] = None,
        timeout: int = 60,
    ) -> LLMResponse:
        return self.groq.transcribe(audio_path, model=model, timeout=timeout)

    def vision(
        self,
        prompt: str,
        image_b64: str,
        *,
        model: Optional[str] = None,
        timeout: int = 30,
        temperature: float = 0,
    ) -> LLMResponse:
        selected = model or settings.vision_model
        provider = _provider_for_model(selected, settings.vision_provider)
        raw_model = _strip_provider_prefix(selected)
        if provider == "openrouter":
            return self.openrouter.vision(
                prompt,
                image_b64,
                model=raw_model,
                timeout=timeout,
                temperature=temperature,
            )
        if provider == "together":
            return self.together.vision(
                prompt,
                image_b64,
                model=raw_model,
                timeout=timeout,
                temperature=temperature,
            )
        return self.groq.vision(
            prompt,
            image_b64,
            model=raw_model,
            timeout=timeout,
            temperature=temperature,
        )

    def chat(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout: int = 30,
        temperature: float = 0,
        response_format: Optional[dict] = None,
    ) -> LLMResponse:
        selected = normalize_model_name(model or settings.sop_synthesis_model)
        provider = _provider_for_model(selected, settings.llm_provider)
        raw_model = _strip_provider_prefix(selected)
        if provider == "openrouter":
            return self.openrouter.chat(
                prompt,
                model=raw_model,
                timeout=timeout,
                temperature=temperature,
                response_format=response_format,
            )
        if provider == "together":
            return self.together.chat(
                prompt,
                model=raw_model,
                timeout=timeout,
                temperature=temperature,
                response_format=response_format,
            )
        return self.groq.chat(
            prompt,
            model=raw_model,
            timeout=timeout,
            temperature=temperature,
            response_format=response_format,
        )
