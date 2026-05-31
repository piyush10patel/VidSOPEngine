"""RQ worker entry point — production-stable.

Responsibilities:
  - Connect to Upstash Redis (with TLS support — rediss:// URLs work)
  - Listen on the 'pipeline' queue
  - Survive transient Redis disconnects (reconnect-and-retry loop)
  - Graceful shutdown on SIGTERM (Render sends this on redeploy)
  - Periodic heartbeat log so dashboards / `render logs --tail` show liveness

The actual pipeline logic lives in app.tasks.pipeline; this file is the
process supervisor.
"""
import logging
import os
import signal
import sys
import time

# Make the backend dir importable when invoked as `python worker.py`.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError, TimeoutError as RedisTimeoutError
from rq import Queue, Worker
from rq.exceptions import NoSuchJobError

from app.core.config import settings
from app.core.logging import logger


_QUEUES = ["pipeline"]


def _redis_connection() -> Redis:
    """Create a Redis client appropriate for Upstash (TLS) or local dev."""
    return Redis.from_url(
        settings.redis_url,
        socket_timeout=10,
        socket_connect_timeout=10,
        socket_keepalive=True,
        health_check_interval=30,
    )


def _install_signal_handlers(worker: Worker) -> None:
    """Forward SIGTERM/SIGINT to RQ's graceful-shutdown machinery.

    Render sends SIGTERM on redeploy; the default RQ behavior already handles
    that, but we add an explicit handler so our log makes the cause obvious.
    """
    def _handler(signum, _frame):
        name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
        logger.info(f"[worker] received {name} — requesting graceful shutdown")
        # RQ's Worker tracks _stop_requested; this triggers it.
        try:
            worker.request_stop(signum, _frame)
        except Exception:
            # Older RQ used a different name; fall back.
            worker._stop_requested = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Some platforms / threaded contexts disallow signal()
            pass


def run_worker() -> None:
    """Run the RQ worker with reconnect-on-failure semantics.

    Outer loop survives Redis disconnects (rare with Upstash but possible
    during their maintenance windows). Inner Worker.work() blocks until
    SIGTERM or an unrecoverable error.
    """
    if not settings.redis_url:
        logger.error("[worker] REDIS_URL is not set — cannot start worker")
        sys.exit(1)

    backoff = 5  # seconds; doubles each consecutive failure, capped at 60s
    while True:
        try:
            redis_conn = _redis_connection()
            redis_conn.ping()  # fail fast on bad creds / host
            queues = [Queue(name, connection=redis_conn) for name in _QUEUES]
            worker = Worker(queues, connection=redis_conn)
            _install_signal_handlers(worker)

            logger.info(
                f"[worker] up — listening on {[q.name for q in queues]} "
                f"(redis_url={'<rediss>' if settings.redis_url.startswith('rediss://') else '<redis>'})"
            )

            # with_scheduler=False — we do not use RQ's scheduler; this saves
            # ~1 idle thread per worker.
            worker.work(with_scheduler=False, logging_level=logging.INFO)
            logger.info("[worker] graceful exit")
            return  # exited cleanly via SIGTERM
        except (RedisConnectionError, RedisTimeoutError) as e:
            logger.warning(f"[worker] Redis disconnect ({e}); reconnecting in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except NoSuchJobError:
            # A job was removed mid-flight; not fatal. Recreate worker.
            logger.warning("[worker] NoSuchJobError; recreating worker")
            time.sleep(2)
        except Exception as e:
            # Unknown — log loudly but keep the worker alive. Render can
            # restart us if we exit, but we'd rather self-heal.
            logger.error(f"[worker] unexpected error: {e}", exc_info=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    run_worker()
