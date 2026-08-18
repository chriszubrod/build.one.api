"""Fleet-wide QBO sync pacing — shared by every pull script/service to avoid
connection exhaustion during large batches. Centralizes the BATCH_SIZE/BATCH_DELAY
constants that were previously hand-copied (byte-identical bodies) across 15 files.

Both are env-configurable, falling back to the values every one of those 15 files
already froze (10 / 0.5s) so a slow pull (e.g. a 26,582-row seed spending ~22min in
pure sleep at the frozen values) can be tuned without a code change.
"""

# Python Standard Library Imports
import logging
import os
import time


def _read_batch_size() -> int:
    raw = os.environ.get("QBO_SYNC_BATCH_SIZE")
    if not raw:
        return 10
    try:
        value = int(raw)
    except ValueError:
        return 10
    return value if value > 0 else 10


def _read_batch_delay() -> float:
    raw = os.environ.get("QBO_SYNC_BATCH_DELAY_SECONDS")
    if not raw:
        return 0.5
    try:
        value = float(raw)
    except ValueError:
        return 0.5
    return value if value >= 0 else 0.5


BATCH_SIZE = _read_batch_size()
BATCH_DELAY = _read_batch_delay()


def pace_batch(index: int, total: int, logger: logging.Logger, label: str) -> None:
    """Sleep BATCH_DELAY seconds every BATCH_SIZE items processed — the identical
    pacing loop every QBO sync file used to hand-copy."""
    if (index + 1) % BATCH_SIZE == 0 and index + 1 < total:
        logger.debug(f"Processed {index + 1}/{total} {label}, pausing...")
        time.sleep(BATCH_DELAY)
