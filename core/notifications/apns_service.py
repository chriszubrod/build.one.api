"""
core/notifications/apns_service.py
===================================
Apple Push Notification service (APNs) integration.

Uses JWT-based authentication (p8 key file) with HTTP/2 transport via httpx.
The JWT is cached for 55 minutes — APNs tokens are valid for 60 minutes.

Configuration (all via environment variables / config.py):
    APNS_KEY_ID       — 10-character Key ID from Apple Developer
    APNS_TEAM_ID      — 10-character Team ID from Apple Developer
    APNS_BUNDLE_ID    — App bundle ID (must match Xcode: one.build.app)
    APNS_PRIVATE_KEY  — Full contents of the .p8 file (PEM string)
    APNS_ENVIRONMENT  — 'sandbox' (development) or 'production'

Usage:
    from core.notifications.apns_service import get_apns_service

    apns = get_apns_service()
    await apns.send_notification(
        device_token="abc123...",
        title="Action Required",
        body="A bill document needs your review.",
        data={"thread_id": "...", "stage": "REVIEW_NEEDED"},
    )
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# APNs HTTP/2 endpoints
_APNS_HOST_PRODUCTION = "https://api.push.apple.com"
_APNS_HOST_SANDBOX    = "https://api.sandbox.push.apple.com"
_APNS_PORT            = 443
_JWT_CACHE_SECONDS    = 55 * 60  # Refresh before the 60-min APNs limit


@dataclass
class APNsResult:
    """Result of a single push notification attempt."""
    device_token:   str
    success:        bool
    status_code:    int
    reason:         Optional[str]   = None
    apns_id:        Optional[str]   = None


class APNsService:
    """
    Sends push notifications to iOS devices via APNs HTTP/2.

    Thread-safe JWT caching — the token is generated once and reused
    until 5 minutes before expiry, then regenerated.
    """

    def __init__(
        self,
        key_id:      str,
        team_id:     str,
        bundle_id:   str,
        private_key: str,
        environment: str = "sandbox",
    ):
        self._key_id      = key_id
        self._team_id     = team_id
        self._bundle_id   = bundle_id
        self._private_key = private_key
        self._host        = (
            _APNS_HOST_PRODUCTION
            if environment == "production"
            else _APNS_HOST_SANDBOX
        )

        self._cached_jwt:     Optional[str]   = None
        self._jwt_generated:  Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_notification(
        self,
        device_token:   str,
        title:          str,
        body:           str,
        data:           Optional[dict[str, Any]] = None,
        badge:          Optional[int]            = None,
        sound:          str                      = "default",
        priority:       int                      = 10,
    ) -> APNsResult:
        """
        Send a single push notification to a device token.

        priority 10 = immediate delivery
        priority 5  = power-saving delivery (may be delayed)
        """
        try:
            import httpx
        except ImportError:
            logger.error(
                "httpx with HTTP/2 support is required. "
                "Run: pip install 'httpx[http2]'"
            )
            return APNsResult(
                device_token=device_token,
                success=False,
                status_code=0,
                reason="httpx[http2] not installed",
            )

        payload = self._build_payload(
            title=title,
            body=body,
            data=data,
            badge=badge,
            sound=sound,
        )

        jwt_token = self._get_jwt()
        url       = f"{self._host}/3/device/{device_token}"

        headers = {
            "authorization": f"bearer {jwt_token}",
            "apns-topic":    self._bundle_id,
            "apns-priority": str(priority),
            "apns-push-type": "alert",
        }

        try:
            async with httpx.AsyncClient(http2=True) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=10.0,
                )

            if response.status_code == 200:
                logger.info(
                    f"APNs: notification sent to token ...{device_token[-8:]}"
                )
                return APNsResult(
                    device_token=device_token,
                    success=True,
                    status_code=200,
                    apns_id=response.headers.get("apns-id"),
                )
            else:
                reason = None
                try:
                    reason = response.json().get("reason")
                except Exception:
                    pass

                logger.warning(
                    f"APNs: failed for token ...{device_token[-8:]} "
                    f"— {response.status_code} {reason}"
                )
                return APNsResult(
                    device_token=device_token,
                    success=False,
                    status_code=response.status_code,
                    reason=reason,
                )

        except Exception as error:
            logger.error(f"APNs: request error for token ...{device_token[-8:]}: {error}")
            return APNsResult(
                device_token=device_token,
                success=False,
                status_code=0,
                reason=str(error),
            )

    async def send_to_user(
        self,
        device_tokens:  list[str],
        title:          str,
        body:           str,
        data:           Optional[dict[str, Any]] = None,
        badge:          Optional[int]            = None,
    ) -> list[APNsResult]:
        """
        Send the same notification to all of a user's active device tokens.
        Returns a result for each token — failed tokens are logged but do not
        raise exceptions.
        """
        results = []
        for token in device_tokens:
            result = await self.send_notification(
                device_token=token,
                title=title,
                body=body,
                data=data,
                badge=badge,
            )
            results.append(result)

            # Token is invalid or unregistered — caller should deactivate it
            if not result.success and result.reason in (
                "BadDeviceToken",
                "Unregistered",
                "DeviceTokenNotForTopic",
            ):
                logger.warning(
                    f"APNs: token ...{token[-8:]} is invalid "
                    f"({result.reason}) — should be deactivated"
                )

        return results

    # ------------------------------------------------------------------
    # JWT generation and caching
    # ------------------------------------------------------------------

    def _get_jwt(self) -> str:
        """Return cached JWT or generate a new one if expired."""
        now = time.time()
        if (
            self._cached_jwt is not None
            and self._jwt_generated is not None
            and now - self._jwt_generated < _JWT_CACHE_SECONDS
        ):
            return self._cached_jwt

        self._cached_jwt    = self._generate_jwt()
        self._jwt_generated = now
        return self._cached_jwt

    def _generate_jwt(self) -> str:
        """
        Generate a signed ES256 JWT for APNs authentication.
        Uses PyJWT + cryptography (both already installed in Build.One).
        """
        try:
            import jwt as pyjwt
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            private_key = load_pem_private_key(
                self._private_key.encode("utf-8"),
                password=None,
            )

            token = pyjwt.encode(
                payload={
                    "iss": self._team_id,
                    "iat": int(time.time()),
                },
                key=private_key,
                algorithm="ES256",
                headers={"kid": self._key_id},
            )
            return token

        except Exception as error:
            logger.error(f"APNs: JWT generation failed: {error}")
            raise

    # ------------------------------------------------------------------
    # Payload builder
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        title:  str,
        body:   str,
        data:   Optional[dict[str, Any]],
        badge:  Optional[int],
        sound:  str,
    ) -> dict[str, Any]:
        """Build the APNs JSON payload."""
        alert: dict[str, Any] = {"title": title, "body": body}
        aps:   dict[str, Any] = {"alert": alert, "sound": sound}

        if badge is not None:
            aps["badge"] = badge

        payload: dict[str, Any] = {"aps": aps}

        if data:
            payload.update(data)

        return payload


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_apns_instance: Optional[APNsService] = None


def get_apns_service() -> Optional[APNsService]:
    """
    Return the singleton APNsService instance.
    Returns None if APNs is not configured — callers should check for None
    and skip notification silently (non-fatal).
    """
    global _apns_instance

    if _apns_instance is not None:
        return _apns_instance

    try:
        from config import settings

        key_id      = getattr(settings, "apns_key_id",      None)
        team_id     = getattr(settings, "apns_team_id",     None)
        bundle_id   = getattr(settings, "apns_bundle_id",   None)
        private_key = getattr(settings, "apns_private_key", None)
        environment = getattr(settings, "apns_environment",  "sandbox")

        if not all([key_id, team_id, bundle_id, private_key]):
            logger.warning(
                "APNs not configured — push notifications disabled. "
                "Set APNS_KEY_ID, APNS_TEAM_ID, APNS_BUNDLE_ID, "
                "APNS_PRIVATE_KEY in environment."
            )
            return None

        _apns_instance = APNsService(
            key_id=key_id,
            team_id=team_id,
            bundle_id=bundle_id,
            private_key=private_key,
            environment=environment,
        )
        logger.info(
            f"APNs service initialized — environment: {environment}, "
            f"bundle: {bundle_id}"
        )
        return _apns_instance

    except Exception as error:
        logger.error(f"APNs service initialization failed: {error}")
        return None
