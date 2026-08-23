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
    assert len(LINE_ENTITY_SPECS) == 4


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
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_invoice_line_id.return_value = None
    mapping_repo.read_by_invoice_line_item_id.return_value = None
    mapping_repo.create.return_value = SimpleNamespace(id=1)

    invoice_line_item_service = MagicMock()
    invoice_line_item_service.create.return_value = SimpleNamespace(id=200, public_id="ili-pub-1")
    invoice_line_item_service.repo = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
    )
    connector._find_and_match_manual_by_fingerprint = MagicMock(return_value=None)
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

    connector.sync_from_qbo_invoice_line(100, "inv-pub", qbo_line, realm_id="realm-create")

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


def test_invoice_line_connector_update_path_dual_writes_identity():
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    mapping = SimpleNamespace(id=10, invoice_line_item_id=200)
    line_item = SimpleNamespace(id=200, public_id="ili-pub-1", row_version="rv", amount=Decimal("100"))

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_invoice_line_id.return_value = mapping

    invoice_line_item_service = MagicMock()
    invoice_line_item_service.read_by_id.return_value = line_item
    invoice_line_item_service.update_by_public_id.return_value = line_item
    invoice_line_item_service.repo = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
    )
    stub_qbo_identity_fastpath_miss(invoice_line_item_service)
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

    connector.sync_from_qbo_invoice_line(100, "inv-pub", qbo_line, realm_id="realm-update")

    invoice_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=200,
        qbo_id="QBO-INV-LINE-UPD",
        realm_id="realm-update",
    )
    invoice_line_item_service.repo.set_source_provenance.assert_called_once_with(
        invoice_line_item_id=200,
        line_num=2,
        qbo_amount=Decimal("100"),
        qbo_description="Service",
        service_date="2026-07-16",
        linked_txn_type=None,
        linked_txn_id=None,
        item_ref_value="ITEM-2",
    )


def test_vendor_credit_line_connector_create_path_dual_writes_identity():
    from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
        VendorCreditLineItemConnector,
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_line_id.return_value = None

    bill_credit_line_item_service = MagicMock()
    line_item = SimpleNamespace(id=300)
    bill_credit_line_item_service.create.return_value = line_item
    bill_credit_line_item_service.repo = MagicMock()

    connector = VendorCreditLineItemConnector()
    connector.mapping_repo = mapping_repo
    connector.bill_credit_line_item_service = bill_credit_line_item_service
    connector._get_project_public_id = MagicMock(return_value=None)
    connector._get_sub_cost_code_id = MagicMock(return_value=None)
    connector._match_unmapped_by_fingerprint = MagicMock(return_value=None)
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

    connector.sync_from_qbo_line(100, "bc-pub", qbo_line, realm_id="realm-create")

    bill_credit_line_item_service.repo.set_qbo_identity.assert_called_once_with(
        id=300,
        qbo_id="QBO-VC-LINE-REAL",
        realm_id="realm-create",
    )


def test_vendor_credit_line_connector_update_path_dual_writes_identity():
    from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
        VendorCreditLineItemConnector,
    )

    mapping = SimpleNamespace(id=10, bill_credit_line_item_id=300)
    existing = SimpleNamespace(id=300, public_id="bcli-pub", row_version="rv")

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_line_id.return_value = mapping

    bill_credit_line_item_service = MagicMock()
    bill_credit_line_item_service.read_by_id.return_value = existing
    bill_credit_line_item_service.update_by_public_id.return_value = existing
    bill_credit_line_item_service.repo = MagicMock()

    connector = VendorCreditLineItemConnector()
    connector.mapping_repo = mapping_repo
    connector.bill_credit_line_item_service = bill_credit_line_item_service
    connector._get_project_public_id = MagicMock(return_value=None)
    connector._get_sub_cost_code_id = MagicMock(return_value=None)
    stub_qbo_identity_fastpath_miss(bill_credit_line_item_service)

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

    connector.sync_from_qbo_line(100, "bc-pub", qbo_line, realm_id="realm-update")

    bill_credit_line_item_service.repo.set_qbo_identity.assert_called_once_with(
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
        mapping_repo=MagicMock(),
        line_mapping_repo=MagicMock(),
        invoice_service=MagicMock(),
        project_service=MagicMock(),
        qbo_customer_repo=MagicMock(),
        customer_project_repo=MagicMock(),
        reconciliation_repo=MagicMock(),
    )
    stub_qbo_identity_fastpath_miss(connector.invoice_service)
    connector.mapping_repo.read_by_qbo_invoice_id.return_value = mapping
    connector._get_project_public_id = MagicMock(return_value="proj-pub")
    connector.invoice_service.read_by_id.return_value = invoice
    connector.invoice_service.update_by_public_id.return_value = invoice
    connector.invoice_service.repo = MagicMock()

    with patch(
        "integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service.InvoiceLineItemConnector"
    ) as mock_line_connector_cls:
        connector.sync_from_qbo_invoice(qbo_invoice, [qbo_line])

    mock_line_connector_cls.assert_called_once_with(
        line_mapping_cache=connector._line_mapping_cache,
        line_item_cache=connector._line_item_cache,
        caches_preloaded=False,
    )
    mock_line_connector_cls.return_value.sync_from_qbo_invoice_line.assert_called_once_with(
        invoice.id,
        invoice.public_id,
        qbo_line,
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
        mapping_repo=MagicMock(read_all=MagicMock(return_value=[])),
        line_mapping_repo=MagicMock(read_all=MagicMock(return_value=[])),
        invoice_service=MagicMock(read_all=MagicMock(return_value=[])),
    )
    assert connector._caches_preloaded is False

    with patch(
        "entities.invoice_line_item.business.service.InvoiceLineItemService"
    ) as mock_li_svc_cls:
        mock_li_svc_cls.return_value.read_all.return_value = []
        connector.preload_caches()

    assert connector._caches_preloaded is True


# ---------------------------------------------------------------------------
# U-247: fingerprint ambiguity, cache-vs-DB, compensating delete
# ---------------------------------------------------------------------------


def test_find_and_match_manual_by_fingerprint_adopts_lowest_id_on_ambiguity():
    """Mutation-test: reverting sort-and-pick-lowest back to return None must fail."""
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    li_low = SimpleNamespace(
        id=100,
        invoice_id=7,
        source_type="Manual",
        description="Work",
        amount=Decimal("50"),
    )
    li_high = SimpleNamespace(
        id=200,
        invoice_id=7,
        source_type="Manual",
        description="Work",
        amount=Decimal("50"),
    )
    li_other_invoice = SimpleNamespace(
        id=1,
        invoice_id=99,
        source_type="Manual",
        description="Work",
        amount=Decimal("50"),
    )

    mapping_repo = MagicMock()
    invoice_line_item_service = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
        line_item_cache={100: li_low, 200: li_high, 1: li_other_invoice},
        line_mapping_cache={
            999: SimpleNamespace(invoice_line_item_id=999, qbo_invoice_line_id=999),
        },
        caches_preloaded=True,
    )

    with patch("entities.invoice_line_item.business.service.InvoiceLineItemService") as mock_svc_cls:
        result = connector._find_and_match_manual_by_fingerprint(
            invoice_id=7,
            description="Work",
            amount=Decimal("50"),
        )

    assert result is not None
    assert int(result.id) == 100
    mock_svc_cls.assert_not_called()
    mapping_repo.read_by_invoice_line_item_id.assert_not_called()


def test_find_and_match_manual_by_fingerprint_uses_cache_not_db():
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    li = SimpleNamespace(
        id=42,
        invoice_id=7,
        source_type="Manual",
        description="Cached",
        amount=Decimal("10"),
    )
    mapped_li = SimpleNamespace(
        id=99,
        invoice_id=7,
        source_type="Manual",
        description="Cached",
        amount=Decimal("10"),
    )
    mapping = SimpleNamespace(invoice_line_item_id=99, qbo_invoice_line_id=1)

    mapping_repo = MagicMock()
    invoice_line_item_service = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
        line_item_cache={42: li, 99: mapped_li},
        line_mapping_cache={1: mapping},
        caches_preloaded=True,
    )

    with patch("entities.invoice_line_item.business.service.InvoiceLineItemService") as mock_svc_cls:
        result = connector._find_and_match_manual_by_fingerprint(
            invoice_id=7,
            description="Cached",
            amount=Decimal("10"),
        )

    assert result is li
    mock_svc_cls.assert_not_called()
    mapping_repo.read_by_invoice_line_item_id.assert_not_called()


def test_find_and_match_manual_by_fingerprint_falls_back_to_db_when_cache_empty():
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    li = SimpleNamespace(
        id=42,
        invoice_id=7,
        source_type="Manual",
        description="DB",
        amount=Decimal("10"),
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_invoice_line_item_id.return_value = None
    invoice_line_item_service = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
    )

    with patch("entities.invoice_line_item.business.service.InvoiceLineItemService") as mock_svc_cls:
        mock_svc_cls.return_value.read_by_invoice_id.return_value = [li]
        result = connector._find_and_match_manual_by_fingerprint(
            invoice_id=7,
            description="DB",
            amount=Decimal("10"),
        )

    assert result is li
    mock_svc_cls.return_value.read_by_invoice_id.assert_called_once_with(7)
    mapping_repo.read_by_invoice_line_item_id.assert_called_once_with(42)


def test_find_and_match_manual_by_fingerprint_ignores_partial_cache_without_preload():
    """Regression: non-empty but incomplete cache must not block DB fallback (outbox path)."""
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    partial_cached = SimpleNamespace(
        id=10,
        invoice_id=7,
        source_type="Manual",
        description="Other",
        amount=Decimal("5"),
    )
    orphan = SimpleNamespace(
        id=42,
        invoice_id=7,
        source_type="Manual",
        description="Orphan",
        amount=Decimal("10"),
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_invoice_line_item_id.return_value = None
    invoice_line_item_service = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
        line_item_cache={10: partial_cached},
    )

    with patch("entities.invoice_line_item.business.service.InvoiceLineItemService") as mock_svc_cls:
        mock_svc_cls.return_value.read_by_invoice_id.return_value = [partial_cached, orphan]
        result = connector._find_and_match_manual_by_fingerprint(
            invoice_id=7,
            description="Orphan",
            amount=Decimal("10"),
        )

    assert result is orphan
    mock_svc_cls.return_value.read_by_invoice_id.assert_called_once_with(7)
    mapping_repo.read_by_invoice_line_item_id.assert_any_call(42)


def test_sync_from_qbo_invoice_line_ignores_partial_cache_without_preload():
    """Regression: partial cache must not skip DB reads or delete valid mappings (outbox path)."""
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    unrelated_line = SimpleNamespace(
        id=10,
        public_id="ili-unrelated",
        invoice_id=7,
        source_type="Manual",
        description="Other",
        amount=Decimal("5"),
        row_version="rv1",
    )
    real_line = SimpleNamespace(
        id=42,
        public_id="ili-real",
        invoice_id=7,
        source_type="Manual",
        description="Service",
        amount=Decimal("100"),
        row_version="rv42",
    )
    real_mapping = SimpleNamespace(
        id=99,
        invoice_line_item_id=42,
        qbo_invoice_line_id=55,
    )
    unrelated_mapping = SimpleNamespace(
        id=88,
        invoice_line_item_id=10,
        qbo_invoice_line_id=11,
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_invoice_line_id.return_value = real_mapping

    invoice_line_item_service = MagicMock()
    invoice_line_item_service.read_by_id.return_value = real_line
    updated_line = SimpleNamespace(**vars(real_line))
    invoice_line_item_service.update_by_public_id.return_value = updated_line
    invoice_line_item_service.repo = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
        line_item_cache={10: unrelated_line},
        line_mapping_cache={11: unrelated_mapping},
        caches_preloaded=False,
    )
    stub_qbo_identity_fastpath_miss(invoice_line_item_service)

    qbo_line = SimpleNamespace(
        id=55,
        qbo_line_id="QBO-INV-LINE-55",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
        line_num=None,
        service_date=None,
        linked_txn_type=None,
        linked_txn_id=None,
        item_ref_value=None,
    )

    with patch(
        "integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service.stamp_line_identity_or_warn"
    ):
        result = connector.sync_from_qbo_invoice_line(100, "inv-pub", qbo_line)

    mapping_repo.read_by_qbo_invoice_line_id.assert_called_once_with(55)
    invoice_line_item_service.read_by_id.assert_called_once_with(42)
    mapping_repo.delete_by_id.assert_not_called()
    invoice_line_item_service.create.assert_not_called()
    assert result is updated_line


def test_sync_from_qbo_invoice_line_compensating_delete_on_mapping_failure():
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_invoice_line_id.return_value = None

    created_line = SimpleNamespace(id=999, public_id="ili-pub-999")
    invoice_line_item_service = MagicMock()
    invoice_line_item_service.create.return_value = created_line
    invoice_line_item_service.repo = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
        line_item_cache={},
        line_mapping_cache={},
    )
    stub_qbo_identity_fastpath_miss(invoice_line_item_service)
    connector._find_and_match_manual_by_fingerprint = MagicMock(return_value=None)
    connector.create_mapping = MagicMock(side_effect=ValueError("already mapped"))

    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-INV-LINE-ORPHAN",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
    )

    with pytest.raises(ValueError, match="already mapped"):
        connector.sync_from_qbo_invoice_line(100, "inv-pub", qbo_line)

    invoice_line_item_service.repo.delete_by_id.assert_called_once_with(999)
    invoice_line_item_service.repo.set_qbo_identity.assert_not_called()
    assert 999 not in connector._line_item_cache


def test_sync_from_qbo_invoice_line_compensating_delete_on_database_constraint_error():
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )
    from shared.database import DatabaseConstraintError
    from shared.db_constraints import UNIQUE_VIOLATION

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_invoice_line_id.return_value = None

    created_line = SimpleNamespace(id=999, public_id="ili-pub-999")
    invoice_line_item_service = MagicMock()
    invoice_line_item_service.create.return_value = created_line
    invoice_line_item_service.repo = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
        line_item_cache={999: created_line},
        line_mapping_cache={},
    )
    stub_qbo_identity_fastpath_miss(invoice_line_item_service)
    connector._find_and_match_manual_by_fingerprint = MagicMock(return_value=None)
    connector.create_mapping = MagicMock(
        side_effect=DatabaseConstraintError(UNIQUE_VIOLATION, "duplicate mapping")
    )

    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-INV-LINE-ORPHAN",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
    )

    with pytest.raises(DatabaseConstraintError):
        connector.sync_from_qbo_invoice_line(100, "inv-pub", qbo_line)

    invoice_line_item_service.repo.delete_by_id.assert_called_once_with(999)
    assert 999 not in connector._line_item_cache


def test_sync_from_qbo_invoice_line_compensating_delete_cleanup_failure_still_raises_original():
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_invoice_line_id.return_value = None

    created_line = SimpleNamespace(id=999, public_id="ili-pub-999")
    invoice_line_item_service = MagicMock()
    invoice_line_item_service.create.return_value = created_line
    invoice_line_item_service.repo = MagicMock()
    invoice_line_item_service.repo.delete_by_id.side_effect = RuntimeError("cleanup failed")

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
        line_item_cache={999: created_line},
        line_mapping_cache={},
    )
    stub_qbo_identity_fastpath_miss(invoice_line_item_service)
    connector._find_and_match_manual_by_fingerprint = MagicMock(return_value=None)
    connector.create_mapping = MagicMock(side_effect=ValueError("already mapped"))

    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-INV-LINE-ORPHAN",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
    )

    with pytest.raises(ValueError, match="already mapped"):
        connector.sync_from_qbo_invoice_line(100, "inv-pub", qbo_line)

    invoice_line_item_service.repo.delete_by_id.assert_called_once_with(999)


def test_sync_from_qbo_invoice_line_adopt_failure_does_not_fall_through_to_create():
    from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import (
        InvoiceLineItemConnector,
    )

    orphan = SimpleNamespace(
        id=42,
        invoice_id=100,
        source_type="Manual",
        description="Service",
        amount=Decimal("100"),
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_invoice_line_id.return_value = None
    invoice_line_item_service = MagicMock()

    connector = InvoiceLineItemConnector(
        mapping_repo=mapping_repo,
        invoice_line_item_service=invoice_line_item_service,
        line_item_cache={42: orphan},
        line_mapping_cache={},
    )
    stub_qbo_identity_fastpath_miss(invoice_line_item_service)
    connector._find_and_match_manual_by_fingerprint = MagicMock(return_value=orphan)
    connector.create_mapping = MagicMock(side_effect=ValueError("already mapped"))

    qbo_line = SimpleNamespace(
        id=1,
        qbo_line_id="QBO-INV-LINE-ORPHAN",
        description="Service",
        amount=Decimal("100"),
        unit_price=None,
        qty=None,
    )

    with pytest.raises(ValueError, match="already mapped"):
        connector.sync_from_qbo_invoice_line(100, "inv-pub", qbo_line)

    invoice_line_item_service.create.assert_not_called()


def _build_invoice_header_create_connector(created_invoice):
    from integrations.intuit.qbo.invoice.connector.invoice.business.service import (
        InvoiceInvoiceConnector,
    )

    mapping_repo = MagicMock()
    mapping_repo.read_by_qbo_invoice_id.return_value = None

    invoice_service = MagicMock()
    invoice_service.create.return_value = created_invoice
    invoice_service.repo.read_by_invoice_number_and_project_id.return_value = None

    project_service = MagicMock()
    project_service.read_by_public_id.return_value = SimpleNamespace(id=200)

    connector = InvoiceInvoiceConnector(
        mapping_repo=mapping_repo,
        line_mapping_repo=MagicMock(),
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


@patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", mock_qbo_app_lock_granted)
def test_sync_from_qbo_invoice_compensating_delete_on_mapping_failure():
    created_invoice = SimpleNamespace(id=1057, public_id="inv-pub-1057")
    connector = _build_invoice_header_create_connector(created_invoice)
    connector.create_mapping = MagicMock(side_effect=ValueError("already mapped"))

    with pytest.raises(ValueError, match="already mapped"):
        connector.sync_from_qbo_invoice(_make_qbo_invoice_for_header_create(), [])

    connector.invoice_service.delete_by_public_id.assert_called_once_with("inv-pub-1057")
    connector._sync_line_items.assert_not_called()


@patch("integrations.intuit.qbo.base.identity_fastpath.qbo_app_lock", mock_qbo_app_lock_granted)
def test_sync_from_qbo_invoice_compensating_delete_on_database_constraint_error():
    from shared.database import DatabaseConstraintError
    from shared.db_constraints import UNIQUE_VIOLATION

    created_invoice = SimpleNamespace(id=1057, public_id="inv-pub-1057")
    connector = _build_invoice_header_create_connector(created_invoice)
    connector.create_mapping = MagicMock(
        side_effect=DatabaseConstraintError(UNIQUE_VIOLATION, "duplicate mapping")
    )

    with pytest.raises(DatabaseConstraintError):
        connector.sync_from_qbo_invoice(_make_qbo_invoice_for_header_create(), [])

    connector.invoice_service.delete_by_public_id.assert_called_once_with("inv-pub-1057")
    connector._sync_line_items.assert_not_called()
