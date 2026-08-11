# Python Standard Library Imports
import json
import logging
import threading
import time
from typing import Optional

# Third-party Imports
import requests

# Local Imports

logger = logging.getLogger(__name__)

DISCOVERY_URL = "https://developer.api.intuit.com/.well-known/openid_configuration"
DISCOVERY_CONNECT_TIMEOUT_SECONDS = 5
DISCOVERY_READ_TIMEOUT_SECONDS = 10
DISCOVERY_CACHE_TTL_SECONDS = 3600

_discovery_cache_lock = threading.Lock()
_discovery_cache: Optional[tuple[dict, float]] = None


def get_intuit_discovery_document(*, force_refresh: bool = False) -> Optional[dict]:
    """
    Fetch Intuit's OpenID discovery document.

    Runs inside the qbo_auth_refresh applock during token refresh, so this
    call must always be bounded (connect + read timeouts) and is cached
    process-locally to avoid a network round-trip on every refresh.
    """
    global _discovery_cache

    if not force_refresh:
        with _discovery_cache_lock:
            if _discovery_cache is not None:
                document, fetched_at = _discovery_cache
                if time.monotonic() - fetched_at < DISCOVERY_CACHE_TTL_SECONDS:
                    return document

    try:
        resp = requests.get(
            url=DISCOVERY_URL,
            headers={"Accept": "application/json"},
            timeout=(DISCOVERY_CONNECT_TIMEOUT_SECONDS, DISCOVERY_READ_TIMEOUT_SECONDS),
        )
    except requests.RequestException as exc:
        logger.warning(
            "Intuit discovery document request failed: %s",
            exc,
        )
        return None

    if resp.status_code != 200:
        logger.warning(
            "Intuit discovery document returned HTTP %s: %s",
            resp.status_code,
            resp.text[:200] if resp.text else "",
        )
        return None

    try:
        document = json.loads(resp.text)
    except (ValueError, TypeError) as exc:
        logger.warning(
            "Intuit discovery document body unparseable: %s",
            exc,
        )
        return None

    if not isinstance(document, dict):
        logger.warning(
            "Intuit discovery document body is not a JSON object: %r",
            type(document).__name__,
        )
        return None

    with _discovery_cache_lock:
        _discovery_cache = (document, time.monotonic())

    return document


def get_intuit_endpoint(name: str) -> Optional[str]:
    """Return a discovery-document endpoint URL by key, or None if unavailable."""
    document = get_intuit_discovery_document()
    if not document or name not in document:
        return None
    return document[name]
