"""Azure AI Document Intelligence — HTTPX client.

Uses the REST API directly (no Azure SDK), mirroring the
`integrations/ms/...` pattern. Three concerns:

  1. Kick off an `analyze` operation against a model (we default to
     `prebuilt-layout` with the `keyValuePairs` add-on so the same
     extractor handles invoices, credit memos, receipts, statements,
     and any other generic document the email agent encounters; the
     call accepts any model id + features combo).
  2. Poll the returned `Operation-Location` until status is `succeeded`
     or `failed` (with backoff + a hard ceiling).
  3. Return the raw `analyzeResult` JSON. Higher-level interpretation
     (which fields matter, doc-type classification, validation) is the
     caller's job — keeps this layer dumb.

Errors raise `DocumentIntelligenceError`. Transient failures
(429/5xx) are retried inside `analyze_document_url`; the caller
sees a final exception only if the retries all fail.
"""
import email.utils
import logging
import random
import time
from decimal import Decimal
from typing import Any, Optional

import httpx

import config

logger = logging.getLogger(__name__)


class DocumentIntelligenceError(Exception):
    """Generic DI failure — message carries the underlying detail."""


class DocumentIntelligenceConfigError(DocumentIntelligenceError):
    """Raised when endpoint / key are not configured."""


# Polling tuning — DI's prebuilt-layout typically completes in 2-6s
# on a single-page PDF. We poll every 1s up to 60s before giving up.
_POLL_INTERVAL_SECONDS = 1.0
_POLL_MAX_SECONDS = 60.0

# Retry tuning for the initial POST + each poll GET on 429 / 5xx.
# Mirrors the canonical RetryPolicy in integrations/intuit/qbo/base/retry.py:
#   - 3 attempts (writes posture; we never want the email-agent loop hung)
#   - 1s base backoff, 2x multiplier with ±25% jitter to defeat thundering herd
#   - 30s total time budget so a chain of throttle responses can't stall the agent
#   - Retry-After header honored (delta-seconds OR HTTP-date), clamped at 60s
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.0
_RETRY_BACKOFF_MULTIPLIER = 2.0
_RETRY_JITTER_FRACTION = 0.25
_RETRY_BUDGET_SECONDS = 30.0
_RETRY_AFTER_CLAMP_SECONDS = 60.0


def _parse_retry_after_header(value: Optional[str]) -> Optional[float]:
    """Parse a Retry-After header (RFC 7231 — delta-seconds OR HTTP-date)
    into a non-negative seconds float. Returns None on parse failure or
    when the header is missing."""
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    # Delta-seconds form (most common).
    try:
        n = float(s)
        return max(0.0, n)
    except ValueError:
        pass
    # HTTP-date form. parsedate_to_datetime returns None or aware datetime.
    try:
        dt = email.utils.parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    delta = dt.timestamp() - time.time()
    return max(0.0, delta)


def _compute_backoff_seconds(
    attempt: int, retry_after_seconds: Optional[float] = None
) -> float:
    """Sleep duration before the next retry. Retry-After (clamped) wins
    when present; otherwise exponential backoff with ±jitter."""
    if retry_after_seconds is not None and retry_after_seconds > 0:
        return min(retry_after_seconds, _RETRY_AFTER_CLAMP_SECONDS)
    computed = _RETRY_BACKOFF_SECONDS * (_RETRY_BACKOFF_MULTIPLIER ** max(0, attempt - 1))
    jitter = computed * _RETRY_JITTER_FRACTION
    # ±25% jitter (symmetric around `computed`).
    return max(0.0, computed + random.uniform(-jitter, jitter))


def _settings() -> tuple[str, str, str]:
    s = config.Settings()
    endpoint = (s.azure_document_intelligence_endpoint or "").strip().rstrip("/")
    key = (s.azure_document_intelligence_key or "").strip()
    api_version = (s.azure_document_intelligence_api_version or "2024-11-30").strip()
    if not endpoint or not key:
        raise DocumentIntelligenceConfigError(
            "Document Intelligence is not configured "
            "(azure_document_intelligence_endpoint / _key missing)."
        )
    return endpoint, key, api_version


def _headers(key: str, content_type: Optional[str] = None) -> dict:
    h = {"Ocp-Apim-Subscription-Key": key}
    if content_type:
        h["Content-Type"] = content_type
    return h


def _do_request(method: str, url: str, *, headers: dict, json_body: Optional[dict] = None,
                content: Optional[bytes] = None, timeout: float = 60.0) -> httpx.Response:
    """Single request with retries on 429 + 5xx + httpx transport errors.

    Retry shape mirrors the canonical RetryPolicy: 3 attempts, exponential
    backoff with ±jitter, Retry-After header honored when present, and a
    30s total-time budget so a chain of throttle responses can't stall
    the calling agent loop. Non-retryable failures (4xx other than 429,
    httpx errors after the budget) raise DocumentIntelligenceError;
    successful responses (any 2xx OR non-retryable 4xx) return through
    so the caller can branch on status_code as before.
    """
    last_error: Optional[Exception] = None
    last_response: Optional[httpx.Response] = None
    start_time = time.monotonic()
    for attempt in range(1, _RETRY_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(
                    method, url, headers=headers, json=json_body, content=content
                )
            last_response = response
            is_retryable_status = response.status_code == 429 or 500 <= response.status_code < 600
            if not is_retryable_status:
                return response
            retry_after = _parse_retry_after_header(response.headers.get("Retry-After"))
            logger.warning(
                "DI %s %s returned %s (attempt %d/%d, retry_after=%s)",
                method, url, response.status_code, attempt, _RETRY_ATTEMPTS, retry_after,
            )
        except httpx.HTTPError as e:
            last_error = e
            retry_after = None
            logger.warning(
                "DI %s %s raised %s (attempt %d/%d)",
                method, url, e, attempt, _RETRY_ATTEMPTS,
            )
        # Out of attempts → exit loop and raise / return below.
        if attempt >= _RETRY_ATTEMPTS:
            break
        # Compute sleep + check budget. If a sleep would push us past the
        # budget, stop early — better to fail fast than burn the budget
        # in sleep before the inevitable failing attempt.
        sleep_seconds = _compute_backoff_seconds(attempt, retry_after)
        elapsed = time.monotonic() - start_time
        remaining = _RETRY_BUDGET_SECONDS - elapsed
        if sleep_seconds > remaining:
            logger.warning(
                "DI %s %s retry budget exhausted (elapsed=%.1fs, would sleep %.1fs > remaining %.1fs)",
                method, url, elapsed, sleep_seconds, remaining,
            )
            break
        time.sleep(sleep_seconds)
    if last_error is not None:
        raise DocumentIntelligenceError(
            f"Request failed after {_RETRY_ATTEMPTS} attempts: {last_error}"
        )
    if last_response is not None:
        # Final retryable status code — return so caller's status check
        # can produce a precise error message including the body text.
        return last_response
    raise DocumentIntelligenceError(
        f"Request failed after {_RETRY_ATTEMPTS} attempts (no response captured)"
    )


def _await_result(operation_location: str, key: str) -> dict:
    """Poll the long-running operation until done; return analyzeResult."""
    deadline = time.monotonic() + _POLL_MAX_SECONDS
    while time.monotonic() < deadline:
        response = _do_request("GET", operation_location, headers=_headers(key))
        if response.status_code != 200:
            raise DocumentIntelligenceError(
                f"DI poll returned {response.status_code}: {response.text[:500]}"
            )
        body = response.json()
        status = (body.get("status") or "").lower()
        if status == "succeeded":
            return body.get("analyzeResult") or {}
        if status == "failed":
            error = body.get("error") or {}
            raise DocumentIntelligenceError(
                f"DI analysis failed: {error.get('code')}: {error.get('message')}"
            )
        # statuses 'notStarted' / 'running' — keep polling
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise DocumentIntelligenceError(
        f"DI analysis did not complete within {_POLL_MAX_SECONDS}s"
    )


def analyze_document_url(blob_url: str, *, model_id: str = "prebuilt-layout",
                         features: Optional[str] = "keyValuePairs") -> dict:
    """Analyze a publicly-accessible PDF/image URL via Document Intelligence.

    NOTE: when the blob is private, the caller must pass a SAS-signed URL
    so DI can fetch it. For now our blob storage is keyed-access only, so
    callers should prefer `analyze_document_bytes` until SAS support is
    wired through `shared.storage`.

    `features` defaults to `"keyValuePairs"` (the add-on that gives us
    the auto-extracted key/value pairs prebuilt-layout doesn't return on
    its own). Pass `None` for other models that don't support add-ons.
    """
    endpoint, key, api_version = _settings()
    url = f"{endpoint}/documentintelligence/documentModels/{model_id}:analyze?api-version={api_version}"
    if features:
        url = f"{url}&features={features}"
    response = _do_request(
        "POST",
        url,
        headers=_headers(key, content_type="application/json"),
        json_body={"urlSource": blob_url},
    )
    if response.status_code != 202:
        raise DocumentIntelligenceError(
            f"DI analyze (URL) returned {response.status_code}: {response.text[:500]}"
        )
    operation_location = response.headers.get("Operation-Location") or response.headers.get("operation-location")
    if not operation_location:
        raise DocumentIntelligenceError("DI did not return Operation-Location header")
    return _await_result(operation_location, key)


_MAX_DOCUMENT_BYTES = 50 * 1024 * 1024  # 50 MB — Azure DI hard limit


def analyze_document_bytes(content: bytes, content_type: str,
                           *, model_id: str = "prebuilt-layout",
                           features: Optional[str] = "keyValuePairs") -> dict:
    """Analyze a PDF/image already in memory. Use this for blobs without
    public/SAS access — the caller downloads the bytes and passes them in.

    `features` defaults to `"keyValuePairs"` for prebuilt-layout. Pass
    `None` when calling another model that doesn't support add-ons.
    """
    if len(content) > _MAX_DOCUMENT_BYTES:
        raise DocumentIntelligenceError(
            f"Document size {len(content)} bytes exceeds {_MAX_DOCUMENT_BYTES // (1024*1024)} MB limit"
        )
    endpoint, key, api_version = _settings()
    url = f"{endpoint}/documentintelligence/documentModels/{model_id}:analyze?api-version={api_version}"
    if features:
        url = f"{url}&features={features}"
    response = _do_request(
        "POST",
        url,
        headers=_headers(key, content_type=content_type or "application/pdf"),
        content=content,
        timeout=120.0,  # large PDFs can take a few seconds just to upload
    )
    if response.status_code != 202:
        raise DocumentIntelligenceError(
            f"DI analyze (bytes) returned {response.status_code}: {response.text[:500]}"
        )
    operation_location = response.headers.get("Operation-Location") or response.headers.get("operation-location")
    if not operation_location:
        raise DocumentIntelligenceError("DI did not return Operation-Location header")
    return _await_result(operation_location, key)
