"""U-307a — direct unit tests for the shared cost_code_resolver module.

Everything else in this diff (bill_line_item / expense_line_item / bill_credit_line_item
connectors, QboInvoiceService) covers this module only indirectly through its own
fixtures. These tests exercise cost_code_resolver.py's functions directly via injected
fakes, so a future consumer (U-307b's push repoint) inherits a real regression net
instead of relying solely on today's 4 callers to notice a break.
"""
from types import SimpleNamespace

import pytest

from integrations.intuit.qbo.base.cost_code_resolver import (
    QboItemRef,
    resolve_dbo_sub_cost_code,
    resolve_dbo_cost_code_direct,
    resolve_qbo_item_ref,
)


class _FakeSubCostCodeService:
    def __init__(self, *, by_qbo_identity=None, by_id=None):
        self._by_qbo_identity = by_qbo_identity or {}
        self._by_id = by_id or {}
        self.identity_calls = []
        self.id_calls = []

    def read_by_qbo_identity(self, qbo_id, realm_id=None):
        self.identity_calls.append((qbo_id, realm_id))
        return self._by_qbo_identity.get((qbo_id, realm_id))

    def read_by_id(self, id):
        self.id_calls.append(id)
        return self._by_id.get(id)


class _FakeCostCodeService:
    def __init__(self, *, by_qbo_identity=None, by_id=None):
        self._by_qbo_identity = by_qbo_identity or {}
        self._by_id = by_id or {}
        self.identity_calls = []

    def read_by_qbo_identity(self, qbo_id, realm_id=None):
        self.identity_calls.append((qbo_id, realm_id))
        return self._by_qbo_identity.get((qbo_id, realm_id))

    def read_by_id(self, id):
        return self._by_id.get(id)


class _FakeQboItemRepo:
    def __init__(self, *, by_qbo_id=None, raises=None):
        self._by_qbo_id = by_qbo_id or {}
        self._raises = raises
        self.calls = []

    def read_by_qbo_id(self, qbo_id):
        self.calls.append(qbo_id)
        if self._raises:
            raise self._raises
        return self._by_qbo_id.get(qbo_id)


class _FakeMappingRepo:
    def __init__(self, *, by_qbo_item_id=None):
        self._by_qbo_item_id = by_qbo_item_id or {}
        self.calls = []

    def read_by_qbo_item_id(self, qbo_item_id):
        self.calls.append(qbo_item_id)
        return self._by_qbo_item_id.get(qbo_item_id)


# ---------------------------------------------------------------------------
# resolve_dbo_sub_cost_code
# ---------------------------------------------------------------------------

def test_forward_sub_cost_code_no_ref_returns_none_without_touching_deps():
    scc_service = _FakeSubCostCodeService()
    assert resolve_dbo_sub_cost_code(None, "realm-1", sub_cost_code_service=scc_service) is None
    assert scc_service.identity_calls == []


def test_forward_sub_cost_code_dbo_native_hit_skips_legacy_hop():
    scc = SimpleNamespace(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1")
    scc_service = _FakeSubCostCodeService(by_qbo_identity={("83", "realm-1"): scc})
    qbo_item_repo = _FakeQboItemRepo()

    result = resolve_dbo_sub_cost_code(
        "83", "realm-1",
        sub_cost_code_service=scc_service,
        qbo_item_repo=qbo_item_repo,
    )

    assert result is scc
    assert qbo_item_repo.calls == []  # legacy hop never touched


def test_forward_sub_cost_code_dbo_native_miss_falls_back_to_legacy_hop():
    scc_service = _FakeSubCostCodeService(by_id={7: SimpleNamespace(id=7, cost_code_id=3)})
    qbo_item_repo = _FakeQboItemRepo(by_qbo_id={"83": SimpleNamespace(id=10)})
    mapping_repo = _FakeMappingRepo(by_qbo_item_id={10: SimpleNamespace(sub_cost_code_id=7)})

    result = resolve_dbo_sub_cost_code(
        "83", "realm-1",
        sub_cost_code_service=scc_service,
        qbo_item_repo=qbo_item_repo,
        item_sub_cost_code_repo=mapping_repo,
    )

    assert result.id == 7
    assert scc_service.identity_calls == [("83", "realm-1")]
    assert qbo_item_repo.calls == ["83"]


def test_forward_sub_cost_code_legacy_hop_unresolvable_qbo_item_returns_none():
    scc_service = _FakeSubCostCodeService()
    qbo_item_repo = _FakeQboItemRepo(by_qbo_id={})

    assert resolve_dbo_sub_cost_code(
        "missing", None, sub_cost_code_service=scc_service, qbo_item_repo=qbo_item_repo,
    ) is None


def test_forward_sub_cost_code_legacy_hop_dangling_mapping_target_returns_none():
    """Mapping row exists but the SubCostCode it points to no longer reads — never
    raises, degrades to None (Codex U-307a review: this is a deliberate, flagged
    behavior refinement for bill/purchase, matching bill_credit_line_item's and
    invoice's pre-existing degrade-gracefully contract)."""
    scc_service = _FakeSubCostCodeService(by_id={})  # 999 not present -> dangling
    qbo_item_repo = _FakeQboItemRepo(by_qbo_id={"83": SimpleNamespace(id=10)})
    mapping_repo = _FakeMappingRepo(by_qbo_item_id={10: SimpleNamespace(sub_cost_code_id=999)})

    assert resolve_dbo_sub_cost_code(
        "83", None,
        sub_cost_code_service=scc_service,
        qbo_item_repo=qbo_item_repo,
        item_sub_cost_code_repo=mapping_repo,
    ) is None


def test_forward_sub_cost_code_legacy_hop_error_propagates():
    scc_service = _FakeSubCostCodeService()
    qbo_item_repo = _FakeQboItemRepo(raises=ValueError("db blip"))

    with pytest.raises(ValueError, match="db blip"):
        resolve_dbo_sub_cost_code(
            "83", None, sub_cost_code_service=scc_service, qbo_item_repo=qbo_item_repo,
        )


# ---------------------------------------------------------------------------
# resolve_dbo_cost_code_direct
# ---------------------------------------------------------------------------

def test_forward_cost_code_direct_dbo_native_hit_skips_legacy_hop():
    cc = SimpleNamespace(id=44, number="00", name="Initial & Suspense", qbo_id="4")
    cc_service = _FakeCostCodeService(by_qbo_identity={("4", "realm-1"): cc})
    qbo_item_repo = _FakeQboItemRepo()

    result = resolve_dbo_cost_code_direct(
        "4", "realm-1", cost_code_service=cc_service, qbo_item_repo=qbo_item_repo,
    )

    assert result is cc
    assert qbo_item_repo.calls == []


def test_forward_cost_code_direct_dbo_native_miss_falls_back_to_legacy_hop():
    cc_service = _FakeCostCodeService(by_id={44: SimpleNamespace(id=44, number="00")})
    qbo_item_repo = _FakeQboItemRepo(by_qbo_id={"4": SimpleNamespace(id=1)})
    mapping_repo = _FakeMappingRepo(by_qbo_item_id={1: SimpleNamespace(cost_code_id=44)})

    result = resolve_dbo_cost_code_direct(
        "4", None,
        cost_code_service=cc_service,
        qbo_item_repo=qbo_item_repo,
        item_cost_code_repo=mapping_repo,
    )

    assert result.id == 44


def test_forward_cost_code_direct_no_ref_returns_none():
    assert resolve_dbo_cost_code_direct(None) is None
    assert resolve_dbo_cost_code_direct("") is None


# ---------------------------------------------------------------------------
# resolve_qbo_item_ref (reverse; unwired in U-307a, built for U-307b)
# ---------------------------------------------------------------------------

def test_reverse_no_sub_cost_code_id_returns_none():
    assert resolve_qbo_item_ref(None) is None
    assert resolve_qbo_item_ref(0) is None


def test_reverse_not_found_returns_none():
    scc_service = _FakeSubCostCodeService(by_id={})
    assert resolve_qbo_item_ref(7, sub_cost_code_service=scc_service) is None


def test_reverse_no_qbo_id_returns_none():
    scc = SimpleNamespace(id=7, qbo_id=None, realm_id="realm-1", name="Dumpsters")
    scc_service = _FakeSubCostCodeService(by_id={7: scc})
    assert resolve_qbo_item_ref(7, sub_cost_code_service=scc_service) is None


def test_reverse_matching_realm_resolves():
    scc = SimpleNamespace(id=7, qbo_id="83", realm_id="realm-1", name="Dumpsters")
    scc_service = _FakeSubCostCodeService(by_id={7: scc})

    result = resolve_qbo_item_ref(7, "realm-1", sub_cost_code_service=scc_service)

    assert result == QboItemRef(value="83", name="Dumpsters")


def test_reverse_no_realm_requested_resolves_regardless():
    scc = SimpleNamespace(id=7, qbo_id="83", realm_id="realm-1", name="Dumpsters")
    scc_service = _FakeSubCostCodeService(by_id={7: scc})

    result = resolve_qbo_item_ref(7, None, sub_cost_code_service=scc_service)

    assert result == QboItemRef(value="83", name="Dumpsters")


def test_reverse_mismatched_realm_rejected():
    scc = SimpleNamespace(id=7, qbo_id="83", realm_id="realm-1", name="Dumpsters")
    scc_service = _FakeSubCostCodeService(by_id={7: scc})

    assert resolve_qbo_item_ref(7, "realm-2", sub_cost_code_service=scc_service) is None


def test_reverse_null_realm_on_row_rejected_when_realm_requested():
    """Codex U-307a review P3: a SubCostCode with a QboId but a NULL RealmId (a
    partial/legacy stamp) must not be trusted as a match for a SPECIFIC requested
    realm — the pre-fix condition only rejected when both realms were present and
    different, silently passing through a null-realm row for any requested realm."""
    scc = SimpleNamespace(id=7, qbo_id="83", realm_id=None, name="Dumpsters")
    scc_service = _FakeSubCostCodeService(by_id={7: scc})

    assert resolve_qbo_item_ref(7, "realm-1", sub_cost_code_service=scc_service) is None
