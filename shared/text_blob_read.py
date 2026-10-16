"""Retry helpers for reading extracted-text blobs (U-654a backtest + U-654b tick)."""
from __future__ import annotations

import time
from typing import Callable

_BLOB_READ_ATTEMPTS = 3
_BLOB_READ_BACKOFF_SECONDS = (2.0, 5.0)


def blob_error_is_transient(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    return status is None or status == 429 or status >= 500


def read_text_blob_with_retry(
    read: Callable[[str], str],
    url: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Read one extracted-text blob, retrying transient storage failures."""
    from shared.storage import AzureBlobStorageError

    for attempt in range(_BLOB_READ_ATTEMPTS):
        try:
            return read(url)
        except AzureBlobStorageError as exc:
            if attempt == _BLOB_READ_ATTEMPTS - 1 or not blob_error_is_transient(exc):
                raise
            sleep(
                _BLOB_READ_BACKOFF_SECONDS[
                    min(attempt, len(_BLOB_READ_BACKOFF_SECONDS) - 1)
                ]
            )
    raise AssertionError("unreachable")
