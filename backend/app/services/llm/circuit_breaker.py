"""In-process circuit breaker for the LLM provider.

State machine:
    closed → open       after N consecutive failures (default 5)
    open → half_open    after `cooldown` seconds elapse
    half_open → closed  on first successful call
    half_open → open    on first failure (resets cooldown)

When OPEN, calls fail fast with ProviderUnavailable so the caller can
fall back instead of waiting for retries to exhaust. Single in-process
instance per worker; no Redis / shared state required (each pod opens
the breaker independently — acceptable at our scale).

Thread-safe: a single lock guards state transitions. The lock is held
only briefly (no IO inside the critical section).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from app.services.llm.provider import ProviderUnavailable

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """Three-state breaker. Defaults tuned for Groq's typical SLO."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        name: str = "llm",
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.name = name
        self._state = "closed"  # closed | open | half_open
        self._consecutive_failures = 0
        self._opened_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        # Lazy state transition: open → half_open after cooldown.
        with self._lock:
            if self._state == "open" and time.monotonic() - self._opened_at >= self.cooldown_seconds:
                self._state = "half_open"
                logger.info(f"[breaker:{self.name}] open → half_open")
        return self._state

    def record_success(self) -> None:
        with self._lock:
            if self._state == "half_open":
                logger.info(f"[breaker:{self.name}] half_open → closed (recovered)")
                self._state = "closed"
            self._consecutive_failures = 0

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._state == "half_open":
                # Probe failed; reopen for another cooldown.
                logger.warning(f"[breaker:{self.name}] half_open → open (probe failed)")
                self._state = "open"
                self._opened_at = time.monotonic()
            elif self._state == "closed" and self._consecutive_failures >= self.failure_threshold:
                logger.warning(
                    f"[breaker:{self.name}] closed → open ({self._consecutive_failures} consecutive failures)"
                )
                self._state = "open"
                self._opened_at = time.monotonic()

    def call(self, fn: Callable, *args, **kwargs):
        """Execute fn through the breaker. Raises ProviderUnavailable when open."""
        state = self.state  # triggers cooldown check
        if state == "open":
            raise ProviderUnavailable(
                f"Circuit breaker '{self.name}' is OPEN — last failure {time.monotonic() - self._opened_at:.0f}s ago"
            )
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        else:
            self.record_success()
            return result

    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "consecutive_failures": self._consecutive_failures,
            "failure_threshold": self.failure_threshold,
            "cooldown_seconds": self.cooldown_seconds,
        }


# Module-level singleton — one breaker for the LLM provider.
_breaker: CircuitBreaker | None = None


def get_breaker() -> CircuitBreaker:
    global _breaker
    if _breaker is None:
        from app.core.config import settings
        _breaker = CircuitBreaker(
            failure_threshold=settings.llm_circuit_breaker_failure_threshold,
            cooldown_seconds=settings.llm_circuit_breaker_cooldown_seconds,
            name="groq",
        )
    return _breaker
