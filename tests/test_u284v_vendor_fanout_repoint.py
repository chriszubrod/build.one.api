"""Pure-logic tests for the cross-family Vendor-reference resolvers first
repointed dbo-first by U-284v, then moved fully dbo-only (no `qbo.VendorVendor`
mapping-table read of any kind) by U-313 (Wave 5's "trust dbo alone" plan,
`docs/design/wave5.md`):
  - Bill pull  (BillBillConnector._get_vendor_public_id)
  - Bill push  (BillBillConnector._get_qbo_vendor_ref)
  - Purchase/Expense pull (PurchaseExpenseConnector._get_vendor_public_id)
  - VendorCredit pull (VendorCreditBillCreditConnector._get_vendor_public_id)
  - Expense-coding cockpit (ExpenseCodingItemService._resolve_vendor_id)

Each now: direct dbo.Vendor.QboId/RealmId lookup, re-verified fresh via
`identity_consistency.py::verify_identity_dbo_only` — a plain unlocked
SECOND call to the same `read_by_qbo_identity(qbo_id, realm_id)` accessor
(or, for the Bill push side, the initial fetch is `read_by_id` and the
re-verify is `read_by_qbo_identity` — different accessors, since push starts
from a local vendor_id not a QBO ref), comparing the fresh read's `.id`
against the originally-resolved row's `.id`. A miss or a failed verify
returns None outright — there is no legacy `qbo.QboVendor` ->
`qbo.VendorVendor` 2-hop left to fall back to; it had no data source left
either once the mapping table stopped being written (Vendor's own pull
connector, `VendorVendorConnector`, moved to the dbo-only create primitive in
the same unit — see test_u290_vendor_qbo_identity_repoint.py). `verify_vendor_
qbo_identity` (the mapping-table-based wrapper these call sites used to call)
is untouched and still directly tested in test_u306_identity_verify_engine.py
— it simply has no callers left in this codebase after U-310/U-311/U-313.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

# --- Section 1: BillBillConnector._get_vendor_public_id (pull) ---


def _build_bill_connector():
    from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector

    vendor_service = Mock()
    connector = BillBillConnector(vendor_service=vendor_service)
    return connector, vendor_service


def test_bill_get_vendor_public_id_verified_direct_hit():
    connector, vendor_service = _build_bill_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1", realm_id="realm-1")
    vendor_service.read_by_qbo_identity.return_value = direct_vendor  # same row both calls

    result = connector._get_vendor_public_id("QV-1", "realm-1")

    assert result == "vendor-pub-10"
    assert vendor_service.read_by_qbo_identity.call_count == 2
    vendor_service.read_by_qbo_identity.assert_called_with("QV-1", "realm-1")


def test_bill_get_vendor_public_id_dbo_miss_returns_none():
    connector, vendor_service = _build_bill_connector()
    vendor_service.read_by_qbo_identity.return_value = None

    result = connector._get_vendor_public_id("QV-2", "realm-1")

    assert result is None
    assert vendor_service.read_by_qbo_identity.call_count == 1  # verify never reached


def test_bill_get_vendor_public_id_stolen_identity_refuses_and_returns_none():
    """The dbo QboId no longer resolves back to the same row on a fresh
    read (identity reassigned between the caller's original read and this
    verify call) — must refuse, not trust the stale hit."""
    connector, vendor_service = _build_bill_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1", realm_id="realm-1")
    stolen = SimpleNamespace(id=99, qbo_id="QV-1", realm_id="realm-1")  # a DIFFERENT row now
    vendor_service.read_by_qbo_identity.side_effect = [direct_vendor, stolen]

    result = connector._get_vendor_public_id("QV-1", "realm-1")

    assert result is None


def test_bill_get_vendor_public_id_no_ref_value_short_circuits():
    connector, vendor_service = _build_bill_connector()
    assert connector._get_vendor_public_id("") is None
    vendor_service.read_by_qbo_identity.assert_not_called()


# --- Section 2: BillBillConnector._get_qbo_vendor_ref (push) ---


def test_bill_get_qbo_vendor_ref_verified_direct_value():
    connector, vendor_service = _build_bill_connector()
    vendor = SimpleNamespace(id=10, qbo_id="QV-1", name="Acme", realm_id="realm-1")
    vendor_service.read_by_id.return_value = vendor
    vendor_service.read_by_qbo_identity.return_value = SimpleNamespace(id=10, qbo_id="QV-1", realm_id="realm-1")

    ref = connector._get_qbo_vendor_ref(10)

    assert ref.value == "QV-1"
    assert ref.name == "Acme"
    vendor_service.read_by_id.assert_called_once_with(10)
    vendor_service.read_by_qbo_identity.assert_called_once_with("QV-1", "realm-1")


def test_bill_get_qbo_vendor_ref_no_local_vendor_returns_none():
    connector, vendor_service = _build_bill_connector()
    vendor_service.read_by_id.return_value = None

    assert connector._get_qbo_vendor_ref(10) is None
    vendor_service.read_by_qbo_identity.assert_not_called()


def test_bill_get_qbo_vendor_ref_refuses_stolen_identity():
    """A stale/"stolen" dbo QboId must never misroute a live Bill push to the
    wrong QBO vendor — refuse rather than trust it, with no mapping table
    left to defer to."""
    connector, vendor_service = _build_bill_connector()
    vendor = SimpleNamespace(id=10, qbo_id="QV-1", name="Acme", realm_id="realm-1")
    vendor_service.read_by_id.return_value = vendor
    vendor_service.read_by_qbo_identity.return_value = SimpleNamespace(id=99, qbo_id="QV-1", realm_id="realm-1")

    assert connector._get_qbo_vendor_ref(10) is None


def test_bill_get_qbo_vendor_ref_not_yet_synced_vendor_returns_none():
    """The ordinary not-yet-pushed-to-QBO state: a real Vendor row exists but
    carries no qbo_id yet — verify_identity_dbo_only short-circuits on the
    missing qbo_id, no legacy hop to fall back to."""
    connector, vendor_service = _build_bill_connector()
    vendor = SimpleNamespace(id=10, qbo_id=None, name="Acme")
    vendor_service.read_by_id.return_value = vendor

    assert connector._get_qbo_vendor_ref(10) is None
    vendor_service.read_by_id.assert_called_once_with(10)
    vendor_service.read_by_qbo_identity.assert_not_called()


def test_bill_get_qbo_vendor_ref_no_vendor_id_short_circuits():
    connector, vendor_service = _build_bill_connector()
    assert connector._get_qbo_vendor_ref(0) is None
    vendor_service.read_by_id.assert_not_called()


# --- Section 3: PurchaseExpenseConnector._get_vendor_public_id (pull, cached) ---


def _build_purchase_connector():
    from integrations.intuit.qbo.purchase.connector.expense.business.service import (
        PurchaseExpenseConnector,
    )

    vendor_service = Mock()
    connector = PurchaseExpenseConnector(vendor_service=vendor_service)
    return connector, vendor_service


def test_purchase_get_vendor_public_id_verified_direct_hit_caches():
    connector, vendor_service = _build_purchase_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1", realm_id="realm-1")
    vendor_service.read_by_qbo_identity.return_value = direct_vendor

    first = connector._get_vendor_public_id("QV-1", "realm-1")
    second = connector._get_vendor_public_id("QV-1", "realm-1")

    assert first == second == "vendor-pub-10"
    assert vendor_service.read_by_qbo_identity.call_count == 2  # cached: 1 resolve + 1 verify, no 2nd resolve


def test_purchase_get_vendor_public_id_cache_keyed_by_realm_too():
    """QBO vendor ref values are only unique WITHIN a realm — a cache keyed
    on ref_value alone could serve realm A's cached vendor to realm B."""
    connector, vendor_service = _build_purchase_connector()

    vendor_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=10, public_id="vendor-pub-10", qbo_id="QV-1", realm_id="realm-1"
    )
    first = connector._get_vendor_public_id("QV-1", "realm-1")
    assert first == "vendor-pub-10"
    assert vendor_service.read_by_qbo_identity.call_count == 2

    vendor_service.read_by_qbo_identity.return_value = SimpleNamespace(
        id=20, public_id="vendor-pub-20", qbo_id="QV-1", realm_id="realm-2"
    )
    second = connector._get_vendor_public_id("QV-1", "realm-2")

    assert second == "vendor-pub-20"
    assert vendor_service.read_by_qbo_identity.call_count == 4


def test_purchase_get_vendor_public_id_dbo_miss_caches_none():
    connector, vendor_service = _build_purchase_connector()
    vendor_service.read_by_qbo_identity.return_value = None

    result = connector._get_vendor_public_id("QV-2", "realm-1")

    assert result is None
    assert connector._vendor_cache[("realm-1", "QV-2")] is None


def test_purchase_get_vendor_public_id_stolen_identity_caches_none():
    connector, vendor_service = _build_purchase_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1", realm_id="realm-1")
    stolen = SimpleNamespace(id=99, qbo_id="QV-1", realm_id="realm-1")
    vendor_service.read_by_qbo_identity.side_effect = [direct_vendor, stolen]

    result = connector._get_vendor_public_id("QV-1", "realm-1")

    assert result is None
    assert connector._vendor_cache[("realm-1", "QV-1")] is None


def test_purchase_get_vendor_public_id_no_ref_value_short_circuits():
    connector, vendor_service = _build_purchase_connector()
    assert connector._get_vendor_public_id("") is None
    vendor_service.read_by_qbo_identity.assert_not_called()


# --- Section 4: VendorCreditBillCreditConnector._get_vendor_public_id (pull) ---


def _build_vendorcredit_connector():
    from integrations.intuit.qbo.vendorcredit.connector.bill_credit.business.service import (
        VendorCreditBillCreditConnector,
    )

    vendor_service = Mock()
    connector = VendorCreditBillCreditConnector(vendor_service=vendor_service)
    return connector, vendor_service


def test_vendorcredit_get_vendor_public_id_verified_direct_hit():
    connector, vendor_service = _build_vendorcredit_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1", realm_id="realm-1")
    vendor_service.read_by_qbo_identity.return_value = direct_vendor

    result = connector._get_vendor_public_id("QV-1", "realm-1")

    assert result == "vendor-pub-10"
    assert vendor_service.read_by_qbo_identity.call_count == 2


def test_vendorcredit_get_vendor_public_id_dbo_miss_returns_none():
    connector, vendor_service = _build_vendorcredit_connector()
    vendor_service.read_by_qbo_identity.return_value = None

    assert connector._get_vendor_public_id("QV-2", "realm-1") is None


def test_vendorcredit_get_vendor_public_id_stolen_identity_returns_none():
    connector, vendor_service = _build_vendorcredit_connector()
    direct_vendor = SimpleNamespace(id=10, public_id="vendor-pub-10", qbo_id="QV-1", realm_id="realm-1")
    stolen = SimpleNamespace(id=99, qbo_id="QV-1", realm_id="realm-1")
    vendor_service.read_by_qbo_identity.side_effect = [direct_vendor, stolen]

    assert connector._get_vendor_public_id("QV-1", "realm-1") is None


def test_vendorcredit_get_vendor_public_id_no_ref_value_short_circuits():
    connector, vendor_service = _build_vendorcredit_connector()
    assert connector._get_vendor_public_id(None) is None
    vendor_service.read_by_qbo_identity.assert_not_called()


def test_vendorcredit_get_vendor_public_id_swallows_errors_and_returns_none():
    """Pre-existing contract on this connector's resolver: this one is wrapped
    in its own broad try/except (unlike Bill's), preserved unchanged."""
    connector, vendor_service = _build_vendorcredit_connector()
    vendor_service.read_by_qbo_identity.side_effect = RuntimeError("boom")

    assert connector._get_vendor_public_id("QV-1", "realm-1") is None


# --- Section 5: ExpenseCodingItemService._resolve_vendor_id (pull) ---


VENDOR_SERVICE_PATH = "entities.vendor.business.service.VendorService"


def _build_expense_coding_service():
    from entities.expense_coding_item.business.service import ExpenseCodingItemService

    return ExpenseCodingItemService()


def test_expense_coding_resolve_vendor_id_verified_direct_hit():
    service = _build_expense_coding_service()
    direct_vendor = SimpleNamespace(id=10, qbo_id="QV-1", realm_id="realm-1")

    with patch(VENDOR_SERVICE_PATH) as MockVendorService:
        MockVendorService.return_value.read_by_qbo_identity.return_value = direct_vendor

        result = service._resolve_vendor_id("QV-1", realm_id="realm-1")

    assert result == 10
    assert MockVendorService.return_value.read_by_qbo_identity.call_count == 2


def test_expense_coding_resolve_vendor_id_dbo_miss_returns_none():
    service = _build_expense_coding_service()

    with patch(VENDOR_SERVICE_PATH) as MockVendorService:
        MockVendorService.return_value.read_by_qbo_identity.return_value = None

        result = service._resolve_vendor_id("QV-2", realm_id="realm-1")

    assert result is None


def test_expense_coding_resolve_vendor_id_stolen_identity_returns_none():
    service = _build_expense_coding_service()
    direct_vendor = SimpleNamespace(id=10, qbo_id="QV-1", realm_id="realm-1")
    stolen = SimpleNamespace(id=99, qbo_id="QV-1", realm_id="realm-1")

    with patch(VENDOR_SERVICE_PATH) as MockVendorService:
        MockVendorService.return_value.read_by_qbo_identity.side_effect = [direct_vendor, stolen]

        result = service._resolve_vendor_id("QV-1", realm_id="realm-1")

    assert result is None


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
    """Same contract as above, but the failure originates inside the direct
    lookup itself, not the surrounding VendorService() construction step —
    proves the broad try/except covers verify_identity_dbo_only's call too."""
    service = _build_expense_coding_service()

    with patch(VENDOR_SERVICE_PATH) as MockVendorService:
        MockVendorService.return_value.read_by_qbo_identity.side_effect = RuntimeError("boom")

        result = service._resolve_vendor_id("QV-1", realm_id="realm-1")

    assert result is None
