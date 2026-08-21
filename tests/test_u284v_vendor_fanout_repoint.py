"""Pure-logic tests for U-284v: close the qbo.VendorVendor mapping-table
fan-out deferred by U-290 (see docs/staging_removal_phase4_5_scoping.md §2/§14
— NOT the doc's literal §3b, which is a separate, broader Bill-push
reference-helper consolidation covering item/customer/account/term too).

U-290 repointed the `vendor` family's OWN header identity onto dbo.Vendor's
native QboId/RealmId but explicitly left every CROSS-FAMILY vendor-reference
resolver reading the qbo.QboVendor -> qbo.VendorVendor mapping hop:
  - Bill pull  (BillBillConnector._get_vendor_public_id)
  - Bill push  (BillBillConnector._get_qbo_vendor_ref)
  - Purchase/Expense pull (PurchaseExpenseConnector._get_vendor_public_id)
  - VendorCredit pull (VendorCreditBillCreditConnector._get_vendor_public_id)
  - Expense-coding cockpit (ExpenseCodingItemService._resolve_vendor_id)

This unit repoints all five to try dbo.Vendor's native QboId/RealmId first,
mirroring U-283/U-283b's `_get_project_public_id` pattern (direct dbo lookup,
verified against the mapping table before being trusted, falling back to the
unchanged legacy 2-hop on a miss or disagreement) rather than
base/identity_fastpath.py's run_identity_fastpath() — that helper answers a
different question ("is THIS entity's own identity already mapped"), not
"resolve a DIFFERENT entity (Vendor) that THIS entity references."

The pull resolvers are read-only: a disagreement just falls through to the
legacy hop, no hard stop (nothing is written here to protect). The Bill PUSH
resolver is the one write-adjacent case — repointed with the same
verify-before-trust discipline `identity_consistency.py::verify_project_qbo_identity`
already established for Project, via the new `verify_vendor_qbo_identity`
added alongside it — a stale/"stolen" dbo QboId must never misroute a live
Bill to the wrong QBO vendor (mirrors U-276 round-4's push-side finding).
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from integrations.intuit.qbo.base.identity_consistency import verify_vendor_qbo_identity


# --- Section 1: verify_vendor_qbo_identity (shared helper) ---


def test_verify_vendor_qbo_identity_no_qbo_id_returns_none():
    vendor = SimpleNamespace(id=1, qbo_id=None)
    assert verify_vendor_qbo_identity(
        vendor, vendor_vendor_repo=Mock(), qbo_vendor_repo=Mock()
    ) is None


def test_verify_vendor_qbo_identity_no_mapping_trusts_dbo_value():
    vendor = SimpleNamespace(id=1, qbo_id="QV-1")
    vendor_vendor_repo = Mock()
    vendor_vendor_repo.read_by_vendor_id.return_value = None

    result = verify_vendor_qbo_identity(
        vendor, vendor_vendor_repo=vendor_vendor_repo, qbo_vendor_repo=Mock()
    )

    assert result == "QV-1"


def test_verify_vendor_qbo_identity_agreeing_mapping_trusts_dbo_value():
    vendor = SimpleNamespace(id=1, qbo_id="QV-1")
    vendor_vendor_repo = Mock()
    vendor_vendor_repo.read_by_vendor_id.return_value = SimpleNamespace(qbo_vendor_id=50)
    qbo_vendor_repo = Mock()
    qbo_vendor_repo.read_by_id.return_value = SimpleNamespace(qbo_id="QV-1")

    result = verify_vendor_qbo_identity(
        vendor, vendor_vendor_repo=vendor_vendor_repo, qbo_vendor_repo=qbo_vendor_repo
    )

    assert result == "QV-1"
    vendor_vendor_repo.read_by_vendor_id.assert_called_once_with(1)
    qbo_vendor_repo.read_by_id.assert_called_once_with(50)


def test_verify_vendor_qbo_identity_disagreeing_mapping_refuses():
    vendor = SimpleNamespace(id=1, qbo_id="QV-1")
    vendor_vendor_repo = Mock()
    vendor_vendor_repo.read_by_vendor_id.return_value = SimpleNamespace(qbo_vendor_id=50)
    qbo_vendor_repo = Mock()
    qbo_vendor_repo.read_by_id.return_value = SimpleNamespace(qbo_id="QV-OTHER")

    result = verify_vendor_qbo_identity(
        vendor, vendor_vendor_repo=vendor_vendor_repo, qbo_vendor_repo=qbo_vendor_repo
    )

    assert result is None
    qbo_vendor_repo.read_by_id.assert_called_once_with(50)


# --- Section 2: BillBillConnector._get_vendor_public_id (pull) ---


def _build_bill_connector():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    vendor_service = Mock()
    vendor_vendor_repo = Mock()
    qbo_vendor_repo = Mock()
    connector = BillBillConnector(
        vendor_service=vendor_service,
        vendor_vendor_repo=vendor_vendor_repo,
        qbo_vendor_repo=qbo_vendor_repo,
    )
    return connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo


def test_bill_get_vendor_public_id_prefers_direct_dbo_lookup():
    connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo = _build_bill_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1")
    vendor_service.read_by_qbo_identity.return_value = direct_vendor
    vendor_vendor_repo.read_by_vendor_id.return_value = None  # no mapping yet -> trusted

    result = connector._get_vendor_public_id("QV-1", "realm-1")

    assert result == "vendor-pub-10"
    vendor_service.read_by_qbo_identity.assert_called_once_with("QV-1", "realm-1")
    qbo_vendor_repo.read_by_qbo_id.assert_not_called()


def test_bill_get_vendor_public_id_falls_back_when_direct_lookup_misses():
    connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo = _build_bill_connector()
    vendor_service.read_by_qbo_identity.return_value = None
    qbo_vendor_repo.read_by_qbo_id.return_value = SimpleNamespace(id=20)
    vendor_vendor_repo.read_by_qbo_vendor_id.return_value = SimpleNamespace(vendor_id=30)
    vendor_service.read_by_id.return_value = SimpleNamespace(id=30, public_id="vendor-pub-30")

    result = connector._get_vendor_public_id("QV-2", "realm-1")

    assert result == "vendor-pub-30"
    qbo_vendor_repo.read_by_qbo_id.assert_called_once_with("QV-2")


def test_bill_get_vendor_public_id_falls_back_when_direct_hit_fails_verification():
    connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo = _build_bill_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1")
    vendor_service.read_by_qbo_identity.return_value = direct_vendor
    # Local-side mapping disagrees: Vendor 10 maps to a DIFFERENT QboVendor.
    vendor_vendor_repo.read_by_vendor_id.return_value = SimpleNamespace(qbo_vendor_id=999)
    qbo_vendor_repo.read_by_id.return_value = SimpleNamespace(qbo_id="QV-OTHER")

    # Legacy hop takes over from here.
    qbo_vendor_repo.read_by_qbo_id.return_value = SimpleNamespace(id=20)
    vendor_vendor_repo.read_by_qbo_vendor_id.return_value = SimpleNamespace(vendor_id=30)
    vendor_service.read_by_id.return_value = SimpleNamespace(id=30, public_id="vendor-pub-30")

    result = connector._get_vendor_public_id("QV-1", "realm-1")

    assert result == "vendor-pub-30"  # legacy hop's answer, NOT the unverified direct hit
    qbo_vendor_repo.read_by_qbo_id.assert_called_once_with("QV-1")


def test_bill_get_vendor_public_id_no_ref_value_short_circuits():
    connector, vendor_service, _, _ = _build_bill_connector()
    assert connector._get_vendor_public_id("") is None
    vendor_service.read_by_qbo_identity.assert_not_called()


# --- Section 3: BillBillConnector._get_qbo_vendor_ref (push) ---


def test_bill_get_qbo_vendor_ref_prefers_verified_direct_dbo_value():
    connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo = _build_bill_connector()
    vendor_service.read_by_id.return_value = SimpleNamespace(id=10, qbo_id="QV-1", name="Acme")
    vendor_vendor_repo.read_by_vendor_id.return_value = None  # no mapping yet -> trusted

    ref = connector._get_qbo_vendor_ref(10)

    assert ref.value == "QV-1"
    assert ref.name == "Acme"
    qbo_vendor_repo.read_by_id.assert_not_called()


def test_bill_get_qbo_vendor_ref_falls_back_when_no_local_vendor():
    connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo = _build_bill_connector()
    vendor_service.read_by_id.return_value = None
    vendor_vendor_repo.read_by_vendor_id.return_value = SimpleNamespace(qbo_vendor_id=50)
    qbo_vendor_repo.read_by_id.return_value = SimpleNamespace(qbo_id="QV-LEGACY")

    ref = connector._get_qbo_vendor_ref(10)

    assert ref.value == "QV-LEGACY"
    assert ref.name is None  # vendor_service.read_by_id returned None both times


def test_bill_get_qbo_vendor_ref_refuses_unverified_dbo_value_and_uses_mapping_table():
    """A stale/"stolen" dbo QboId must never misroute a live Bill push to the
    wrong QBO vendor — must defer to the mapping table, not trust dbo blindly."""
    connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo = _build_bill_connector()
    vendor = SimpleNamespace(id=10, qbo_id="QV-1", name="Acme")
    vendor_service.read_by_id.return_value = vendor
    # Local-side mapping disagrees.
    vendor_vendor_repo.read_by_vendor_id.side_effect = [
        SimpleNamespace(qbo_vendor_id=999),  # verify_vendor_qbo_identity's check
        SimpleNamespace(qbo_vendor_id=50),  # legacy hop's own lookup
    ]
    qbo_vendor_repo.read_by_id.side_effect = [
        SimpleNamespace(qbo_id="QV-OTHER"),  # verify's mapped QboVendor (disagrees)
        SimpleNamespace(qbo_id="QV-MAPPED"),  # legacy hop's QboVendor
    ]

    ref = connector._get_qbo_vendor_ref(10)

    assert ref.value == "QV-MAPPED"  # the mapping table's answer, NOT the unverified dbo value
    assert ref.name == "Acme"
    # vendor_service.read_by_id was only called once — the legacy path reuses it, no re-fetch.
    vendor_service.read_by_id.assert_called_once_with(10)


def test_bill_get_qbo_vendor_ref_not_yet_synced_vendor_falls_back_to_mapping_table():
    """The ordinary not-yet-pushed-to-QBO state: a real Vendor row exists but
    carries no qbo_id yet. Distinct from both "vendor is None" (no name to
    propagate) and "qbo_id disagrees" (verify makes 2 mapping-repo calls) —
    verify_vendor_qbo_identity short-circuits on the missing qbo_id BEFORE
    touching the mapping repo at all, so the legacy hop's call is the only
    one, and vendor_name must still come from the real vendor."""
    connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo = _build_bill_connector()
    vendor = SimpleNamespace(id=10, qbo_id=None, name="Acme")
    vendor_service.read_by_id.return_value = vendor
    vendor_vendor_repo.read_by_vendor_id.return_value = SimpleNamespace(qbo_vendor_id=50)
    qbo_vendor_repo.read_by_id.return_value = SimpleNamespace(qbo_id="QV-LEGACY")

    ref = connector._get_qbo_vendor_ref(10)

    assert ref.value == "QV-LEGACY"
    assert ref.name == "Acme"  # real vendor's name, NOT None
    vendor_vendor_repo.read_by_vendor_id.assert_called_once_with(10)  # verify never touched it


def test_bill_get_qbo_vendor_ref_no_vendor_id_short_circuits():
    connector, vendor_service, _, _ = _build_bill_connector()
    assert connector._get_qbo_vendor_ref(0) is None
    vendor_service.read_by_id.assert_not_called()


# --- Section 4: PurchaseExpenseConnector._get_vendor_public_id (pull, cached) ---


def _build_purchase_connector():
    from integrations.intuit.qbo.purchase.connector.expense.business.service import (
        PurchaseExpenseConnector,
    )

    vendor_service = Mock()
    vendor_vendor_repo = Mock()
    qbo_vendor_repo = Mock()
    connector = PurchaseExpenseConnector(
        vendor_service=vendor_service,
        vendor_vendor_repo=vendor_vendor_repo,
        qbo_vendor_repo=qbo_vendor_repo,
    )
    return connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo


def test_purchase_get_vendor_public_id_prefers_direct_dbo_lookup_and_caches():
    connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo = _build_purchase_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1")
    vendor_service.read_by_qbo_identity.return_value = direct_vendor
    vendor_vendor_repo.read_by_vendor_id.return_value = None

    first = connector._get_vendor_public_id("QV-1", "realm-1")
    second = connector._get_vendor_public_id("QV-1", "realm-1")

    assert first == second == "vendor-pub-10"
    vendor_service.read_by_qbo_identity.assert_called_once_with("QV-1", "realm-1")
    qbo_vendor_repo.read_by_qbo_id.assert_not_called()


def test_purchase_get_vendor_public_id_cache_keyed_by_realm_too():
    """QBO vendor ref values are only unique WITHIN a realm — a cache keyed
    on ref_value alone could serve realm A's cached vendor to realm B."""
    connector, vendor_service, vendor_vendor_repo, _ = _build_purchase_connector()
    vendor_vendor_repo.read_by_vendor_id.return_value = None
    vendor_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=10, public_id="vendor-pub-10", qbo_id="QV-1"
    )

    first = connector._get_vendor_public_id("QV-1", "realm-1")
    assert first == "vendor-pub-10"
    assert vendor_service.read_by_qbo_identity.call_count == 1

    vendor_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=20, public_id="vendor-pub-20", qbo_id="QV-1"
    )
    second = connector._get_vendor_public_id("QV-1", "realm-2")

    assert second == "vendor-pub-20"
    assert vendor_service.read_by_qbo_identity.call_count == 2


def test_purchase_get_vendor_public_id_falls_back_when_direct_lookup_misses():
    connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo = _build_purchase_connector()
    vendor_service.read_by_qbo_identity.return_value = None
    qbo_vendor_repo.read_by_qbo_id.return_value = SimpleNamespace(id=20)
    vendor_vendor_repo.read_by_qbo_vendor_id.return_value = SimpleNamespace(vendor_id=30)
    vendor_service.read_by_id.return_value = SimpleNamespace(id=30, public_id="vendor-pub-30")

    result = connector._get_vendor_public_id("QV-2", "realm-1")

    assert result == "vendor-pub-30"


def test_purchase_get_vendor_public_id_falls_back_when_direct_hit_fails_verification():
    connector, vendor_service, vendor_vendor_repo, qbo_vendor_repo = _build_purchase_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1")
    vendor_service.read_by_qbo_identity.return_value = direct_vendor
    # Local-side mapping disagrees: Vendor 10 maps to a DIFFERENT QboVendor.
    vendor_vendor_repo.read_by_vendor_id.return_value = SimpleNamespace(qbo_vendor_id=999)
    qbo_vendor_repo.read_by_id.return_value = SimpleNamespace(qbo_id="QV-OTHER")

    # Legacy hop takes over from here.
    qbo_vendor_repo.read_by_qbo_id.return_value = SimpleNamespace(id=20)
    vendor_vendor_repo.read_by_qbo_vendor_id.return_value = SimpleNamespace(vendor_id=30)
    vendor_service.read_by_id.return_value = SimpleNamespace(id=30, public_id="vendor-pub-30")

    result = connector._get_vendor_public_id("QV-1", "realm-1")

    assert result == "vendor-pub-30"  # legacy hop's answer, NOT the unverified direct hit
    qbo_vendor_repo.read_by_qbo_id.assert_called_once_with("QV-1")
    # Not cached under the "verified" branch — the eventual legacy-success cache write covers it.
    assert connector._vendor_cache.get(("realm-1", "QV-1")) == "vendor-pub-30"


def test_purchase_get_vendor_public_id_no_ref_value_short_circuits():
    connector, vendor_service, _, _ = _build_purchase_connector()
    assert connector._get_vendor_public_id("") is None
    vendor_service.read_by_qbo_identity.assert_not_called()


# --- Section 5: VendorCreditBillCreditConnector._get_vendor_public_id (pull) ---


def _build_vendorcredit_connector():
    from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
        VendorCreditBillCreditConnector,
    )

    vendor_service = Mock()
    connector = VendorCreditBillCreditConnector(vendor_service=vendor_service)
    return connector, vendor_service


VV_REPO_PATH = "integrations.intuit.qbo.vendor.connector.vendor.persistence.repo.VendorVendorRepository"
QV_REPO_PATH = "integrations.intuit.qbo.vendor.persistence.repo.QboVendorRepository"


def test_vendorcredit_get_vendor_public_id_prefers_direct_dbo_lookup():
    connector, vendor_service = _build_vendorcredit_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1")
    vendor_service.read_by_qbo_identity.return_value = direct_vendor

    with patch(VV_REPO_PATH) as MockVVRepo, patch(QV_REPO_PATH):
        MockVVRepo.return_value.read_by_vendor_id.return_value = None

        result = connector._get_vendor_public_id("QV-1", "realm-1")

    assert result == "vendor-pub-10"
    vendor_service.read_by_qbo_identity.assert_called_once_with("QV-1", "realm-1")


def test_vendorcredit_get_vendor_public_id_falls_back_when_direct_lookup_misses():
    connector, vendor_service = _build_vendorcredit_connector()
    vendor_service.read_by_qbo_identity.return_value = None
    vendor_service.read_by_id.return_value = SimpleNamespace(id=30, public_id="vendor-pub-30")

    with patch(VV_REPO_PATH) as MockVVRepo, patch(QV_REPO_PATH) as MockQVRepo:
        MockQVRepo.return_value.read_by_qbo_id.return_value = SimpleNamespace(id=20)
        MockVVRepo.return_value.read_by_qbo_vendor_id.return_value = SimpleNamespace(vendor_id=30)

        result = connector._get_vendor_public_id("QV-2", "realm-1")

    assert result == "vendor-pub-30"


def test_vendorcredit_get_vendor_public_id_falls_back_when_direct_hit_fails_verification():
    connector, vendor_service = _build_vendorcredit_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1")
    vendor_service.read_by_qbo_identity.return_value = direct_vendor
    vendor_service.read_by_id.return_value = SimpleNamespace(id=30, public_id="vendor-pub-30")

    with patch(VV_REPO_PATH) as MockVVRepo, patch(QV_REPO_PATH) as MockQVRepo:
        # verify_vendor_qbo_identity's own check: local-side mapping disagrees.
        # Legacy hop's lookups take over after that.
        MockVVRepo.return_value.read_by_vendor_id.return_value = SimpleNamespace(qbo_vendor_id=999)
        MockQVRepo.return_value.read_by_id.side_effect = [
            SimpleNamespace(qbo_id="QV-OTHER"),  # verify's mapped QboVendor (disagrees)
        ]
        MockQVRepo.return_value.read_by_qbo_id.return_value = SimpleNamespace(id=20)
        MockVVRepo.return_value.read_by_qbo_vendor_id.return_value = SimpleNamespace(vendor_id=30)

        result = connector._get_vendor_public_id("QV-1", "realm-1")

    assert result == "vendor-pub-30"  # legacy hop's answer, NOT the unverified direct hit


def test_vendorcredit_get_vendor_public_id_no_ref_value_short_circuits():
    connector, vendor_service = _build_vendorcredit_connector()
    assert connector._get_vendor_public_id(None) is None
    vendor_service.read_by_qbo_identity.assert_not_called()


# --- Section 6: ExpenseCodingItemService._resolve_vendor_id (pull) ---


VENDOR_SERVICE_PATH = "entities.vendor.business.service.VendorService"


def _build_expense_coding_service():
    from entities.expense_coding_item.business.service import ExpenseCodingItemService

    return ExpenseCodingItemService()


def test_expense_coding_resolve_vendor_id_prefers_direct_dbo_lookup():
    service = _build_expense_coding_service()
    direct_vendor = SimpleNamespace(id=10, qbo_id="QV-1")

    with patch(VENDOR_SERVICE_PATH) as MockVendorService, \
         patch(VV_REPO_PATH) as MockVVRepo, \
         patch(QV_REPO_PATH):
        MockVendorService.return_value.read_by_qbo_identity.return_value = direct_vendor
        MockVVRepo.return_value.read_by_vendor_id.return_value = None

        result = service._resolve_vendor_id("QV-1", realm_id="realm-1")

    assert result == 10
    MockVendorService.return_value.read_by_qbo_identity.assert_called_once_with("QV-1", "realm-1")


def test_expense_coding_resolve_vendor_id_falls_back_when_direct_lookup_misses():
    service = _build_expense_coding_service()

    with patch(VENDOR_SERVICE_PATH) as MockVendorService, \
         patch(VV_REPO_PATH) as MockVVRepo, \
         patch(QV_REPO_PATH) as MockQVRepo:
        MockVendorService.return_value.read_by_qbo_identity.return_value = None
        MockQVRepo.return_value.read_by_qbo_id_and_realm_id.return_value = SimpleNamespace(id=20)
        MockVVRepo.return_value.read_by_qbo_vendor_id.return_value = SimpleNamespace(vendor_id=30)

        result = service._resolve_vendor_id("QV-2", realm_id="realm-1")

    assert result == 30


def test_expense_coding_resolve_vendor_id_falls_back_when_direct_hit_fails_verification():
    service = _build_expense_coding_service()
    direct_vendor = SimpleNamespace(id=10, qbo_id="QV-1")

    with patch(VENDOR_SERVICE_PATH) as MockVendorService, \
         patch(VV_REPO_PATH) as MockVVRepo, \
         patch(QV_REPO_PATH) as MockQVRepo:
        MockVendorService.return_value.read_by_qbo_identity.return_value = direct_vendor
        # Local-side mapping disagrees.
        MockVVRepo.return_value.read_by_vendor_id.return_value = SimpleNamespace(qbo_vendor_id=999)
        MockQVRepo.return_value.read_by_id.return_value = SimpleNamespace(qbo_id="QV-OTHER")

        # Legacy hop takes over from here.
        MockQVRepo.return_value.read_by_qbo_id_and_realm_id.return_value = SimpleNamespace(id=20)
        MockVVRepo.return_value.read_by_qbo_vendor_id.return_value = SimpleNamespace(vendor_id=30)

        result = service._resolve_vendor_id("QV-1", realm_id="realm-1")

    assert result == 30  # legacy hop's answer, NOT the unverified direct hit's id (10)


def test_expense_coding_resolve_vendor_id_no_qbo_id_short_circuits():
    service = _build_expense_coding_service()
    assert service._resolve_vendor_id(None) is None


def test_expense_coding_resolve_vendor_id_swallows_errors_and_returns_none():
    """Pre-existing contract: any resolution error is logged and swallowed,
    never propagated (this is a best-effort coding-queue enrichment, not a
    hard dependency)."""
    service = _build_expense_coding_service()

    with patch(VENDOR_SERVICE_PATH) as MockVendorService:
        MockVendorService.side_effect = RuntimeError("boom")

        result = service._resolve_vendor_id("QV-1", realm_id="realm-1")

    assert result is None


def test_expense_coding_resolve_vendor_id_swallows_errors_from_new_direct_lookup_code():
    """Same contract as above, but the failure originates inside THIS unit's
    own new code (read_by_qbo_identity), not the pre-existing VendorService()
    construction step — proves the broad try/except covers the new call too,
    not just the surrounding scaffolding."""
    service = _build_expense_coding_service()

    with patch(VENDOR_SERVICE_PATH) as MockVendorService, \
         patch(VV_REPO_PATH), patch(QV_REPO_PATH):
        MockVendorService.return_value.read_by_qbo_identity.side_effect = RuntimeError("boom")

        result = service._resolve_vendor_id("QV-1", realm_id="realm-1")

    assert result is None
