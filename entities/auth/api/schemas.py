# Python Standard Library Imports
from typing import Optional

# Third-party Imports
from pydantic import BaseModel, Field


class AuthCreate(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)


class AuthUpdate(BaseModel):
    row_version: str = Field(description="The row version of the auth (base64 encoded).")
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)
    user_id: int = Field(description="The ID of the user.")


class AuthLogin(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)


class AuthSignup(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)
    confirm_password: str = Field(min_length=8, max_length=255)


class AuthRefreshRequest(BaseModel):
    refresh_token: Optional[str] = Field(default=None, description="Refresh token (optional when sent via cookie)")
