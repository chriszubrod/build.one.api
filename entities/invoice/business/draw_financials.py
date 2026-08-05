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
was never entered on its coded invoice still reconciles.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_MAX_PROJECT_INVOICES = 500


class DrawFinancialsService:
    def __init__(self, invoice_service=None, line_item_service=None):
        self._invoice_service = invoice_service
        self._line_item_service = line_item_service

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
        from entities.invoice.business.enrichment import enrich_line_items
        from entities.invoice.business.cover import build_cover_rollup

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
        draws: list[dict] = []
        for inv in invoices:
            line_items = self._line_items().read_by_invoice_id(invoice_id=inv.id)
            toc = [r for r in enrich_line_items(line_items) if r.get("source_type") != "Manual"]
            rollup = build_cover_rollup(toc, fee_rate)
            if not rollup.categories:
                # Manual-only duplicate or uncoded invoice — not a draw. Excluded.
                continue
            draws.append({
                "label": inv.invoice_number or "",
                "date": str(inv.invoice_date or "")[:10],
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
            })
        # read_paginated sorts by date server-side; make the order deterministic on
        # same-date ties (the coded draws have distinct dates, but don't rely on it).
        draws.sort(key=lambda d: (d["date"], d["label"]))
        return draws
