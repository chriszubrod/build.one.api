"""Internal HTTP client for agents calling their own API surface.

Tools call the app's own endpoints rather than invoking services directly
so every action flows through the same stack a human user's request hits:
RBAC checks, ProcessEngine routing for writes, standardized response
envelopes, and HTTP access logs as audit trail.

A single module-level AsyncClient is kept for connection pooling. The
base URL comes from config.Settings.internal_api_base_url; default is
http://localhost:8000 for dev.
"""
from typing import Any, Optional, Tuple

import httpx

import config


_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    """Return the shared internal-API HTTP client, creating it if needed."""
    global _client
    if _client is None or _client.is_closed:
        settings = config.Settings()
        _client = httpx.AsyncClient(
            base_url=settings.internal_api_base_url,
            timeout=30.0,
        )
    return _client


async def aclose() -> None:
    """Close the shared client. Call on application shutdown if desired."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def call_internal_api(
    method: str,
    path: str,
    *,
    auth_token: Optional[str] = None,
    body: Any = None,
) -> Tuple[int, str]:
    """POST/GET/PUT/DELETE an internal API path and return (status, text).

    The caller is responsible for interpreting status codes. Tool handlers
    will typically wrap the result in ToolResult via ToolContext.call_api().
    """
    headers: dict[str, str] = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    client = _get_client()
    resp = await client.request(method, path, json=body, headers=headers or None)
    return resp.status_code, resp.text
