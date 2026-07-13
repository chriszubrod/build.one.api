# Python Standard Library Imports
import logging
from contextlib import contextmanager
from typing import Iterator

# Local Imports
from shared.database import get_connection

logger = logging.getLogger(__name__)


@contextmanager
def app_lock(resource_name: str, timeout_ms: int = 15000) -> Iterator[bool]:
    """
    Acquire a named SQL Server application lock for cross-process serialization.

    Domain-neutral `sp_getapplock` primitive: the correct tool whenever two
    separate Python processes (e.g. the API and a scheduler-triggered sweep, or
    two overlapping manual re-runs) might contend for a resource — an in-process
    `threading.Lock` cannot coordinate across processes.

    Yields True if the lock was acquired, False if acquisition timed out or
    otherwise failed. On exit the lock is explicitly released via
    `sp_releaseapplock`; session-scope ownership means that on an unexpected
    termination the lock also auto-releases when the connection closes.

    Pass `timeout_ms=0` for skip-if-held semantics (return immediately rather
    than block) — the caller decides whether to skip or wait.

    Args:
        resource_name: Lock name. Neutral by convention (e.g.
                        `"time_autosubmit_sweep"`); any two contenders must use
                        the byte-identical string.
        timeout_ms:    How long to wait before giving up. 0 = don't wait (skip
                        if held); the 15s default suits short critical sections.

    Yields:
        True if the lock was acquired; False if timeout/failure.

    Note: the per-integration `qbo_app_lock` / `ms_app_lock` / `box_app_lock`
    copies predate this shared home and are byte-identical modulo their event
    prefix; collapsing them onto this helper is deferred tech-debt (see TODO).
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        acquired = False
        try:
            cursor.execute(
                """
                DECLARE @result INT;
                EXEC @result = sp_getapplock
                    @Resource = ?,
                    @LockMode = 'Exclusive',
                    @LockOwner = 'Session',
                    @LockTimeout = ?;
                SELECT @result AS Result;
                """,
                resource_name,
                timeout_ms,
            )
            row = cursor.fetchone()
            result_code = int(row[0]) if row and row[0] is not None else -999

            # sp_getapplock return codes:
            #   0 = acquired immediately
            #   1 = acquired after waiting
            #  -1 = timeout
            #  -2 = canceled
            #  -3 = deadlock victim
            # -999 = indicates a validation error or other parameter problem
            if result_code >= 0:
                acquired = True
                logger.debug(
                    "db.lock.acquired",
                    extra={
                        "event_name": "db.lock.acquired",
                        "resource": resource_name,
                        "result_code": result_code,
                        "timeout_ms": timeout_ms,
                    },
                )
                yield True
            else:
                logger.warning(
                    "db.lock.timeout",
                    extra={
                        "event_name": "db.lock.timeout",
                        "resource": resource_name,
                        "result_code": result_code,
                        "timeout_ms": timeout_ms,
                    },
                )
                yield False
        finally:
            if acquired:
                try:
                    cursor.execute(
                        "EXEC sp_releaseapplock @Resource = ?, @LockOwner = 'Session';",
                        resource_name,
                    )
                    logger.debug(
                        "db.lock.released",
                        extra={"event_name": "db.lock.released", "resource": resource_name},
                    )
                except Exception as error:
                    # Session-scope ownership means closing the connection auto-releases
                    # the lock — this is a best-effort explicit release.
                    logger.warning(
                        "db.lock.release_failed",
                        extra={
                            "event_name": "db.lock.release_failed",
                            "resource": resource_name,
                            "error": str(error),
                        },
                    )
            try:
                cursor.close()
            except Exception:
                pass
