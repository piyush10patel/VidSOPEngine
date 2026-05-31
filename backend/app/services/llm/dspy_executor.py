"""Dedicated executor for DSPy work.

DSPy settings are process-global and can only be configured by the thread
that first configured them. The app runs blocking generation from async
routes, so all DSPy work must share one executor thread instead of the
default thread pool.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, TypeVar

T = TypeVar("T")

_DSPY_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dspy")
_DSPY_THREAD_IDS: set[int] = set()


def _run_in_dspy_thread(fn: Callable[..., T], args: tuple[Any, ...], kwargs: dict[str, Any]) -> T:
    _DSPY_THREAD_IDS.add(threading.get_ident())
    return fn(*args, **kwargs)


def in_dspy_thread() -> bool:
    return threading.get_ident() in _DSPY_THREAD_IDS


async def run_dspy_async(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run blocking DSPy work on the single DSPy executor thread."""
    if in_dspy_thread():
        return fn(*args, **kwargs)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _DSPY_EXECUTOR,
        _run_in_dspy_thread,
        fn,
        args,
        kwargs,
    )


def run_dspy_sync(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run DSPy work from synchronous callers without changing threads twice."""
    if in_dspy_thread():
        return fn(*args, **kwargs)
    future = _DSPY_EXECUTOR.submit(_run_in_dspy_thread, fn, args, kwargs)
    return future.result()
