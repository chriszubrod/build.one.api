"""U-483 — externally resolved expense coding items (orphaned staging lines).

Pure-logic / source pins — no live DB. Orphaned dbo.ExpenseCodingItem rows whose
QboPurchaseLineId no longer exists in staging but whose parent purchase has no
58999 lines left must transition to `resolved_externally`, tracked separately
from cockpit `written` wins.
"""

import inspect
import re
from unittest.mock import MagicMock, patch

import pytest

from tests.sproc_text import REPO_ROOT, sproc_body, sproc_params, strip_sql_comments

EXPENSE_CODING_SQL = (
    REPO_ROOT / "entities/expense_coding_item/sql/dbo.expense_coding_item.sql"
)
METRICS_SQL = (
    REPO_ROOT / "integrations/intuit/qbo/purchase/sql/qbo.expense_coding_queue.sql"
)

DETECTION_SPROC = "ReadExternallyResolvedCodingItemCandidates"
MARK_SPROC = "MarkExpenseCodingResolvedExternally"

ALLOWED_TRANSITION_FROM = frozenset(
    {"pending", "suggested", "flagged", "changed_in_qbo"}
)
FORBIDDEN_TRANSITION_FROM = frozenset(
    {"written", "confirmed", "enqueued", "error"}
)


def _status_in_list(body: str, status: str) -> bool:
    """True when `status` appears inside a Status IN (...) list."""
    for match in re.finditer(
        r"Status\]\s+IN\s*\(([^)]+)\)", body, re.IGNORECASE | re.DOTALL
    ):
        chunk = match.group(1).lower()
        if f"n'{status}'" in chunk or f"'{status}'" in chunk:
            return True
    return False


# ---------------------------------------------------------------------------
# 1 — detection sproc requires no-58999-left guard
# ---------------------------------------------------------------------------


def test_detection_sproc_requires_no_need_to_categorize_lines_left():
    body = strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, DETECTION_SPROC))
    assert "NOT EXISTS" in body.upper()
    assert re.search(
        r"AccountRefName\]\s+LIKE\s+N'%NEED TO CATEGORIZE%'",
        body,
        re.IGNORECASE,
    ), (
        "ReadExternallyResolvedCodingItemCandidates must NOT EXISTS over parent "
        "PurchaseLine rows still at NEED TO CATEGORIZE — without it, outstanding "
        "work would be closed as externally resolved"
    )


# ---------------------------------------------------------------------------
# 2 — mark sproc cannot transition from terminal / in-flight statuses
# ---------------------------------------------------------------------------


def test_mark_sproc_only_transitions_from_non_terminal_statuses():
    body = strip_sql_comments(sproc_body(EXPENSE_CODING_SQL, MARK_SPROC))
    for status in ALLOWED_TRANSITION_FROM:
        assert _status_in_list(body, status), (
            f"MarkExpenseCodingResolvedExternally must allow transition from {status!r}"
        )
    for status in FORBIDDEN_TRANSITION_FROM:
        assert not _status_in_list(body, status), (
            f"MarkExpenseCodingResolvedExternally must NOT transition from {status!r}"
        )


# ---------------------------------------------------------------------------
# 3 — metrics: ResolvedExternallyCount added; written-only acceptance metrics
# ---------------------------------------------------------------------------


def test_metrics_sproc_adds_resolved_externally_count():
    body = strip_sql_comments(
        sproc_body(METRICS_SQL, "ReadExpenseCodingMetrics")
    )
    assert "ResolvedExternallyCount" in body
    assert "N'resolved_externally'" in body


def test_metrics_acceptance_counts_stay_written_only():
    body = strip_sql_comments(
        sproc_body(METRICS_SQL, "ReadExpenseCodingMetrics")
    )
    for metric in ("AcceptedCount", "OverriddenCount"):
        # Each expression must key on written, never resolved_externally.
        idx = body.index(f"[{metric}]")
        window = body[max(0, idx - 120) : idx + 40]
        assert "resolved_externally" not in window.lower()
        assert "N'written'" in window, (
            f"{metric} must remain keyed on Status = 'written' only"
        )


# ---------------------------------------------------------------------------
# 4 — service defaults to dry-run (no mark writes)
# ---------------------------------------------------------------------------


def test_reconcile_externally_resolved_defaults_to_dry_run_without_marking():
    from entities.expense_coding_item.business.service import ExpenseCodingItemService

    candidates = [
        {
            "public_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "id": 1,
            "status": "pending",
            "qbo_purchase_qbo_id": "123",
            "qbo_line_id": "1",
        }
    ]
    svc = ExpenseCodingItemService()
    svc.repo = MagicMock()
    svc.repo.read_externally_resolved_candidates.return_value = candidates

    result = svc.reconcile_externally_resolved()

    assert result == {"candidates": 1, "items": candidates}
    svc.repo.mark_resolved_externally.assert_not_called()


def test_reconcile_externally_resolved_apply_calls_mark_for_each_candidate():
    from entities.expense_coding_item.business.service import ExpenseCodingItemService

    candidates = [
        {
            "public_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "id": 1,
            "status": "suggested",
            "qbo_purchase_qbo_id": "123",
            "qbo_line_id": "1",
        },
        {
            "public_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "id": 2,
            "status": "flagged",
            "qbo_purchase_qbo_id": "456",
            "qbo_line_id": "2",
        },
    ]
    svc = ExpenseCodingItemService()
    svc.repo = MagicMock()
    svc.repo.read_externally_resolved_candidates.return_value = candidates
    svc.repo.mark_resolved_externally.side_effect = [MagicMock(), None]

    result = svc.reconcile_externally_resolved(dry_run=False)

    assert result == {"marked": 1, "skipped": 1}
    assert svc.repo.mark_resolved_externally.call_count == 2


# ---------------------------------------------------------------------------
# 5 — admin endpoint: drain secret + apply defaults false
# ---------------------------------------------------------------------------


def test_admin_reconcile_externally_resolved_route_uses_drain_secret():
    import shared.api.admin as admin_module

    src = inspect.getsource(
        admin_module.reconcile_expense_coding_externally_resolved_router
    )
    assert "Depends(_require_drain_secret)" in src, (
        "admin reconcile endpoint must gate on the drain secret like its neighbours"
    )


def test_admin_reconcile_externally_resolved_defaults_apply_to_false():
    import shared.api.admin as admin_module

    src = inspect.getsource(
        admin_module.reconcile_expense_coding_externally_resolved_router
    )
    assert "apply" in src
    assert re.search(
        r"apply\s*:\s*bool\s*=\s*Query\s*\(\s*default\s*=\s*False",
        src,
    ), "apply query param must default to False (dry-run)"


# ---------------------------------------------------------------------------
# 6 — both new sprocs: SET NOCOUNT ON + GO-terminated (file-level pin)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [DETECTION_SPROC, MARK_SPROC])
def test_new_sproc_starts_with_set_nocount_on(name):
    body = sproc_body(EXPENSE_CODING_SQL, name)
    after_as = body.split("AS", 1)[1]
    assert "SET NOCOUNT ON" in after_as.split("BEGIN", 1)[1][:80]


def test_new_sprocs_are_go_terminated_in_file():
    text = EXPENSE_CODING_SQL.read_text()
    for name in (DETECTION_SPROC, MARK_SPROC):
        match = re.search(
            rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+(?:dbo\.)?{name}\b.*?^GO\s*$",
            text,
            re.DOTALL | re.MULTILINE | re.IGNORECASE,
        )
        assert match is not None, f"{name} must be GO-terminated in the SQL file"
