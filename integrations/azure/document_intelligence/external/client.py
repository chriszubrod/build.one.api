"""Azure AI Document Intelligence — HTTPX client.

Uses the REST API directly (no Azure SDK), mirroring the
`integrations/ms/...` pattern. Three concerns:

  1. Kick off an `analyze` operation against a model (we default to
     `prebuilt-invoice` but the call accepts any model id).
  2. Poll the returned `Operation-Location` until status is `succeeded`
     or `failed` (with backoff + a hard ceiling).
  3. Return the raw `analyzeResult` JSON. Field hoisting (turning
     DI's nested `valueCurrency` shape into our `DiTotalAmount`
     decimal) is the caller's job — keeps this layer dumb.

Errors raise `DocumentIntelligenceError`. Transient failures
(429/5xx) are retried inside `analyze_document_url`; the caller
sees a final exception only if the retries all fail.
"""
import logging
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


# Polling tuning — DI's prebuilt-invoice typically completes in 2-6s
# on a single-page PDF. We poll every 1s up to 60s before giving up.
_POLL_INTERVAL_SECONDS = 1.0
_POLL_MAX_SECONDS = 60.0

# Retry tuning for the initial POST + each poll GET on 429 / 5xx.
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.0


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
    """Single request with retries on 429 + 5xx."""
    last_error: Optional[Exception] = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.request(method, url, headers=headers, json=json_body, content=content)
            if response.status_code == 429 or 500 <= response.status_code < 600:
                logger.warning(
                    "DI %s %s returned %s (attempt %d/%d)",
                    method, url, response.status_code, attempt + 1, _RETRY_ATTEMPTS,
                )
                if attempt + 1 < _RETRY_ATTEMPTS:
                    time.sleep(_RETRY_BACKOFF_SECONDS * (2 ** attempt))
                    continue
            return response
        except httpx.HTTPError as e:
            last_error = e
            logger.warning("DI %s %s raised %s (attempt %d/%d)",
                           method, url, e, attempt + 1, _RETRY_ATTEMPTS)
            if attempt + 1 < _RETRY_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * (2 ** attempt))
    if last_error:
        raise DocumentIntelligenceError(f"Request failed after {_RETRY_ATTEMPTS} attempts: {last_error}")
    # Final failure with retried response codes — return the last response so caller can act.
    raise DocumentIntelligenceError(f"Request failed after {_RETRY_ATTEMPTS} attempts (non-2xx)")


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


def analyze_document_url(blob_url: str, *, model_id: str = "prebuilt-invoice") -> dict:
    """Analyze a publicly-accessible PDF/image URL via Document Intelligence.

    NOTE: when the blob is private, the caller must pass a SAS-signed URL
    so DI can fetch it. For now our blob storage is keyed-access only, so
    callers should prefer `analyze_document_bytes` until SAS support is
    wired through `shared.storage`.
    """
    endpoint, key, api_version = _settings()
    url = f"{endpoint}/documentintelligence/documentModels/{model_id}:analyze?api-version={api_version}"
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


def analyze_document_bytes(content: bytes, content_type: str,
                           *, model_id: str = "prebuilt-invoice") -> dict:
    """Analyze a PDF/image already in memory. Use this for blobs without
    public/SAS access — the caller downloads the bytes and passes them in.
    """
    endpoint, key, api_version = _settings()
    url = f"{endpoint}/documentintelligence/documentModels/{model_id}:analyze?api-version={api_version}"
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
