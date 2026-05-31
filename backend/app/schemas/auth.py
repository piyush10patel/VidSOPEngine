"""Pydantic schemas for authentication-related API operations."""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, Field, field_serializer

from app.schemas._datetime import utc_iso


class UserCreate(BaseModel):
    """Schema for user registration request."""
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters")


class UserResponse(BaseModel):
    """Response schema for user data."""
    id: str
    email: str
    created_at: datetime
    role: str = "admin"
    active: bool = True

    @field_serializer("created_at")
    def _ser_dt(self, v):
        return utc_iso(v)

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    """Schema for login request."""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    """Response schema for successful authentication."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds
    user: Optional[UserResponse] = None


class TokenPayload(BaseModel):
    """Schema for JWT token payload."""
    sub: str  # user_id
    email: str
    exp: datetime
    iat: datetime
