"""U-486 Phase F (v2) — a newly imported uncoded 58999 purchase is born draft.

Pure-logic: mock services, no DB. CREATE-only behavior on
PurchaseExpenseConnector._create_expense / sync_from_qbo_purchase MISS path.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
)

SERVICE_MODULE = "integrations.intuit.qbo.purchase.connector.expense.business.service"
LINE_CONNECTOR_PATH = (
    "integrations.intuit.qbo.purchase.connector.expense_line_item.business.service"
    ".PurchaseLineExpenseLineItemConnector"
)

pytestmark = pytest.mark.usefixtures("grant_qbo_app_lock")

UNCODED_ACCOUNT = "Cost of construction : NEED TO CATEGORIZE"


def _make_qbo_purchase(**overrides):
    defaults = dict(
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
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _line(*, account_ref_name=None, item_ref_value=None, line_id=1):
    return SimpleNamespace(
        id=line_id,
        account_ref_name=account_ref_name,
        item_ref_value=item_ref_value,
        qbo_line_id=str(line_id),
    )


def _build_connector():
    expense_service = Mock()
    expense_service.repo = Mock()
    with patch(LINE_CONNECTOR_PATH, return_value=Mock()):
        connector = PurchaseExpenseConnector(expense_service=expense_service)
    connector._get_vendor_public_id = Mock(return_value="vendor-pub-1")
    connector._sync_line_items = Mock()
    return connector, expense_service


def _miss_sync(connector, expense_service, qbo_purchase, lines):
    expense_service.read_by_qbo_identity.return_value = None
    created = SimpleNamespace(id=77, public_id="pub-77", qbo_id=None, realm_id=None)
    expense_service.create.return_value = created
    refreshed = SimpleNamespace(
        id=77, public_id="pub-77", qbo_id=qbo_purchase.qbo_id, realm_id=qbo_purchase.realm_id
    )
    expense_service.read_by_id.return_value = refreshed
    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        return connector.sync_from_qbo_purchase(qbo_purchase, lines)


def _create_kwargs(expense_service):
    expense_service.create.assert_called_once()
    return expense_service.create.call_args.kwargs


# ---------------------------------------------------------------------------
# CREATE — status from purchase lines (58999 predicate requires both halves)
# ---------------------------------------------------------------------------


def test_uncoded_58999_line_creates_expense_as_draft():
    connector, expense_service = _build_connector()
    qbo = _make_qbo_purchase()
    lines = [_line(account_ref_name=UNCODED_ACCOUNT, item_ref_value=None)]

    _miss_sync(connector, expense_service, qbo, lines)
    kwargs = _create_kwargs(expense_service)
    assert kwargs["status"] == "draft"


def test_stale_58999_label_with_item_ref_still_creates_completed():
    """U-484: recode sets ItemRef but leaves AccountRef on the placeholder."""
    connector, expense_service = _build_connector()
    qbo = _make_qbo_purchase()
    lines = [_line(account_ref_name=UNCODED_ACCOUNT, item_ref_value="item-42")]

    _miss_sync(connector, expense_service, qbo, lines)
    kwargs = _create_kwargs(expense_service)
    assert kwargs["status"] == "completed"


def test_no_58999_line_creates_completed():
    connector, expense_service = _build_connector()
    qbo = _make_qbo_purchase()
    lines = [_line(account_ref_name="Materials", item_ref_value=None)]

    _miss_sync(connector, expense_service, qbo, lines)
    kwargs = _create_kwargs(expense_service)
    assert kwargs["status"] == "completed"


def test_multi_line_any_uncoded_58999_among_coded_siblings_is_draft():
    connector, expense_service = _build_connector()
    qbo = _make_qbo_purchase()
    lines = [
        _line(line_id=1, account_ref_name=UNCODED_ACCOUNT, item_ref_value="coded"),
        _line(line_id=2, account_ref_name=UNCODED_ACCOUNT, item_ref_value=None),
    ]

    _miss_sync(connector, expense_service, qbo, lines)
    kwargs = _create_kwargs(expense_service)
    assert kwargs["status"] == "draft"


@pytest.mark.parametrize("existing_status", ["completed", "draft"])
def test_update_hit_path_never_passes_status(existing_status):
    connector, expense_service = _build_connector()
    qbo = _make_qbo_purchase()
    direct = SimpleNamespace(
        id=55,
        public_id="pub-55",
        reference_number="R-1",
        row_version="rv-55",
        status=existing_status,
    )
    expense_service.read_by_qbo_identity.return_value = direct
    expense_service.update_by_public_id.return_value = direct

    with patch(f"{SERVICE_MODULE}.guard_lines_present"):
        connector.sync_from_qbo_purchase(qbo, [_line(account_ref_name=UNCODED_ACCOUNT)])

    expense_service.create.assert_not_called()
    expense_service.update_by_public_id.assert_called_once()
    kwargs = expense_service.update_by_public_id.call_args.kwargs
    assert "status" not in kwargs
    assert "is_draft" not in kwargs


@pytest.mark.parametrize(
    "lines,expected_status",
    [
        ([_line(account_ref_name=UNCODED_ACCOUNT, item_ref_value=None)], "draft"),
        ([_line(account_ref_name=UNCODED_ACCOUNT, item_ref_value="item-1")], "completed"),
        ([_line(account_ref_name="Office supplies", item_ref_value=None)], "completed"),
    ],
)
def test_create_always_sets_qbo_pull_origin_and_source_ref(lines, expected_status):
    connector, expense_service = _build_connector()
    qbo = _make_qbo_purchase(qbo_id="PURCH-42", realm_id="realm-9")

    _miss_sync(connector, expense_service, qbo, lines)
    kwargs = _create_kwargs(expense_service)
    assert kwargs["status"] == expected_status
    assert kwargs["status_origin"] == "qbo_pull"
    assert kwargs["status_source_ref"] == "qbo:realm-9/PURCH-42"
