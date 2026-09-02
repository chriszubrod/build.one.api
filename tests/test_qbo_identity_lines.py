"""Pure-logic tests for U-238b dbo-native QBO identity on line-item entities."""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from integrations.intuit.qbo.base.identity_drift import (
    LINE_ENTITY_SPECS,
    classify_qbo_identity_drift,
    stamp_line_identity_or_warn,
)
from conftest import mock_qbo_app_lock_granted, stub_qbo_identity_fastpath_miss


# ---------------------------------------------------------------------------
# classify_qbo_identity_drift (line entities: QboId + RealmId only)
# ---------------------------------------------------------------------------


def test_line_entity_specs_count():
    # U-361: 4 -> 3 — bill_credit_line_item is dbo-native only now (its mapping
    # table is retired, so its row left the registry; see identity_drift.py).
    # U-362: 3 -> 2 — invoice_line_item is dbo-native only now too.
    assert len(LINE_ENTITY_SPECS) == 2
    assert {s.key for s in LINE_ENTITY_SPECS} == {"bill_line_item", "expense_line_item"}


@pytest.mark.parametrize(
    "dbo_qbo,dbo_realm,has_mapping,staging_qbo,staging_realm,expected",
    [
        (None, None, False, None, None, "match"),
        (None, None, True, "line-1", "realm", "pending_backfill"),
        ("line-99", "realm", False, None, None, "orphan_dbo_value"),
        ("line-1", "realm", True, "line-1", "realm", "match"),
        ("line-1", "realm", True, "line-2", "realm", "drift"),
        ("line-1", "realm-a", True, "line-1", "realm-b", "drift"),
    ],
)
def test_classify_qbo_identity_drift_line_fields(
    dbo_qbo, dbo_realm, has_mapping, staging_qbo, staging_realm, expected
):
    """Line items use has_sync_token=False — same path as Project/Company headers."""
    assert classify_qbo_identity_drift(
        dbo_qbo_id=dbo_qbo,
        dbo_realm_id=dbo_realm,
        dbo_sync_token=None,
        has_mapping=has_mapping,
        staging_qbo_id=staging_qbo,
        staging_realm_id=staging_realm,
        staging_sync_token=None,
        has_sync_token=False,
    ) == expected


# ---------------------------------------------------------------------------
# Repository set_qbo_identity → sproc dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "repo_path,sproc",
    [
        ("entities.bill_line_item.persistence.repo.BillLineItemRepository", "SetBillLineItemQboIdentity"),
        ("entities.invoice_line_item.persistence.repo.InvoiceLineItemRepository", "SetInvoiceLineItemQboIdentity"),
        ("entities.expense_line_item.persistence.repo.ExpenseLineItemRepository", "SetExpenseLineItemQboIdentity"),
        (
            "entities.bill_credit_line_item.persistence.repo.BillCreditLineItemRepository",
            "SetBillCreditLineItemQboIdentity",
        ),
    ],
)
def test_set_qbo_identity_calls_sproc(repo_path, sproc):
    module_path, class_name = repo_path.rsplit(".", 1)
    mod = __import__(module_path, fromlist=[class_name])
    repo_cls = getattr(mod, class_name)
    repo = repo_cls()

    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(Stolen=False)

    expected_params = {"Id": 42, "QboId": "qbo-line-1", "RealmId": "realm-1"}

    with patch(f"{repo_path.rsplit('.', 1)[0]}.get_connection") as mock_conn_ctx, patch(
        f"{repo_path.rsplit('.', 1)[0]}.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        repo.set_qbo_identity(id=42, qbo_id="qbo-line-1", realm_id="realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == sproc
    assert mock_call.call_args.kwargs["params"] == expected_params


@pytest.mark.parametrize(
    "repo_path,entity_label",
    [
        ("entities.bill_line_item.persistence.repo.BillLineItemRepository", "BillLineItem"),
        ("entities.invoice_line_item.persistence.repo.InvoiceLineItemRepository", "InvoiceLineItem"),
        ("entities.expense_line_item.persistence.repo.ExpenseLineItemRepository", "ExpenseLineItem"),
        (
            "entities.bill_credit_line_item.persistence.repo.BillCreditLineItemRepository",
            "BillCreditLineItem",
        ),
    ],
)
def test_set_qbo_identity_stolen_logs_warning(repo_path, entity_label, caplog):
    module_path, class_name = repo_path.rsplit(".", 1)
    mod = __import__(module_path, fromlist=[class_name])
    repo_cls = getattr(mod, class_name)
    repo = repo_cls()

    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(Stolen=True)

    with patch(f"{repo_path.rsplit('.', 1)[0]}.get_connection") as mock_conn_ctx, patch(
        f"{repo_path.rsplit('.', 1)[0]}.call_procedure"
    ):
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        with caplog.at_level("WARNING"):
            repo.set_qbo_identity(id=7, qbo_id="line-stolen", realm_id="realm-x")

    assert any(entity_label in record.message and "stole QBO identity" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# stamp_line_identity_or_warn — U-293-dw atomic-pair guard
# ---------------------------------------------------------------------------
#
# A QBO line id is only unique within its own parent transaction (real
# cross-parent collisions confirmed live — see run_line_identity_fastpath's
# own docstring), so QboId alone is not a complete identity. Before this
# guard, a caller with qbo_id but no realm_id would silently partial-stamp:
# the underlying Set*LineItemQboIdentity sproc's preserve-on-NULL UPDATE sets
# QboId (from the row's own known value) but leaves RealmId untouched — NULL
# on a brand-new row. Confirmed live in prod: dbo.BillLineItem Ids 24621 and
# 24668.
#
# `enforce_realm_pairing` is opt-in (default False): a Claude Workflow review
# (Codex out-of-credits fallback) confirmed that applying the guard blanket to
# all 4 line families would silently regress invoice/expense/bill_credit line
# items' pre-existing unconditional every-touch self-heal, since only
# BillLineItemConnector was given the compensating "fall back to the row's own
# realm_id" fix. Only bill_line_item opts in until each sibling family gets
# the same fallback wired (U-293b).


def test_stamp_line_identity_or_warn_skips_when_enforced_and_realm_id_missing(caplog):
    """Reproduces the exact live-prod shape: qbo_id known, realm_id missing,
    caller opts in. Must not reach the repo at all — a partial stamp is worse
    than no stamp."""
    repo = MagicMock()
    with caplog.at_level("WARNING"):
        stamp_line_identity_or_warn(
            repo, id=24621, qbo_id="2", realm_id=None, context="test", enforce_realm_pairing=True
        )
    repo.set_qbo_identity.assert_not_called()
    assert any("refusing to stamp" in record.message for record in caplog.records)


def test_stamp_line_identity_or_warn_skips_on_empty_string_realm_id_when_enforced():
    """An empty-string realm_id is equally incomplete, not just None."""
    repo = MagicMock()
    stamp_line_identity_or_warn(
        repo, id=1, qbo_id="1", realm_id="", context="test", enforce_realm_pairing=True
    )
    repo.set_qbo_identity.assert_not_called()


def test_stamp_line_identity_or_warn_default_does_not_enforce_pairing():
    """The default (enforce_realm_pairing=False) must behave exactly as
    before this unit — this is what keeps invoice/expense/bill_credit line
    items' pre-existing self-heal-on-every-touch behavior unregressed; those
    3 families' call sites were not changed to opt in."""
    repo = MagicMock()
    stamp_line_identity_or_warn(repo, id=1, qbo_id="1", realm_id=None, context="test")
    repo.set_qbo_identity.assert_called_once_with(id=1, qbo_id="1", realm_id=None)


def test_stamp_line_identity_or_warn_stamps_when_both_present():
    """The healthy, overwhelmingly common case must be unaffected."""
    repo = MagicMock()
    stamp_line_identity_or_warn(
        repo, id=1, qbo_id="1", realm_id="realm-1", context="test", enforce_realm_pairing=True
    )
    repo.set_qbo_identity.assert_called_once_with(id=1, qbo_id="1", realm_id="realm-1")


def test_stamp_line_identity_or_warn_allows_qbo_id_none_with_realm_id_none():
    """qbo_id=None is a legitimate no-op/clear call, not the partial-stamp
    hazard this guard targets — it must still reach the repo unchanged."""
    repo = MagicMock()
    stamp_line_identity_or_warn(repo, id=1, qbo_id=None, realm_id=None, context="test")
    repo.set_qbo_identity.assert_called_once_with(id=1, qbo_id=None, realm_id=None)


# ---------------------------------------------------------------------------
# Connector dual-write (create + update paths)
# ---------------------------------------------------------------------------


def test_invoice_line_connector_create_path_dual_writes_identity():
    """U-362: the CREATE path stamps dbo-native identity via the bare
    repo.set_qbo_identity inside run_line_identity_fastpath_dbo_only's MISS
    branch (no mapping row to create first any more), plus the U-272 source
    provenance mirror on the same stamp call."""
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    invoice_line_item_service = MagicMock()
    invoice_line_item_service.create.return_value = SimpleNamespace(id=200, public_id="ili-pub-1")
    invoice_line_item_service.read_by_invoice_id.return_value = []
    invoice_line_item_service.read_by_id.return_value = SimpleNamespace(
        id=200, public_id="ili-pub-1", qbo_id="QBO-INV-LINE-REAL", realm_id="realm-create",
    )
    invoice_line_item_service.repo = MagicMock()

    connector = InvoiceLineItemConnector(invoice_line_item_service=invoice_line_item_service)
    stub_qbo_identity_fastpath_miss(invoice_line_item_service)
    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-INV-LINE-REAL",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
        line_num=1,
        service_date="2026-07-15",
        linked_txn_type="ReimburseCharge",
        linked_txn_id="RC-1",
        item_ref_value="ITEM-1",
    )

    with patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", mock_qbo_app_lock_granted):
        connector.sync_from_qbo_invoice_line(
            100, "inv-pub", qbo_line, frozenset({"QBO-INV-LINE-REAL"}), realm_id="realm-create"
        )

    invoice_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=200,
        qbo_id="QBO-INV-LINE-REAL",
        realm_id="realm-create",
    )
    invoice_line_item_service.repo.set_source_provenance.assert_called_once_with(
        invoice_line_item_id=200,
        line_num=1,
        qbo_amount=Decimal("100"),
        qbo_description="Service",
        service_date="2026-07-15",
        linked_txn_type="ReimburseCharge",
        linked_txn_id="RC-1",
        item_ref_value="ITEM-1",
    )


def test_invoice_line_connector_update_path_heals_missing_realm_only():
    """U-362: the UPDATE path no longer re-stamps identity on every touch (the
    row was found BY its (InvoiceId, QboId) identity). The one remaining write
    is the U-293-dw realm self-heal for a legacy row stamped with a QboId but
    no RealmId — and only then. The U-272 source provenance mirror still fires
    on every touch regardless (a separate, unconditional mirror)."""
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    def _run(existing):
        invoice_line_item_service = MagicMock()
        invoice_line_item_service.read_by_qbo_identity.return_value = existing
        invoice_line_item_service.update_by_public_id.return_value = existing
        invoice_line_item_service.repo = MagicMock()

        connector = InvoiceLineItemConnector(invoice_line_item_service=invoice_line_item_service)
        qbo_line = SimpleNamespace(
            id=1,
            qbo_line_id="QBO-INV-LINE-UPD",
            description="Service",
            amount=Decimal("100"),
            unit_price=None,
            qty=None,
            line_num=2,
            service_date="2026-07-16",
            linked_txn_type=None,
            linked_txn_id=None,
            item_ref_value="ITEM-2",
        )
        connector.sync_from_qbo_invoice_line(
            100, "inv-pub", qbo_line, frozenset({"QBO-INV-LINE-UPD"}), realm_id="realm-update"
        )
        return invoice_line_item_service

    realm_complete = SimpleNamespace(
        id=200, public_id="ili-pub-1", row_version="rv", qbo_id="QBO-INV-LINE-UPD",
        realm_id="realm-update", source_type="Manual", amount=Decimal("100"),
    )
    _run(realm_complete).repo.set_qbo_identity.assert_not_called()

    realm_missing = SimpleNamespace(
        id=200, public_id="ili-pub-1", row_version="rv", qbo_id="QBO-INV-LINE-UPD",
        realm_id=None, source_type="Manual", amount=Decimal("100"),
    )
    _run(realm_missing).repo.set_qbo_identity.assert_called_once_with(
        id=200,
        qbo_id="QBO-INV-LINE-UPD",
        realm_id="realm-update",
    )


def test_vendor_credit_line_connector_create_path_dual_writes_identity():
    """U-361: the CREATE path stamps dbo-native identity via the bare
    repo.set_qbo_identity inside run_line_identity_fastpath_dbo_only's MISS
    branch (no mapping row to create first any more)."""
    from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
        VendorCreditLineItemConnector,
    )

    bill_credit_line_item_service = MagicMock()
    line_item = SimpleNamespace(id=300, public_id="bcli-pub-300")
    bill_credit_line_item_service.create.return_value = line_item
    bill_credit_line_item_service.read_by_id.return_value = SimpleNamespace(
        id=300, public_id="bcli-pub-300", qbo_id="QBO-VC-LINE-REAL", realm_id="realm-create",
    )
    bill_credit_line_item_service.read_by_bill_credit_id.return_value = []
    bill_credit_line_item_service.repo = MagicMock()

    connector = VendorCreditLineItemConnector()
    connector.bill_credit_line_item_service = bill_credit_line_item_service
    connector._get_project_public_id = MagicMock(return_value=None)
    connector._get_sub_cost_code_id = MagicMock(return_value=None)
    stub_qbo_identity_fastpath_miss(bill_credit_line_item_service)

    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-VC-LINE-REAL",
        description="Credit",
        amount=Decimal("50"),
        qty=Decimal("1"),
        unit_price=Decimal("50"),
        billable_status=None,
        customer_ref_value=None,
        item_ref_value=None,
    )

    with patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", mock_qbo_app_lock_granted):
        connector.sync_from_qbo_line(100, "bc-pub", qbo_line, frozenset({"QBO-VC-LINE-REAL"}), realm_id="realm-create")

    bill_credit_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=300,
        qbo_id="QBO-VC-LINE-REAL",
        realm_id="realm-create",
    )


def test_vendor_credit_line_connector_update_path_heals_missing_realm_only():
    """U-361: the UPDATE path no longer re-stamps identity on every touch (the
    row was found BY its (BillCreditId, QboId) identity). The one remaining
    write is the U-293-dw realm self-heal for a legacy row stamped with a QboId
    but no RealmId — and only then."""
    from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
        VendorCreditLineItemConnector,
    )

    def _run(existing):
        bill_credit_line_item_service = MagicMock()
        bill_credit_line_item_service.read_by_qbo_identity.return_value = existing
        bill_credit_line_item_service.update_by_public_id.return_value = existing
        bill_credit_line_item_service.repo = MagicMock()

        connector = VendorCreditLineItemConnector()
        connector.bill_credit_line_item_service = bill_credit_line_item_service
        connector._get_project_public_id = MagicMock(return_value=None)
        connector._get_sub_cost_code_id = MagicMock(return_value=None)

        qbo_line = SimpleNamespace(
            id=1,
            qbo_line_id="QBO-VC-LINE-UPD",
            description="Credit",
            amount=Decimal("50"),
            qty=Decimal("1"),
            unit_price=Decimal("50"),
            billable_status=None,
            customer_ref_value=None,
            item_ref_value=None,
        )
        connector.sync_from_qbo_line(100, "bc-pub", qbo_line, frozenset({"QBO-VC-LINE-UPD"}), realm_id="realm-update")
        return bill_credit_line_item_service

    realm_complete = SimpleNamespace(
        id=300, public_id="bcli-pub", row_version="rv", qbo_id="QBO-VC-LINE-UPD", realm_id="realm-update",
    )
    _run(realm_complete).repo.set_qbo_identity.assert_not_called()

    realm_missing = SimpleNamespace(
        id=300, public_id="bcli-pub", row_version="rv", qbo_id="QBO-VC-LINE-UPD", realm_id=None,
    )
    _run(realm_missing).repo.set_qbo_identity.assert_called_once_with(
        id=300,
        qbo_id="QBO-VC-LINE-UPD",
        realm_id="realm-update",
    )


def test_invoice_sync_from_qbo_invoice_forwards_realm_id_to_line_connector():
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )

    mapping = SimpleNamespace(id=1, invoice_id=7, qbo_invoice_id=8)
    invoice = SimpleNamespace(
        id=7,
        public_id="inv-pub-7",
        row_version="rv",
        invoice_number="INV-1",
    )
    qbo_invoice = SimpleNamespace(
        id=8,
        qbo_id="INV-QBO",
        realm_id="realm-forward-test",
        customer_ref_value="cust1",
        doc_number="INV-1",
        txn_date="2026-01-01",
        due_date="",
        private_note="",
        total_amt=Decimal("100"),
    )
    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-INV-LINE-FWD",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
    )

    connector = InvoiceInvoiceConnector(
        invoice_service=MagicMock(),
        project_service=MagicMock(),
        qbo_customer_repo=MagicMock(),
        customer_project_repo=MagicMock(),
        reconciliation_repo=MagicMock(),
    )
    # U-356: dbo-only fast path -- a direct dbo.Invoice identity HIT replaces the
    # retired "existing qbo.InvoiceInvoice mapping" UPDATE branch.
    connector.invoice_service.read_by_qbo_identity.return_value = invoice
    connector._get_project_public_id = MagicMock(return_value="proj-pub")
    connector.invoice_service.update_by_public_id.return_value = invoice
    connector.invoice_service.repo = MagicMock()

    with patch(
        "integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service.InvoiceLineItemConnector"
    ) as mock_line_connector_cls:
        connector.sync_from_qbo_invoice(qbo_invoice, [qbo_line])

    # U-362: no more line_mapping_cache to hand the line connector.
    mock_line_connector_cls.assert_called_once_with(
        line_item_cache=connector._line_item_cache,
        caches_preloaded=False,
    )
    mock_line_connector_cls.return_value.sync_from_qbo_invoice_line.assert_called_once_with(
        invoice.id,
        invoice.public_id,
        qbo_line,
        frozenset({"QBO-INV-LINE-FWD"}),
        "realm-forward-test",
    )


def test_invoice_invoice_connector_fresh_instance_caches_not_preloaded():
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )

    connector = InvoiceInvoiceConnector()
    assert connector._caches_preloaded is False


def test_invoice_invoice_connector_preload_caches_sets_flag():
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )

    connector = InvoiceInvoiceConnector(
        invoice_service=MagicMock(read_all=MagicMock(return_value=[])),
    )
    assert connector._caches_preloaded is False

    with patch(
        "entities.invoice_line_item.business.service.InvoiceLineItemService"
    ) as mock_li_svc_cls:
        mock_li_svc_cls.return_value.read_all.return_value = []
        connector.preload_caches()

    assert connector._caches_preloaded is True


def _build_invoice_header_create_connector(created_invoice):
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )

    invoice_service = MagicMock()
    invoice_service.create.return_value = created_invoice
    invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None

    project_service = MagicMock()
    project_service.read_by_public_id.return_value = SimpleNamespace(id=200)

    connector = InvoiceInvoiceConnector(
        invoice_service=invoice_service,
        project_service=project_service,
        qbo_customer_repo=MagicMock(),
        customer_project_repo=MagicMock(),
        reconciliation_repo=MagicMock(),
    )
    stub_qbo_identity_fastpath_miss(connector.invoice_service)
    connector._get_project_public_id = MagicMock(return_value="proj-pub-1")
    connector._find_adoptable_invoice_by_fingerprint = MagicMock(return_value=None)
    connector._sync_line_items = MagicMock()
    return connector


def _make_qbo_invoice_for_header_create():
    return SimpleNamespace(
        id=5001,
        qbo_id="INV-QBO",
        customer_ref_value="cust1",
        realm_id="realm-1",
        doc_number="INV-100",
        txn_date="2026-07-01",
        due_date="2026-07-15",
        private_note="note",
        total_amt=Decimal("100"),
    )


# U-356: the two compensating-delete tests below used to fire on a
# `create_mapping` failure (a qbo.InvoiceInvoice insert). That mapping is
# retired; the equivalent hazard is now a failed dbo identity STAMP on the
# just-created header — the identity-stamp rollback race fix (U-354/U-355
# pattern). Same assertions, different trigger.


@patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", mock_qbo_app_lock_granted)
def test_sync_from_qbo_invoice_compensating_delete_on_identity_stamp_failure():
    created_invoice = SimpleNamespace(id=1057, public_id="inv-pub-1057")
    connector = _build_invoice_header_create_connector(created_invoice)
    connector.invoice_service.repo.set_qbo_identity.side_effect = ValueError("stamp refused")

    with pytest.raises(ValueError, match="stamp refused"):
        connector.sync_from_qbo_invoice(_make_qbo_invoice_for_header_create(), [])

    connector.invoice_service.delete_by_public_id.assert_called_once_with("inv-pub-1057")
    connector._sync_line_items.assert_not_called()


@patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", mock_qbo_app_lock_granted)
def test_sync_from_qbo_invoice_compensating_delete_on_database_constraint_error():
    from shared.database import DatabaseConstraintError
    from shared.db_constraints import UNIQUE_VIOLATION

    created_invoice = SimpleNamespace(id=1057, public_id="inv-pub-1057")
    connector = _build_invoice_header_create_connector(created_invoice)
    connector.invoice_service.repo.set_qbo_identity.side_effect = DatabaseConstraintError(
        UNIQUE_VIOLATION, "duplicate identity"
    )

    with pytest.raises(DatabaseConstraintError):
        connector.sync_from_qbo_invoice(_make_qbo_invoice_for_header_create(), [])

    connector.invoice_service.delete_by_public_id.assert_called_once_with("inv-pub-1057")
    connector._sync_line_items.assert_not_called()
