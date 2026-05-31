"""Groq implementation of LLMProvider.

Wraps the Groq SDK with:
- explicit timeouts on every call
- retry-on-transient (3 attempts, exponential backoff)
- token-usage extraction from the SDK response
- typed errors — providers raise ProviderUnavailable when retries exhausted

The Groq SDK is imported lazily so app startup doesn't pay for it when
LLM_PROVIDER selects a different implementation in the future.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from app.core.config import settings
from app.services.llm.provider import LLMProvider, LLMResponse, LLMUsage, ProviderUnavailable

logger = logging.getLogger(__name__)


# Transient error markers — Groq can raise these on overload, rate limit,
# or transient network issues. Distinct from invalid-input errors which
# we never retry.
_TRANSIENT_MARKERS = (
    "timeout",
    "connection",
    "rate_limit",
    "ratelimit",
    "internal_server",
    "service_unavailable",
    "503",
    "502",
    "504",
    "overloaded",
)


def _is_transient(err: Exception) -> bool:
    msg = str(err).lower()
    return any(m in msg for m in _TRANSIENT_MARKERS)


class GroqProvider:
    """Groq implementation of LLMProvider."""

    def __init__(
        self,
        *,
        max_retries: int = 3,
        backoff_base: float = 1.0,
    ) -> None:
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from groq import Groq
            self._client = Groq(api_key=settings.groq_api_key)
        return self._client

    # ------------------------------------------------------------------
    # Public LLMProvider methods
    # ------------------------------------------------------------------

    def transcribe(
        self,
        audio_path: str,
        *,
        model: Optional[str] = None,
        timeout: int = 60,
    ) -> LLMResponse:
        model_name = model or "whisper-large-v3"

        def _call() -> LLMResponse:
            with open(audio_path, "rb") as f:
                resp = self.client.audio.transcriptions.create(
                    file=(os.path.basename(audio_path), f),
                    model=model_name,
                    timeout=timeout,
                )
            return LLMResponse(
                text=resp.text or "",
                usage=LLMUsage(model=model_name),
            )

        return self._with_retry(_call, op="transcribe")

    def vision(
        self,
        prompt: str,
        image_b64: str,
        *,
        model: Optional[str] = None,
        timeout: int = 30,
        temperature: float = 0,
    ) -> LLMResponse:
        model_name = model or settings.groq_vision_model

        # Cache check (Phase E). Same prompt + same image + same model →
        # same response. Skipped when the cache is disabled or temperature
        # is non-zero (would mask sampling variance).
        cache_key = None
        if settings.llm_cache_enabled and temperature == 0:
            from app.services.llm.cache import (
                get_cached_response,
                put_cached_response,
                vision_cache_key,
            )
            cache_key = vision_cache_key(prompt, image_b64, model_name)
            cached = get_cached_response(cache_key)
            if cached is not None:
                logger.info(f"[llm.groq] vision cache HIT model={model_name}")
                return cached

        def _call() -> LLMResponse:
            resp = self.client.chat.completions.create(
                model=model_name,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}"
                        }},
                    ],
                }],
                temperature=temperature,
                timeout=timeout,
            )
            return _wrap_response(resp, model_name)

        result = self._with_retry(_call, op="vision")
        # Write-through on success.
        if cache_key:
            from app.services.llm.cache import put_cached_response
            put_cached_response(cache_key, result)
        return result

    def chat(
        self,
        prompt: str,
        *,
        model: Optional[str] = None,
        timeout: int = 30,
        temperature: float = 0,
        response_format: Optional[dict] = None,
    ) -> LLMResponse:
        model_name = model or settings.llm_model

        def _call() -> LLMResponse:
            kwargs = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "timeout": timeout,
            }
            if response_format:
                kwargs["response_format"] = response_format
            resp = self.client.chat.completions.create(**kwargs)
            return _wrap_response(resp, model_name)

        return self._with_retry(_call, op="chat")

    # ------------------------------------------------------------------
    # Retry harness
    # ------------------------------------------------------------------

    def _with_retry(self, fn, *, op: str) -> LLMResponse:
        """Retry transient failures with exponential backoff, gated by the
        circuit breaker.

        - Breaker OPEN     → fail fast with ProviderUnavailable.
        - Breaker CLOSED   → 3 attempts, exponential backoff. Permanent
          errors (auth, invalid input) re-raise immediately and do NOT
          increment the failure counter.
        - All retries exhausted → record failure on the breaker and raise
          ProviderUnavailable.
        """
        from app.services.llm.circuit_breaker import get_breaker
        breaker = get_breaker()
        if breaker.state == "open":
            raise ProviderUnavailable(
                f"Groq {op} blocked — circuit breaker is open"
            )

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                result = fn()
                breaker.record_success()
                return result
            except Exception as e:
                if not _is_transient(e):
                    # Permanent error — don't penalise the breaker.
                    logger.warning(f"[llm.groq] {op} permanent failure: {e}")
                    raise
                last_exc = e
                wait = self.backoff_base * (2 ** attempt)
                logger.warning(
                    f"[llm.groq] {op} transient failure (attempt {attempt + 1}/"
                    f"{self.max_retries}), retrying in {wait:.1f}s: {e}"
                )
                time.sleep(wait)
        # All retries exhausted — record on breaker and surface as unavailable.
        breaker.record_failure()
        raise ProviderUnavailable(
            f"Groq {op} failed after {self.max_retries} attempts: {last_exc}"
        )


def _wrap_response(resp, model_name: str) -> LLMResponse:
    """Extract text + usage from a Groq chat-completions response."""
    text = ""
    try:
        text = resp.choices[0].message.content or ""
    except (AttributeError, IndexError):
        text = ""
    usage = LLMUsage(model=model_name)
    if hasattr(resp, "usage") and resp.usage is not None:
        usage.prompt_tokens = getattr(resp.usage, "prompt_tokens", 0) or 0
        usage.completion_tokens = getattr(resp.usage, "completion_tokens", 0) or 0
        usage.total_tokens = getattr(resp.usage, "total_tokens", 0) or 0
    return LLMResponse(text=text, usage=usage)
