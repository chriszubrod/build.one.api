# Python Standard Library Imports
import logging
from contextlib import contextmanager
from typing import Iterator

# Local Imports
from shared.database import get_connection

logger = logging.getLogger(__name__)


def qbo_entity_sync_lock_resource(entity: str) -> str:
    """
    Shared `qbo_app_lock` resource name for a QBO entity sync, so every
    entry point that can trigger the same entity's sync (the admin
    `sync/qbo/{entity}` dispatcher, a per-entity API route, a scheduler
    tick, a standalone script) contends on the SAME SQL applock instead of
    each hand-writing its own `f"qbo_sync:{entity}"` string that can
    silently drift apart (U-337 Pass-1 P1: the account API route computed
    a realm-suffixed key that never contended with the admin route's
    entity-only key, leaving the two able to race each other unlocked).

    Deliberately entity-only, not realm-scoped: this system has one QBO
    realm today (see `qbo/auth/business/service.py`'s single-tenant
    fallback), so entity-only matches the existing admin-path convention
    without inventing realm-scoping that nothing else in the integration
    layer supports yet. Revisit if/when multi-realm support lands.
    """
    return f"qbo_sync:{entity}"


@contextmanager
def qbo_app_lock(resource_name: str, timeout_ms: int = 15000) -> Iterator[bool]:
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
        resource_name: Lock name. By convention `"qbo_<scope>:<key>"`,
                        e.g., `"qbo_auth_refresh:9341453129481934"`.
        timeout_ms:    How long to wait before giving up. 15s is generous
                        for the refresh path (which typically completes in
                        < 2s); callers may tune per use case.

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
                    "qbo.lock.acquired",
                    extra={
                        "event_name": "qbo.lock.acquired",
                        "resource": resource_name,
                        "result_code": result_code,
                        "timeout_ms": timeout_ms,
                    },
                )
                yield True
            else:
                logger.warning(
                    "qbo.lock.timeout",
                    extra={
                        "event_name": "qbo.lock.timeout",
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
                        "qbo.lock.released",
                        extra={"event_name": "qbo.lock.released", "resource": resource_name},
                    )
                except Exception as error:
                    # Session-scope ownership means closing the connection auto-releases
                    # the lock — this is a best-effort explicit release.
                    logger.warning(
                        "qbo.lock.release_failed",
                        extra={
                            "event_name": "qbo.lock.release_failed",
                            "resource": resource_name,
                            "error": str(error),
                        },
                    )
            try:
                cursor.close()
            except Exception:
                pass
