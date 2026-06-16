# Python Standard Library Imports
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

# Third-party Imports
import httpx

# Local Imports
import config
from integrations.box.base.client import BOX_OAUTH_TOKEN_URL, _parse_retry_after
from integrations.box.base.errors import (
    BoxAuthError,
    BoxRateLimitError,
    BoxServerError,
    BoxTimeoutError,
    BoxTransportError,
)
from integrations.box.base.logger import get_box_logger

logger = get_box_logger(__name__)


# Mint a fresh token this many seconds before the cached one actually
# expires, so an in-flight request never carries a token that dies mid-call.
TOKEN_EXPIRY_BUFFER_SECONDS = 60

# Box access tokens default to 60 minutes; used only as a defensive fallback
# when the token endpoint omits `expires_in`.
DEFAULT_TOKEN_LIFETIME_SECONDS = 3600

# The token endpoint is a single fast POST — tier-A-equivalent timeouts.
_MINT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


@dataclass
class _TokenCache:
    """Process-wide cached CCG access token. Mutations guarded by _token_lock."""

    access_token: Optional[str] = None
    expires_at: Optional[datetime] = None


_token_cache = _TokenCache()

# Stampede protection, not correctness: concurrent CCG mints are harmless
# (Box rotates nothing on mint, unlike the QBO/MS refresh-token paths, so no
# sp_getapplock is needed) — the lock just stops N threads from burning N-1
# redundant token-endpoint calls when the cache goes stale under load.
_token_lock = threading.Lock()


class BoxAuthService:
    """
    Service for Box auth via Client Credentials Grant (CCG).

    Radically simpler than the MS delegated OAuth flow: there are NO refresh
    tokens and NO DB table. Sixty-minute access tokens are minted on demand
    from `box_client_id` / `box_client_secret` / `box_enterprise_id` and
    cached in process memory with a freshness buffer.
    """

    def __init__(self, settings: Optional[config.Settings] = None):
        """Initialize the BoxAuthService."""
        self._settings = settings or config.Settings()

    def is_configured(self) -> bool:
        """
        True iff CCG can mint: client id + secret, plus a subject — either a
        managed user to impersonate (`box_as_user_id`, as-user mode) or the
        enterprise (`box_enterprise_id`, service-account mode).
        """
        creds = bool(self._settings.box_client_id and self._settings.box_client_secret)
        subject = bool(self._settings.box_as_user_id or self._settings.box_enterprise_id)
        return creds and subject

    def _subject(self) -> tuple:
        """
        The CCG token subject: ('user', <user_id>) when `box_as_user_id` is set
        (act AS that managed user — production auth model), else
        ('enterprise', <enterprise_id>) (act as the service account).
        """
        if self._settings.box_as_user_id:
            return "user", str(self._settings.box_as_user_id)
        return "enterprise", str(self._settings.box_enterprise_id)

    def ensure_valid_token(self, force_refresh: bool = False) -> str:
        """
        Return a valid CCG access token, minting a fresh one if needed.

        Args:
            force_refresh: When True, skip the cache and mint unconditionally.
                            Used by the shared BoxHttpClient's 401-recovery path
                            when the cached token appears revoked.

        Returns:
            The access token string.

        Raises:
            BoxAuthError: credentials missing or rejected by the token endpoint.
            BoxTimeoutError / BoxTransportError: infrastructure blip reaching
                the token endpoint (retryable — the caller's retry layer or the
                Phase 2 drain handles it; never classified as poison).
            BoxServerError: token endpoint 5xx (retryable).
        """
        if not force_refresh:
            cached = self._read_fresh_cached_token()
            if cached:
                return cached

        with _token_lock:
            # Double-checked: another thread may have minted while we waited
            # on the lock — re-check freshness before calling Box.
            if not force_refresh:
                cached = self._read_fresh_cached_token()
                if cached:
                    return cached
            return self._mint_and_cache()

    def status(self) -> Dict[str, Any]:
        """
        Operator-facing status payload for the auth router.

        Booleans and timestamps ONLY — never secret material (no client
        secret, no token value).
        """
        now = datetime.now(timezone.utc)
        expires_at_iso: Optional[str] = None
        seconds_remaining: Optional[int] = None
        cached = False
        if _token_cache.access_token and _token_cache.expires_at:
            expires_at_iso = _token_cache.expires_at.isoformat()
            seconds_remaining = int((_token_cache.expires_at - now).total_seconds())
            cached = seconds_remaining > 0
        subject_type, subject_id = self._subject()
        return {
            "configured": {
                "client_id": bool(self._settings.box_client_id),
                "client_secret": bool(self._settings.box_client_secret),
                "enterprise_id": bool(self._settings.box_enterprise_id),
                "as_user_id": bool(self._settings.box_as_user_id),
            },
            # Which identity tokens are minted as — 'user' (as-user, prod model)
            # or 'enterprise' (service account). subject_id is a Box id, not a
            # secret. None when no subject is configured.
            "subject": {
                "type": subject_type if self.is_configured() else None,
                "id": subject_id if self.is_configured() else None,
            },
            "token": {
                "cached": cached,
                "expires_at": expires_at_iso,
                "seconds_remaining": seconds_remaining,
            },
        }

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _read_fresh_cached_token() -> Optional[str]:
        """Return the cached token if present and not yet inside the expiry buffer."""
        token = _token_cache.access_token
        expires_at = _token_cache.expires_at
        if token and expires_at and datetime.now(timezone.utc) < expires_at:
            return token
        return None

    def _mint_and_cache(self) -> str:
        """
        Mint a fresh CCG token from the Box token endpoint and cache it.

        Caller must hold `_token_lock`. Never logs the client secret or the
        access token value — only durations and the token type.
        """
        if not self.is_configured():
            raise BoxAuthError(
                "Box CCG credentials are not configured (need box_client_id + "
                "box_client_secret + a subject: box_as_user_id or box_enterprise_id)"
            )

        subject_type, subject_id = self._subject()
        logger.info(
            "box.auth.token.mint.started",
            extra={
                "event_name": "box.auth.token.mint.started",
                "subject_type": subject_type,
                "subject_id": subject_id,
            },
        )

        form = {
            "grant_type": "client_credentials",
            "client_id": self._settings.box_client_id,
            "client_secret": self._settings.box_client_secret,
            "box_subject_type": subject_type,
            "box_subject_id": subject_id,
        }

        try:
            response = httpx.post(BOX_OAUTH_TOKEN_URL, data=form, timeout=_MINT_TIMEOUT)
        except httpx.TimeoutException as error:
            logger.warning(
                "box.auth.token.mint.failed",
                extra={"event_name": "box.auth.token.mint.failed", "outcome": "timeout"},
            )
            raise BoxTimeoutError(
                f"Box token mint timed out: {error}",
                request_method="POST",
                request_path="/oauth2/token",
            ) from error
        except httpx.TransportError as error:
            logger.warning(
                "box.auth.token.mint.failed",
                extra={"event_name": "box.auth.token.mint.failed", "outcome": "transport"},
            )
            raise BoxTransportError(
                f"Box token mint transport failure: {error}",
                request_method="POST",
                request_path="/oauth2/token",
            ) from error

        if response.status_code in (400, 401):
            description = self._extract_error_description(response)
            logger.error(
                "box.auth.token.mint.failed",
                extra={
                    "event_name": "box.auth.token.mint.failed",
                    "http_status": response.status_code,
                    "outcome": "rejected",
                },
            )
            raise BoxAuthError(
                f"Box token mint rejected (config/credential problem): {description}",
                http_status=response.status_code,
                detail=response.text[:500] if response.text else None,
                request_method="POST",
                request_path="/oauth2/token",
            )
        if response.status_code == 429:
            # A rate-limited token endpoint is transient contention, not a
            # config/credential problem — raise retryable so the caller's
            # retry layer backs off and re-mints.
            logger.warning(
                "box.auth.token.mint.failed",
                extra={
                    "event_name": "box.auth.token.mint.failed",
                    "http_status": 429,
                    "outcome": "rate_limited",
                },
            )
            raise BoxRateLimitError(
                "Box token endpoint rate-limited the mint request",
                http_status=429,
                retry_after_seconds=_parse_retry_after(
                    response.headers.get("Retry-After")
                    or response.headers.get("retry-after")
                ),
                request_method="POST",
                request_path="/oauth2/token",
            )
        if response.status_code >= 500:
            logger.error(
                "box.auth.token.mint.failed",
                extra={
                    "event_name": "box.auth.token.mint.failed",
                    "http_status": response.status_code,
                    "outcome": "server_error",
                },
            )
            raise BoxServerError(
                f"Box token endpoint returned HTTP {response.status_code}",
                http_status=response.status_code,
                detail=response.text[:500] if response.text else None,
                request_method="POST",
                request_path="/oauth2/token",
            )
        if response.status_code != 200:
            raise BoxAuthError(
                f"Box token mint returned unexpected HTTP {response.status_code}",
                http_status=response.status_code,
                detail=response.text[:500] if response.text else None,
                request_method="POST",
                request_path="/oauth2/token",
            )

        try:
            payload = response.json()
        except Exception as error:
            raise BoxAuthError(
                "Box token mint returned HTTP 200 with a non-JSON body",
                http_status=200,
                request_method="POST",
                request_path="/oauth2/token",
            ) from error

        access_token = payload.get("access_token") if isinstance(payload, dict) else None
        if not access_token:
            raise BoxAuthError(
                "Box token mint succeeded but the response carried no access_token",
                http_status=200,
                request_method="POST",
                request_path="/oauth2/token",
            )

        try:
            expires_in = int(payload.get("expires_in") or DEFAULT_TOKEN_LIFETIME_SECONDS)
        except (TypeError, ValueError):
            expires_in = DEFAULT_TOKEN_LIFETIME_SECONDS

        _token_cache.access_token = access_token
        _token_cache.expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=max(0, expires_in - TOKEN_EXPIRY_BUFFER_SECONDS)
        )

        logger.info(
            "box.auth.token.mint.completed",
            extra={
                "event_name": "box.auth.token.mint.completed",
                "expires_in": expires_in,
                "token_type": payload.get("token_type"),
            },
        )
        return access_token

    @staticmethod
    def _extract_error_description(response: httpx.Response) -> str:
        """Pull Box's `error_description` from a token-endpoint error body, defensively."""
        try:
            body = response.json()
            if isinstance(body, dict):
                description = body.get("error_description") or body.get("error")
                if description:
                    return str(description)
        except Exception:
            pass
        return f"HTTP {response.status_code}"
