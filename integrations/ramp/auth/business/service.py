# Python Standard Library Imports
import base64
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# Third-party Imports
import httpx

# Local Imports
import config
from integrations.ramp.base.client import _parse_retry_after
from integrations.ramp.base.errors import (
    RampAuthError,
    RampRateLimitError,
    RampServerError,
    RampTimeoutError,
    RampTransportError,
)
from integrations.ramp.base.logger import get_ramp_logger
from integrations.ramp.base.retry import RetryPolicy, execute_with_retry


logger = get_ramp_logger(__name__)

TOKEN_EXPIRY_BUFFER_SECONDS = 60
DEFAULT_TOKEN_LIFETIME_SECONDS = 864000  # ~10 days per Ramp docs; fallback if omitted

# Space-separated scopes — REQUIRED on the token request (§4.1.1).
RAMP_OAUTH_SCOPES = "transactions:read users:read"

_MINT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


@dataclass
class _TokenCache:
    access_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    granted_scope: Optional[str] = None


_token_cache = _TokenCache()
_token_lock = threading.Lock()


class RampAuthService:
    """OAuth2 client-credentials token mint for Ramp (no refresh token)."""

    def __init__(self, settings: Optional[config.Settings] = None):
        self._settings = settings or config.Settings()

    def is_configured(self) -> bool:
        return bool(
            self._settings.ramp_client_id
            and self._settings.ramp_client_secret
            and self._settings.ramp_api_base_url
        )

    @property
    def requested_scopes(self) -> str:
        return RAMP_OAUTH_SCOPES

    def ensure_valid_token(self, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = self._read_fresh_cached_token()
            if cached:
                return cached

        with _token_lock:
            if not force_refresh:
                cached = self._read_fresh_cached_token()
                if cached:
                    return cached
            return self._mint_and_cache()

    @staticmethod
    def _read_fresh_cached_token() -> Optional[str]:
        token = _token_cache.access_token
        expires_at = _token_cache.expires_at
        if token and expires_at and datetime.now(timezone.utc) < expires_at:
            return token
        return None

    def _token_url(self) -> str:
        base = (self._settings.ramp_api_base_url or "https://api.ramp.com").rstrip("/")
        return f"{base}/developer/v1/token"

    def _mint_and_cache(self) -> str:
        if not self.is_configured():
            raise RampAuthError(
                "Ramp credentials are not configured (need ramp_client_id + ramp_client_secret)"
            )

        client_id = self._settings.ramp_client_id or ""
        client_secret = self._settings.ramp_client_secret or ""
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")

        form = {
            "grant_type": "client_credentials",
            "scope": self.requested_scopes,
        }

        def _do_mint() -> Dict[str, Any]:
            try:
                response = httpx.post(
                    self._token_url(),
                    data=form,
                    headers={
                        "Authorization": f"Basic {basic}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    timeout=_MINT_TIMEOUT,
                )
            except httpx.TimeoutException as error:
                raise RampTimeoutError(
                    f"Ramp token mint timed out: {error}",
                    request_method="POST",
                    request_path="/developer/v1/token",
                ) from error
            except httpx.TransportError as error:
                raise RampTransportError(
                    f"Ramp token mint transport failure: {error}",
                    request_method="POST",
                    request_path="/developer/v1/token",
                ) from error

            retry_after_seconds = _parse_retry_after(
                response.headers.get("Retry-After") or response.headers.get("retry-after")
            )

            if response.status_code == 429:
                raise RampRateLimitError(
                    "Ramp token endpoint rate-limited the mint request",
                    http_status=429,
                    request_method="POST",
                    request_path="/developer/v1/token",
                    retry_after_seconds=retry_after_seconds,
                )
            if response.status_code in (400, 401):
                raise RampAuthError(
                    f"Ramp token mint rejected: HTTP {response.status_code}",
                    http_status=response.status_code,
                    detail=response.text[:500] if response.text else None,
                    request_method="POST",
                    request_path="/developer/v1/token",
                )
            if response.status_code >= 500:
                raise RampServerError(
                    f"Ramp token endpoint returned HTTP {response.status_code}",
                    http_status=response.status_code,
                    request_method="POST",
                    request_path="/developer/v1/token",
                    retry_after_seconds=retry_after_seconds,
                )
            if response.status_code != 200:
                raise RampAuthError(
                    f"Ramp token mint returned unexpected HTTP {response.status_code}",
                    http_status=response.status_code,
                    request_method="POST",
                    request_path="/developer/v1/token",
                )

            payload = response.json()
            if not isinstance(payload, dict):
                raise RampAuthError("Ramp token mint returned non-object JSON")
            return payload

        payload = execute_with_retry(
            _do_mint,
            RetryPolicy.for_ramp_rate_limit(),
            log=logger,
            operation_name="ramp.auth.token.mint",
        )

        access_token = payload.get("access_token")
        if not access_token:
            raise RampAuthError("Ramp token mint succeeded but carried no access_token")

        granted_scope = payload.get("scope")
        self._assert_granted_scopes(granted_scope)

        try:
            expires_in = int(payload.get("expires_in") or DEFAULT_TOKEN_LIFETIME_SECONDS)
        except (TypeError, ValueError):
            expires_in = DEFAULT_TOKEN_LIFETIME_SECONDS

        _token_cache.access_token = str(access_token)
        _token_cache.granted_scope = str(granted_scope) if granted_scope is not None else None
        _token_cache.expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=max(0, expires_in - TOKEN_EXPIRY_BUFFER_SECONDS)
        )

        logger.info(
            "ramp.auth.token.mint.completed",
            extra={"event_name": "ramp.auth.token.mint.completed", "expires_in": expires_in},
        )
        return str(access_token)

    def _assert_granted_scopes(self, granted_scope: Optional[str]) -> None:
        requested = self.requested_scopes.split()
        if not granted_scope or not str(granted_scope).strip():
            raise RampAuthError(
                "Ramp issued a scopeless token — refusing to use it (send scope on token request)"
            )
        granted_set = set(str(granted_scope).split())
        missing = [s for s in requested if s not in granted_set]
        if missing:
            raise RampAuthError(
                f"Ramp token missing requested scope(s): {', '.join(missing)}"
            )
