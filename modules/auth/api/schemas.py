# Python Standard Library Imports
from typing import List, Optional
from uuid import UUID

# Third-party Imports
from pydantic import BaseModel, Field, model_validator



class AuthCreate(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)


class AuthUpdate(BaseModel):
    row_version: str = Field(description="The row version of the auth (base64 encoded).")
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)


class AuthLogin(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)


class AuthSignup(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)
    confirm_password: str = Field(min_length=8, max_length=255)


class AuthLogoutResponse(BaseModel):
    message: str = Field(default="Logged out.")


class AuthResetRequest(BaseModel):
    tenant_id: UUID = Field(description="Tenant identifier (tid claim).")
    username: str = Field(min_length=1, max_length=255)


class AuthResetConfirmRequest(BaseModel):
    tenant_id: UUID = Field(description="Tenant identifier (tid claim).")
    token: str = Field(min_length=10, max_length=128)
    new_password: str = Field(min_length=8, max_length=255)


class AuthUpsertRequest(BaseModel):
    public_id: Optional[UUID] = Field(default=None, description="Public identifier; omitted for creates.")
    row_version: Optional[str] = Field(default=None, description="Base64 encoded rowversion required for updates.")
    username: str = Field(min_length=1, max_length=255)
    password: Optional[str] = Field(default=None, min_length=8, max_length=255)

    @model_validator(mode="after")
    def _validate_update(self) -> "AuthUpsertRequest":
        if self.public_id and not self.row_version:
            raise ValueError("row_version is required when updating an account.")
        if not self.public_id and not self.password:
            raise ValueError("password is required when creating an account.")
        return self


class AuthResponse(BaseModel):
    public_id: UUID
    username: str
    row_version: str
    row_version_hex: Optional[str]
    created_datetime: Optional[str]
    modified_datetime: Optional[str]


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    expires_in: int


class AuthSessionResponse(BaseModel):
    account: AuthResponse
    token: AuthTokenResponse


class AuthListResponse(BaseModel):
    items: List[AuthResponse]
    page: int
    size: int
    total: int


class PasswordResetResponse(BaseModel):
    reset_token: Optional[str] = Field(default=None, description="Password reset token (blank when username not found).")
    expires_datetime: Optional[str] = Field(default=None, description="ISO-8601 expiry for the reset token.")
