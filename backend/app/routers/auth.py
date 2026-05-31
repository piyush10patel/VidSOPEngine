"""Authentication router for user registration and login."""
import logging
import secrets as _secrets
from datetime import datetime as _dt, timedelta as _td
from typing import Callable, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel as _BM, EmailStr as _Email, Field as _Field
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import get_db
from app.models.user import User
from app.services.auth_service import AuthService
from app.schemas.auth import (
    UserCreate,
    UserResponse,
    LoginRequest,
    TokenResponse,
)
from app.core.errors import InvalidTokenError
from app.core.config import settings


router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)
log = logging.getLogger(__name__)


def user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        created_at=user.created_at,
        role=getattr(user, "role", "admin") or "admin",
        active=getattr(user, "active", True),
    )


def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.refresh_token_expiration_days * 24 * 60 * 60,
        httponly=True,
        secure=settings.auth_cookie_secure or settings.app_env == "production",
        samesite=settings.auth_cookie_samesite,
        path="/",
    )


def clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=settings.auth_cookie_name, path="/")


def get_auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(db)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    auth_service: AuthService = Depends(get_auth_service)
) -> User:
    if not credentials:
        raise InvalidTokenError("No authentication token provided")
    return await auth_service.get_current_user(credentials.credentials)


def require_role(*roles: str) -> Callable:
    allowed = {role.lower() for role in roles}

    async def _dependency(current_user: User = Depends(get_current_user)) -> User:
        role = (getattr(current_user, "role", "staff") or "staff").lower()
        if role not in allowed:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return current_user

    return _dependency


def require_admin() -> Callable:
    return require_role("admin", "superadmin")


def require_manager_or_admin() -> Callable:
    return require_role("admin", "manager", "superadmin")


def require_superadmin() -> Callable:
    return require_role("superadmin")


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    auth_service: AuthService = Depends(get_auth_service)
) -> Optional[User]:
    if not credentials:
        return None
    return await auth_service.get_current_user(credentials.credentials)


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    user_data: UserCreate,
    auth_service: AuthService = Depends(get_auth_service)
) -> UserResponse:
    """Register a new user with email and password."""
    user = await auth_service.register(email=user_data.email, password=user_data.password)
    return user_to_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(
    credentials: LoginRequest,
    response: Response,
    request: Request,
    auth_service: AuthService = Depends(get_auth_service)
) -> TokenResponse:
    token, expires_in, user = await auth_service.login(
        email=credentials.email,
        password=credentials.password
    )
    refresh_token, _ = await auth_service.create_session(
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    set_refresh_cookie(response, refresh_token)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=user_to_response(user),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_session(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None, alias=settings.auth_cookie_name),
    auth_service: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    if not refresh_token:
        raise InvalidTokenError("No refresh session provided")
    token, expires_in, user, new_refresh = await auth_service.refresh_access_token(refresh_token)
    set_refresh_cookie(response, new_refresh)
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=user_to_response(user),
    )


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None, alias=settings.auth_cookie_name),
    auth_service: AuthService = Depends(get_auth_service),
) -> None:
    await auth_service.revoke_session(refresh_token)
    clear_refresh_cookie(response)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return user_to_response(current_user)


@router.get("/me/usage")
async def get_my_usage(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Monthly token-budget snapshot for the calling user.

    Shape: ``{used, budget, remaining, unlimited, period_started_at}``.
    """
    from app.services.budget_service import check_and_reset, usage_snapshot
    user = await check_and_reset(db, current_user)
    return usage_snapshot(user)


@router.get("/languages")
async def list_languages() -> dict:
    """Public — registry of platform-supported languages."""
    from app.core.languages import registry_items
    return {
        "languages": [
            {
                "code": code,
                "label": entry["label"],
                "native": entry["native"],
                "iso": entry["iso"],
            }
            for code, entry in registry_items()
        ],
    }


# -----------------------------------------------------------------------------
# Forgot-password / OTP flow
# -----------------------------------------------------------------------------

class ForgotPasswordRequest(_BM):
    email: _Email


class VerifyOtpRequest(_BM):
    email: _Email
    code: str = _Field(..., min_length=4, max_length=10)


class ResetPasswordRequest(_BM):
    reset_token: str
    new_password: str = _Field(..., min_length=8, max_length=128)


_OTP_TTL_MINUTES = 10
_OTP_MAX_ATTEMPTS = 5
_OTP_MIN_INTERVAL_SECONDS = 60
_RESET_TOKEN_TTL_MINUTES = 15


def _generate_otp() -> str:
    return f"{_secrets.randbelow(1_000_000):06d}"


def _hash_otp(code: str) -> str:
    return AuthService.hash_password(code)


def _verify_otp(code: str, code_hash: str) -> bool:
    return AuthService.verify_password(code, code_hash)


def _issue_reset_token(user_id: str) -> str:
    import jwt as _jwt
    now = _dt.utcnow()
    payload = {
        "sub": user_id,
        "purpose": "password_reset",
        "iat": now,
        "exp": now + _td(minutes=_RESET_TOKEN_TTL_MINUTES),
    }
    return _jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _decode_reset_token(token: str) -> str | None:
    import jwt as _jwt
    try:
        payload = _jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except Exception:
        return None
    if payload.get("purpose") != "password_reset":
        return None
    return payload.get("sub")


@router.post("/forgot-password")
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Issue a 6-digit OTP for password reset.

    Generic 200 response on miss so the API can't be used to enumerate
    accounts. In this minimal portfolio build there is no SMTP integration;
    the OTP is logged and (in dev) returned in the response.
    """
    from app.models.password_reset_otp import PasswordResetOtp

    email = payload.email.lower().strip()
    generic = {"message": "If an account exists for that email, a code is on its way."}

    user = (await db.execute(_select(User).where(User.email == email))).scalar_one_or_none()
    if not user or not user.active:
        return generic

    cutoff = _dt.utcnow() - _td(seconds=_OTP_MIN_INTERVAL_SECONDS)
    recent = (await db.execute(
        _select(PasswordResetOtp)
        .where(PasswordResetOtp.user_id == user.id)
        .where(PasswordResetOtp.created_at >= cutoff)
        .order_by(PasswordResetOtp.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if recent:
        return {**generic, "retry_after_seconds": _OTP_MIN_INTERVAL_SECONDS}

    code = _generate_otp()
    db.add(PasswordResetOtp(
        user_id=user.id,
        code_hash=_hash_otp(code),
        expires_at=_dt.utcnow() + _td(minutes=_OTP_TTL_MINUTES),
        request_ip=(request.client.host if request.client else None),
    ))
    await db.commit()
    log.info("[forgot_password] OTP issued for %s (dev-only, no mailer): %s", email, code)

    out = dict(generic)
    if settings.auth_expose_otp_in_dev:
        out["dev_otp"] = code
    return out


@router.post("/verify-otp")
async def verify_otp(
    payload: VerifyOtpRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.models.password_reset_otp import PasswordResetOtp

    email = payload.email.lower().strip()
    user = (await db.execute(_select(User).where(User.email == email))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid code")

    row = (await db.execute(
        _select(PasswordResetOtp)
        .where(PasswordResetOtp.user_id == user.id)
        .where(PasswordResetOtp.consumed_at.is_(None))
        .order_by(PasswordResetOtp.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    if row.expires_at < _dt.utcnow():
        raise HTTPException(status_code=400, detail="Invalid or expired code")
    if (row.attempts or 0) >= _OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many attempts. Request a new code.")

    if not _verify_otp(payload.code.strip(), row.code_hash):
        row.attempts = (row.attempts or 0) + 1
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired code")

    row.consumed_at = _dt.utcnow()
    await db.commit()

    token = _issue_reset_token(user.id)
    return {"reset_token": token, "expires_in_minutes": _RESET_TOKEN_TTL_MINUTES}


@router.post("/reset-password")
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
    auth_service: AuthService = Depends(get_auth_service),
) -> dict:
    user_id = _decode_reset_token(payload.reset_token)
    if not user_id:
        raise HTTPException(status_code=400, detail="Reset token is invalid or expired")
    user = await db.get(User, user_id)
    if not user or not user.active:
        raise HTTPException(status_code=400, detail="Reset token is invalid or expired")
    user.password_hash = auth_service.hash_password(payload.new_password)
    await db.commit()
    return {"ok": True, "email": user.email}
