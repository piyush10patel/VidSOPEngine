"""LLM provider interface — Protocol + shared types.

The interface is intentionally narrow: three operations cover every AI
call in the codebase. Provider implementations enforce timeouts, do
retries on transient errors, and report token usage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol


@dataclass
class LLMUsage:
    """Token usage for a single call. Provider returns; caller rolls up."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""

    def __add__(self, other: "LLMUsage") -> "LLMUsage":
        return LLMUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            model=self.model or other.model,
        )


@dataclass
class LLMResponse:
    """Provider response wrapper — keeps text + usage together."""
    text: str
    usage: LLMUsage = field(default_factory=LLMUsage)


class ProviderUnavailable(Exception):
    """Raised when the LLM provider is unreachable (or open circuit).

    Caller should fall back (text-only path, cached result, etc.) rather
    than retry — the provider implementation already exhausted its retry
    budget before raising this.
    """
    pass


class LLMProvider(Protocol):
    """Common shape every AI provider must implement.

    Timeouts are mandatory (default values are reasonable; callers can
    override per-request). Retries on transient errors are the provider's
    responsibility.
    """

    def transcribe(
        self,
        audio_path: str,
        *,
        model: Optional[str] = None,
        timeout: int = 60,
    ) -> LLMResponse:
        """Speech-to-text. ``audio_path`` is a local file path."""
        ...

    def vision(
        self,
        prompt: str,
        image_b64: str,
        *,
        model: Optional[str] = None,
        timeout: int = 30,
        temperature: float = 0,
    ) -> LLMResponse:
        """Single-image multimodal generation."""
        ...

    def chat(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout: int = 30,
        temperature: float = 0,
        response_format: Optional[dict] = None,
    ) -> LLMResponse:
        """Text-only generation. ``response_format`` for JSON mode etc."""
        ...
