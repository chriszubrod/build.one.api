"""U-206 — DrawFinancialsService.coded_draws_for_project (canonical-draw select + fee)."""

from decimal import Decimal


class _Inv:
    def __init__(self, id, number, date, total_amount=None):
        self.id = id
        self.invoice_number = number
        self.invoice_date = date
        self.total_amount = total_amount


class _FakeInvoices:
    def __init__(self, invoices):
        self._invoices = invoices

    def read_paginated(self, **kwargs):
        return list(self._invoices)


class _FakeLineItems:
    def read_by_invoice_id(self, invoice_id):
        # Opaque marker rows; enrich_line_items is monkeypatched to key on the id.
        return [{"id": invoice_id}]


def _patch_enrich(monkeypatch, by_invoice):
    def fake_enrich(line_items):
        iid = line_items[0]["id"]
        return by_invoice.get(iid, [])
    monkeypatch.setattr("entities.invoice.business.enrichment.enrich_line_items", fake_enrich)


def test_coded_draws_selects_coded_only_and_computes_fee(monkeypatch):
    from entities.invoice.business.draw_financials import DrawFinancialsService

    invoices = [
        _Inv(1, "HA-01", "2026-02-27"),   # coded
        _Inv(2, "HA-01-2", "2026-03-11"),  # Manual-only duplicate -> excluded
        _Inv(3, "HA-02", "2026-04-10"),   # coded
        _Inv(4, "HA-05", "2026-08-01"),   # uncoded (all Manual) -> excluded
    ]
    _patch_enrich(monkeypatch, {
        1: [{"source_type": "BillLineItem", "cost_code_number": "06", "cost_code_name": "Grading", "billed_price": 1000.0}],
        2: [{"source_type": "Manual", "cost_code_number": "06", "cost_code_name": "Grading", "billed_price": 500.0}],
        3: [{"source_type": "ExpenseLineItem", "cost_code_number": "13", "cost_code_name": "Framing", "billed_price": 2000.0}],
        4: [{"source_type": "Manual", "cost_code_number": "59", "cost_code_name": "Pool", "billed_price": 9999.0}],
    })

    svc = DrawFinancialsService(invoice_service=_FakeInvoices(invoices), line_item_service=_FakeLineItems())
    draws = svc.coded_draws_for_project(128, Decimal("0.14"))

    # Only the two CODED draws survive; the Manual-only duplicate + uncoded HA-05 are dropped.
    assert [d["label"] for d in draws] == ["HA-01", "HA-02"]
    ha01 = draws[0]
    assert ha01["subtotal"] == Decimal("1000.00")
    assert ha01["builders_fee"] == Decimal("140.00")   # 1000 * 0.14
    assert ha01["total"] == Decimal("1140.00")
    assert ha01["categories"] == [{"cost_code_number": "06", "cost_code_name": "Grading", "amount": Decimal("1000.00")}]


def test_coded_draws_no_fee_when_rate_none(monkeypatch):
    from entities.invoice.business.draw_financials import DrawFinancialsService

    _patch_enrich(monkeypatch, {
        1: [{"source_type": "BillLineItem", "cost_code_number": "06", "cost_code_name": "Grading", "billed_price": 1000.0}],
    })
    svc = DrawFinancialsService(
        invoice_service=_FakeInvoices([_Inv(1, "HA-01", "2026-02-27")]),
        line_item_service=_FakeLineItems(),
    )
    draws = svc.coded_draws_for_project(128, None)
    assert draws[0]["builders_fee"] == Decimal("0")
    assert draws[0]["total"] == Decimal("1000.00")


def test_coded_draws_empty_project_returns_empty():
    from entities.invoice.business.draw_financials import DrawFinancialsService
    assert DrawFinancialsService().coded_draws_for_project(None, Decimal("0.14")) == []


# ─── U-271: all_draws_for_project (early/QBO-derived + dedup + merge) ──────────
# U-292: the QBO-derivation source moved onto QboInvoiceService.cost_coded_lines_
# for_invoice() (a dbo-native ID seam, tested on its own in
# tests/test_qbo_invoice_cost_coded_lines.py). These tests fake that seam directly
# — draw_financials.py's own responsibility is grouping/dedup/merge of whatever
# (cost_code_number, cost_code_name, amount) triples it returns, not QBO resolution.

def test_reissue_base_label():
    from entities.invoice.business.draw_financials import _reissue_base_label
    assert _reissue_base_label("MR2-MAIN-04-2") == "MR2-MAIN-04"
    assert _reissue_base_label("MR2-MAIN-04") == "MR2-MAIN-04"      # single -N unchanged
    assert _reissue_base_label("MR2-MAIN-09") == "MR2-MAIN-09"
    assert _reissue_base_label("HA-05") == "HA-05"


def _patch_qbo_seam(monkeypatch, triples_by_invoice):
    """Fake QboInvoiceService.cost_coded_lines_for_invoice — already-resolved
    (cost_code_number, cost_code_name, amount) triples per LOCAL invoice id,
    exactly the shape the real seam (dbo-native ID resolution) hands back."""
    class _FakeQboInvoiceService:
        def cost_coded_lines_for_invoice(self, invoice_id):
            return triples_by_invoice.get(invoice_id, [])

    monkeypatch.setattr(
        "integrations.intuit.qbo.invoice.business.service.QboInvoiceService",
        _FakeQboInvoiceService,
    )


def test_all_draws_includes_qbo_derived_early_draw_with_uncoded_bucket(monkeypatch):
    from entities.invoice.business.draw_financials import DrawFinancialsService

    invoices = [
        _Inv(1, "MR2-MAIN-01", "2025-10-28", Decimal("1050.00")),  # all-Manual -> QBO-derived
        _Inv(2, "MR2-MAIN-02", "2026-02-27", Decimal("2000.00")),  # coded
    ]
    # inv 1 has NO local coded lines; inv 2 is coded.
    _patch_enrich(monkeypatch, {
        1: [{"source_type": "Manual", "billed_price": 100.0}],
        2: [{"source_type": "BillLineItem", "cost_code_number": "13", "cost_code_name": "Framing", "billed_price": 2000.0}],
    })
    _patch_qbo_seam(monkeypatch, {
        1: [
            ("2", "Dumpsters", Decimal("715.00")),
            ("13", "Framing", Decimal("285.00")),
            ("", "Uncoded", Decimal("50.00")),   # no resolvable item -> Uncoded (SubTotal line already excluded by the seam)
        ],
    })
    svc = DrawFinancialsService(invoice_service=_FakeInvoices(invoices), line_item_service=_FakeLineItems())
    draws = svc.all_draws_for_project(1, None)

    assert [d["label"] for d in draws] == ["MR2-MAIN-01", "MR2-MAIN-02"]
    early = draws[0]
    # subtotal foots to item lines + uncoded: 715 + 285 + 50 = 1050
    assert early["subtotal"] == Decimal("1050.00")
    assert early["total"] == Decimal("1050.00")
    assert early["builders_fee"] == Decimal("0")
    cats = {c["cost_code_number"]: c for c in early["categories"]}
    assert cats["2"]["amount"] == Decimal("715.00")
    assert cats["2"]["cost_code_name"] == "Dumpsters"
    assert cats["13"]["amount"] == Decimal("285.00")
    assert cats[""]["amount"] == Decimal("50.00")            # Uncoded bucket
    assert cats[""]["cost_code_name"] == "Uncoded"


def test_all_draws_drops_qbo_mirror_duplicate_of_coded_draw(monkeypatch):
    from entities.invoice.business.draw_financials import DrawFinancialsService

    invoices = [
        _Inv(1, "MR2-MAIN-05", "2026-02-27", Decimal("102729.54")),    # coded
        _Inv(2, "MR2-MAIN-05-2", "2026-02-27", Decimal("102729.54")),  # QBO mirror, SAME date+total -> dropped
    ]
    _patch_enrich(monkeypatch, {
        1: [{"source_type": "BillLineItem", "cost_code_number": "13", "cost_code_name": "Framing", "billed_price": 102729.54}],
        2: [{"source_type": "Manual", "billed_price": 1.0}],
    })
    _patch_qbo_seam(monkeypatch, {2: [("13", "Framing", Decimal("102729.54"))]})
    svc = DrawFinancialsService(invoice_service=_FakeInvoices(invoices), line_item_service=_FakeLineItems())
    draws = svc.all_draws_for_project(1, None)
    # The mirror (same date + cents-total as the coded draw) is dropped: one column only.
    assert [d["label"] for d in draws] == ["MR2-MAIN-05"]


def test_all_draws_merges_reissue_variants_into_one_column(monkeypatch):
    from entities.invoice.business.draw_financials import DrawFinancialsService

    invoices = [
        _Inv(1, "MR2-MAIN-04-2", "2025-12-31", Decimal("262459.51")),  # earlier, distinct amount
        _Inv(2, "MR2-MAIN-04", "2026-01-23", Decimal("119944.51")),    # later
    ]
    _patch_enrich(monkeypatch, {
        1: [{"source_type": "Manual", "billed_price": 1.0}],
        2: [{"source_type": "Manual", "billed_price": 1.0}],
    })
    _patch_qbo_seam(monkeypatch, {
        1: [("13", "Framing", Decimal("262459.51"))],
        2: [("13", "Framing", Decimal("100000.00")), ("25", "HVAC", Decimal("19944.51"))],
    })
    svc = DrawFinancialsService(invoice_service=_FakeInvoices(invoices), line_item_service=_FakeLineItems())
    draws = svc.all_draws_for_project(1, None)
    # 04 + 04-2 collapse to ONE column labeled with the base, latest date wins.
    assert [d["label"] for d in draws] == ["MR2-MAIN-04"]
    merged = draws[0]
    assert merged["date"] == "2026-01-23"
    assert merged["total"] == Decimal("382404.02")           # 262459.51 + 100000 + 19944.51
    cats = {c["cost_code_number"]: c["amount"] for c in merged["categories"]}
    assert cats["13"] == Decimal("362459.51")                # summed across both variants
    assert cats["25"] == Decimal("19944.51")


def test_all_draws_does_not_merge_distinct_numeric_tails(monkeypatch):
    from entities.invoice.business.draw_financials import DrawFinancialsService

    # Date-style numbers: base '128-2024' is NOT a draw, so these must stay separate.
    invoices = [
        _Inv(1, "128-2024-05", "2024-05-31", Decimal("1000.00")),
        _Inv(2, "128-2024-06", "2024-06-30", Decimal("2000.00")),
    ]
    _patch_enrich(monkeypatch, {
        1: [{"source_type": "Manual", "billed_price": 1.0}],
        2: [{"source_type": "Manual", "billed_price": 1.0}],
    })
    _patch_qbo_seam(monkeypatch, {
        1: [("13", "Framing", Decimal("1000.00"))],
        2: [("13", "Framing", Decimal("2000.00"))],
    })
    svc = DrawFinancialsService(invoice_service=_FakeInvoices(invoices), line_item_service=_FakeLineItems())
    draws = svc.all_draws_for_project(1, None)
    assert [d["label"] for d in draws] == ["128-2024-05", "128-2024-06"]  # NOT merged


def test_all_draws_qbo_derivation_skips_invoice_with_no_mapping(monkeypatch):
    from entities.invoice.business.draw_financials import DrawFinancialsService

    invoices = [_Inv(1, "MR2-MAIN-01", "2025-10-28")]  # all-Manual, but no QBO mapping
    _patch_enrich(monkeypatch, {1: [{"source_type": "Manual", "billed_price": 5.0}]})
    _patch_qbo_seam(monkeypatch, {})
    svc = DrawFinancialsService(invoice_service=_FakeInvoices(invoices), line_item_service=_FakeLineItems())
    assert svc.all_draws_for_project(1, None) == []


def test_all_draws_reuses_one_qbo_invoice_service_across_the_project(monkeypatch):
    """The entire point of U-292's bulk-index redesign: a fresh QboInvoiceService
    per manual invoice previously did a per-line DB round-trip burst that dropped
    a real SQL connection generating a real project's draw matrix. draw_financials
    must construct QboInvoiceService AT MOST ONCE per all_draws_for_project() call,
    not once per manual invoice, so its internal cost-code index amortizes."""
    from entities.invoice.business.draw_financials import DrawFinancialsService

    construct_count = {"n": 0}

    class _CountingFakeQboInvoiceService:
        def __init__(self):
            construct_count["n"] += 1

        def cost_coded_lines_for_invoice(self, invoice_id):
            return [("13", "Framing", Decimal("1.00"))]

    monkeypatch.setattr(
        "integrations.intuit.qbo.invoice.business.service.QboInvoiceService",
        _CountingFakeQboInvoiceService,
    )

    invoices = [
        _Inv(1, "MR2-MAIN-01", "2025-10-28", Decimal("1.00")),
        _Inv(2, "MR2-MAIN-02", "2025-11-28", Decimal("1.00")),
        _Inv(3, "MR2-MAIN-03", "2025-12-28", Decimal("1.00")),
    ]
    _patch_enrich(monkeypatch, {
        1: [{"source_type": "Manual", "billed_price": 1.0}],
        2: [{"source_type": "Manual", "billed_price": 1.0}],
        3: [{"source_type": "Manual", "billed_price": 1.0}],
    })
    svc = DrawFinancialsService(invoice_service=_FakeInvoices(invoices), line_item_service=_FakeLineItems())
    draws = svc.all_draws_for_project(1, None)

    assert len(draws) == 3  # all 3 manual invoices resolved via the seam
    assert construct_count["n"] == 1  # ONE QboInvoiceService for the whole project, not 3


# ─── U-503b: a coded draw's column must foot to its invoice total ─────────────
# The Trend rolled up a coded draw's CODED lines only and took the Builder's Fee
# from the project's Contract rate. A project with no Contract therefore produced
# fee=0, and the invoice's own fee (a Manual line the rollup excludes) vanished —
# the column under-reported the draw on a client-facing page. Live instance:
# TB-REPAIR-04 showed $9,681.82 against an $11,618.20 invoice, short by exactly
# the $1,936.38 fee; 31 draws across 14 projects were affected.
#
# The fix derives the fee from the persisted invoice ONLY when there is no rate,
# mirroring the Draw Request page's `_draw_fee_from_invoice`. The rate path is
# deliberately untouched: several real draws (HA-01, HA-02, BR-MAIN-20, OHR2-32,
# SSC2-03) carry NO fee line at all, and their computed rate fee is the only thing
# putting a fee on their G702/G703.


def test_coded_draw_without_rate_takes_fee_from_invoice_total(monkeypatch):
    """No Contract rate + a Manual fee line -> the column still foots to the invoice."""
    from entities.invoice.business.draw_financials import DrawFinancialsService

    _patch_enrich(monkeypatch, {
        1: [
            {"source_type": "BillLineItem", "cost_code_number": "06",
             "cost_code_name": "Grading", "billed_price": 1000.0},
            # The fee QBO carries as its own Manual line — excluded from the rollup.
            {"source_type": "Manual", "cost_code_number": "90",
             "cost_code_name": "Builder's Fee", "billed_price": 140.0},
        ],
    })
    svc = DrawFinancialsService(
        invoice_service=_FakeInvoices([_Inv(1, "HA-01", "2026-02-27", Decimal("1140.00"))]),
        line_item_service=_FakeLineItems(),
    )
    draw = svc.coded_draws_for_project(128, None)[0]

    assert draw["subtotal"] == Decimal("1000.00")
    assert draw["builders_fee"] == Decimal("140.00")   # 1140.00 - 1000.00
    assert draw["total"] == Decimal("1140.00")         # == the invoice total
    assert draw["total"] == draw["subtotal"] + draw["builders_fee"]


def test_coded_draw_with_rate_ignores_trivial_invoice_residue(monkeypatch):
    """Regression guard (BR-MAIN-21): a $0.02 rounding residue on the invoice must
    NOT displace a real Contract-rate fee. Its live numbers are subtotal
    $792,999.34 against an invoice total of $792,999.36 — an 'invoice fee wins when
    positive' rule would have replaced a $95,159.92 fee with two cents."""
    from entities.invoice.business.draw_financials import DrawFinancialsService

    _patch_enrich(monkeypatch, {
        1: [{"source_type": "BillLineItem", "cost_code_number": "06",
             "cost_code_name": "Grading", "billed_price": 1000.0}],
    })
    svc = DrawFinancialsService(
        invoice_service=_FakeInvoices([_Inv(1, "BR-MAIN-21", "2026-02-27", Decimal("1000.02"))]),
        line_item_service=_FakeLineItems(),
    )
    draw = svc.coded_draws_for_project(64, Decimal("0.12"))[0]

    assert draw["builders_fee"] == Decimal("120.00")   # rate fee, NOT the $0.02
    assert draw["total"] == Decimal("1120.00")


def test_coded_draw_without_rate_or_invoice_total_keeps_zero_fee(monkeypatch):
    """No rate and no persisted total -> nothing to derive from; fee stays 0."""
    from entities.invoice.business.draw_financials import DrawFinancialsService

    _patch_enrich(monkeypatch, {
        1: [{"source_type": "BillLineItem", "cost_code_number": "06",
             "cost_code_name": "Grading", "billed_price": 1000.0}],
    })
    svc = DrawFinancialsService(
        invoice_service=_FakeInvoices([_Inv(1, "HA-01", "2026-02-27", None)]),
        line_item_service=_FakeLineItems(),
    )
    draw = svc.coded_draws_for_project(128, None)[0]

    assert draw["builders_fee"] == Decimal("0")
    assert draw["total"] == Decimal("1000.00")


def test_coded_draw_without_rate_never_produces_a_negative_fee(monkeypatch):
    """A credit-heavy draw whose invoice total sits BELOW the coded subtotal must not
    render a negative Builder's Fee row."""
    from entities.invoice.business.draw_financials import DrawFinancialsService

    _patch_enrich(monkeypatch, {
        1: [{"source_type": "BillLineItem", "cost_code_number": "06",
             "cost_code_name": "Grading", "billed_price": 1000.0}],
    })
    svc = DrawFinancialsService(
        invoice_service=_FakeInvoices([_Inv(1, "HA-01", "2026-02-27", Decimal("900.00"))]),
        line_item_service=_FakeLineItems(),
    )
    draw = svc.coded_draws_for_project(128, None)[0]

    assert draw["builders_fee"] == Decimal("0")
    assert draw["total"] == Decimal("1000.00")


def test_all_draws_coded_column_also_foots_without_rate(monkeypatch):
    """The Trend consumes all_draws_for_project — its coded columns inherit the fix."""
    from entities.invoice.business.draw_financials import DrawFinancialsService

    _patch_enrich(monkeypatch, {
        1: [
            {"source_type": "BillLineItem", "cost_code_number": "06",
             "cost_code_name": "Grading", "billed_price": 1000.0},
            {"source_type": "Manual", "cost_code_number": "90",
             "cost_code_name": "Builder's Fee", "billed_price": 140.0},
        ],
    })
    svc = DrawFinancialsService(
        invoice_service=_FakeInvoices([_Inv(1, "HA-01", "2026-02-27", Decimal("1140.00"))]),
        line_item_service=_FakeLineItems(),
    )
    draw = svc.all_draws_for_project(128, None)[0]

    assert draw["builders_fee"] == Decimal("140.00")
    assert draw["total"] == Decimal("1140.00")


# ─── U-503b hardening: the derived fee must be PLAUSIBLY a fee ────────────────
# Review found that `fee = invoice.total - subtotal` is unbounded, and the
# canonical-draw rule admits an invoice with as few as ONE source-linked line.
# QBO-pulled invoices arrive all-Manual and are back-matched line by line, so a
# half-linked draw would sweep its whole uncoded remainder into a row labelled
# "Builder's Fee" — turning an obviously-broken $0 into an authoritative-looking
# six-figure number on the Trend and on the G702/G703 the owner signs.
#
# The bound is principled rather than a magic threshold: a Builder's Fee is a
# markup ON the coded work, so a residual exceeding the entire coded subtotal is
# definitionally misattribution, not a fee. That one rule also covers a rollup
# whose categories net to zero.


def test_coded_draw_rejects_residual_larger_than_the_coded_subtotal(monkeypatch):
    """Half-linked draw: $5k coded against a $200k invoice must NOT render a
    $195k Builder's Fee."""
    from entities.invoice.business.draw_financials import DrawFinancialsService

    _patch_enrich(monkeypatch, {
        1: [{"source_type": "BillLineItem", "cost_code_number": "06",
             "cost_code_name": "Grading", "billed_price": 5000.0}],
    })
    svc = DrawFinancialsService(
        invoice_service=_FakeInvoices([_Inv(1, "HA-01", "2026-02-27", Decimal("200000.00"))]),
        line_item_service=_FakeLineItems(),
    )
    draw = svc.coded_draws_for_project(128, None)[0]

    assert draw["builders_fee"] == Decimal("0")
    assert draw["total"] == Decimal("5000.00")


def test_coded_draw_rejects_fee_when_coded_subtotal_nets_to_zero(monkeypatch):
    """A bill line and a matching credit collapse to one $0 category; the whole
    invoice must not become the fee."""
    from entities.invoice.business.draw_financials import DrawFinancialsService

    _patch_enrich(monkeypatch, {
        1: [
            {"source_type": "BillLineItem", "cost_code_number": "06",
             "cost_code_name": "Grading", "billed_price": 1000.0},
            # _signed_line_amount negates a positive BillCreditLineItem.
            {"source_type": "BillCreditLineItem", "cost_code_number": "06",
             "cost_code_name": "Grading", "billed_price": 1000.0},
        ],
    })
    svc = DrawFinancialsService(
        invoice_service=_FakeInvoices([_Inv(1, "HA-01", "2026-02-27", Decimal("50000.00"))]),
        line_item_service=_FakeLineItems(),
    )
    draw = svc.coded_draws_for_project(128, None)[0]

    assert draw["subtotal"] == Decimal("0.00")
    assert draw["builders_fee"] == Decimal("0")
    assert draw["total"] == Decimal("0.00")


def test_zero_contract_rate_is_treated_as_no_rate(monkeypatch):
    """`_resolve_builders_fee_rate` filters on `is not None`, so a Contract row
    carrying 0.000000 yields Decimal('0'). That must take the derived-fee path,
    not silently re-open the bug this unit closed."""
    from entities.invoice.business.draw_financials import DrawFinancialsService

    _patch_enrich(monkeypatch, {
        1: [{"source_type": "BillLineItem", "cost_code_number": "06",
             "cost_code_name": "Grading", "billed_price": 1000.0}],
    })
    svc = DrawFinancialsService(
        invoice_service=_FakeInvoices([_Inv(1, "HA-01", "2026-02-27", Decimal("1140.00"))]),
        line_item_service=_FakeLineItems(),
    )
    draw = svc.coded_draws_for_project(128, Decimal("0"))[0]

    assert draw["builders_fee"] == Decimal("140.00")
    assert draw["total"] == Decimal("1140.00")
