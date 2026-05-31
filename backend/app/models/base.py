"""Base model and database configuration."""
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncEngine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase


class Base(AsyncAttrs, DeclarativeBase):
    """Base class for all database models."""
    pass


# Module-level singletons. Building these on every request (the prior
# behavior) created a fresh asyncpg connection pool per HTTP call —
# each request paid the ~50-150ms cost of opening a new TLS+auth
# connection to Neon, and connections were never reused across
# requests. With caching, every request shares the same engine + pool.
_engine: "AsyncEngine | None" = None
_session_factory: "async_sessionmaker | None" = None


def _normalize_asyncpg_url(url: str) -> tuple[str, dict]:
    """Translate libpq ?sslmode=... into asyncpg connect_args.

    Neon (and most Postgres providers) hand out URLs ending in
    ?sslmode=require. asyncpg does not accept sslmode as a query parameter
    and fails with `TypeError: connect() got an unexpected keyword
    argument 'sslmode'`. Strip it from the URL and pass ssl="require" via
    connect_args instead. Same translation applied for ?ssl= aliases.
    """
    if "+asyncpg" not in url:
        return url, {}
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    parts = urlsplit(url)
    q = parse_qsl(parts.query)
    ssl_value = None
    rest = []
    for k, v in q:
        if k in ("sslmode", "ssl"):
            ssl_value = ssl_value or v
        else:
            rest.append((k, v))
    if ssl_value is None:
        return url, {}
    new_url = urlunsplit(parts._replace(query=urlencode(rest)))
    # asyncpg accepts: "disable" | "allow" | "prefer" | "require" | "verify-ca" | "verify-full"
    return new_url, {"ssl": ssl_value}


def _build_engine() -> "AsyncEngine":
    """Construct the async engine with dialect-appropriate pool settings.

    Postgres (Neon): real connection pool with pre-ping (detects
    connections the cloud provider has silently dropped) and a 30-min
    recycle (Neon idles connections out after ~5 min on free, 15 min
    on paid — recycle keeps them fresh). Sizes assume one uvicorn
    process; if you switch to gunicorn -w N, divide pool_size by N.

    SQLite (dev): SQLAlchemy already picks an appropriate pool for
    aiosqlite. Passing pool_size/max_overflow to it is harmless but
    meaningless, so we skip them.
    """
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.core.config import settings

    url, connect_args = _normalize_asyncpg_url(settings.database_url)
    kwargs: dict = {"echo": settings.debug}
    if not url.startswith("sqlite"):
        kwargs.update(
            pool_size=20,
            max_overflow=10,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        if connect_args:
            kwargs["connect_args"] = connect_args
    return create_async_engine(url, **kwargs)


def get_async_engine() -> "AsyncEngine":
    """Return the cached process-wide async engine (lazy first-build)."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_async_session_factory() -> "async_sessionmaker":
    """Return the cached session factory bound to the shared engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_async_engine(), expire_on_commit=False)
    return _session_factory


async def get_db():
    """Dependency to get database session."""
    AsyncSessionLocal = get_async_session_factory()
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def dispose_engine() -> None:
    """Close the engine + drain its pool. Called from lifespan shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def init_db():
    """Initialize database tables and run lightweight column migrations.

    IMPORTANT - transaction layout:
      - create_all() runs in its OWN transaction so it commits cleanly.
      - Each ALTER TABLE migration runs in its OWN transaction. On Postgres,
        any error inside a transaction aborts the WHOLE transaction (so a
        try/except around an aborted statement does NOT let following
        statements succeed). Per-statement transactions sidestep that.
        SQLite tolerates this layout too.
    """
    import logging
    from sqlalchemy import text

    # Force-import all model classes onto Base.metadata before create_all.
    # If main.py's import chain somehow misses one, this is the safety net.
    import app.models  # noqa: F401

    log = logging.getLogger(__name__)
    engine = get_async_engine()

    # ------------------------------------------------------------------
    # 1. Create tables - own transaction.
    # ------------------------------------------------------------------
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info(
        "init_db: created/verified tables: %s",
        sorted(Base.metadata.tables.keys()),
    )

    # ------------------------------------------------------------------
    # 2. Per-migration transactions - failure of one cannot poison others.
    # ------------------------------------------------------------------
    # Each migration runs in its own transaction. Statements that target
    # already-existing columns silently fail through the except block, so
    # this list is safe to extend without coordinating with existing prod.
    migrations: list[str] = [
        "ALTER TABLE videos ADD COLUMN video_type VARCHAR(20) NOT NULL DEFAULT 'physical'",
        "ALTER TABLE videos ADD COLUMN pipeline_complexity VARCHAR(30) NOT NULL DEFAULT 'auto'",
        "ALTER TABLE videos ADD COLUMN pipeline_complexity_confidence FLOAT",
        "ALTER TABLE videos ADD COLUMN operational_context VARCHAR(500)",
        "ALTER TABLE videos ADD COLUMN operational_mode VARCHAR(40)",
        "ALTER TABLE videos ADD COLUMN content_hash VARCHAR(64)",
        "ALTER TABLE videos ADD COLUMN artifact_generation_status JSON",
        "ALTER TABLE videos ADD COLUMN cleanup_eligible BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE videos ADD COLUMN cleanup_completed_at TIMESTAMP",
        "ALTER TABLE videos ADD COLUMN cleanup_last_error VARCHAR(500)",
        "ALTER TABLE videos ADD COLUMN cleanup_attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE sops ADD COLUMN is_finalized BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE sops ADD COLUMN folder_id VARCHAR(36)",
        "ALTER TABLE sops ADD COLUMN category VARCHAR(100) NOT NULL DEFAULT 'Uncategorized'",
        "ALTER TABLE sops ADD COLUMN tags_json JSON",
        "ALTER TABLE sops ADD COLUMN archived BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE sops ADD COLUMN created_by VARCHAR(36)",
        "ALTER TABLE sops ADD COLUMN updated_by VARCHAR(36)",
        "ALTER TABLE sops ADD COLUMN visibility_scope VARCHAR(30) NOT NULL DEFAULT 'private'",
        "ALTER TABLE sops ADD COLUMN allowed_role_min VARCHAR(20) NOT NULL DEFAULT 'manager'",
        "ALTER TABLE sops ADD COLUMN shared_with_users_json JSON",
        "ALTER TABLE sops ADD COLUMN simplified_steps_json JSON",
        "ALTER TABLE sops ADD COLUMN source_type VARCHAR(20) NOT NULL DEFAULT 'ai_generated'",
        "ALTER TABLE sops ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'draft'",
        "ALTER TABLE sops ADD COLUMN last_reviewed_at TIMESTAMP",
        "ALTER TABLE sops ADD COLUMN estimated_duration_minutes INTEGER",
        "ALTER TABLE users ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'admin'",
        "ALTER TABLE users ADD COLUMN active BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN pinned_examples_json JSON",
    ]

    for stmt in migrations:
        try:
            async with engine.begin() as conn:
                await conn.execute(text(stmt))
        except Exception:
            # Most failures are 'column already exists' - fully expected on
            # rerun. The per-statement transaction means the next migration
            # gets a fresh transaction regardless.
            pass
