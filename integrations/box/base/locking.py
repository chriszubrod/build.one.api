# Python Standard Library Imports
from contextlib import contextmanager
from typing import Iterator

# Local Imports
from integrations.box.base.logger import get_box_logger
from shared.database import get_connection

logger = get_box_logger(__name__)


@contextmanager
def box_app_lock(resource_name: str, timeout_ms: int = 15000) -> Iterator[bool]:
    """
    Acquire a named SQL Server application lock for cross-process serialization.

    Yields True if the lock was acquired, False if acquisition timed out or
    otherwise failed. On exit, the lock is explicitly released via
    `sp_releaseapplock`; session-scope ownership means that in the event of
    an unexpected termination the lock also auto-releases when the
    connection closes.

    This is the correct primitive when two separate Python processes (e.g.,
    the API and a standalone sync script) might contend for a resource —
    an in-process `threading.Lock` cannot coordinate across processes.

    Args:
        resource_name: Lock name. By convention `"box_<scope>:<key>"`:
                        `"box_file_write:{box_file_id}"` serializes writes
                        (new versions / etag-guarded updates) to a single
                        Box file; `"box_outbox_drain"` serializes the
                        Phase 2 outbox drain across workers. Note the CCG
                        token mint needs NO app lock (unlike the QBO/MS
                        refresh paths) — minting does not rotate anything.
        timeout_ms:    How long to wait before giving up. 15s is generous
                        for typical write serialization; callers may tune
                        per use case.

    Yields:
        True if the lock was acquired; False if timeout/failure.
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
                    "box.lock.acquired",
                    extra={
                        "event_name": "box.lock.acquired",
                        "resource": resource_name,
                        "result_code": result_code,
                        "timeout_ms": timeout_ms,
                    },
                )
                yield True
            else:
                logger.warning(
                    "box.lock.timeout",
                    extra={
                        "event_name": "box.lock.timeout",
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
                        "box.lock.released",
                        extra={"event_name": "box.lock.released", "resource": resource_name},
                    )
                except Exception as error:
                    # Session-scope ownership means closing the connection auto-releases
                    # the lock — this is a best-effort explicit release.
                    logger.warning(
                        "box.lock.release_failed",
                        extra={
                            "event_name": "box.lock.release_failed",
                            "resource": resource_name,
                            "error": str(error),
                        },
                    )
            try:
                cursor.close()
            except Exception:
                pass
