"""
Per-row access helpers for Gap 1 by-id direct-URL scoping.

Closes the leak where a non-admin who knows an entity's id / public_id can
fetch it directly even when no UserProject membership grants access. List
paths were scoped at the sproc layer (commit e2d3afb); these helpers extend
the same model to direct lookups by post-fetch checking via the existing
`dbo.UserCanAccess*` UDFs.

Single-row UDF calls are cheap (microseconds) — the 113s perf issue from
Gap 1 v1 only applied to list-path scans where the UDF was evaluated per
row across tens of thousands of rows.

Service-layer pattern:

    bill = self.bill_repository.read_by_public_id(public_id)
    if bill is None:
        return None
    assert_can_access_bill(bill.id)
    return bill

Admins (`current_is_system_admin == True`) bypass. Unauthenticated callers
(`current_user_id is None`) **fail closed** as of 2026-05-12 — the prior
`current_user_id is None → bypass` mirrored the SQL-side
`@ActorUserId IS NULL` clause we just removed, and was the same shape of
silent leak. Scheduler / system callers reach the admin bypass via the
`set_authz_context(is_system_admin=True)` call in
`shared/api/admin.py::_require_drain_secret`.
"""
from typing import Optional

from shared.authz import current_user_id, current_is_system_admin, current_can_view_team
from shared.database import get_connection


class EntityNotAccessibleError(Exception):
    """The current actor lacks UserProject access to the requested entity.

    Mapped to HTTP 404 (not 403) so the URL doesn't confirm the entity exists
    to a user without access.
    """

    def __init__(self, entity: str, entity_id: int):
        self.entity = entity
        self.entity_id = entity_id
        super().__init__(f"{entity} {entity_id} is not accessible to the current actor")


def _should_bypass() -> bool:
    """True iff the request explicitly carries system-admin authz context.

    Removed (2026-05-12) the prior `current_user_id is None → bypass` branch.
    A missing actor used to silently allow direct-URL by-id reads, mirroring
    the SQL-side legacy bypass. Both were closed in the same sweep.

    Drain-secret callers (outbox drain, QBO sync, reconciliation) reach this
    bypass via `set_authz_context(is_system_admin=True)` at the dependency
    layer — `current_is_system_admin.get()` is True for them.
    """
    return current_is_system_admin.get()


_ALLOWED_UDFS = frozenset({
    "UserCanAccessBill",
    "UserCanAccessBillCredit",
    "UserCanAccessExpense",
    "UserCanAccessProject",
})


def _check(udf_name: str, entity_id: int) -> bool:
    """Run SELECT dbo.<UDF>(@uid, 0, @id) and return True if accessible."""
    if udf_name not in _ALLOWED_UDFS:
        raise ValueError(f"UDF '{udf_name}' is not in the access-check whitelist")
    actor_user_id = current_user_id.get()
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"SELECT dbo.{udf_name}(?, 0, ?)",
                (actor_user_id, entity_id),
            )
            row = cursor.fetchone()
            return bool(row[0]) if row and row[0] is not None else False
        finally:
            cursor.close()


def _check_time_entry(time_entry_id: int) -> bool:
    """
    Run `SELECT dbo.UserCanAccessTimeEntry(@uid, 0, @can_view_team, @id)`.
    Distinct from `_check` because the TimeEntry UDF takes a 4th parameter
    (`@ActorCanViewTeam`) — the project-scope branch that opens visibility
    on rows whose TimeLogs touch the actor's UserProject set.
    """
    actor_user_id = current_user_id.get()
    can_view_team = current_can_view_team("Time Tracking")
    with get_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT dbo.UserCanAccessTimeEntry(?, 0, ?, ?)",
                (actor_user_id, 1 if can_view_team else 0, time_entry_id),
            )
            row = cursor.fetchone()
            return bool(row[0]) if row and row[0] is not None else False
        finally:
            cursor.close()


def assert_can_access_bill(bill_id: Optional[int]) -> None:
    if bill_id is None or _should_bypass():
        return
    if not _check("UserCanAccessBill", bill_id):
        raise EntityNotAccessibleError("Bill", bill_id)


def assert_can_access_bill_credit(bill_credit_id: Optional[int]) -> None:
    if bill_credit_id is None or _should_bypass():
        return
    if not _check("UserCanAccessBillCredit", bill_credit_id):
        raise EntityNotAccessibleError("BillCredit", bill_credit_id)


def assert_can_access_expense(expense_id: Optional[int]) -> None:
    if expense_id is None or _should_bypass():
        return
    if not _check("UserCanAccessExpense", expense_id):
        raise EntityNotAccessibleError("Expense", expense_id)


def assert_can_access_project(project_id: Optional[int]) -> None:
    """Used directly for Project/Invoice/ContractLabor (which carry ProjectId)."""
    if project_id is None or _should_bypass():
        return
    if not _check("UserCanAccessProject", project_id):
        raise EntityNotAccessibleError("Project", project_id)


def assert_can_access_time_entry(time_entry_id: Optional[int]) -> None:
    """
    Gate direct-id reads/mutations of a TimeEntry.

    Visibility rules (mirrors `dbo.UserCanAccessTimeEntry`):
      - System admins bypass (handled by `_should_bypass`).
      - Owners (TimeEntry.UserId == actor) always pass.
      - Actors holding `can_view_team` on Time Tracking pass when any of the
        entry's TimeLogs has a ProjectId in their UserProject set.
    """
    if time_entry_id is None or _should_bypass():
        return
    if not _check_time_entry(time_entry_id):
        raise EntityNotAccessibleError("TimeEntry", time_entry_id)
