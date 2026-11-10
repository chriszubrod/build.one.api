"""U-652d5 — the purchase pull stamps CodingSuggestion.ExpenseId (best-effort, never raises).

Pins the StampCodingSuggestionExpenseId procedure text, the repo call, and the connector's
stamp on the create / update paths, including a stamp failure that must not fail the pull.
"""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from shared.database import get_connection  # noqa: F401  (import proves the module graph loads)
from tests.sproc_text import REPO_ROOT, sproc_body, strip_sql_comments

CS_SQL = REPO_ROOT / "entities/coding_suggestion/sql/dbo.coding_suggestion.sql"
STAMP = "StampCodingSuggestionExpenseId"
REPO_MODULE = "entities.coding_suggestion.persistence.repo"
SERVICE_MODULE = "integrations.intuit.qbo.purchase.connector.expense.business.service"
LINE_CONNECTOR_PATH = (
    "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
    ".PurchaseLineExpenseLineItemConnector"
)

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")


# ---------------------------------------------------------------------------
# 1. Procedure text
# ---------------------------------------------------------------------------


def _stamp_body() -> str:
    return strip_sql_comments(sproc_body(CS_SQL, STAMP))


def test_stamp_procedure_is_defined_with_the_three_parameters():
    text = (REPO_ROOT / CS_SQL).read_text()
    assert re.search(
        rf"CREATE\s+OR\s+ALTER\s+PROCEDURE\s+{STAMP}\s*\(\s*"
        r"@RealmId\s+NVARCHAR\(50\)\s*,\s*@QboPurchaseQboId\s+NVARCHAR\(50\)\s*,\s*@ExpenseId\s+BIGINT\s*\)",
        text,
        re.IGNORECASE,
    )


def test_stamp_procedure_is_nocount_and_has_no_rollback():
    raw = sproc_body(CS_SQL, STAMP)
    assert re.search(r"SET\s+NOCOUNT\s+ON", raw, re.IGNORECASE)
    assert "ROLLBACK" not in raw.upper()


def test_stamp_procedure_only_touches_expense_target_rows():
    body = _stamp_body()
    assert re.search(r"TargetKind\]?\s*=\s*N'expense'", body, re.IGNORECASE)


def test_stamp_procedure_guards_null_or_dangling_and_never_steals_an_id():
    body = _stamp_body()
    # NULL or pointing at a missing Expense is the only case that gets written.
    assert re.search(r"ExpenseId\]?\s+IS\s+NULL", body, re.IGNORECASE)
    assert re.search(r"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+dbo\.\[?Expense\]?\s+e\s+WHERE\s+e\.\[?Id\]?\s*=\s*cs\.\[?ExpenseId\]?", body, re.IGNORECASE)
    # Never set an ExpenseId another suggestion row already holds (UQ_CodingSuggestion_ExpenseId).
    assert re.search(
        r"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+dbo\.\[?CodingSuggestion\]?\s+x\s+"
        r"WHERE\s+x\.\[?ExpenseId\]?\s*=\s*@ExpenseId\s+AND\s+x\.\[?Id\]?\s*<>\s*cs\.\[?Id\]?",
        body,
        re.IGNORECASE,
    )


def test_stamp_procedure_matches_purchase_identity():
    body = _stamp_body()
    assert re.search(r"RealmId\]?\s*=\s*@RealmId", body, re.IGNORECASE)
    assert re.search(r"QboPurchaseQboId\]?\s*=\s*@QboPurchaseQboId", body, re.IGNORECASE)


def test_stamp_procedure_returns_a_one_row_count():
    body = _stamp_body()
    assert re.search(r"SELECT\s+@@ROWCOUNT\s+AS\s+\[?Stamped\]?", body, re.IGNORECASE)


def test_stamp_procedure_does_not_bump_modified_datetime():
    """A ModifiedDatetime bump would restart the enqueued_stranded age clock (sweeper)."""
    assert "ModifiedDatetime" not in _stamp_body()


def test_upsert_resolves_expense_id_at_write_time_with_the_uniqueness_guard():
    body = strip_sql_comments(sproc_body(CS_SQL, "UpsertCodingSuggestionForPurchase"))
    assert re.search(r"COALESCE\(\s*@ExpenseId", body, re.IGNORECASE)
    assert re.search(r"FROM\s+dbo\.\[Expense\]\s+e\s+WHERE\s+e\.\[QboId\]\s*=\s*@QboPurchaseQboId", body, re.IGNORECASE)
    assert re.search(r"x\.\[ExpenseId\]\s*=\s*@ExpenseIdToStamp", body, re.IGNORECASE)
    # matched branch still fills only when NULL, now from the resolved id
    assert re.search(
        r"WHEN\s+@ExpenseIdToStamp\s+IS\s+NOT\s+NULL\s+AND\s+target\.\[ExpenseId\]\s+IS\s+NULL",
        body,
        re.IGNORECASE,
    )


def _heal_body() -> str:
    return strip_sql_comments(sproc_body(CS_SQL, "HealCodingSuggestionExpenseIds"))


def test_heal_procedure_is_batched_nocount_and_rollback_free():
    raw = sproc_body(CS_SQL, "HealCodingSuggestionExpenseIds")
    assert re.search(r"@Limit\s+INT\s*=\s*500", raw, re.IGNORECASE)
    assert re.search(r"UPDATE\s+TOP\s*\(\s*@Limit\s*\)", _heal_body(), re.IGNORECASE)
    assert re.search(r"SET\s+NOCOUNT\s+ON", raw, re.IGNORECASE)
    assert "ROLLBACK" not in raw.upper()
    assert re.search(r"SELECT\s+@@ROWCOUNT\s+AS\s+\[?Healed\]?", _heal_body(), re.IGNORECASE)
    assert "ModifiedDatetime" not in _heal_body()


def test_heal_procedure_guard_correlates_the_expense_not_the_target_row():
    """The uniqueness guard must test the id being written (e.[Id]), not the row's own ExpenseId."""
    body = _heal_body()
    assert re.search(r"x\.\[ExpenseId\]\s*=\s*e\.\[Id\]", body, re.IGNORECASE)
    assert re.search(r"x\.\[Id\]\s*<>\s*cs\.\[Id\]", body, re.IGNORECASE)


def test_heal_procedure_only_touches_expense_target_rows_and_null_or_dangling():
    body = _heal_body()
    assert re.search(r"TargetKind\]?\s*=\s*N'expense'", body, re.IGNORECASE)
    assert re.search(r"ExpenseId\]?\s+IS\s+NULL", body, re.IGNORECASE)
    assert re.search(r"NOT\s+EXISTS\s*\(\s*SELECT\s+1\s+FROM\s+dbo\.\[Expense\]\s+e0", body, re.IGNORECASE)


# ---------------------------------------------------------------------------
# 1b. Repo method
# ---------------------------------------------------------------------------


def test_repo_stamp_expense_id_calls_the_procedure_and_returns_the_count():
    from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(Stamped=1)
    with patch(f"{REPO_MODULE}.get_connection") as gc, patch(f"{REPO_MODULE}.call_procedure") as cp:
        gc.return_value.__enter__.return_value.cursor.return_value = cursor
        count = CodingSuggestionRepository().stamp_expense_id(
            realm_id="realm-1", qbo_purchase_qbo_id="P-9", expense_id=55,
        )
    assert count == 1
    cp.assert_called_once()
    assert cp.call_args.kwargs["name"] == STAMP
    assert cp.call_args.kwargs["params"] == {
        "RealmId": "realm-1",
        "QboPurchaseQboId": "P-9",
        "ExpenseId": 55,
    }


def test_repo_heal_expense_ids_returns_the_healed_count():
    from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(Healed=7)
    with patch(f"{REPO_MODULE}.get_connection") as gc, patch(f"{REPO_MODULE}.call_procedure") as cp:
        gc.return_value.__enter__.return_value.cursor.return_value = cursor
        assert CodingSuggestionRepository().heal_expense_ids(limit=250) == 7
    assert cp.call_args.kwargs["name"] == "HealCodingSuggestionExpenseIds"
    assert cp.call_args.kwargs["params"] == {"Limit": 250}


def test_repo_stamp_failure_logs_the_exception_class_only(caplog):
    """The REPO's except logs the class name, never the exception text (the text can carry row values)."""
    from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

    caplog.set_level(logging.ERROR, logger=REPO_MODULE)
    with patch(f"{REPO_MODULE}.get_connection", side_effect=RuntimeError("secret-detail-xyz")):
        with pytest.raises(Exception):
            CodingSuggestionRepository().stamp_expense_id(
                realm_id="realm-1", qbo_purchase_qbo_id="P-9", expense_id=55,
            )
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in text
    assert "secret-detail-xyz" not in text


def test_repo_stamp_expense_id_zero_when_nothing_matched():
    from entities.coding_suggestion.persistence.repo import CodingSuggestionRepository

    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(Stamped=0)
    with patch(f"{REPO_MODULE}.get_connection") as gc, patch(f"{REPO_MODULE}.call_procedure"):
        gc.return_value.__enter__.return_value.cursor.return_value = cursor
        assert CodingSuggestionRepository().stamp_expense_id(
            realm_id="realm-1", qbo_purchase_qbo_id="P-0", expense_id=1,
        ) == 0


# ---------------------------------------------------------------------------
# 2. Connector: stamp on create / update, best-effort
# ---------------------------------------------------------------------------


def _qbo_purchase(**over):
    base = dict(
        id=4,
        qbo_id="PURCH-99",
        realm_id="realm-1",
        entity_ref_value="V-1",
        doc_number="INV-100",
        txn_date="2026-08-01",
        private_note="memo",
        total_amt=100,
        credit=False,
        sync_token="3",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _expense(**over):
    base = dict(
        id=55,
        public_id="pub-55",
        reference_number="R-1",
        row_version="rv-55",
        status="draft",
        status_origin="qbo_pull",
        is_draft=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _connector(*, path: str, stamp_repo=None):
    """`path` is 'hit' (dbo identity matches an existing Expense) or 'create' (genuine miss)."""
    from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector

    expense_service = Mock()
    direct = _expense()
    created = _expense(id=77, public_id="pub-77", row_version="rv-77")
    if path == "hit":
        expense_service.read_by_qbo_identity.return_value = direct
        expense_service.update_by_public_id.return_value = direct
    else:
        expense_service.read_by_qbo_identity.return_value = None
        expense_service.create.return_value = created
    expense_service.repo = Mock()
    expense_service.repo.read_by_id.return_value = created

    line_connector = Mock()
    line_connector.expense_line_item_service.read_by_expense_id.return_value = []
    scc = Mock()
    scc.is_holding_code.return_value = False

    with patch(LINE_CONNECTOR_PATH, return_value=line_connector):
        connector = PurchaseExpenseConnector(
            expense_service=expense_service,
            sub_cost_code_service=scc,
            coding_suggestion_repo=stamp_repo or Mock(),
        )
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    connector._line_connector = line_connector
    connector._create_expense = Mock(return_value=created) if path == "create" else connector._create_expense
    return connector, expense_service, created if path == "create" else direct


def _run(connector):
    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        return connector.sync_from_qbo_purchase(_qbo_purchase(), [])


def test_update_path_stamps_expense_id_with_purchase_identity():
    repo = Mock()
    repo.stamp_expense_id.return_value = 1
    connector, _svc, direct = _connector(path="hit", stamp_repo=repo)
    _run(connector)
    repo.stamp_expense_id.assert_called_once_with(
        realm_id="realm-1", qbo_purchase_qbo_id="PURCH-99", expense_id=direct.id,
    )


def test_create_path_stamps_the_new_expense_id():
    repo = Mock()
    repo.stamp_expense_id.return_value = 1
    connector, _svc, _created = _connector(path="create", stamp_repo=repo)
    result = _run(connector)
    repo.stamp_expense_id.assert_called_once_with(
        realm_id="realm-1", qbo_purchase_qbo_id="PURCH-99", expense_id=result.id,
    )


def test_connector_stamp_failure_logs_only_the_exception_class(caplog):
    repo = Mock()
    repo.stamp_expense_id.side_effect = RuntimeError("secret-detail-do-not-log")
    connector, svc, _created = _connector(path="create", stamp_repo=repo)
    caplog.set_level(logging.WARNING, logger=SERVICE_MODULE)
    result = _run(connector)
    assert result is not None
    assert repo.stamp_expense_id.called
    svc.delete_by_public_id.assert_not_called()
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "RuntimeError" in text
    assert "PURCH-99" in text
    assert "secret-detail-do-not-log" not in text


def test_stamp_is_called_on_the_completed_expense_autocomplete_branch():
    """The hit branch can return a refreshed (auto-completed) expense instead of `updated`. The stamp sits
    after the single `return outcome.entity`, so that branch is stamped too: prove it with the refreshed id."""
    repo = Mock()
    repo.stamp_expense_id.return_value = 1
    connector, _svc, _direct = _connector(path="hit", stamp_repo=repo)
    refreshed = _expense(id=99, public_id="pub-99", row_version="rv-99")
    connector._try_autocomplete_expense_after_pull_update = Mock(return_value=refreshed)
    result = _run(connector)
    assert result.id == 99
    repo.stamp_expense_id.assert_called_once_with(
        realm_id="realm-1", qbo_purchase_qbo_id="PURCH-99", expense_id=99,
    )
