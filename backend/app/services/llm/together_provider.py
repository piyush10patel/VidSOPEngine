"""Together AI implementation of LLMProvider for chat and vision."""
from __future__ import annotations

import logging
import time
from typing import Optional

from app.core.config import settings
from app.services.llm.model_aliases import normalize_model_name
from app.services.llm.provider import LLMResponse, LLMUsage, ProviderUnavailable

logger = logging.getLogger(__name__)


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
    return any(marker in msg for marker in _TRANSIENT_MARKERS)


class TogetherProvider:
    """Together AI provider via its OpenAI-compatible API."""

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
        if not settings.together_api_key:
            raise ProviderUnavailable("TOGETHER_API_KEY is required for Together AI calls")
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=settings.together_base_url,
                api_key=settings.together_api_key,
            )
        return self._client

    def transcribe(
        self,
        audio_path: str,
        *,
        model: Optional[str] = None,
        timeout: int = 60,
    ) -> LLMResponse:
        raise ProviderUnavailable("TogetherProvider does not implement transcription")

    def vision(
        self,
        prompt: str,
        image_b64: str,
        *,
        model: Optional[str] = None,
        timeout: int = 30,
        temperature: float = 0,
    ) -> LLMResponse:
        model_name = model or settings.vision_model

        cache_key = None
        if settings.llm_cache_enabled and temperature == 0:
            from app.services.llm.cache import (
                get_cached_response,
                vision_cache_key,
            )
            cache_key = vision_cache_key(prompt, image_b64, model_name)
            cached = get_cached_response(cache_key)
            if cached is not None:
                logger.info("[llm.together] vision cache HIT model=%s", model_name)
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
        model_name = normalize_model_name(model or settings.sop_synthesis_model)

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

    def _with_retry(self, fn, *, op: str) -> LLMResponse:
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                return fn()
            except Exception as e:
                if not _is_transient(e):
                    logger.warning("[llm.together] %s permanent failure: %s", op, e)
                    raise
                last_exc = e
                wait = self.backoff_base * (2 ** attempt)
                logger.warning(
                    "[llm.together] %s transient failure (attempt %s/%s), retrying in %.1fs: %s",
                    op,
                    attempt + 1,
                    self.max_retries,
                    wait,
                    e,
                )
                time.sleep(wait)
        raise ProviderUnavailable(
            f"Together {op} failed after {self.max_retries} attempts: {last_exc}"
        )


def _wrap_response(resp, model_name: str) -> LLMResponse:
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
