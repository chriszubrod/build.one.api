"""U-292 — QboInvoiceService.cost_coded_lines_for_invoice, the dbo-native cost-code
seam draw_financials.py consumes in place of its former ItemRefName string parser.
Resolution: a one-time in-memory index (5 small bulk reads — QboItem/SubCostCode/
CostCode/ItemSubCostCode/ItemCostCode) built lazily per QboInvoiceService instance,
keyed by QboItem id, never by parsing a display name. Bulk (not point-query) so a
project with many invoices resolves each recurring QBO item once, not per line —
a real many-invoice project hit connection drops under a naive per-line-query shape
during this unit's own equivalence testing against live data."""

from decimal import Decimal


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
    def __init__(self, id, cost_code_id):
        self.id = id
        self.cost_code_id = cost_code_id


class _FakeCostCode:
    def __init__(self, id, number, name):
        self.id = id
        self.number = number
        self.name = name


class _FakeLineRepo:
    def __init__(self, lines_by_qbo_invoice_id):
        self._lines = lines_by_qbo_invoice_id

    def read_by_qbo_invoice_id(self, qbo_invoice_id):
        return self._lines.get(qbo_invoice_id, [])


def _service(monkeypatch, *, mapping, lines_by_qbo_invoice_id,
             qbo_items=(), item_sub_cost_codes=(), item_cost_codes=(),
             sub_cost_codes=(), cost_codes=()):
    """Wire the seam's full bulk-read dependency chain and return a QboInvoiceService
    with its line_repo injected (mirroring how draw_financials.py's caller only ever
    controls the invoice/line data, never the QBO reference tables)."""
    from integrations.intuit.qbo.invoice.business.service import QboInvoiceService

    class _IIR:
        def read_by_invoice_id(self, invoice_id):
            return mapping

    class _QIR:
        def read_all(self):
            return list(qbo_items)

    class _ISCR:
        def read_all(self):
            return list(item_sub_cost_codes)

    class _ICCR:
        def read_all(self):
            return list(item_cost_codes)

    class _SCCS:
        def read_all(self):
            return list(sub_cost_codes)

    class _CCS:
        def read_all(self):
            return list(cost_codes)

    monkeypatch.setattr(
        "integrations.intuit.qbo.invoice.connector.invoice.persistence.repo.InvoiceInvoiceRepository", _IIR)
    monkeypatch.setattr(
        "integrations.intuit.qbo.item.persistence.repo.QboItemRepository", _QIR)
    monkeypatch.setattr(
        "integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo.ItemSubCostCodeRepository", _ISCR)
    monkeypatch.setattr(
        "integrations.intuit.qbo.item.connector.cost_code.persistence.repo.ItemCostCodeRepository", _ICCR)
    monkeypatch.setattr(
        "entities.sub_cost_code.business.service.SubCostCodeService", _SCCS)
    monkeypatch.setattr(
        "entities.cost_code.business.service.CostCodeService", _CCS)

    return QboInvoiceService(line_repo=_FakeLineRepo(lines_by_qbo_invoice_id))


def test_resolves_cost_code_by_id_not_by_name(monkeypatch):
    """The line's ItemRefValue is the only signal used — no display name involved."""
    svc = _service(
        monkeypatch,
        mapping=_FakeMapping(900),
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
        mapping=_FakeMapping(900),
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
        mapping=_FakeMapping(900),
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
        mapping=_FakeMapping(900),
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
        mapping=_FakeMapping(900),
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
        mapping=_FakeMapping(900),
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
        mapping=_FakeMapping(900),
        lines_by_qbo_invoice_id={900: [_FakeQboLine(None, 50.00)]},
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("50.00"))]


def test_unresolvable_qbo_item_falls_to_uncoded(monkeypatch):
    svc = _service(
        monkeypatch,
        mapping=_FakeMapping(900),
        lines_by_qbo_invoice_id={900: [_FakeQboLine("does-not-exist", 25.00)]},
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("25.00"))]


def test_missing_item_sub_cost_code_mapping_falls_to_uncoded(monkeypatch):
    svc = _service(
        monkeypatch,
        mapping=_FakeMapping(900),
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
        mapping=_FakeMapping(900),
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 25.00)]},
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=999)],  # dangling
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("25.00"))]


def test_dangling_cost_code_falls_to_uncoded(monkeypatch):
    svc = _service(
        monkeypatch,
        mapping=_FakeMapping(900),
        lines_by_qbo_invoice_id={900: [_FakeQboLine("83", 25.00)]},
        qbo_items=[_FakeQboItem(id=10, qbo_id="83")],
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=7)],
        sub_cost_codes=[_FakeSubCostCode(id=7, cost_code_id=999)],  # dangling
    )
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("", "Uncoded", Decimal("25.00"))]


def test_no_mapping_returns_empty(monkeypatch):
    svc = _service(monkeypatch, mapping=None, lines_by_qbo_invoice_id={})
    assert svc.cost_coded_lines_for_invoice(invoice_id=1) == []


def test_mapping_with_no_qbo_invoice_id_returns_empty(monkeypatch):
    svc = _service(monkeypatch, mapping=_FakeMapping(None), lines_by_qbo_invoice_id={})
    assert svc.cost_coded_lines_for_invoice(invoice_id=1) == []


def test_mapped_invoice_with_no_lines_returns_empty(monkeypatch):
    svc = _service(monkeypatch, mapping=_FakeMapping(900), lines_by_qbo_invoice_id={})
    assert svc.cost_coded_lines_for_invoice(invoice_id=1) == []


def test_index_built_once_per_instance(monkeypatch):
    """The bulk index is built lazily on first resolution and reused for every
    subsequent line/invoice on the same QboInvoiceService instance — the whole
    point of the bulk-read redesign (was N point-queries per line before)."""
    calls = {"qbo_items": 0}

    class _CountingQIR:
        def read_all(self):
            calls["qbo_items"] += 1
            return [_FakeQboItem(id=10, qbo_id="83")]

    svc = _service(
        monkeypatch,
        mapping=_FakeMapping(900),
        lines_by_qbo_invoice_id={900: [
            _FakeQboLine("83", 1.00), _FakeQboLine("83", 2.00), _FakeQboLine("83", 3.00),
        ]},
        item_sub_cost_codes=[_FakeItemSubCostCode(qbo_item_id=10, sub_cost_code_id=7)],
        sub_cost_codes=[_FakeSubCostCode(id=7, cost_code_id=3)],
        cost_codes=[_FakeCostCode(id=3, number="02", name="Dumpsters")],
    )
    # re-patch AFTER _service() so the counting fake is the one actually used
    monkeypatch.setattr(
        "integrations.intuit.qbo.item.persistence.repo.QboItemRepository", _CountingQIR)
    triples = svc.cost_coded_lines_for_invoice(invoice_id=1)
    assert triples == [("02", "Dumpsters", Decimal("1.00")),
                        ("02", "Dumpsters", Decimal("2.00")),
                        ("02", "Dumpsters", Decimal("3.00"))]
    assert calls["qbo_items"] == 1
