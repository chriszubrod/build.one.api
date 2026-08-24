"""U-292 / U-307a — QboInvoiceService.cost_coded_lines_for_invoice, the dbo-native
cost-code seam draw_financials.py consumes in place of its former ItemRefName string
parser.

U-307a repointed the resolution mechanism itself onto the shared
cost_code_resolver.py: dbo.SubCostCode.QboId / dbo.CostCode.QboId (U-289, 100% live
parity) are now tried FIRST, falling back to the legacy qbo.Item -> qbo.ItemSubCostCode
/ qbo.ItemCostCode staging hop only on a dbo-native miss. Every test below models the
pre-backfill/miss case (dbo-native identity lookups return None) so it exercises the
same legacy-hop value-based tiebreak U-292 originally proved — the fixture wiring
changed from bulk `read_all()` index-building to point-queries, but the business
behavior (and every `triples ==` assertion) is unchanged. Dedicated tests at the bottom
cover the NEW dbo-native primary path directly.

Resolution is always by ID -- QboItem -> ItemSubCostCode -> SubCostCode -> CostCode (or
the dbo-native equivalent) -- never by parsing an Item's display name."""

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


class _FakeQboItem:
    def __init__(self, id, qbo_id):
        self.id = id
        self.qbo_id = qbo_id


class _FakeItemSubCostCode:
    def __init__(self, qbo_item_id, sub_cost_code_id):
        self.qbo_item_id = qbo_item_id
        self.sub_cost_code_id = sub_cost_code_id


class _FakeItemCostCode:
    def __init__(self, qbo_item_id, cost_code_id):
        self.qbo_item_id = qbo_item_id
        self.cost_code_id = cost_code_id


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
             qbo_items=(), item_sub_cost_codes=(), item_cost_codes=(),
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
    dbo-native MISS, forcing every test through the legacy
    qbo.Item -> qbo.ItemSubCostCode/qbo.ItemCostCode hop this file was
    originally written to prove (U-292's value-based tiebreak).
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

    # --- Legacy qbo.Item staging hop -- point-query fakes, by qbo_id/qbo_item_id ---

    class _QboItemRepoFake:
        def __init__(self):
            self._by_qbo_id = {item.qbo_id: item for item in qbo_items}

        def read_by_qbo_id(self, qbo_id):
            return self._by_qbo_id.get(qbo_id)

    class _ItemSubCostCodeRepoFake:
        def __init__(self):
            self._by_qbo_item_id = {m.qbo_item_id: m for m in item_sub_cost_codes}

        def read_by_qbo_item_id(self, qbo_item_id):
            return self._by_qbo_item_id.get(qbo_item_id)

    class _ItemCostCodeRepoFake:
        def __init__(self):
            self._by_qbo_item_id = {m.qbo_item_id: m for m in item_cost_codes}

        def read_by_qbo_item_id(self, qbo_item_id):
            return self._by_qbo_item_id.get(qbo_item_id)

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
        "integrations.intuit.qbo.item.persistence.repo.QboItemRepository", _QboItemRepoFake)
    monkeypatch.setattr(
        "integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo.ItemSubCostCodeRepository",
        _ItemSubCostCodeRepoFake)
    monkeypatch.setattr(
        "integrations.intuit.qbo.item.connector.cost_code.persistence.repo.ItemCostCodeRepository",
        _ItemCostCodeRepoFake)
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
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=7)],
        sub_cost_codes=[_FakeSubCostCode(id=7, cost_code_id=3)],
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
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=7)],
        sub_cost_codes=[_FakeSubCostCode(id=7, cost_code_id=3)],
        cost_codes=[_FakeCostCode(id=3, number="02", name="Dumpsters")],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("715.00"))]


def test_cost_code_level_item_resolves_via_item_cost_code_fallback(monkeypatch):
    """An Item with no SubCostCode granularity (e.g. 'Initial Deposit') maps directly
    at the CostCode level via qbo.ItemCostCode, not qbo.ItemSubCostCode."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("4", 5000.00)]},
        # id / cost_code_id / CostCode.id deliberately DISTINCT (1 / 44 / 77) so a
        # regression that swaps the ItemCostCode lookup key (mapping.qbo_item_id
        # instead of mapping.cost_code_id — the same wrong-ID-space bug class this
        # unit's seam exists to prevent) can't hide behind coincidentally-equal ids.
        qbo_items=[_FakeQboItem(id=1, qbo_id="4")],
        item_sub_cost_codes=[],  # no SubCostCode-level mapping for item 1
        item_cost_codes=[_FakeItemCostCode(qbo_item_id=1, cost_code_id=44)],
        cost_codes=[_FakeCostCode(id=44, number="00", name="Initial & Suspense")],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("00", "Initial & Suspense", Decimal("5000.00"))]


def test_non_numeric_cost_code_falls_to_uncoded(monkeypatch):
    """QBO-admin pseudo-codes ('Hours'/'Sales') never counted as coded under the
    prior ItemRefName parser (it required a leading digit) — the seam must not
    start counting them now just because it can resolve them by ID."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("2", 100.00)]},
        qbo_items=[_FakeQboItem(id=1, qbo_id="2")],
        item_cost_codes=[_FakeItemCostCode(qbo_item_id=1, cost_code_id=44)],
        cost_codes=[_FakeCostCode(id=44, number="Sales", name="Sales")],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("100.00"))]


def test_dangling_sub_cost_code_still_falls_back_to_valid_item_cost_code(monkeypatch):
    """A dangling SubCostCode-level mapping must not shadow a perfectly resolvable
    CostCode-level one for the SAME item — precedence is decided by whether the
    SubCostCode-level entry actually RESOLVES, not by whether the row exists."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 25.00)]},
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=999)],  # dangling
        item_cost_codes=[_FakeItemCostCode(qbo_item_id=10, cost_code_id=3)],  # valid fallback, same item
        cost_codes=[_FakeCostCode(id=3, number="02", name="Dumpsters")],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("25.00"))]


def test_sub_cost_code_mapping_takes_precedence_over_item_cost_code(monkeypatch):
    """If (in principle) both mapping tables somehow named the same item, the
    finer-grained SubCostCode-level mapping wins."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 10.00)]},
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=7)],
        item_cost_codes=[_FakeItemCostCode(qbo_item_id=10, cost_code_id=99)],
        sub_cost_codes=[_FakeSubCostCode(id=7, cost_code_id=3)],
        cost_codes=[
            _FakeCostCode(id=3, number="02", name="Dumpsters"),
            _FakeCostCode(id=99, number="99", name="Bad Debt"),
        ],
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("10.00"))]


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


def test_missing_item_sub_cost_code_mapping_falls_to_uncoded(monkeypatch):
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 25.00)]},
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        # no ItemSubCostCode and no ItemCostCode mapping for item 10
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("25.00"))]


def test_dangling_sub_cost_code_falls_to_uncoded(monkeypatch):
    """Mapping row exists but its SubCostCode has since been deleted — defensive,
    never raise, always degrade to Uncoded so a draw still foots."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 25.00)]},
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=999)],  # dangling
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("25.00"))]


def test_dangling_cost_code_falls_to_uncoded(monkeypatch):
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 25.00)]},
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=7)],
        sub_cost_codes=[_FakeSubCostCode(id=7, cost_code_id=999)],  # dangling
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("25.00"))]


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
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=7)],
        sub_cost_codes=[_FakeSubCostCode(id=7, cost_code_id=3)],
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
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=7)],
        sub_cost_codes=[_FakeSubCostCode(id=7, cost_code_id=3)],
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
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=7)],
        sub_cost_codes=[_FakeSubCostCode(id=7, cost_code_id=3)],
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
    qbo.ItemSubCostCode -- no qbo_items/item_sub_cost_codes fixture data at all."""
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


def test_dbo_native_non_numeric_still_falls_to_legacy_cost_code_fallback(monkeypatch):
    """The value-based tiebreak applies to a dbo-native SubCostCode-level hit too:
    if its resolved CostCode is a non-numeric pseudo-code, the resolver still tries
    the (here legacy) CostCode-level fallback and SUCCEEDS with a distinct, valid
    numeric CostCode — proving the non-numeric primary result doesn't shadow a
    usable fallback (Codex U-307a review finding: the prior version of this test
    left the fallback CostCode out of the fixture, so both branches failed for
    unrelated reasons and it never actually exercised a successful fallback)."""
    svc = _service(
        monkeypatch,
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 25.00)]},
        direct_sub_cost_code=_FakeSubCostCode(id=7, cost_code_id=3, qbo_id="83", realm_id="realm-1"),
        cost_codes=[
            _FakeCostCode(id=3, number="Hours", name="Hours"),
            _FakeCostCode(id=44, number="02", name="Dumpsters"),
        ],
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_cost_codes=[_FakeItemCostCode(qbo_item_id=10, cost_code_id=44)],
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
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=7)],
        sub_cost_codes=[_FakeSubCostCode(id=7, cost_code_id=3)],
        cost_codes=[_FakeCostCode(id=3, number="02", name="Dumpsters")],
    )
    svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert svc._test_scc_identity_calls == [("83", "realm-1")]
