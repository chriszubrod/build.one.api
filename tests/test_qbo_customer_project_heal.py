"""Pure-logic tests for CustomerProject heal-don't-delete mapping fixes (U-022)
and InvoiceInvoiceConnector's project-resolver heal-by-name fallback (U-311).
See each connector's own dedicated repoint suite (test_u276/u278/u283/u283b)
for the dbo-only fast-path/verify coverage this file used to duplicate."""
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from integrations.intuit.qbo.customer.connector.project.business.service import CustomerProjectConnector
from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector
from conftest import stub_qbo_identity_fastpath_miss

# The invoice connector imports CustomerProjectConnector lazily from its defining module,
# so the heal auto-heal path is patched where the class is defined.
HEAL_CONNECTOR_PATH = (
    "integrations.intuit.qbo.customer.connector.project.business.service.CustomerProjectConnector"
)


def _make_qbo_customer(
    *,
    customer_id=1,
    qbo_id="QBO-100",
    display_name="OHR2 - Chapel",
    company_name=None,
    is_job=True,
    active=True,
    notes="",
    realm_id="realm-1",
    parent_ref_value=None,
    bill_addr_id=None,
    ship_addr_id=None,
):
    return SimpleNamespace(
        id=customer_id,
        qbo_id=qbo_id,
        display_name=display_name,
        company_name=company_name,
        is_job=is_job,
        active=active,
        notes=notes,
        realm_id=realm_id,
        parent_ref_value=parent_ref_value,
        bill_addr_id=bill_addr_id,
        ship_addr_id=ship_addr_id,
    )


def _make_project(
    *,
    project_id=200,
    public_id="proj-pub-200",
    name="OHR2 - Chapel",
    description="",
    status="active",
    customer_id=None,
):
    return SimpleNamespace(
        id=project_id,
        public_id=public_id,
        name=name,
        description=description,
        status=status,
        customer_id=customer_id,
    )


def _build_customer_project_connector():
    project_service = Mock()
    project_service.repo = Mock()
    # U-276: the connector tries a direct dbo-identity lookup before the
    # mapping-table path these tests exercise. Default it to a miss so
    # existing fallback-path assertions are unaffected; the fast-path itself
    # is covered by its own tests below.
    project_service.read_by_qbo_identity.return_value = None
    reconciliation_repo = Mock()
    connector = CustomerProjectConnector(
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=reconciliation_repo,
        # U-297: never used here (every fixture sets parent_ref_value=None) —
        # injected so a truthy-parent test can't default to live-DB collaborators.
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
    )
    connector._sync_addresses = Mock()
    return connector, project_service, reconciliation_repo


def _build_invoice_connector(**overrides):
    connector = InvoiceInvoiceConnector(
        invoice_service=Mock(),
        project_service=Mock(),
        qbo_customer_repo=Mock(),
        customer_project_repo=Mock(),
        reconciliation_repo=Mock(),
    )
    stub_qbo_identity_fastpath_miss(connector.invoice_service)
    # U-311: _get_project_public_id now tries dbo.Project's native identity
    # directly before the qbo.Customer -> heal-by-name fallback these PART-2
    # tests exercise. Default it to a miss so those assertions are unaffected.
    connector.project_service.read_by_qbo_identity.return_value = None
    for key, value in overrides.items():
        setattr(connector, key, value)
    return connector


# PART 1 (CustomerProjectConnector.sync_from_qbo_customer's OLD mapping-table
# heal/update/duplicate branches) removed U-311 -- Wave-5 Option B retired
# qbo.CustomerProject as this connector's own pull data source, so none of
# that branch structure exists any more (there's no second store left to go
# stale, per docs/design/wave5.md §2). The dbo-only equivalents (fast-path
# hit, name-match adopt + duplicate-QboId guard, identity stamp) are tested
# in tests/test_u276_customer_project_qbo_identity_repoint.py, mirroring
# where U-310 put CustomerCustomerConnector's own analogous coverage.
# `_build_customer_project_connector` is kept -- still used by PART 3 below.
# heal_missing_mapping itself no longer reads/writes qbo.CustomerProject
# either (U-314-prereq repointed it onto dbo.Project.QboId; U-314 dropped the
# table + the mapping_repo constructor param entirely).


# --- PART 2: InvoiceInvoiceConnector._get_project_public_id ---


def test_get_project_public_id_auto_heals_missing_mapping():
    """(a) Missing CustomerProject mapping auto-heals via name match and returns public_id."""
    qbo_customer = _make_qbo_customer()
    healed_project = _make_project(public_id="healed-pub-id")

    project_service = Mock()
    project_service.read_by_name.return_value = healed_project
    # U-311 round-3 fix: heal_missing_mapping re-reads via read_by_id (the
    # sproc that actually projects QboId/RealmId) before its duplicate-identity
    # guard. Default to the same (identity-free) row so this "genuinely
    # unmapped" fixture doesn't spuriously trip that guard against an
    # auto-truthy bare Mock.
    project_service.read_by_id.return_value = healed_project
    # U-314-prereq anti-theft guard re-reads read_by_qbo_identity under the
    # QboCustomer's realm before binding; None = no other Project holds it, so
    # this genuinely-unmapped fixture binds cleanly.
    project_service.read_by_qbo_identity.return_value = None

    heal_connector = CustomerProjectConnector(
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=Mock(),
        # U-297: never used here (every fixture sets parent_ref_value=None) —
        # injected so a truthy-parent test can't default to live-DB collaborators.
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
    )
    heal_connector._sync_addresses = Mock()

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = None

    invoice_connector = _build_invoice_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(HEAL_CONNECTOR_PATH, return_value=heal_connector):
        result = invoice_connector._get_project_public_id("QBO-100")

    assert result == "healed-pub-id"
    # U-314-prereq: heal binds by stamping dbo.Project.QboId/RealmId, NOT by
    # writing a qbo.CustomerProject mapping row (table dropped entirely, U-314).
    project_service.repo.set_qbo_identity.assert_called_once_with(
        id=healed_project.id,
        qbo_id=qbo_customer.qbo_id,
        realm_id=qbo_customer.realm_id,
    )


def test_heal_refuses_to_steal_identity_held_by_another_project():
    """U-314-prereq anti-theft guard: when a DIFFERENT Project already holds the
    QboCustomer's (qbo_id, realm), heal must NOT bind the name-matched Project —
    that would steal the identity via SetProjectQboIdentity's theft-clear. Records a
    duplicate_qbo_customer issue and returns None. Reachable when the invoice realm
    is falsy (here _get_project_public_id is called with no realm), so the dbo-miss
    precondition checked a different realm than the one being stamped."""
    qbo_customer = _make_qbo_customer()  # qbo_id="QBO-100", realm_id="realm-1"
    name_matched = _make_project(project_id=71, public_id="name-matched-pub")
    other_holder = _make_project(project_id=99, public_id="holder-pub")

    reconciliation_repo = Mock()

    project_service = Mock()
    project_service.read_by_name.return_value = name_matched
    project_service.read_by_id.return_value = name_matched  # identity-free -> no 718 conflict
    project_service.read_by_qbo_identity.return_value = other_holder  # a DIFFERENT project holds it

    heal_connector = CustomerProjectConnector(
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=reconciliation_repo,
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
    )
    heal_connector._sync_addresses = Mock()

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer
    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = None

    invoice_connector = _build_invoice_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(HEAL_CONNECTOR_PATH, return_value=heal_connector):
        result = invoice_connector._get_project_public_id("QBO-100")

    assert result is None
    project_service.repo.set_qbo_identity.assert_not_called()  # did NOT steal
    reconciliation_repo.create.assert_called_once()  # collision recorded


def test_get_project_public_id_returns_none_when_heal_cannot_resolve():
    """(b-i) _get_project_public_id returns None when heal cannot resolve a local Project."""
    qbo_customer = _make_qbo_customer()

    project_service = Mock()
    project_service.read_by_name.return_value = None

    heal_connector = CustomerProjectConnector(
        project_service=project_service,
        project_address_service=Mock(),
        address_connector=Mock(),
        reconciliation_repo=Mock(),
        # U-297: never used here (every fixture sets parent_ref_value=None) —
        # injected so a truthy-parent test can't default to live-DB collaborators.
        customer_service=Mock(),
        qbo_customer_repo=Mock(),
    )

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = None

    invoice_connector = _build_invoice_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(HEAL_CONNECTOR_PATH, return_value=heal_connector):
        result = invoice_connector._get_project_public_id("QBO-100")

    assert result is None


def test_get_project_public_id_returns_none_when_verification_fails_never_falls_through_to_heal():
    """Codex xhigh P1 (U-311): a direct dbo hit that FAILS `verify_identity_dbo_only`
    (the identity was reassigned between the read and this call) must return
    None outright — NEVER fall through to the heal-by-name path below, which
    is keyed purely on QboCustomer.DisplayName and could silently bind the
    invoice line to a DIFFERENT Project than the one verify just refused to
    trust (and `heal_missing_mapping` can itself mint/stamp a mapping — the
    same class of action a refused verify exists to prevent). Mirrors the
    bill/purchase/vendorcredit sibling resolvers' identical guard."""
    qbo_customer = _make_qbo_customer()
    direct_project = SimpleNamespace(id=10, public_id="proj-pub-10", qbo_id="QBO-100", realm_id="realm-1")
    stolen_by = SimpleNamespace(id=99, public_id="proj-pub-99", qbo_id="QBO-100", realm_id="realm-1")

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    invoice_connector = _build_invoice_connector(qbo_customer_repo=qbo_customer_repo)
    invoice_connector.project_service.read_by_qbo_identity.side_effect = [direct_project, stolen_by]

    with patch(HEAL_CONNECTOR_PATH) as mock_connector_cls:
        result = invoice_connector._get_project_public_id("QBO-100")

    assert result is None
    qbo_customer_repo.read_by_qbo_id.assert_not_called()
    mock_connector_cls.assert_not_called()


def test_sync_from_qbo_invoice_raises_when_project_public_id_unresolvable():
    """(b-ii) sync_from_qbo_invoice fails loud when project binding cannot be resolved."""
    invoice_connector = _build_invoice_connector()
    invoice_connector._get_project_public_id = Mock(return_value=None)

    qbo_invoice = SimpleNamespace(
        id=50,
        qbo_id="INV-50",
        customer_ref_value="QBO-100",
        realm_id="realm-1",
        doc_number="1001",
        txn_date="2026-07-01",
        due_date="2026-07-31",
        private_note=None,
        total_amt=Decimal("1000.00"),
    )

    with pytest.raises(ValueError, match="No project mapping found for QBO customer ref"):
        invoice_connector.sync_from_qbo_invoice(qbo_invoice, [])


# --- PART 3: Code-review follow-up guards ---


def test_heal_missing_mapping_rejects_non_job_customer():
    """Non-job (top-level) QboCustomer must not be name-bound to a Project."""
    connector, project_service, _ = _build_customer_project_connector()
    qbo_customer = _make_qbo_customer(is_job=False)
    matching_project = _make_project()

    project_service.read_by_name.return_value = matching_project

    result = connector.heal_missing_mapping(qbo_customer)

    assert result is None
    project_service.read_by_name.assert_not_called()


def test_heal_missing_mapping_refuses_when_name_matched_project_carries_different_identity():
    """Codex xhigh round-2 P1 (U-311), corrected round-3: dbo-only pulls no
    longer create a qbo.CustomerProject mapping row, so the mapping-table
    check below this guard can no longer be trusted as a proxy for "already
    carries a different identity" — a Project synced via the new dbo-only
    path has NO mapping row regardless of its dbo QboId. Without this guard,
    heal_missing_mapping would fall through to create_mapping (which
    unconditionally re-stamps SetProjectQboIdentity), silently stealing the
    name-matched Project's existing, DIFFERENT identity.

    ReadProjectByName does NOT project QboId/RealmId at all (same class of
    gap as U-310's ReadCustomerByName finding) — `read_by_name`'s result here
    deliberately carries NO qbo_id attribute, matching the real sproc's
    projection, so this test fails if the guard naively trusts that result
    instead of the separate read_by_id re-read that actually carries identity."""
    connector, project_service, reconciliation_repo = _build_customer_project_connector()
    qbo_customer = _make_qbo_customer(qbo_id="QBO-100", realm_id="realm-1")
    name_matched = _make_project()  # no qbo_id attr — mirrors ReadProjectByName's real projection
    already_identified = _make_project()
    already_identified.qbo_id = "QBO-OTHER"
    already_identified.realm_id = "realm-1"
    project_service.read_by_name.return_value = name_matched
    project_service.read_by_id.return_value = already_identified

    result = connector.heal_missing_mapping(qbo_customer)

    assert result is None
    project_service.repo.set_qbo_identity.assert_not_called()
    project_service.read_by_id.assert_called_once_with(name_matched.id)
    reconciliation_repo.create.assert_called_once()
    assert reconciliation_repo.create.call_args.kwargs["drift_type"] == "project_identity_conflict"


def test_get_project_public_id_uses_realm_scoped_lookup_when_realm_given():
    """Realm-scoped customer lookup when realm_id is provided."""
    qbo_customer = _make_qbo_customer()
    healed_project = _make_project(public_id="healed-pub-id")

    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = qbo_customer
    qbo_customer_repo.read_by_qbo_id.return_value = qbo_customer

    customer_project_repo = Mock()
    customer_project_repo.read_by_qbo_customer_id.return_value = None

    invoice_connector = _build_invoice_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(HEAL_CONNECTOR_PATH) as mock_connector_cls:
        mock_connector_cls.return_value.heal_missing_mapping.return_value = healed_project
        result = invoice_connector._get_project_public_id("QBO-100", "realm-1")

    assert result == "healed-pub-id"
    qbo_customer_repo.read_by_qbo_id_and_realm_id.assert_called_once_with("QBO-100", "realm-1")
    qbo_customer_repo.read_by_qbo_id.assert_not_called()


def test_get_project_public_id_realm_miss_returns_none_without_heal():
    """Realm miss returns None without attempting heal or mapping lookup."""
    qbo_customer_repo = Mock()
    qbo_customer_repo.read_by_qbo_id_and_realm_id.return_value = None

    customer_project_repo = Mock()

    invoice_connector = _build_invoice_connector(
        qbo_customer_repo=qbo_customer_repo,
        customer_project_repo=customer_project_repo,
    )

    with patch(HEAL_CONNECTOR_PATH) as mock_connector_cls:
        result = invoice_connector._get_project_public_id("QBO-100", "realm-1")

    assert result is None
    customer_project_repo.read_by_qbo_customer_id.assert_not_called()
    mock_connector_cls.assert_not_called()


# PARTs 4-6 (PurchaseLineExpenseLineItemConnector / VendorCreditLineItemConnector /
# BillLineItemConnector's realm-scoped LEGACY qbo.Customer -> qbo.CustomerProject
# hop) removed U-311 -- that hop is deleted from all three resolvers (Wave-5
# Option A retires qbo.CustomerProject as their fallback data source). The
# realm-scoped DIRECT dbo lookup that remains is covered in
# tests/test_u283_bill_qbo_identity_repoint.py,
# tests/test_u283b_purchase_qbo_identity_repoint.py, and
# tests/test_u278_vendorcredit_qbo_identity_repoint.py — the dedicated per-
# connector suites this coverage belongs in, mirroring where U-310/U-313 put
# their own family's post-repoint resolver tests.
