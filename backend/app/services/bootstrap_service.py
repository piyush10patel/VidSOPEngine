"""Startup bootstrap helpers for platform administration."""
from __future__ import annotations

from app.core.config import settings
from app.core.logging import logger
from app.models.base import get_async_session_factory
from app.models.user import User
from app.services.auth_service import AuthService


async def bootstrap_superadmin() -> None:
    """Promote or create the configured platform superadmin account.

    This is intentionally environment-driven instead of exposed as a public API.
    Set SUPERADMIN_EMAIL in Render. If the account already exists, it is promoted
    to superadmin. If it does not exist, SUPERADMIN_PASSWORD must be provided.
    """
    email = (settings.superadmin_email or "").strip().lower()
    if not email:
        return

    AsyncSessionLocal = get_async_session_factory()
    async with AsyncSessionLocal() as db:
        auth = AuthService(db)
        user = await auth.get_user_by_email(email)
        password = settings.superadmin_password or ""

        if user:
            changed = False
            if (user.role or "").lower() != "superadmin":
                user.role = "superadmin"
                changed = True
            if not user.active:
                user.active = True
                changed = True
            if settings.superadmin_reset_password_on_startup:
                if len(password) < 8:
                    logger.warning(
                        "[bootstrap] SUPERADMIN_RESET_PASSWORD_ON_STARTUP=true "
                        "but SUPERADMIN_PASSWORD is missing or too short"
                    )
                else:
                    user.password_hash = auth.hash_password(password)
                    changed = True
            if changed:
                await db.commit()
                logger.info("[bootstrap] superadmin account promoted: %s", email)
            else:
                logger.info("[bootstrap] superadmin account already ready: %s", email)
            return

        if len(password) < 8:
            logger.warning(
                "[bootstrap] SUPERADMIN_EMAIL=%s set but account does not exist "
                "and SUPERADMIN_PASSWORD is missing or too short",
                email,
            )
            return

        user = User(
            email=email,
            password_hash=auth.hash_password(password),
            role="superadmin",
            active=True,
        )
        db.add(user)
        await db.commit()
        logger.info("[bootstrap] superadmin account created: %s", email)
