"""LLM response cache.

Two backends, picked automatically:
  - Upstash Redis when REDIS_URL is set (production / shared cache)
  - In-memory dict otherwise (dev / unit tests)

Cache keys are SHA-256 of (operation + model + prompt + image-hash). Same
inputs → same key → cache hit. The cache is read-through: callers ask the
cache first, fall through to the provider on miss, and write back on
success.

Used today only by the vision call path (per-frame analysis is the most
expensive AND most cacheable — same frame from a re-uploaded video, same
result). Transcription cache lives in the `transcripts` table already
(checked via `has_transcript()` upstream); a redundant Redis cache here
would only help across users for the same audio, which doesn't happen.

TTL defaults to 30 days. JSON-serialised LLMResponse.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Optional

from app.core.config import settings
from app.services.llm.provider import LLMResponse, LLMUsage

logger = logging.getLogger(__name__)


def _key(*parts: str) -> str:
    """SHA-256 over the parts. Stable across processes, hex string."""
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8", errors="ignore"))
        h.update(b"\x00")  # separator so 'a'+'bc' != 'ab'+'c'
    return h.hexdigest()


def vision_cache_key(prompt: str, image_b64: str, model: str) -> str:
    """Cache key for a vision call. Includes model so prompt re-engineering
    on a different model doesn't return stale results."""
    image_hash = hashlib.sha256(image_b64.encode("ascii", errors="ignore")).hexdigest()
    return _key("vision", model, prompt, image_hash)


# ----------------------------------------------------------------------
# Backends
# ----------------------------------------------------------------------


class _MemoryCache:
    """In-memory dict with TTL. Single-process; fine for dev."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}  # key → (expires_at, json_blob)

    def get(self, key: str) -> Optional[str]:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, blob = entry
        if expires_at < time.time():
            self._data.pop(key, None)
            return None
        return blob

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self._data[key] = (time.time() + ttl_seconds, value)


class _RedisCache:
    """Upstash Redis backed. Lazy-imported."""

    def __init__(self) -> None:
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import redis  # type: ignore[import-not-found]
            self._client = redis.Redis.from_url(
                settings.redis_url,
                socket_timeout=2,
                socket_connect_timeout=2,
                decode_responses=True,
            )
        return self._client

    def get(self, key: str) -> Optional[str]:
        try:
            return self.client.get(key)
        except Exception as e:
            logger.warning(f"[llm.cache] redis get failed: {e}")
            return None

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        try:
            self.client.setex(key, ttl_seconds, value)
        except Exception as e:
            logger.warning(f"[llm.cache] redis set failed: {e}")


_backend = None


def _get_backend():
    global _backend
    if _backend is None:
        if settings.redis_url and settings.redis_url.startswith(("redis://", "rediss://")):
            _backend = _RedisCache()
            logger.info("[llm.cache] using Redis backend")
        else:
            _backend = _MemoryCache()
            logger.info("[llm.cache] using in-memory backend (no REDIS_URL)")
    return _backend


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def get_cached_response(key: str) -> Optional[LLMResponse]:
    """Return a cached LLMResponse for `key`, or None on miss."""
    if not settings.llm_cache_enabled:
        return None
    blob = _get_backend().get(key)
    if not blob:
        return None
    try:
        d = json.loads(blob)
        return LLMResponse(
            text=d.get("text", ""),
            usage=LLMUsage(
                prompt_tokens=d.get("prompt_tokens", 0),
                completion_tokens=d.get("completion_tokens", 0),
                total_tokens=d.get("total_tokens", 0),
                model=d.get("model", ""),
            ),
        )
    except Exception as e:
        logger.warning(f"[llm.cache] failed to deserialize cache hit: {e}")
        return None


def put_cached_response(key: str, resp: LLMResponse) -> None:
    """Persist an LLMResponse under `key` for the configured TTL."""
    if not settings.llm_cache_enabled:
        return
    blob = json.dumps({
        "text": resp.text,
        "prompt_tokens": resp.usage.prompt_tokens,
        "completion_tokens": resp.usage.completion_tokens,
        "total_tokens": resp.usage.total_tokens,
        "model": resp.usage.model,
    })
    _get_backend().set(key, blob, settings.llm_cache_ttl_seconds)
