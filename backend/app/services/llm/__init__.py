"""LLM provider abstraction.

All AI inference goes through this interface. Direct SDK calls (Groq,
OpenAI, Anthropic, etc.) are forbidden in routers/services/pipelines; use
``get_provider()`` instead.

Why:
- Single place to enforce timeouts, retries, and token tracking.
- Single place to swap providers (Groq, Together, OpenRouter) without
  touching pipeline code.
- Single place to add a circuit breaker (Phase D).
- Single place to add caching (Phase E).
"""
from app.services.llm.provider import (
    LLMProvider,
    LLMUsage,
    LLMResponse,
    ProviderUnavailable,
)
from app.services.llm.groq_provider import GroqProvider
from app.services.llm.openrouter_provider import OpenRouterProvider
from app.services.llm.routing_provider import RoutingProvider
from app.services.llm.together_provider import TogetherProvider


# Default app path: model-aware router. Named providers are still exposed for
# admin/A-B paths that intentionally call a specific provider directly.
_router: "RoutingProvider | None" = None
_providers: dict[str, LLMProvider] = {}


def get_provider(name: str | None = None):
    """Return the model-aware router or a named provider singleton.

    ``get_provider()`` routes by operation/model: Groq for transcription,
    OpenRouter for vision, and Together for Together text models.

    ``get_provider("together")`` / ``get_provider("groq")`` are for admin
    and A/B testing code that explicitly chooses a provider.
    """
    global _router

    provider_name = (name or "router").lower()
    if provider_name in {"router", "routing", "auto"}:
        if _router is None:
            _router = RoutingProvider()
        return _router

    if provider_name not in _providers:
        if provider_name == "groq":
            _providers[provider_name] = GroqProvider()
        elif provider_name == "openrouter":
            _providers[provider_name] = OpenRouterProvider()
        elif provider_name in {"together", "together_ai"}:
            _providers[provider_name] = TogetherProvider()
        else:
            raise ValueError(f"Unknown LLM provider: {name!r}")
    return _providers[provider_name]


__all__ = [
    "LLMProvider",
    "LLMUsage",
    "LLMResponse",
    "ProviderUnavailable",
    "GroqProvider",
    "OpenRouterProvider",
    "TogetherProvider",
    "RoutingProvider",
    "get_provider",
]
