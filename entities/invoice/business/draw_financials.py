"""Draw-financials assembly for the invoice draw packet (U-206).

Selects a project's CODED draws — invoices whose source-linked cost-code rollup is
non-empty — and computes each draw's Builder's Fee from the Contract rate. This is
the shared data layer the multi-draw packet pages consume (Trend now; G702/G703
later), so their numbers reconcile by construction.

Canonical-draw rule: an invoice counts as a draw only if its non-Manual rollup has
cost codes. That excludes (a) QBO Manual-only duplicate invoices — re-entries of a
draw's work with no source documents — and (b) uncoded invoices (work not started
locally yet). Both would otherwise double-count or mis-report the historical draws.
Fee is computed (subtotal x rate), not read from the invoice, so a draw whose fee
was never entered on its coded invoice still reconciles. When the project has no
usable Contract rate (absent, or 0) there is nothing to compute from, and the fee is
instead derived from the persisted invoice (`_fee_from_invoice`, U-503b) so the
column foots to the invoice total — without that fallback a rate-less project
dropped the invoice's own fee entirely and under-reported the draw on a
client-facing page. That derivation is bounded and declines rather than guessing, so
a column can still legitimately fail to foot; `_fee_from_invoice` logs when it does.

`all_draws_for_project` (U-271) additionally surfaces EARLY/historical draws whose
cost codes live only in QBO — invoices migrated in as all-`Manual` lines (no local
source FKs), rolled up via `QboInvoiceService.cost_coded_lines_for_invoice` (U-292),
the dbo-native seam that resolves each QBO invoice line's cost code by ID (QboItem ->
ItemSubCostCode -> SubCostCode -> CostCode) rather than parsing the QBO Item's display
name. It is used by the Trend so every historical pay application is a column;
G702/G703 keep consuming `coded_draws_for_project` (coded draws only).
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from decimal import Decimal
from typing import Optional

from entities.invoice.business.packet_render import as_decimal as _as_decimal

logger = logging.getLogger(__name__)

_MAX_PROJECT_INVOICES = 500


def _group_into_categories(items) -> tuple:
    """Fold ``(cost_code_number, cost_code_name, amount)`` triples into the sorted
    category dicts the draw renderers consume + their subtotal. Grouped by number
    (first non-empty name wins), ordered by cost-code number, amounts summed. Returns
    ``(categories, subtotal)``. One home for the accumulate→sort→sum shape both the
    QBO-derived rollup and the re-issue merge need."""
    from entities.invoice.business.cover import _cost_code_sort_key

    groups: dict = {}
    for number, name, amount in items:
        cell = groups.get(number)
        if cell is None:
            groups[number] = {"cost_code_number": number,
                              "cost_code_name": name or "",
                              "amount": _as_decimal(amount)}
        else:
            cell["amount"] += _as_decimal(amount)
            if not cell["cost_code_name"] and name:
                cell["cost_code_name"] = name
    categories = sorted(groups.values(),
                        key=lambda c: _cost_code_sort_key(c["cost_code_number"]))
    subtotal = sum((c["amount"] for c in categories), Decimal("0"))
    return categories, subtotal


def _reissue_base_label(label: str) -> str:
    """Base draw number shared by a re-issued draw and its original: a label ending in
    TWO ``-N`` segments collapses to the first ('MR2-MAIN-04-2' -> 'MR2-MAIN-04'). A
    single trailing '-N' (the draw number itself, 'MR2-MAIN-09') is left untouched."""
    return re.sub(r"(-\d+)-\d+$", r"\1", label or "")


class DrawFinancialsService:
    def __init__(self, invoice_service=None, line_item_service=None, qbo_invoice_service=None):
        self._invoice_service = invoice_service
        self._line_item_service = line_item_service
        self._qbo_invoice_svc = qbo_invoice_service

    def _invoices(self):
        if self._invoice_service is None:
            from entities.invoice.business.service import InvoiceService
            self._invoice_service = InvoiceService()
        return self._invoice_service

    def _line_items(self):
        if self._line_item_service is None:
            from entities.invoice_line_item.business.service import InvoiceLineItemService
            self._line_item_service = InvoiceLineItemService()
        return self._line_item_service

    def _qbo_invoice_service(self):
        # Reused across every invoice in a coded_draws_for_project/all_draws_for_project
        # call so QboInvoiceService's own internal cost-code cache amortizes across the
        # whole project instead of resolving the same recurring QBO item from scratch
        # per invoice (U-292 — the DB round-trip count that surfaced during equivalence
        # testing against a real, many-invoice project).
        if self._qbo_invoice_svc is None:
            from integrations.intuit.qbo.invoice.business.service import QboInvoiceService
            self._qbo_invoice_svc = QboInvoiceService()
        return self._qbo_invoice_svc

    def _project_invoices(self, project_id: int):
        invoices = self._invoices().read_paginated(
            page_number=1,
            page_size=_MAX_PROJECT_INVOICES,
            project_id=project_id,
            sort_by="InvoiceDate",
            sort_direction="ASC",
        )
        if len(invoices) >= _MAX_PROJECT_INVOICES:
            # Don't silently truncate a client-facing draw matrix — flag it. A
            # project this large needs pagination or a project-scoped sproc (TODO).
            logger.warning(
                "draw_financials: project %s hit the %s-invoice page cap; the draw "
                "matrix may be truncated (paginate / use a sproc)",
                project_id, _MAX_PROJECT_INVOICES,
            )
        return invoices

    def _coded_rollup(self, invoice, fee_rate):
        """The invoice's source-linked (non-Manual) cost-code rollup, or None when it
        has no coded lines (a Manual-only / uncoded invoice). With no Contract rate
        the fee comes from the persisted invoice instead — see `_fee_from_invoice`."""
        from entities.invoice.business.enrichment import enrich_line_items
        from entities.invoice.business.cover import build_cover_rollup

        line_items = self._line_items().read_by_invoice_id(invoice_id=invoice.id)
        toc = [r for r in enrich_line_items(line_items) if r.get("source_type") != "Manual"]
        rollup = build_cover_rollup(toc, fee_rate)
        if not rollup.categories:
            return None
        # A rate of exactly 0 is "no usable rate", not a deliberate 0% fee:
        # `_resolve_builders_fee_rate` selects on `is not None`, so a Contract row
        # carrying 0.000000 — or a transient lookup failure degrading to no-fee —
        # would otherwise take the rate path and re-open the bug below.
        if not fee_rate:
            return self._fee_from_invoice(rollup, invoice)
        return rollup

    @staticmethod
    def _fee_from_invoice(rollup, invoice):
        """Builder's Fee for a coded draw on a project with NO Contract rate (U-503b).

        The fee is a Manual line the cost-code rollup excludes, so `fee = invoice
        total - subtotal` makes the column foot to the invoice total by construction.
        Same derivation the Draw Request page uses (`_draw_fee_from_invoice`), so on
        the rate-less path pages 1 and 2 of a packet report one number. NB that is
        NOT a packet-wide guarantee: the router applies its copy unconditionally
        while this runs only without a rate, so a rated draw carrying a residue can
        still show two figures (BR-MAIN-21: $0.02 on page 1, $95,159.92 on page 2).
        Reconciling that is the router's side to fix.

        Before this, a rate-less project rolled up fee=0 and the invoice's own fee
        simply vanished from the Trend — TB-REPAIR-04 showed $9,681.82 against an
        $11,618.20 invoice (31 draws / 14 projects).

        Applies ONLY when there is no usable rate; the rate path is deliberately
        untouched. Two live shapes make "invoice fee wins" unsafe as a general rule:
          * a draw whose fee was never entered on its coded invoice (HA-01, HA-02,
            BR-MAIN-20, OHR2-32, SSC2-03 — invoice total == subtotal) depends on the
            computed rate fee for its G702/G703 numbers, per this module's
            fee-is-computed-not-read contract; and
          * a trivial rounding residue must never displace a real fee — BR-MAIN-21
            bills $0.02 over its subtotal against a $95,159.92 rate fee.

        NOTE (inherited from the router original, and this is the layer it deferred
        to): the residual folds in EVERY non-source line, so a manual retainage,
        discount or GC adjustment lands in a row labelled "Builder's Fee". Refining
        that attribution — identifying the fee line by its cost code rather than by
        subtraction — is still open. The bound below keeps the damage visible rather
        than authoritative-looking, but it does not identify the fee.

        Declines (leaving fee 0, so the column stays visibly short) when the residual
        is <= 0 — a credit-heavy draw billed below its coded subtotal — or when it
        EXCEEDS the coded subtotal. A Builder's Fee is a markup ON the coded work, so
        a residual bigger than all of that work is misattribution, not a fee: the
        signature of a part-linked draw (QBO pulls land all-`Manual` and are
        back-matched line by line, so a $200k invoice with one $5k line matched would
        otherwise print a $195k fee) or of a rollup whose categories net to zero.
        """
        total = _as_decimal(invoice.total_amount)
        fee = total - rollup.subtotal
        if fee <= 0 or fee > rollup.subtotal:
            if total and total != rollup.total:
                logger.warning(
                    "draw_financials: invoice %s total %s does not reconcile to its "
                    "coded rollup (subtotal %s, residual %s) — fee left at 0, so this "
                    "draw's column will not foot to the invoice",
                    getattr(invoice, "invoice_number", None) or getattr(invoice, "id", None),
                    total, rollup.subtotal, fee,
                )
            return rollup
        return replace(rollup, builders_fee=fee, total=total)

    @staticmethod
    def _draw_from_rollup(invoice, rollup) -> dict:
        """The canonical draw dict the Trend/G703 renderers consume."""
        return {
            "label": invoice.invoice_number or "",
            "date": str(invoice.invoice_date or "")[:10],
            "categories": [
                {
                    "cost_code_number": c.cost_code_number,
                    "cost_code_name": c.cost_code_name,
                    "amount": c.amount,
                }
                for c in rollup.categories
            ],
            "subtotal": rollup.subtotal,
            "builders_fee": rollup.builders_fee,
            "total": rollup.total,
        }

    def coded_draws_for_project(self, project_id: int, fee_rate=None) -> list[dict]:
        """Ordered (by invoice date, then number) list of the project's coded draws.

        Each entry: ``{label, date, categories:[{cost_code_number, cost_code_name,
        amount}], subtotal, builders_fee, total}`` — the exact shape the Trend (and
        later G703/Trend) renderers consume. ``builders_fee = subtotal * fee_rate``
        (0 when ``fee_rate`` is None). Invoices whose coded rollup is empty are
        skipped (the canonical-draw rule).
        """
        if not project_id:
            return []
        draws: list[dict] = []
        for inv in self._project_invoices(project_id):
            rollup = self._coded_rollup(inv, fee_rate)
            if rollup is None:
                # Manual-only duplicate or uncoded invoice — not a coded draw. Excluded.
                continue
            draws.append(self._draw_from_rollup(inv, rollup))
        # read_paginated sorts by date server-side; make the order deterministic on
        # same-date ties (the coded draws have distinct dates, but don't rely on it).
        draws.sort(key=lambda d: (d["date"], d["label"]))
        return draws

    def all_draws_for_project(self, project_id: int, fee_rate=None) -> list[dict]:
        """Every historical pay application as a draw column — the Trend's data source.

        Superset of ``coded_draws_for_project``: a coded draw is taken as-is; an
        all-Manual (early/migrated) invoice is instead rolled up from its QBO invoice
        lines via the dbo-native cost-code seam, UNLESS its (date, total) matches
        an already-selected coded draw — that is a QBO-pull mirror duplicate (e.g.
        MR2-MAIN-05-2 mirrors the coded MR2-MAIN-05) and is dropped. Finally, same-draw
        re-issues (MR2-MAIN-04 + MR2-MAIN-04-2) merge into one column.

        Same dict shape as ``coded_draws_for_project`` so the Trend renderer is
        agnostic to a column's source.
        """
        if not project_id:
            return []

        # First pass takes every coded draw (and records its identity); manual/uncoded
        # invoices are held for the QBO-derived pass so a mirror can be tested against
        # ALL coded keys before it is admitted.
        draws: list[dict] = []
        seen: set = set()          # (date, billed total) of every draw already taken
        manual_invoices: list = []
        for inv in self._project_invoices(project_id):
            rollup = self._coded_rollup(inv, fee_rate)
            if rollup is not None:
                draws.append(self._draw_from_rollup(inv, rollup))
                seen.add(self._invoice_key(inv))
            else:
                manual_invoices.append(inv)

        # A QBO-pull mirror of an already-counted draw carries the same (date, billed
        # total). Key on the INVOICE's own total_amount on BOTH sides — never the coded
        # draw's computed `total` (subtotal + rate*subtotal), which diverges from the
        # billed amount whenever a fee rate is set and would let a mirror slip through.
        for inv in manual_invoices:
            key = self._invoice_key(inv)
            if key in seen:
                continue  # mirror of a coded (or earlier manual) draw — skip
            derived = self._qbo_derived_draw(inv)
            if derived is None:
                continue
            seen.add(key)
            draws.append(derived)

        draws = self._merge_reissue_draws(draws)
        draws.sort(key=lambda d: (d["date"], d["label"]))
        return draws

    @staticmethod
    def _invoice_key(invoice) -> tuple:
        """(date, cents-rounded billed total) — the source-agnostic identity used to
        drop a QBO-pull mirror of a draw already counted from another source."""
        return (
            str(invoice.invoice_date or "")[:10],
            _as_decimal(invoice.total_amount).quantize(Decimal("0.01")),
        )

    def _qbo_derived_draw(self, invoice) -> Optional[dict]:
        """Roll up an all-Manual invoice from its QBO-mapped lines via the dbo-native
        cost-code seam (``QboInvoiceService.cost_coded_lines_for_invoice``, U-292) —
        each line's cost code is resolved by ID (QboItem -> ItemSubCostCode ->
        SubCostCode -> CostCode), not by parsing the QBO Item's display name; lines
        with no resolvable cost code (e.g. split markup lines, which carry no Item)
        fall into a single 'Uncoded' row so the column foots to the invoice total.
        Returns None when the invoice has no QBO mapping/lines.

        The historical draw's billed total already embeds any fee, so ``builders_fee``
        is 0 and ``subtotal == total`` (== sum of the QBO lines == the invoice total).
        """
        items = self._qbo_invoice_service().cost_coded_lines_for_invoice(invoice.id)
        if not items:
            return None
        categories, subtotal = _group_into_categories(items)
        return {
            "label": invoice.invoice_number or "",
            "date": str(invoice.invoice_date or "")[:10],
            "categories": categories,
            "subtotal": subtotal,
            "builders_fee": Decimal("0"),
            "total": subtotal,
        }

    def _merge_reissue_draws(self, draws: list[dict]) -> list[dict]:
        """Combine same-draw-number re-issues (MR2-MAIN-04 + MR2-MAIN-04-2) into one
        column. A ``-N-M`` label collapses to its base ``-N`` ONLY when that base is
        itself a draw in this set, so genuinely distinct numbers that merely carry a
        numeric tail (e.g. a ``128-2024-05`` date-style number, whose ``128-2024`` base
        is absent) are never merged. Cost-code cells sum; the merged **subtotal is
        recomputed from the merged cells** so a fee-exclusive coded column and a
        fee-inclusive QBO column combine coherently; total = subtotal + fee; the latest
        date wins for ordering. A draw with a unique key passes through unchanged
        (its recomputed subtotal equals its original)."""
        from collections import OrderedDict

        labels = {d["label"] for d in draws}

        def merge_key(label: str) -> str:
            base = _reissue_base_label(label)
            return base if (base != label and base in labels) else label

        merged: "OrderedDict[str, dict]" = OrderedDict()
        for d in draws:
            key = merge_key(d["label"])
            m = merged.get(key)
            if m is None:
                m = {"label": key, "date": d["date"], "items": [],
                     "builders_fee": Decimal("0")}
                merged[key] = m
            if d["date"] > m["date"]:
                m["date"] = d["date"]
            m["items"].extend(
                (c["cost_code_number"], c["cost_code_name"], c["amount"])
                for c in d["categories"]
            )
            m["builders_fee"] += _as_decimal(d["builders_fee"])

        out: list[dict] = []
        for m in merged.values():
            categories, subtotal = _group_into_categories(m["items"])
            out.append({"label": m["label"], "date": m["date"], "categories": categories,
                        "subtotal": subtotal, "builders_fee": m["builders_fee"],
                        "total": subtotal + m["builders_fee"]})
        return out
