"""U-292 / U-307a — QboInvoiceService.cost_coded_lines_for_invoice, the dbo-native
cost-code seam draw_financials.py consumes in place of its former ItemRefName string
parser.

U-307a repointed the resolution mechanism onto the shared cost_code_resolver.py
(dbo.SubCostCode.QboId / dbo.CostCode.QboId, U-289, 100% live parity). U-307d-prereq
then RETIRED the legacy qbo.Item -> qbo.ItemSubCostCode/qbo.ItemCostCode staging-hop
fallback entirely — resolution is now dbo-native only. Tests that model a successful
resolution pass `direct_sub_cost_code` / `direct_cost_code` (a dbo-native identity hit);
the value-based (numeric) two-tier tiebreak U-292 originally proved is exercised via
those dbo-native hits. The legacy-hop-specific tests (and the qbo.Item* mapping-table
precedence rules) were removed with the fallback; U-307d then dropped the `qbo.Item*`
tables themselves plus `_service`'s now-fully-inert `qbo_items`/`item_sub_cost_codes`/
`item_cost_codes` passthrough params — a dbo-native miss just means Uncoded, no
staging-table shape left to model.

Resolution is always by ID -- dbo.SubCostCode.QboId / dbo.CostCode.QboId -- never by
parsing an Item's display name."""

from decimal import Decimal


class _FakeInvoice:
    def __init__(self, qbo_id, realm_id="realm-1"):
        self.qbo_id = qbo_id
        self.realm_id = realm_id


class _FakeQboInvoice:
    def __init__(self, id):
        self.id = id


class _FakeMapping:
    def __init__(self, qbo_invoice_id):
        self.qbo_invoice_id = qbo_invoice_id


class _FakeQboLine:
    def __init__(self, item_ref_value, amount, detail_type="SalesItemLineDetail"):
        self.item_ref_value = item_ref_value
        self.amount = Decimal(str(amount)) if amount is not None else None
        self.detail_type = detail_type


class _FakeSubCostCode:
    def __init__(self, id, cost_code_id, qbo_id=None, realm_id=None):
        self.id = id
        self.cost_code_id = cost_code_id
        self.qbo_id = qbo_id
        self.realm_id = realm_id


class _FakeCostCode:
    def __init__(self, id, number, name, qbo_id=None, realm_id=None):
        self.id = id
        self.number = number
        self.name = name
        self.qbo_id = qbo_id
        self.realm_id = realm_id


class _FakeLineRepo:
    def __init__(self, lines_by_qbo_invoice_id):
        self._lines = lines_by_qbo_invoice_id

    def read_by_qbo_invoice_id(self, qbo_invoice_id):
        return self._lines.get(qbo_invoice_id, [])


def _service(monkeypatch, *, invoice_qbo_id="83-INV", qbo_invoice_id=900,
             legacy_mapping_qbo_invoice_id="_unset",
             lines_by_qbo_invoice_id,
             sub_cost_codes=(), cost_codes=(),
             direct_sub_cost_code=None, direct_cost_code=None):
    """Wire the seam's full resolution dependency chain and return a QboInvoiceService
    with its line_repo injected (mirroring how draw_financials.py's caller only ever
    controls the invoice/line data, never the QBO reference tables).

    U-284: identity resolution tries dbo.Invoice.QboId/RealmId -> qbo.Invoice as
    the fast path (invoice_qbo_id=None models an unsynced/unbackfilled Invoice;
    qbo_invoice_id=None models a stamped Invoice whose qbo.Invoice staging row
    can't be found by that identity), falling back to the pre-existing
    qbo.InvoiceInvoice mapping table on a fast-path miss —
    legacy_mapping_qbo_invoice_id models that fallback's result (the sentinel
    "_unset" means "no legacy mapping row exists at all", distinct from a
    mapping row that itself carries a NULL qbo_invoice_id).

    U-307a: `direct_sub_cost_code` / `direct_cost_code` model a dbo-native
    identity HIT (SubCostCode.QboId / CostCode.QboId already stamped for this
    line's item ref) — the fast tier. Omitted (None, the default) models a
    dbo-native MISS -- since U-307d retired the legacy qbo.Item staging hop,
    a miss now resolves straight to Uncoded (no fallback tier left to try).
    """
    from integrations.intuit.qbo.invoice.business.service import QboInvoiceService

    fake_invoice = _FakeInvoice(qbo_id=invoice_qbo_id) if invoice_qbo_id is not None else None

    class _FakeInvoiceService:
        def read_by_id(self, invoice_id):
            return fake_invoice

    class _FakeQboInvoiceRepo:
        def read_by_qbo_id_and_realm_id(self, qbo_id, realm_id):
            return _FakeQboInvoice(id=qbo_invoice_id) if qbo_invoice_id is not None else None

    class _FakeInvoiceInvoiceRepository:
        def read_by_invoice_id(self, invoice_id):
            if legacy_mapping_qbo_invoice_id == "_unset":
                return None
            return _FakeMapping(qbo_invoice_id=legacy_mapping_qbo_invoice_id)

    monkeypatch.setattr(
        "integrations.intuit.qbo.invoice.connector.invoice.persistence.repo.InvoiceInvoiceRepository",
        _FakeInvoiceInvoiceRepository,
    )

    # --- dbo-native identity + by-id fakes ---
    # Shared (not per-instance) call log — a fresh service instance is constructed
    # per resolution inside cost_code_resolver.py, so a per-instance log would
    # reset every call; tests read `sub_cost_code_identity_calls` off the closure
    # via the returned QboInvoiceService's `_test_scc_identity_calls` attribute.
    sub_cost_code_identity_calls = []

    class _SubCostCodeServiceFake:
        def __init__(self):
            self._by_id = {scc.id: scc for scc in sub_cost_codes}

        def read_by_qbo_identity(self, qbo_id, realm_id=None):
            sub_cost_code_identity_calls.append((qbo_id, realm_id))
            return direct_sub_cost_code

        def read_by_id(self, id):
            return self._by_id.get(id)

    class _CostCodeServiceFake:
        def __init__(self):
            self._by_id = {cc.id: cc for cc in cost_codes}

        def read_by_qbo_identity(self, qbo_id, realm_id=None):
            return direct_cost_code

        def read_by_id(self, id):
            return self._by_id.get(id)

    monkeypatch.setattr(
        "entities.invoice.business.service.InvoiceService", _FakeInvoiceService)
    monkeypatch.setattr(
        "entities.sub_cost_code.business.service.SubCostCodeService", _SubCostCodeServiceFake)
    monkeypatch.setattr(
        "entities.cost_code.business.service.CostCodeService", _CostCodeServiceFake)

    svc = QboInvoiceService(
        repo=_FakeQboInvoiceRepo(), line_repo=_FakeLineRepo(lines_by_qbo_invoice_id)
    )
    svc._test_scc_identity_calls = sub_cost_code_identity_calls
    return svc


def test_resolves_cost_code_by_id_not_by_name(monkeypatch):
    """The line's ItemRefValue is the only signal used — no display name involved."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 715.00)]},
        direct_sub_cost_code=_FakeSubCostCode(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1"),
        cost_codes=[_FakeCostCode(id=3, number="02", name="Dumpsters")],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("715.00"))]


def test_subtotal_and_none_amount_lines_are_skipped(monkeypatch):
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [
            _FakeQboLine("83", 715.00),
            _FakeQboLine("83", 1000.00, detail_type="SubTotalLineDetail"),
            _FakeQboLine("83", None),
        ]},
        direct_sub_cost_code=_FakeSubCostCode(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1"),
        cost_codes=[_FakeCostCode(id=3, number="02", name="Dumpsters")],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("715.00"))]


# U-307d retired 3 legacy-qbo.Item-hop tests here (cost_code_level_item_resolves_via_
# item_cost_code_fallback, dangling_sub_cost_code_still_falls_back_to_valid_item_cost_code,
# sub_cost_code_mapping_takes_precedence_over_item_cost_code): they exercised the deleted
# qbo.Item -> qbo.ItemSubCostCode/qbo.ItemCostCode staging hop and its row-existence
# precedence rules. The dbo-native tier's CostCode-level resolution and the
# value-based (numeric) tiebreak are covered by the dbo-native tests at the bottom of
# this file.


def test_non_numeric_cost_code_falls_to_uncoded(monkeypatch):
    """QBO-admin pseudo-codes ('Hours'/'Sales') never counted as coded under the
    prior ItemRefName parser (it required a leading digit) — the seam must not
    start counting them now just because it can resolve them by ID. Terminal case:
    BOTH dbo-native tiers RESOLVE (SubCostCode-level -> 'Hours', CostCode-level
    fallback -> 'Sales'), but the value-based _numeric_result filter rejects both
    non-numeric codes -> Uncoded — distinct from a plain nothing-resolved miss."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 100.00)]},
        direct_sub_cost_code=_FakeSubCostCode(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1"),
        direct_cost_code=_FakeCostCode(id=9, number="Sales", name="Sales", qbo_id="83"),
        cost_codes=[
            _FakeCostCode(id=3, number="Hours", name="Hours"),
            _FakeCostCode(id=9, number="Sales", name="Sales"),
        ],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("100.00"))]


def test_no_item_ref_value_falls_to_uncoded(monkeypatch):
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine(None, 50.00)]},
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("50.00"))]


def test_unresolvable_qbo_item_falls_to_uncoded(monkeypatch):
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("does-not-exist", 25.00)]},
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("25.00"))]


# U-307d retired 3 more legacy-qbo.Item-hop tests here
# (test_missing_item_sub_cost_code_mapping_falls_to_uncoded,
# test_dangling_sub_cost_code_falls_to_uncoded, test_dangling_cost_code_falls_to_uncoded):
# each modeled a distinct qbo.Item*/mapping-table shape (no mapping / dangling
# SubCostCode / dangling CostCode) that only mattered to the legacy hop -- with
# it gone, all 3 collapsed to the exact same dbo-native-miss-with-no-hit
# scenario already covered by test_unresolvable_qbo_item_falls_to_uncoded.


def test_unsynced_invoice_with_no_legacy_mapping_returns_empty(monkeypatch):
    """Invoice never QBO-synced (no QboId stamped) AND no legacy qbo.InvoiceInvoice
    mapping row either -> genuinely never synced -> []."""
    svc = _service(monkeypatch, invoice_qbo_id=None, lines_by_qbo_invoice_id={})
    assert svc.cost_coded_lines_for_invoice(invoice_id=1) == []


def test_qbo_invoice_not_found_with_no_legacy_mapping_returns_empty(monkeypatch):
    """Invoice carries a QboId/RealmId but no qbo.Invoice staging row resolves for
    it, AND no legacy mapping row exists either -> [] rather than raising."""
    svc = _service(monkeypatch, qbo_invoice_id=None, lines_by_qbo_invoice_id={})
    assert svc.cost_coded_lines_for_invoice(invoice_id=1) == []


def test_unbackfilled_invoice_falls_back_to_legacy_mapping(monkeypatch):
    """U-284 regression guard: dbo.Invoice.QboId not (yet) stamped must NOT be
    treated as 'never synced' when a legacy qbo.InvoiceInvoice mapping row still
    resolves it (e.g. predates the identity backfill, or SetInvoiceQboIdentity's
    theft-clear UPDATE nulled this row's identity without touching the mapping
    table) — the fast-path miss must fall back, exactly like every other
    Phase-4 repoint's identity_fastpath.py contract, not silently drop lines."""
    svc = _service(
        monkeypatch,
        invoice_qbo_id=None,
        legacy_mapping_qbo_invoice_id=900,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 715.00)]},
        direct_sub_cost_code=_FakeSubCostCode(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1"),
        cost_codes=[_FakeCostCode(id=3, number="02", name="Dumpsters")],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("715.00"))]


def test_stale_dbo_identity_falls_back_to_legacy_mapping(monkeypatch):
    """Same regression guard as above, for the OTHER fast-path-miss shape: dbo.Invoice
    carries a QboId, but no qbo.Invoice staging row resolves by that identity (e.g.
    a stale/theft-cleared realm mismatch) — must still fall back to the legacy
    mapping table rather than returning [] for an invoice that IS actually mapped."""
    svc = _service(
        monkeypatch,
        qbo_invoice_id=None,
        legacy_mapping_qbo_invoice_id=900,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 715.00)]},
        direct_sub_cost_code=_FakeSubCostCode(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1"),
        cost_codes=[_FakeCostCode(id=3, number="02", name="Dumpsters")],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("715.00"))]


def test_legacy_mapping_with_no_qbo_invoice_id_returns_empty(monkeypatch):
    """A legacy mapping row that itself carries a NULL qbo_invoice_id must not be
    treated as a hit."""
    svc = _service(
        monkeypatch,
        invoice_qbo_id=None,
        legacy_mapping_qbo_invoice_id=None,
        lines_by_qbo_invoice_id={},
    )
    assert svc.cost_coded_lines_for_invoice(invoice_id=1) == []


def test_mapped_invoice_with_no_lines_returns_empty(monkeypatch):
    svc = _service(monkeypatch, lines_by_qbo_invoice_id={})
    assert svc.cost_coded_lines_for_invoice(invoice_id=1) == []


def test_resolution_memoized_once_per_distinct_item_per_instance(monkeypatch):
    """U-307a: the per-instance resolution cache is built lazily and reused for
    every subsequent line/invoice on the same QboInvoiceService instance sharing
    the same (realm_id, item_ref) — the same amortization guarantee the original
    U-292 bulk index gave, now via point-query memoization instead of a
    bulk-read-once index."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [
            _FakeQboLine("83", 1.00), _FakeQboLine("83", 2.00), _FakeQboLine("83", 3.00),
        ]},
        direct_sub_cost_code=_FakeSubCostCode(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1"),
        cost_codes=[_FakeCostCode(id=3, number="02", name="Dumpsters")],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("1.00")),
                        ("02", "Dumpsters", Decimal("2.00")),
                        ("02", "Dumpsters", Decimal("3.00"))]
    # Codex U-307a round-2 review: len(cache) == 1 alone doesn't prove memoization
    # avoided redundant resolution — a regression that re-resolves every line but
    # overwrites the same cache key each time would still leave len == 1. Assert
    # the underlying dbo-native identity lookup itself was only called once.
    assert svc._test_scc_identity_calls == [("83", "realm-1")]
    resolved = svc._resolve_cost_code_for_qbo_item_ref("83", "realm-1")
    assert resolved == ("02", "Dumpsters")
    # And confirms the direct re-call above served from cache too (no 2nd call).
    assert svc._test_scc_identity_calls == [("83", "realm-1")]


# ---------------------------------------------------------------------------
# U-307a — dbo-native PRIMARY path (dbo.SubCostCode.QboId / dbo.CostCode.QboId
# already stamped). These are the new tier; every test above models a miss.
# ---------------------------------------------------------------------------

def test_dbo_native_sub_cost_code_hit_skips_legacy_hop_entirely(monkeypatch):
    """A stamped dbo.SubCostCode.QboId resolves without ever touching qbo.Item /
    qbo.ItemSubCostCode -- no legacy staging-table fixture data at all."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 715.00)]},
        direct_sub_cost_code=_FakeSubCostCode(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1"),
        cost_codes=[_FakeCostCode(id=3, number="02", name="Dumpsters")],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("715.00"))]


def test_dbo_native_cost_code_level_hit_skips_legacy_hop(monkeypatch):
    """A stamped dbo.CostCode.QboId (Item with no SubCostCode granularity) resolves
    without touching qbo.Item / qbo.ItemCostCode."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("4", 5000.00)]},
        direct_cost_code=_FakeCostCode(id=44, number="00", name="Initial & Suspense", qbo_id="4"),
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("00", "Initial & Suspense", Decimal("5000.00"))]


def test_dbo_native_non_numeric_still_falls_to_cost_code_level_fallback(monkeypatch):
    """The value-based tiebreak applies to a dbo-native SubCostCode-level hit too:
    if its resolved CostCode is a non-numeric pseudo-code, the resolver still tries
    the CostCode-level dbo-native fallback (resolve_dbo_cost_code_direct) and
    SUCCEEDS with a distinct, valid numeric CostCode — proving the non-numeric
    primary result doesn't shadow a usable fallback."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 25.00)]},
        direct_sub_cost_code=_FakeSubCostCode(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1"),
        direct_cost_code=_FakeCostCode(id=44, number="02", name="Dumpsters", qbo_id="83"),
        cost_codes=[
            _FakeCostCode(id=3, number="Hours", name="Hours"),
            _FakeCostCode(id=44, number="02", name="Dumpsters"),
        ],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("25.00"))]


def test_dbo_native_lookup_receives_invoice_realm_id(monkeypatch):
    """realm_id threads from the invoice's own dbo.Invoice.RealmId (default
    "realm-1" in _FakeInvoice) into the dbo-native identity lookup — U-307a added
    this; the legacy hop stayed realm-blind, matching every hand-copied chain it
    replaces."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 715.00)]},
    )
    svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert svc._test_scc_identity_calls == [("83", "realm-1")]
