"""Pure-logic tests for U-238a dbo-native QBO identity on header entities."""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import stub_qbo_identity_fastpath_miss
from integrations.intuit.qbo.base.identity_drift import classify_qbo_identity_drift
from integrations.intuit.qbo.company_info.connector.business.service import CompanyInfoCompanyConnector
from integrations.intuit.qbo.customer.connector.project.business.service import CustomerProjectConnector
from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector


# ---------------------------------------------------------------------------
# classify_qbo_identity_drift
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "dbo_qbo,dbo_realm,has_mapping,staging_qbo,staging_realm,expected",
    [
        (None, None, False, None, None, "match"),
        (None, None, True, "1", "realm", "pending_backfill"),
        ("99", "realm", False, None, None, "orphan_dbo_value"),
        ("1", "realm", True, "1", "realm", "match"),
        ("1", "realm", True, "2", "realm", "drift"),
        ("1", "realm-a", True, "1", "realm-b", "drift"),
    ],
)
def test_classify_qbo_identity_drift_header_fields(
    dbo_qbo, dbo_realm, has_mapping, staging_qbo, staging_realm, expected
):
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


def test_classify_drift_sync_token_mismatch():
    assert (
        classify_qbo_identity_drift(
            dbo_qbo_id="1",
            dbo_realm_id="r",
            dbo_sync_token="tok-a",
            has_mapping=True,
            staging_qbo_id="1",
            staging_realm_id="r",
            staging_sync_token="tok-b",
            has_sync_token=True,
        )
        == "drift"
    )


def test_classify_match_sync_token_when_enabled():
    assert (
        classify_qbo_identity_drift(
            dbo_qbo_id="1",
            dbo_realm_id="r",
            dbo_sync_token="tok",
            has_mapping=True,
            staging_qbo_id="1",
            staging_realm_id="r",
            staging_sync_token="tok",
            has_sync_token=True,
        )
        == "match"
    )


# ---------------------------------------------------------------------------
# Repository set_qbo_identity → sproc dispatch
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "repo_path,sproc,extra_params",
    [
        ("entities.bill.persistence.repo.BillRepository", "SetBillQboIdentity", {"SyncToken": "st1"}),
        ("entities.expense.persistence.repo.ExpenseRepository", "SetExpenseQboIdentity", {"SyncToken": "st2"}),
        ("entities.invoice.persistence.repo.InvoiceRepository", "SetInvoiceQboIdentity", {"SyncToken": "st3"}),
        ("entities.project.persistence.repo.ProjectRepository", "SetProjectQboIdentity", {}),
        ("entities.company.persistence.repo.CompanyRepository", "SetCompanyQboIdentity", {}),
    ],
)
def test_set_qbo_identity_calls_sproc(repo_path, sproc, extra_params):
    module_path, class_name = repo_path.rsplit(".", 1)
    mod = __import__(module_path, fromlist=[class_name])
    repo_cls = getattr(mod, class_name)
    repo = repo_cls()

    cursor = MagicMock()
    cursor.fetchone.return_value = SimpleNamespace(Id=1)

    expected_params = {"Id": 42, "QboId": "qbo-1", "RealmId": "realm-1", **extra_params}

    with patch(f"{repo_path.rsplit('.', 1)[0]}.get_connection") as mock_conn_ctx, patch(
        f"{repo_path.rsplit('.', 1)[0]}.call_procedure"
    ) as mock_call:
        mock_conn_ctx.return_value.__enter__.return_value.cursor.return_value = cursor
        if "SyncToken" in extra_params:
            repo.set_qbo_identity(
                id=42,
                qbo_id="qbo-1",
                realm_id="realm-1",
                sync_token=extra_params["SyncToken"],
            )
        else:
            repo.set_qbo_identity(id=42, qbo_id="qbo-1", realm_id="realm-1")

    mock_call.assert_called_once()
    assert mock_call.call_args.kwargs["name"] == sproc
    assert mock_call.call_args.kwargs["params"] == expected_params


# ---------------------------------------------------------------------------
# Connector dual-write call sites
# ---------------------------------------------------------------------------


def _make_invoice_connector():
    invoice_service = Mock()
    invoice_service.repo = Mock()
    stub_qbo_identity_fastpath_miss(invoice_service)
    connector = InvoiceInvoiceConnector(
        line_mapping_repo=Mock(), invoice_service=invoice_service, reconciliation_repo=Mock()
    )
    return connector, invoice_service.repo


def _qbo_invoice(**overrides):
    defaults = dict(
        id=8,
        qbo_id="INV-QBO",
        realm_id="realm-z",
        sync_token="tok",
        customer_ref_value="cust1",
        doc_number="INV-1",
        txn_date="2026-01-01",
        due_date="",
        private_note="",
        total_amt=100,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.usefixtures("grant_qbo_app_lock")
def test_invoice_create_path_stamps_dbo_identity_only():
    """U-356: the create path stamps dbo.Invoice.QboId/RealmId/SyncToken ONLY —
    there is no qbo.InvoiceInvoice mapping row left to dual-write (the table +
    its repo are retired). dbo identity is the sole store."""
    connector, invoice_repo = _make_invoice_connector()
    connector._get_project_public_id = Mock(return_value="proj-pub")
    connector.project_service = Mock()
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=5)
    invoice_repo.read_by_invoice_number_and_project_id.return_value = None
    connector._find_adoptable_invoice_by_fingerprint = Mock(return_value=None)
    connector.invoice_service.create.return_value = SimpleNamespace(id=7, public_id="inv7")
    connector.invoice_service.read_by_id.return_value = SimpleNamespace(id=7, public_id="inv7")
    connector._sync_line_items = Mock()

    connector.sync_from_qbo_invoice(_qbo_invoice(), [])

    invoice_repo.set_qbo_identity.assert_called_once_with(
        id=7, qbo_id="INV-QBO", realm_id="realm-z", sync_token="tok"
    )


@pytest.mark.usefixtures("grant_qbo_app_lock")
def test_invoice_create_path_identity_failure_propagates():
    connector, invoice_repo = _make_invoice_connector()
    connector._get_project_public_id = Mock(return_value="proj-pub")
    connector.project_service = Mock()
    connector.project_service.read_by_public_id.return_value = SimpleNamespace(id=5)
    invoice_repo.read_by_invoice_number_and_project_id.return_value = None
    connector._find_adoptable_invoice_by_fingerprint = Mock(return_value=None)
    connector.invoice_service.create.return_value = SimpleNamespace(id=7, public_id="inv7")
    connector._sync_line_items = Mock()
    invoice_repo.set_qbo_identity.side_effect = RuntimeError("stamp failed")

    with pytest.raises(RuntimeError, match="stamp failed"):
        connector.sync_from_qbo_invoice(_qbo_invoice(), [])
    connector._sync_line_items.assert_not_called()


def _make_project_connector():
    project_service = Mock()
    project_service.repo = Mock()
    # U-276: the connector tries a direct dbo-identity lookup before the
    # mapping-table path these tests exercise. Default it to a miss so
    # existing fallback-path assertions are unaffected.
    project_service.read_by_qbo_identity.return_value = None
    connector = CustomerProjectConnector(
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=Mock(),
        # U-297: never used here (every fixture sets parent_ref_value=None) —
        # injected so a truthy-parent test can't default to live-DB collaborators.
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
    )
    connector._sync_addresses = Mock()
    return connector, project_service.repo


def test_project_create_mapping_stamps_dbo_identity_only():
    """U-314: create_mapping stamps dbo.Project.QboId/RealmId ONLY — it no
    longer writes a qbo.CustomerProject mapping row (that table + its Python
    repo are dropped/deleted entirely in this unit). dbo.Project identity is
    the sole store."""
    connector, project_repo = _make_project_connector()
    connector.create_mapping(
        project_id=3,
        qbo_customer_id=4,
        qbo_id="C-1",
        realm_id="realm-p",
    )
    project_repo.set_qbo_identity.assert_called_once_with(id=3, qbo_id="C-1", realm_id="realm-p")


# test_project_repoint_heal_stamps_identity removed U-311 -- it tested
# sync_from_qbo_customer's OLD "mapping exists but Project missing, heal by
# name" branch, which Wave-5 Option B retired entirely (no mapping table left
# to go stale). The dbo-only equivalent (name-match adopt via
# _resolve_project_candidate + _stamp_project_identity) is tested in
# tests/test_u276_customer_project_qbo_identity_repoint.py.


def test_project_create_mapping_identity_failure_propagates():
    connector, project_repo = _make_project_connector()
    project_repo.set_qbo_identity.side_effect = RuntimeError("stamp failed")
    with pytest.raises(RuntimeError, match="stamp failed"):
        connector.create_mapping(
            project_id=3,
            qbo_customer_id=4,
            qbo_id="C-1",
            realm_id="realm-p",
        )


# test_project_ordinary_update_path_stamps_identity removed U-311 -- it
# tested sync_from_qbo_customer's OLD "existing mapping -> update Project"
# branch, retired the same way (see the note above
# test_project_create_mapping_identity_failure_propagates). The dbo-only fast
# path's HIT-branch identity refresh is covered in test_u276.


def _make_company_connector():
    company_service = Mock()
    company_service.repo = Mock()
    connector = CompanyInfoCompanyConnector(
        company_service=company_service,
    )
    return connector, company_service.repo


def test_company_create_mapping_stamps_identity():
    connector, company_repo = _make_company_connector()
    connector.create_mapping(
        company_id=1,
        qbo_company_info_id=2,
        qbo_id="CI-1",
        realm_id="realm-c",
    )
    company_repo.set_qbo_identity.assert_called_once_with(
        id=1, qbo_id="CI-1", realm_id="realm-c"
    )


def test_company_create_mapping_identity_failure_propagates():
    connector, company_repo = _make_company_connector()
    company_repo.set_qbo_identity.side_effect = RuntimeError("stamp failed")
    with pytest.raises(RuntimeError, match="stamp failed"):
        connector.create_mapping(
            company_id=1,
            qbo_company_info_id=2,
            qbo_id="CI-1",
            realm_id="realm-c",
        )


# ---------------------------------------------------------------------------
# FIX 5: UPDATE/re-pull branch dual-write
# NOTE: FIX 1/2 sproc no-op + steal guards are SQL-only; this harness has no live DB,
# so those behaviors are not regression-tested here — the guards live in the sprocs themselves.
# ---------------------------------------------------------------------------


def test_invoice_update_path_stamps_identity():
    """U-356: the "existing invoice" UPDATE path is a direct dbo identity HIT
    (read_by_qbo_identity), re-stamping SyncToken on every pull."""
    connector, invoice_repo = _make_invoice_connector()
    invoice = SimpleNamespace(id=7, public_id="inv7", row_version="rv", invoice_number="INV-1")
    connector.invoice_service.read_by_qbo_identity.return_value = invoice
    connector._get_project_public_id = Mock(return_value="proj-pub")
    connector.invoice_service.update_by_public_id = Mock(return_value=invoice)
    connector._sync_line_items = Mock()

    connector.sync_from_qbo_invoice(_qbo_invoice(sync_token="tok-new"), [])

    invoice_repo.set_qbo_identity.assert_called_once_with(
        id=7, qbo_id="INV-QBO", realm_id="realm-z", sync_token="tok-new"
    )


@pytest.mark.usefixtures("grant_qbo_app_lock")
def test_invoice_adopt_update_path_stamps_identity():
    """U-356: the adopt path stamps under stamp_dbo_identity_with_lock (the
    candidate's own lock + theft-guard), against the fresh by-id re-read."""
    connector, invoice_repo = _make_invoice_connector()
    existing = SimpleNamespace(
        id=99,
        public_id="inv99",
        row_version="rv",
        invoice_number="INV-1",
        total_amount=Decimal("100.00"),
        invoice_date="2026-01-01",
        qbo_id=None,
        realm_id=None,
    )
    proj = SimpleNamespace(id=5, public_id="proj5")
    connector._get_project_public_id = Mock(return_value="proj5")
    connector.project_service.read_by_public_id = Mock(return_value=proj)
    connector.invoice_service.repo.read_by_invoice_number_and_project_id = Mock(return_value=existing)
    connector.invoice_service.read_by_id = Mock(return_value=existing)
    connector._header_fingerprint_matches = Mock(return_value=True)
    connector._has_qbo_line_provenance = Mock(return_value=True)
    connector.invoice_service.update_by_public_id = Mock(return_value=existing)
    connector._sync_line_items = Mock()

    with patch(
        "entities.invoice_line_item.business.service.InvoiceLineItemService"
    ) as mock_lis:
        mock_lis.return_value.read_by_invoice_id.return_value = [SimpleNamespace()]

        connector.sync_from_qbo_invoice(_qbo_invoice(sync_token="tok-adopt"), [])

    invoice_repo.set_qbo_identity.assert_called_once_with(
        id=99, qbo_id="INV-QBO", realm_id="realm-z", sync_token="tok-adopt"
    )
