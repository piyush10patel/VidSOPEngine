"""Per-user monthly token budget — MLOps cost guardrail.

Goal: prevent a single user (or a runaway bug) from burning the
operator's entire LLM budget. The check is intentionally simple:

* Each User row carries ``tokens_used_this_period`` and
  ``period_started_at``.
* :func:`check_and_reset` rolls the counter to zero whenever the period
  has crossed into a new UTC month — no separate scheduler is needed.
* :func:`assert_within_budget` raises a 429 when a new generation would
  exceed ``settings.token_budget_monthly_default``. A budget of 0 means
  unlimited (the default for the portfolio deploy) — the counter is
  still recorded so the operator can see usage in
  ``GET /auth/me/usage``.
* :func:`record_tokens` is called after each generation to advance the
  counter. Estimates are fine — we don't need per-call token accounting
  for budget enforcement, just a representative running total.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import AppException, ErrorCodes
from app.models.user import User

# Conservative estimate for a single SOP generation. Real number depends
# heavily on transcript length and frame count — see
# generation_metadata.timings in any persisted SOP for actuals. Used only
# to decide whether starting a new generation would blow the budget.
ESTIMATED_TOKENS_PER_GENERATION = 8_000


class BudgetExceededError(AppException):
    """Raised when a user has consumed their monthly token allotment."""

    def __init__(self, user_id: str, used: int, budget: int):
        super().__init__(
            error_code=ErrorCodes.QUOTA_EXCEEDED,
            message=(
                f"Monthly token budget exceeded ({used:,} / {budget:,} used). "
                f"Counter resets on the 1st of next month UTC."
            ),
            status_code=429,
            details={"user_id": user_id, "used": used, "budget": budget},
        )


def _same_period(at: datetime, now: datetime) -> bool:
    """True iff ``at`` falls in the same UTC year+month as ``now``."""
    return at.year == now.year and at.month == now.month


async def check_and_reset(db: AsyncSession, user: User) -> User:
    """Reset the user's counter if we've crossed into a new UTC month.

    Returns the (possibly updated) User object. Safe to call before
    every read or write of the counter.
    """
    now = datetime.utcnow()
    if user.period_started_at is None or not _same_period(user.period_started_at, now):
        user.tokens_used_this_period = 0
        user.period_started_at = now
        await db.commit()
        await db.refresh(user)
    return user


async def assert_within_budget(
    db: AsyncSession,
    user: User,
    additional_tokens: int = ESTIMATED_TOKENS_PER_GENERATION,
) -> None:
    """Raise 429 when ``user`` cannot afford ``additional_tokens`` more.

    A budget of 0 means unlimited. The counter is still kept up to date
    so the operator can monitor usage.
    """
    budget = settings.token_budget_monthly_default
    if budget <= 0:
        return  # unlimited
    await check_and_reset(db, user)
    if user.tokens_used_this_period + additional_tokens > budget:
        raise BudgetExceededError(
            user_id=user.id,
            used=user.tokens_used_this_period,
            budget=budget,
        )


async def record_tokens(db: AsyncSession, user: User, tokens: int) -> None:
    """Add ``tokens`` to the user's running monthly counter."""
    if tokens <= 0:
        return
    await check_and_reset(db, user)
    user.tokens_used_this_period = (user.tokens_used_this_period or 0) + tokens
    await db.commit()


def usage_snapshot(user: User) -> dict:
    """Compact dict for serialising at ``GET /auth/me/usage``."""
    budget = settings.token_budget_monthly_default
    used = user.tokens_used_this_period or 0
    remaining = max(0, budget - used) if budget > 0 else None
    return {
        "used": used,
        "budget": budget,
        "remaining": remaining,
        "unlimited": budget <= 0,
        "period_started_at": (
            user.period_started_at.isoformat() if user.period_started_at else None
        ),
    }
