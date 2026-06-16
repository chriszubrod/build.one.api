# Python Standard Library Imports
import logging

# Local Imports
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class TimeEntryDigestRepository:
    """
    Read-side persistence for the time-entry daily digest.

    Returns plain dicts (one flat row per TimeEntry x TimeLog) rather than
    TimeEntry dataclasses — the digest needs denormalized worker / project /
    status / email fields the dataclass doesn't carry, and the service groups
    the rows by worker. Runs in system context (the drain-secret admin
    endpoint sets `is_system_admin=True`); the sproc itself is unscoped and
    reads across all workers by design.
    """

    def read_for_work_date(self, work_date: str) -> list[dict]:
        """
        One row per (TimeEntry x TimeLog) for `work_date` (YYYY-MM-DD),
        joined to worker name + first non-null Contact email, the entry's
        current status, and each log's Project name. Entries with no logs
        still appear (NULL log columns). LLM agents + persona accounts are
        excluded by the sproc.
        """
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                try:
                    call_procedure(
                        cursor=cursor,
                        name="ReadTimeEntriesForDigestByWorkDate",
                        params={"WorkDate": work_date},
                    )
                    columns = [c[0] for c in cursor.description]
                    return [dict(zip(columns, row)) for row in cursor.fetchall() if row]
                finally:
                    try:
                        cursor.close()
                    except Exception:
                        pass
        except Exception as error:
            logger.error(f"Error reading time entry digest rows: {error}")
            raise map_database_error(error)
