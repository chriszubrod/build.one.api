"""Pure helpers for invoice draw delta / untag planning (no I/O)."""


def _normalize_source_pid_set(current_source_pids) -> set[str]:
    # strip+lower, identical to _z_key, so the membership test in
    # partition_removal_candidates compares both sides the same way.
    return {
        str(pid).strip().lower()
        for pid in (current_source_pids or [])
        if pid is not None and str(pid).strip() != ""
    }


def _z_key(z) -> str | None:
    if z is None:
        return None
    s = str(z).strip()
    if not s:
        return None
    return s.lower()


def partition_removal_candidates(already_tagged, current_source_pids):
    """Split Direction-B tagged rows into confident vs ambiguous removal candidates.

    Confident: non-empty col-Z public_id that is no longer on the invoice.
    Ambiguous: empty/missing col-Z (human review only).
    Rows whose col-Z still matches a current invoice source line are omitted.
    """
    linked = _normalize_source_pid_set(current_source_pids)
    confident: list[dict] = []
    ambiguous: list[dict] = []

    for entry in already_tagged or []:
        key = _z_key(entry.get("z"))
        if key is None:
            ambiguous.append(entry)
            continue
        if key in linked:
            continue
        confident.append(entry)

    return {"confident": confident, "ambiguous": ambiguous}


def build_untag_plan(confident):
    """De-duplicated, lowercased Box/SharePoint col-Z keys to clear (first-seen
    order). Both surfaces un-tag strictly by col-Z key — never by worksheet row
    index (row numbers are re-resolved fresh at write time), so the plan carries
    only keys."""
    box_keys: list[str] = []
    seen_keys: set[str] = set()

    for entry in confident or []:
        key = _z_key(entry.get("z"))
        if key is not None and key not in seen_keys:
            seen_keys.add(key)
            box_keys.append(key)

    return {"box_keys": box_keys}


class InvoiceDrawDeltaService:
    """
    U7 delta / removal path — detect and clear stale DRAW REQUEST tags.

    After a draw packet is pushed, a source line can be unlinked from the invoice.
    The DETAILS/Box stamp is insert/upsert-only, so the row stays tagged for a
    draw it no longer belongs to and the worksheet over-reports that draw. This
    service surfaces those stale rows (reconcile's `already_tagged` bucket, split
    CONFIDENT vs AMBIGUOUS) and — on an explicit, gated apply — blanks column H by
    col-Z key on BOTH surfaces (SharePoint inline + Box enqueued).

    propose_removals is read-only; apply_removals mutates and is double-gated on
    ALLOW_MS_WRITES + ALLOW_BOX_WRITES. Only CONFIDENT (col-Z-keyed) rows are ever
    cleared — ambiguous rows (blank col-Z) are surfaced for a human, never touched.
    """

    def __init__(self, *, reconcile_service=None, invoice_service=None):
        self._reconcile_service = reconcile_service
        self._invoice_service = invoice_service

    def _reconcile(self):
        if self._reconcile_service is None:
            from entities.invoice.business.worksheet_reconcile import (
                WorksheetReconcileService,
            )
            self._reconcile_service = WorksheetReconcileService()
        return self._reconcile_service

    def _invoice(self):
        if self._invoice_service is None:
            from entities.invoice.business.service import InvoiceService
            self._invoice_service = InvoiceService()
        return self._invoice_service

    def _current_source_pids(self, invoice) -> set:
        """The invoice's currently-linked source-line public_ids (lowercased).

        Needed so the partition drops a still-linked row that lands in
        `already_tagged` via reconcile's duplicate-tag branch (col-Z already
        matched elsewhere) — such a row must NOT be cleared.
        """
        from entities.invoice_line_item.business.service import InvoiceLineItemService
        from entities.invoice.business.enrichment import enrich_line_items

        line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)
        enriched = enrich_line_items(line_items)
        return {
            str(r.get("source_line_public_id")).strip().lower()
            for r in enriched
            if r.get("source_line_public_id")
        }

    def propose_removals(self, invoice_public_id: str) -> dict:
        """Read-only: the stale-tag removal candidates for this draw, split
        confident (col-Z-keyed, safe to clear) vs ambiguous (blank col-Z)."""
        invoice = self._invoice().read_by_public_id(public_id=invoice_public_id)
        if not invoice:
            raise ValueError("invoice_not_found")
        reconcile_result = self._reconcile().reconcile(invoice_public_id)
        already_tagged = reconcile_result.get("already_tagged") or []
        current = self._current_source_pids(invoice)
        parts = partition_removal_candidates(already_tagged, current)
        return {
            "invoice_public_id": invoice_public_id,
            "invoice_number": invoice.invoice_number,
            "confident": parts["confident"],
            "ambiguous": parts["ambiguous"],
            "confident_count": len(parts["confident"]),
            "ambiguous_count": len(parts["ambiguous"]),
            "untag_plan": build_untag_plan(parts["confident"]),
        }

    def apply_removals(self, invoice_public_id: str, *, force: bool = False) -> dict:
        """Gated mutate: blank column H for the CONFIDENT removals on both
        surfaces. SharePoint clears inline; Box is enqueued (drains ~5-60s), so
        re-run box-draw-verify AFTER the Box drain to confirm parity — an
        immediate verify would show an expected transient SP-cleared/Box-not-yet
        mismatch. Ambiguous rows are never touched.
        """
        from shared.env_flags import env_flag_enabled

        ms_ok = env_flag_enabled("ALLOW_MS_WRITES")
        box_ok = env_flag_enabled("ALLOW_BOX_WRITES")
        if not (ms_ok and box_ok):
            return {
                "status": "halt",
                "reason": "writes_disabled",
                "ms_writes": ms_ok,
                "box_writes": box_ok,
            }

        proposal = self.propose_removals(invoice_public_id)
        confident = proposal["confident"]
        box_keys = proposal["untag_plan"]["box_keys"]
        if not confident:
            return {
                "status": "noop",
                "reason": "no_confident_removals",
                "invoice_public_id": invoice_public_id,
                "ambiguous_count": proposal["ambiguous_count"],
            }

        invoice = self._invoice().read_by_public_id(public_id=invoice_public_id)
        project_id = invoice.project_id

        # Box FIRST — a durable outbox enqueue is cheap and the more likely thing
        # to fail fast (DB insert). Halting here on a real error leaves SharePoint
        # untouched, so we never end up with SP cleared and Box silently un-cleared.
        # `unmapped` is NOT an error — the project simply has no Box surface, so we
        # proceed with a SharePoint-only clear (mirrors the push local-only branch).
        box = self._invoice()._enqueue_box_excel_clear(invoice, project_id, box_keys)
        if box.get("reason") == "error":
            return {
                "status": "halt",
                "reason": "box_enqueue_failed",
                "invoice_public_id": invoice_public_id,
                "confident_count": len(confident),
                "box": box,
                "note": "SharePoint left untouched — no surface was mutated.",
            }

        # SharePoint (inline) — keyed by col-Z, last-wins (exact inverse of the
        # stamp); never trusts a row index.
        ms_result = self._invoice().unstamp_draw_from_excel(project_id, box_keys)

        return {
            "status": "applied",
            "invoice_public_id": invoice_public_id,
            "invoice_number": proposal["invoice_number"],
            "cleared_source_public_ids": box_keys,
            "confident_count": len(confident),
            "ambiguous_count": proposal["ambiguous_count"],
            "ms_result": ms_result,
            "box_enqueued": box.get("enqueued", False),
            "box_reason": box.get("reason"),
            "note": (
                "SharePoint DRAW REQUEST cleared inline; Box clear "
                + ("enqueued (drains ~5-60s)" if box.get("enqueued") else f"not enqueued ({box.get('reason')})")
                + ". Re-run box-draw-verify after the Box drain to confirm SP/Box parity."
            ),
        }
