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
    """Credentials presented for authentication — NOT a place for password policy.

    Length/complexity rules belong on the models that *set* a password
    (``AuthCreate``, ``AuthSignup``, ``AdminSetCredentials``,
    ``ChangePasswordRequest.new_password``). Enforcing them here rejects the
    body before the credential check ever runs, which permanently locks out any
    account whose stored password predates or undercuts the current policy: the
    caller gets a 422 no retry can clear, not a 400 "Invalid credentials".
    It also leaks whether a password is under the policy length (422 vs 400).
    Mirrors ``ChangePasswordRequest.current_password``, which is already
    ``min_length=1`` for exactly this reason. (U-410: an 8-char floor here vs.
    the iOS client's 6-char gate locked field users out of the mobile app.)
    """

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=255)


class AuthSignup(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)
    confirm_password: str = Field(min_length=8, max_length=255)
    registration_code: str = Field(min_length=1, max_length=255)


class AuthRefreshRequest(BaseModel):
    refresh_token: Optional[str] = Field(default=None, description="Refresh token (optional when sent via cookie)")


class MobileRefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1, description="Refresh token (required for mobile clients)")


class AdminSetCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8, max_length=255)


class SwitchCompanyRequest(BaseModel):
    company_public_id: str = Field(
        min_length=36,
        max_length=36,
        description="UUID of the Company to switch to.",
    )


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=255)
    new_password: str = Field(min_length=8, max_length=255)
