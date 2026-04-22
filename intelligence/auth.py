"""Agent authentication — logs in as an agent user to obtain a bearer JWT.

Each agent declares a `credentials_key` on its definition (e.g. "scout_agent").
This module reads `{key}_username` / `{key}_password` from config.Settings,
POSTs to /api/v1/mobile/auth/login, and returns the access token string.

The token is passed into ToolContext so every tool call routes through RBAC
under the agent's own identity.
"""
import json
import logging
from typing import Optional

import config
from intelligence.transport.internal_api import call_internal_api


logger = logging.getLogger(__name__)


LOGIN_PATH = "/api/v1/mobile/auth/login"


class AgentAuthError(Exception):
    pass


def _resolve_credentials(credentials_key: str) -> tuple[str, str]:
    """Read username/password off config.Settings by the agent's key."""
    settings = config.Settings()
    username = getattr(settings, f"{credentials_key}_username", None)
    password = getattr(settings, f"{credentials_key}_password", None)
    if not username or not password:
        raise AgentAuthError(
            f"Missing credentials for agent key {credentials_key!r}. "
            f"Expected {credentials_key}_username and {credentials_key}_password in config."
        )
    return username, password


async def login_agent(credentials_key: str) -> str:
    """Log in as the agent user and return the JWT access token.

    Uses the mobile login endpoint because it returns tokens in the response
    body (no cookie/CSRF dance). Any non-200 response raises AgentAuthError.
    """
    username, password = _resolve_credentials(credentials_key)
    status, text = await call_internal_api(
        "POST",
        LOGIN_PATH,
        body={"username": username, "password": password},
    )
    if status != 200:
        raise AgentAuthError(
            f"Agent login failed for key {credentials_key!r}: "
            f"HTTP {status}: {text[:300]}"
        )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AgentAuthError(f"Login response was not JSON: {exc}") from exc

    # Response shape: {"data": {"auth": {...}, "token": {"access_token": "...", ...}, "refresh_token": {...}}}
    token_obj = (payload.get("data") or {}).get("token") or {}
    access_token = token_obj.get("access_token")
    if not access_token:
        raise AgentAuthError(
            f"Login response missing data.token.access_token: {text[:300]}"
        )
    return access_token


async def login_agent_with_user_id(credentials_key: str) -> tuple[str, Optional[int]]:
    """Log in and return (access_token, auth_user_id).

    auth_user_id is the User.id of the agent's user record, used to populate
    AgentSession.AgentUserId for audit attribution.
    """
    username, password = _resolve_credentials(credentials_key)
    status, text = await call_internal_api(
        "POST",
        LOGIN_PATH,
        body={"username": username, "password": password},
    )
    if status != 200:
        raise AgentAuthError(
            f"Agent login failed for key {credentials_key!r}: "
            f"HTTP {status}: {text[:300]}"
        )
    payload = json.loads(text)
    data = payload.get("data") or {}
    token_obj = data.get("token") or {}
    auth_obj = data.get("auth") or {}
    access_token = token_obj.get("access_token")
    if not access_token:
        raise AgentAuthError("Login response missing access_token")
    # auth.user_id is the FK to the User table
    auth_user_id = auth_obj.get("user_id")
    return access_token, auth_user_id
