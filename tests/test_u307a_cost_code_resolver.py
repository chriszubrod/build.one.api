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


# U-307d retired the legacy qbo.Item -> qbo.Item*CostCode staging-hop fallback and the
# _FakeQboItemRepo / _FakeMappingRepo fakes that drove it. The forward resolvers are now
# dbo-native only; the fallback-tier tests (dbo miss -> legacy hop, dangling mapping,
# legacy-hop error propagation, and the "skips legacy hop" call-count assertions) were
# removed. dbo-native hit and dbo-native miss coverage is retained below.


# ---------------------------------------------------------------------------
# resolve_dbo_sub_cost_code
# ---------------------------------------------------------------------------

def test_forward_sub_cost_code_no_ref_returns_none_without_touching_deps():
    scc_service = _FakeSubCostCodeService()
    assert resolve_dbo_sub_cost_code(None, "realm-1", sub_cost_code_service=scc_service) is None
    assert scc_service.identity_calls == []


def test_forward_sub_cost_code_dbo_native_hit():
    scc = SimpleNamespace(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1")
    scc_service = _FakeSubCostCodeService(by_qbo_identity={("83", "realm-1"): scc})

    result = resolve_dbo_sub_cost_code("83", "realm-1", sub_cost_code_service=scc_service)

    assert result is scc
    assert scc_service.identity_calls == [("83", "realm-1")]


def test_forward_sub_cost_code_dbo_native_miss_returns_none():
    scc_service = _FakeSubCostCodeService()  # no dbo-native match

    result = resolve_dbo_sub_cost_code("missing", "realm-1", sub_cost_code_service=scc_service)

    assert result is None
    assert scc_service.identity_calls == [("missing", "realm-1")]


# ---------------------------------------------------------------------------
# resolve_dbo_cost_code_direct
# ---------------------------------------------------------------------------

def test_forward_cost_code_direct_dbo_native_hit():
    cc = SimpleNamespace(id=44, number="00", name="Initial & Suspense", qbo_id="4")
    cc_service = _FakeCostCodeService(by_qbo_identity={("4", "realm-1"): cc})

    result = resolve_dbo_cost_code_direct("4", "realm-1", cost_code_service=cc_service)

    assert result is cc
    assert cc_service.identity_calls == [("4", "realm-1")]


def test_forward_cost_code_direct_dbo_native_miss_returns_none():
    cc_service = _FakeCostCodeService()  # no dbo-native match

    assert resolve_dbo_cost_code_direct("4", "realm-1", cost_code_service=cc_service) is None


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
