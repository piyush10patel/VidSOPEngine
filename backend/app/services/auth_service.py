"""Authentication service for user management and JWT handling."""
from datetime import datetime, timedelta
import hashlib
import secrets
from typing import Optional

import bcrypt
from jose import jwt, JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import (
    InvalidCredentialsError,
    InvalidTokenError,
    AppException,
)
from app.models.user import User
from app.models.auth_session import AuthSession


class EmailAlreadyExistsError(AppException):
    """Raised when attempting to register with an existing email."""

    def __init__(self, email: str):
        super().__init__(
            error_code="EMAIL_ALREADY_EXISTS",
            message=f"A user with email '{email}' already exists",
            status_code=400,
            details={"email": email}
        )


class UserNotFoundError(AppException):
    """Raised when a user is not found."""

    def __init__(self, user_id: str):
        super().__init__(
            error_code="USER_NOT_FOUND",
            message=f"User with ID '{user_id}' was not found",
            status_code=404,
            details={"user_id": user_id}
        )


class AuthService:
    """Service for authentication operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def hash_password(password: str) -> str:
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))

    @staticmethod
    def is_valid_bcrypt_hash(password_hash: str) -> bool:
        try:
            if not password_hash or len(password_hash) != 60:
                return False
            return password_hash.startswith(('$2a$', '$2b$', '$2y$'))
        except Exception:
            return False

    @staticmethod
    def create_access_token(user_id: str, email: str) -> tuple[str, datetime]:
        now = datetime.utcnow()
        expires = now + timedelta(minutes=settings.jwt_expiration_minutes)
        payload = {"sub": user_id, "email": email, "iat": now, "exp": expires}
        token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
        return token, expires

    @staticmethod
    def hash_refresh_token(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def create_refresh_token() -> str:
        return secrets.token_urlsafe(48)

    @staticmethod
    def decode_token(token: str) -> dict:
        try:
            return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        except JWTError as e:
            raise InvalidTokenError(f"Invalid or expired token: {str(e)}")

    async def get_user_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_user_by_id(self, user_id: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def register(self, email: str, password: str) -> User:
        existing_user = await self.get_user_by_email(email)
        if existing_user:
            raise EmailAlreadyExistsError(email)

        user = User(email=email, password_hash=self.hash_password(password))
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def authenticate(self, email: str, password: str) -> User:
        user = await self.get_user_by_email(email)
        if not user or not getattr(user, "active", True):
            raise InvalidCredentialsError()
        if not self.verify_password(password, user.password_hash):
            raise InvalidCredentialsError()
        return user

    async def login(self, email: str, password: str) -> tuple[str, int, User]:
        user = await self.authenticate(email, password)
        token, _ = self.create_access_token(user.id, user.email)
        expires_in = settings.jwt_expiration_minutes * 60
        return token, expires_in, user

    async def create_session(
        self,
        user: User,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> tuple[str, AuthSession]:
        raw = self.create_refresh_token()
        session = AuthSession(
            user_id=user.id,
            refresh_token_hash=self.hash_refresh_token(raw),
            user_agent=(user_agent or "")[:255] or None,
            ip_address=(ip_address or "")[:64] or None,
            expires_at=datetime.utcnow() + timedelta(days=settings.refresh_token_expiration_days),
        )
        self.db.add(session)
        await self.db.commit()
        await self.db.refresh(session)
        return raw, session

    async def refresh_access_token(self, refresh_token: str) -> tuple[str, int, User, str]:
        token_hash = self.hash_refresh_token(refresh_token)
        result = await self.db.execute(
            select(AuthSession).where(
                AuthSession.refresh_token_hash == token_hash,
                AuthSession.revoked_at.is_(None),
            )
        )
        session = result.scalar_one_or_none()
        if not session or session.expires_at < datetime.utcnow():
            raise InvalidTokenError("Refresh session is invalid or expired")

        user = await self.get_user_by_id(session.user_id)
        if not user or not getattr(user, "active", True):
            raise InvalidTokenError("User is inactive or missing")

        new_refresh = self.create_refresh_token()
        session.refresh_token_hash = self.hash_refresh_token(new_refresh)
        session.expires_at = datetime.utcnow() + timedelta(days=settings.refresh_token_expiration_days)
        token, _ = self.create_access_token(user.id, user.email)
        await self.db.commit()
        expires_in = settings.jwt_expiration_minutes * 60
        return token, expires_in, user, new_refresh

    async def revoke_session(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        token_hash = self.hash_refresh_token(refresh_token)
        result = await self.db.execute(
            select(AuthSession).where(AuthSession.refresh_token_hash == token_hash)
        )
        session = result.scalar_one_or_none()
        if session and not session.revoked_at:
            session.revoked_at = datetime.utcnow()
            await self.db.commit()

    async def get_current_user(self, token: str) -> User:
        payload = self.decode_token(token)
        user_id = payload.get("sub")
        if not user_id:
            raise InvalidTokenError("Token missing user ID")

        user = await self.get_user_by_id(user_id)
        if not user:
            raise UserNotFoundError(user_id)
        if not getattr(user, "active", True):
            raise InvalidTokenError("User is inactive")
        return user
