# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.invoice.business.model import QboInvoiceLine
from entities.invoice_line_item.business.service import InvoiceLineItemService
from entities.invoice_line_item.business.model import InvoiceLineItem
from entities.invoice.business.service import InvoiceService
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.base.identity_fastpath import (
    run_line_identity_fastpath_dbo_only,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.line_orphan_adopt import find_stale_identity_orphan
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

logger = logging.getLogger(__name__)

# U-362c: sentinel for `find_stale_identity_orphan`'s `position_key` when a
# sibling carries no provenance LineNum — sorts after every real LineNum
# (which QBO always assigns as a small positive int) without mixing float
# and int in the same sort-key tuple.
_NO_LINE_NUM_RANK = 2**31


def _stamp_source_provenance_or_warn(repo, *, qbo_invoice_line: QboInvoiceLine, invoice_line_item_id: int, context: str) -> None:
    """Best-effort dbo source-link provenance mirror (U-272). By every call
    site the line item is already committed, so a stamp failure must never
    abort or roll back otherwise-successful sync work — log and move on, same
    non-blocking shape as the identity stamp itself. NOTE the blast radius
    differs from that precedent: nothing reads InvoiceLineItem.QboId/RealmId
    directly for billing, but ProposeInvoiceSourceLinks/ReadInvoiceSourceLinkLines
    read THIS table live — a swallowed failure here silently drops that one
    line from source-link proposals until it self-heals on a future QBO pull
    of the same invoice (not guaranteed to be soon; the pull is
    watermark-incremental). U-362c added a SECOND, higher-stakes consumer:
    `read_by_linked_txn`'s sibling-set recognition (`_recognize_source_
    linked_line`) INNER JOINs this table, so a line with no provenance row at
    all is invisible to that recognizer too — if this stamp never lands for a
    line AND that line's QBO `Line.Id` later regenerates, its next pull can
    mint a phantom Manual duplicate instead of self-healing (booked in
    TODO.md "U-362c follow-ups", not fixed in this unit: narrow, pre-existing
    since U-362b, and closing it fully means deciding what a sibling with no
    fingerprint data should even match against)."""
    try:
        repo.set_source_provenance(
            invoice_line_item_id=invoice_line_item_id,
            line_num=qbo_invoice_line.line_num,
            qbo_amount=qbo_invoice_line.amount,
            qbo_description=qbo_invoice_line.description,
            service_date=qbo_invoice_line.service_date,
            linked_txn_type=qbo_invoice_line.linked_txn_type,
            linked_txn_id=qbo_invoice_line.linked_txn_id,
            item_ref_value=qbo_invoice_line.item_ref_value,
        )
    except Exception as stamp_err:
        logger.warning(f"{context} but could not stamp dbo source provenance: {stamp_err}")


class InvoiceLineItemConnector:
    """
    Connector service for synchronization between QboInvoiceLine and InvoiceLineItem modules.

    U-362: dbo.InvoiceLineItem.QboId/RealmId (U-238b), scoped to this line's
    own parent Invoice (U-293b), is the SOLE identity store — the
    qbo.InvoiceLineItemInvoiceLine mapping table is retired (U-349 program
    family 9/11, cloning U-361/U-361b's shared run_line_identity_fastpath_
    dbo_only primitive; see that helper's own docstring for the create lock,
    the create-only MISS, and the stamp-rollback guarantee this connector
    relies on rather than re-implements).

      * HIT — a dbo row already carries `(invoice_id, qbo_line_id)`: write the
        QBO-derived fields onto it in place. No identity re-stamp (the row was
        found BY that identity; re-stamping every touch was the mapping era's
        dual-write), except a one-off realm self-heal for a legacy row stamped
        with a QboId but no RealmId.
      * MISS — create the line, then stamp identity with the bare
        `set_qbo_identity`. An unstamped line has no mapping row left to make
        it findable on the next pull (it would be re-created as a duplicate
        every pull), so a stamp that raises or does not land is rolled back
        by the helper and re-raised.
      * Re-adopt before create (U-361b's shared matcher). QBO regenerates a
        line's `Line.Id` on certain edits with its content unchanged; a
        genuine MISS first looks for a local **Manual**-sourced line under
        this parent whose CURRENT identity is no longer in
        `live_qbo_line_ids` (this pull's live line-id set) AND whose content
        fingerprint (description, amount) matches the incoming line — a
        "stale-identity orphan" — and re-stamps THAT row (reusing its
        dbo.Id, its attachments, any completion/draw references) instead of
        minting a sibling. Only Manual lines are candidates: Bill/Expense/
        BillCreditLineItem-sourced lines are matched via their source FK
        elsewhere, and adopting one by content fingerprint would steal it
        from its true source (mirrors the pre-U-362 Shape-B matcher's own
        Manual-only restriction, task #17).

    Raises on any projection failure; the parent's per-line try/except
    (`InvoiceInvoiceConnector._sync_line_items`) logs and retries the line on
    the next pull tick rather than partial-committing the invoice.
    """

    def __init__(
        self,
        invoice_line_item_service: Optional[InvoiceLineItemService] = None,
        invoice_service: Optional[InvoiceService] = None,
        line_item_cache: Optional[dict] = None,
        caches_preloaded: bool = False,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the InvoiceLineItemConnector."""
        self.invoice_line_item_service = invoice_line_item_service or InvoiceLineItemService()
        self.invoice_service = invoice_service or InvoiceService()
        # Shared cache from the parent connector: {invoice_line_item_id: InvoiceLineItem}
        self._line_item_cache: dict = line_item_cache if line_item_cache is not None else {}
        self._caches_preloaded = caches_preloaded
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_invoice_line(
        self,
        invoice_id: int,
        invoice_public_id: str,
        qbo_invoice_line: QboInvoiceLine,
        live_qbo_line_ids: frozenset,
        realm_id: Optional[str] = None,
    ) -> InvoiceLineItem:
        """
        Sync data from QboInvoiceLine to InvoiceLineItem module via the shared
        dbo-only line identity fast path.

        Args:
            invoice_id: Database ID of the Invoice in our system
            invoice_public_id: Public ID of the Invoice
            qbo_invoice_line: QboInvoiceLine record
            live_qbo_line_ids: this pull's full set of live QBO line ids under
                the parent Invoice — the re-adopt matcher's "still bound
                elsewhere in this pull" guard (U-361b).
            realm_id: QBO realm ID from the parent staging header

        Returns:
            InvoiceLineItem: The synced InvoiceLineItem record
        """
        if not qbo_invoice_line.qbo_line_id:
            # Without a QBO Line.Id there is no dbo-native identity to resolve or
            # stamp, and no mapping row keyed on the staging PK to make an
            # unstamped line findable next pull - creating one would duplicate it
            # on every re-pull. QBO always assigns Line.Id on a persisted
            # transaction, so this is a fail-closed guard, not a path.
            raise ValueError(
                f"QboInvoiceLine {qbo_invoice_line.id} on Invoice {invoice_id} has no "
                f"QBO Line.Id - cannot resolve or stamp dbo-native line identity; skipping."
            )

        # Map QBO InvoiceLine fields to InvoiceLineItem module fields
        description = qbo_invoice_line.description
        amount = qbo_invoice_line.amount

        # Calculate price from line detail. QBO invoices don't have a direct "markup"
        # concept (discount_rate is a reduction, not an addition), so markup is left None.
        # The QBO amount field already reflects all discounts applied.
        markup = None
        price = None
        if qbo_invoice_line.unit_price is not None and qbo_invoice_line.qty is not None:
            price = qbo_invoice_line.unit_price

        def _fingerprint_amount(existing_source_type, value):
            # U-344: a BillCreditLineItem-sourced line stores Amount
            # signed-negative (write-site fix + relabel negation), but QBO's
            # own reported amount for this line is not guaranteed same-signed
            # (see dbo.invoice.sql's Tier-3 fingerprint comment) — compare by
            # MAGNITUDE for this source type, or every routine re-pull sees a
            # spurious "amount changed" from the sign convention alone and
            # resets the line back to Manual, un-billing and un-negating it.
            if existing_source_type == "BillCreditLineItem" and value is not None:
                try:
                    value = abs(Decimal(str(value)))
                except Exception:
                    pass
            return self._normalize_for_fingerprint(value)

        def _apply_line_fields(direct: InvoiceLineItem) -> Optional[InvoiceLineItem]:
            """
            `apply_fields` for the HIT branch (and a successful readopt):
            write the QBO-derived fields onto a matched InvoiceLineItem.
            Shared by the direct hit and the re-adopt path (both call this
            through the primitive) — one update-logic site, not two
            hand-copies that could drift. `direct`'s CURRENT stored
            source_type/amount feed the source-reset decision exactly as the
            with-mapping era's connector did.
            """
            existing_source_type = getattr(direct, "source_type", None)
            amount_changed = (
                _fingerprint_amount(existing_source_type, direct.amount)
                != _fingerprint_amount(existing_source_type, amount)
            )
            if existing_source_type and existing_source_type != "Manual" and amount_changed:
                logger.warning(
                    f"InvoiceLineItem {direct.id} amount differs from QBO "
                    f"({direct.amount!r} -> {amount!r}); resetting SourceType "
                    f"{existing_source_type!r} -> 'Manual' (re-link required)"
                )
                # The abandoned source must become billable again — otherwise
                # the corrected charge can never be re-billed (its old source
                # stays IsBilled=1 and never reappears in billable-items).
                try:
                    self.invoice_service._reset_source_as_unbilled(direct)
                except Exception as unbill_error:
                    logger.warning(
                        f"Could not un-bill abandoned source for "
                        f"InvoiceLineItem {direct.id}: {unbill_error}"
                    )
            source_type = (
                existing_source_type
                if existing_source_type and not amount_changed
                else "Manual"
            )

            updated = self.invoice_line_item_service.update_by_public_id(
                direct.public_id,
                row_version=direct.row_version,
                invoice_public_id=invoice_public_id,
                source_type=source_type,
                description=description,
                amount=Decimal(str(amount)) if amount is not None else None,
                markup=Decimal(str(markup)) if markup is not None else None,
                price=Decimal(str(price)) if price is not None else None,
                is_draft=False,
            )
            if updated is None:
                # ROWVERSION race: a concurrent writer touched this exact
                # InvoiceLineItem between the read and this UPDATE. Handled by
                # the primitive's own raise_concurrent_write_race contract
                # (on_apply_returned_none is not wired — the default None
                # return already triggers it).
                logger.error(
                    f"Failed to update InvoiceLineItem {direct.id} from QboInvoiceLine "
                    f"{qbo_invoice_line.id} - update_by_public_id returned None "
                    f"(concurrent write race)"
                )
                return None
            if realm_id and getattr(direct, "qbo_id", None) and not getattr(direct, "realm_id", None):
                # Legacy realm gap (U-293-dw): the row was found by its QboId but
                # never got the RealmId half of the atomic pair. Heal it once,
                # best-effort — a failure here must not fail the line (the row
                # is still correctly identified by (InvoiceId, QboId)), and on
                # the readopt path specifically must not escape apply_fields:
                # that call sits OUTSIDE the primitive's own stamp_identity
                # try/except (base/identity_fastpath.py's _readopt), so an
                # uncaught raise here would skip on_readopt_stamp_failed
                # entirely instead of being recorded.
                # The `direct.qbo_id` guard (U-362b) scopes this to an already-
                # PARTIALLY-stamped row specifically — a HIT is always one by
                # construction (read_by_qbo_identity only returns rows WHERE
                # QboId = @QboId), but a readopted candidate (U-362b's
                # provenance recognition, or the pre-existing Manual-fingerprint
                # match) can be entirely unstamped (QboId AND RealmId both
                # None); without this guard, that case redundantly double-
                # stamps here AND in stamp_identity right after — harmless
                # (the sproc's own idempotent CASE-WHEN makes the second call a
                # no-op) but a wasted round trip, and it muddies "legacy realm
                # gap" (this branch's real intent) with "never stamped at all"
                # (stamp_identity's job, exclusively, below).
                try:
                    self.invoice_line_item_service.repo.set_qbo_identity(
                        id=coerce_id(updated.id), qbo_id=qbo_invoice_line.qbo_line_id, realm_id=realm_id,
                    )
                except Exception as heal_error:
                    logger.warning(
                        f"Could not heal missing RealmId for InvoiceLineItem "
                        f"{updated.id}: {heal_error}"
                    )
            # U-272: dbo source-link provenance mirror (create+update pairing).
            _stamp_source_provenance_or_warn(
                self.invoice_line_item_service.repo,
                qbo_invoice_line=qbo_invoice_line,
                invoice_line_item_id=int(updated.id),
                context=f"Updated InvoiceLineItem {updated.id}",
            )
            if self._line_item_cache is not None:
                self._line_item_cache[updated.id] = updated
            return updated

        def _manual_line_candidates() -> list:
            """Unmapped-adoption candidate pool: this invoice's Manual-sourced
            lines only (U-247 restriction, carried into U-362). Bill/Expense/
            BillCreditLineItem-sourced lines are matched via their source FK
            elsewhere; fingerprint-adopting one here would steal it from its
            true source. Reads from the connector's pre-loaded
            `_line_item_cache` when `caches_preloaded=True` (avoids O(n^2) DB
            round trips on large invoices), else a direct DB read."""
            if self._caches_preloaded:
                existing = [
                    li for li in self._line_item_cache.values()
                    if getattr(li, "invoice_id", None) == invoice_id
                ]
            else:
                existing = self.invoice_line_item_service.read_by_invoice_id(invoice_id)
            return [li for li in existing if getattr(li, "source_type", None) == "Manual"]

        def _readopt_stale_line() -> Optional[InvoiceLineItem]:
            """
            `readopt_candidate` for the MISS branch: two recognition steps,
            tried in order, feeding the SAME apply_fields -> stamp_identity
            pipeline (both are "reuse this dbo.Id, never mint a sibling").

            1. Source-linked recognition (U-362b, any SourceType != Manual) —
               tried FIRST, see `_recognize_source_linked_line`'s own
               docstring.
            2. Manual-only fingerprint readopt (U-361b's shared matcher,
               cloned) — find a local Manual InvoiceLineItem under this
               Invoice whose current identity is stale (not in
               `live_qbo_line_ids`) and whose (description, amount)
               fingerprint matches this QBO line — identical selection to
               the pre-U-362 `_find_and_match_manual_by_fingerprint` (task
               #17), generalized onto the shared `find_stale_identity_orphan`
               (base/line_orphan_adopt.py) so U-362/363/364 share the SAME
               matcher rather than hand-copying it.

            Pure lookup: no field writes here — the primitive applies
            `apply_fields` to whatever this returns before stamping.
            """
            recognized = self._recognize_source_linked_line(
                invoice_id, qbo_invoice_line, live_qbo_line_ids,
            )
            if recognized is not None:
                return recognized
            return find_stale_identity_orphan(
                existing_lines=_manual_line_candidates(),
                live_qbo_line_ids=live_qbo_line_ids,
                fingerprint=lambda li: self._content_fingerprint(li.description, li.amount),
                target=self._content_fingerprint(description, amount),
            )

        def _create_line() -> Optional[InvoiceLineItem]:
            """
            `resolve_candidate` for the MISS branch: create the line fresh (no
            adopt — see `_readopt_stale_line` above). Fails closed BEFORE
            creating when the realm needed for the stamp is missing:
            SetInvoiceLineItemQboIdentity's own atomic-pair guard declines to
            write a QboId without a RealmId, so the line would come out
            unstamped — which post-U-362 means unfindable, i.e. re-created as
            a duplicate on every pull. Refusing up front saves the
            create+delete round trip the helper would otherwise perform.
            """
            if not realm_id:
                raise RuntimeError(
                    f"Refusing to create InvoiceLineItem for QboInvoiceLine "
                    f"{qbo_invoice_line.qbo_line_id} on Invoice {invoice_id}: realm_id is "
                    f"missing, so its dbo-native identity stamp could not land and the line "
                    f"would be an unfindable orphan. Holding for retry."
                )
            line_item = self.invoice_line_item_service.create(
                invoice_public_id=invoice_public_id,
                source_type="Manual",
                description=description,
                amount=amount,
                markup=markup,
                price=price,
                is_draft=False,
            )
            if self._line_item_cache is not None:
                self._line_item_cache[line_item.id] = line_item
            return line_item

        def _stamp_line_identity(candidate: InvoiceLineItem) -> Optional[InvoiceLineItem]:
            """
            `stamp_identity` for both the MISS/create and the readopt
            branches: the bare dbo-native stamp plus the U-272 provenance
            mirror, then a re-read — `set_qbo_identity` is a void DB write
            that never mutates `candidate` in memory, and the helper verifies
            the returned row actually carries the identity (its "stamp did
            not land" guard).
            """
            candidate_id = coerce_id(candidate.id)
            self.invoice_line_item_service.repo.set_qbo_identity(
                id=candidate_id, qbo_id=qbo_invoice_line.qbo_line_id, realm_id=realm_id,
            )
            _stamp_source_provenance_or_warn(
                self.invoice_line_item_service.repo,
                qbo_invoice_line=qbo_invoice_line,
                invoice_line_item_id=candidate_id,
                context=f"Stamped InvoiceLineItem {candidate_id} for QboInvoiceLine {qbo_invoice_line.id}",
            )
            reread = self.invoice_line_item_service.read_by_id(candidate_id)
            # The cache entry written by _create_line (pre-stamp, qbo_id=None)
            # or already present from preload_caches() is now stale — without
            # this refresh, a line stamped earlier in the SAME batch run still
            # reads as unstamped to _manual_line_candidates' readopt scan for
            # the rest of the run (money-double-count risk, see that method's
            # docstring).
            if reread is not None and self._line_item_cache is not None:
                self._line_item_cache[reread.id] = reread
            return reread

        def _rollback_line(candidate: InvoiceLineItem) -> None:
            """
            `rollback_candidate` for the fresh-create MISS branch only (never
            invoked for a readopt — see the primitive's own decision §2):
            best-effort delete of the just-created, unstamped line.
            `rollback_orphan_header` is the shared compensating-delete
            mechanism (not header-specific beyond its name — same
            isolate-each-delete, never-raise, record-on-final-failure shape).
            A failed delete leaves an unstamped orphan that inflates this
            invoice's local lines on every future pull, so it is recorded to
            reconciliation monitoring.
            """
            rollback_orphan_header(
                delete_header=lambda: self.invoice_line_item_service.delete_by_public_id(
                    candidate.public_id
                ),
                delete_mapping=lambda: None,
                entity_label="InvoiceLineItem",
                entity_id=candidate.id,
                on_header_delete_failed=lambda exc: self._record_orphan_line_issue(
                    line_item=candidate, qbo_invoice_line=qbo_invoice_line, invoice_id=invoice_id,
                    realm_id=realm_id, exc=exc,
                ),
            )

        outcome = run_line_identity_fastpath_dbo_only(
            parent_local_id=invoice_id,
            qbo_line_id=qbo_invoice_line.qbo_line_id,
            entity_label="InvoiceLineItem",
            external_label="QboInvoiceLine",
            lock_resource_label="InvoiceLineItem",
            read_direct_by_parent_and_qbo_line_id=self.invoice_line_item_service.read_by_qbo_identity,
            readopt_candidate=_readopt_stale_line,
            resolve_candidate=_create_line,
            stamp_identity=_stamp_line_identity,
            rollback_candidate=_rollback_line,
            apply_fields=_apply_line_fields,
            on_readopt_stamp_failed=lambda readopted, exc: self._record_readopt_stamp_failed_issue(
                line_item=readopted, qbo_invoice_line=qbo_invoice_line, invoice_id=invoice_id,
                realm_id=realm_id, exc=exc,
            ),
            on_create_failed=lambda exc: self._record_create_failed_issue(
                qbo_invoice_line=qbo_invoice_line, invoice_id=invoice_id, realm_id=realm_id, exc=exc,
            ),
        )
        # qbo_line_id is guaranteed truthy above, so the helper's only hit=False
        # outcome is unreachable here and hit=True never carries entity=None.
        return outcome.entity

    def _recognize_source_linked_line(
        self, invoice_id: int, qbo_invoice_line: QboInvoiceLine, live_qbo_line_ids: frozenset,
    ) -> Optional[InvoiceLineItem]:
        """
        U-362b/U-362c: dbo-native replacement for the OTHER job the retired
        qbo.InvoiceLineItemInvoiceLine mapping used to do — recognizing that a
        re-pulled QBO invoice line already corresponds to an EXISTING local
        InvoiceLineItem created by the billing/complete flow and linked to its
        source (SourceType=BillLineItem/ExpenseLineItem/BillCreditLineItem,
        via `LinkInvoiceLineItemSource`), NOT by this pull path. Without this,
        a source-linked line whose dbo.QboId was never stamped MISSes on
        every future pull, is correctly excluded from the Manual-only readopt
        pool (it isn't Manual), and `_create_line` mints a phantom Manual
        sibling with the same amount — a straight invoice-draw double-count.

        Recognition key: (InvoiceId, LinkedTxnType, LinkedTxnId) — the U-272
        source-provenance mirror (`InvoiceLineItemSourceProvenance`,
        `_stamp_source_provenance_or_warn`) already writes this on every
        touch. (`entities/invoice/sql/dbo.invoice.sql`'s `ProposeInvoiceSource
        Links`/`ReadInvoiceSourceLinkLines` already read the same table for a
        DIFFERENT question — "which local Bill/Expense line is THIS invoice
        line's source" — this method answers "does an incoming QBO invoice
        line correspond to an EXISTING local InvoiceLineItem"; not the same
        lookup, but see that sproc if this one's shape ever needs to change.)
        Stable across a QBO Line.Id regeneration, unlike the line id itself.

        The key is NOT unique: EVERY sibling invoice line drawn from ONE
        multi-line source Bill/Expense shares the SAME LinkedTxnId (it's the
        source TRANSACTION id — QBO gives no per-line TxnLineId here) — the
        COMMON case, not an edge case (U-362c: 1,354 prod groups / 28,979
        lines collide). `read_by_linked_txn` returns the FULL sibling set;
        this method tie-breaks to the ONE sibling the incoming QBO line
        actually corresponds to via `find_stale_identity_orphan` (the SAME
        matcher/rule the Manual-only fallback below uses):
          - CONTENT fingerprint off the sibling's own PROVENANCE QboAmount/
            QboDescription (the immutable last-pulled-QBO snapshot) — NEVER
            the sibling's live InvoiceLineItem.amount/description, which are
            user-editable and would corrupt the match (U-362b's own P2: a
            content-blind rebind could overwrite the wrong line's amount and
            un-bill its true source — worse than a duplicate). No
            content-matching sibling -> None (falls through to Manual).
          - POSITION tie-break by the sibling's provenance LineNum when >1
            sibling shares the same content fingerprint (e.g. two identical-
            amount draws from the same source) — LineNum is the SOURCE
            document's own line order, unlike dbo.Id which only reflects
            local creation order. An EXACT match against the INCOMING line's
            OWN `line_num` ranks first, ahead of pure position order: a
            content-matching sibling whose LineNum equals the incoming
            line's is preferred over one that merely comes first by
            LineNum/.id, so the pairing stays correct even when a PRIOR
            sibling in the group failed to stamp (still-eligible, still
            content-matching, but the WRONG one for THIS incoming line) or
            the parent connector processes lines out of LineNum order —
            without this, that prior-still-eligible sibling would otherwise
            win purely on being "first by position," a real gap a Gate-2
            adversarial review caught (Codex, 2026-09-03). Falls back to
            plain LineNum-then-`.id` order only when no sibling's LineNum
            exactly matches (LineNum missing on one or both sides, or a
            genuine same-position tie) — money-neutral either way (same
            amount), but still needs a deterministic 1:1 pairing so N
            incoming QBO lines don't all race for the same sibling.
            ServiceDate is captured in the sibling's fingerprint fields but
            deliberately NOT part of the content match: it is a raw QBO
            string mirror (not a typed date), and requiring exact string
            equality risks a false-negative fall-through to a phantom Manual
            create on a cosmetic QBO formatting drift — worse than the
            disambiguation gap it would close, which the LineNum-exact
            preference above already closes more reliably.
          - Theft guard: `find_stale_identity_orphan` already excludes any
            sibling whose CURRENT qbo_id is in `live_qbo_line_ids` (normalized
            via `normalize_qbo_id`, U-361c) — correctly bound to a DIFFERENT
            live QBO line in this same pull, never stolen. This is also what
            makes repeated calls within one pull pair 1:1: once a sibling is
            recognized and stamped, its qbo_id becomes a live id, so the next
            incoming line's fresh DB read finds it no longer eligible.

        Returns None (falls through to the Manual-only fingerprint readopt)
        when the incoming line carries no LinkedTxn, no sibling exists, or no
        sibling content-matches. Not restricted to a particular SourceType: a
        Manual line that happens to carry a real LinkedTxn (rare, but the
        schema doesn't forbid it) is a MORE precise match here than the
        Manual-fingerprint fallback would give it, so recognizing it via this
        path first is strictly better, not a scope violation.
        """
        linked_txn_type = qbo_invoice_line.linked_txn_type
        linked_txn_id = qbo_invoice_line.linked_txn_id
        if not linked_txn_type or not linked_txn_id:
            return None
        siblings = self.invoice_line_item_service.read_by_linked_txn(
            invoice_id, linked_txn_type, linked_txn_id,
        )
        if not siblings:
            return None
        incoming_line_num = qbo_invoice_line.line_num
        recognized = find_stale_identity_orphan(
            existing_lines=siblings,
            live_qbo_line_ids=live_qbo_line_ids,
            fingerprint=lambda sib: self._content_fingerprint(sib.qbo_description, sib.qbo_amount),
            target=self._content_fingerprint(qbo_invoice_line.description, qbo_invoice_line.amount),
            position_key=lambda sib: (
                0 if incoming_line_num is not None and sib.line_num == incoming_line_num else 1,
                sib.line_num if sib.line_num is not None else _NO_LINE_NUM_RANK,
                sib.id or 0,
            ),
        )
        return recognized.line_item if recognized is not None else None

    @classmethod
    def _content_fingerprint(cls, description, amount) -> tuple:
        """Shared (description, amount) content-fingerprint tuple — the SAME
        two-value shape used by both `_recognize_source_linked_line`'s
        sibling tie-break above and the Manual-only fingerprint readopt in
        `_readopt_stale_line` below, so a future fix to the fingerprint rule
        (this codebase's own history: BillCreditLineItem's sign-magnitude
        normalization, U-344) touches one place, not two near-identical
        lambda literals that could silently drift apart."""
        return (
            cls._normalize_for_fingerprint(description),
            cls._normalize_for_fingerprint(amount),
        )

    def _record_orphan_line_issue(
        self, *, line_item: InvoiceLineItem, qbo_invoice_line: QboInvoiceLine, invoice_id: int,
        realm_id: Optional[str], exc: Exception,
    ) -> None:
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphan_ili_line_item",
            entity_type="InvoiceLineItem",
            entity_public_id=str(line_item.public_id) if getattr(line_item, "public_id", None) else None,
            qbo_id=str(qbo_invoice_line.qbo_line_id) if qbo_invoice_line.qbo_line_id else None,
            realm_id=realm_id or "",
            details=(
                f"Compensating rollback failed to delete unstamped InvoiceLineItem "
                f"{line_item.id} ({getattr(line_item, 'public_id', None)}) on Invoice "
                f"{invoice_id} after its identity stamp for QboInvoiceLine "
                f"{qbo_invoice_line.qbo_line_id} failed: {exc}. The orphan is invisible "
                f"to the dbo-native fast path, so every re-pull will mint a duplicate "
                f"line until it is deleted or stamped by hand."
            ),
        )

    def _record_readopt_stamp_failed_issue(
        self, *, line_item: InvoiceLineItem, qbo_invoice_line: QboInvoiceLine, invoice_id: int,
        realm_id: Optional[str], exc: Exception,
    ) -> None:
        """`on_readopt_stamp_failed` (U-361b shape): a stale-identity orphan was
        found and matched, but re-applying/re-stamping it failed. NOTHING is
        deleted — the row stays exactly as it was, under its OLD identity.
        Recorded so a human knows this invoice will keep re-adopting on retry
        rather than silently double-counting draws forever."""
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="ili_line_readopt_failed",
            entity_type="InvoiceLineItem",
            entity_public_id=str(line_item.public_id) if getattr(line_item, "public_id", None) else None,
            qbo_id=str(qbo_invoice_line.qbo_line_id) if qbo_invoice_line.qbo_line_id else None,
            realm_id=realm_id or "",
            details=(
                f"Found a stale-identity orphan InvoiceLineItem {line_item.id} "
                f"({getattr(line_item, 'public_id', None)}) on Invoice {invoice_id} "
                f"matching QboInvoiceLine {qbo_invoice_line.qbo_line_id} by content "
                f"fingerprint, but re-adopting it failed: {exc}. The row was left "
                f"UNTOUCHED under its previous identity (never deleted) - this invoice "
                f"will keep retrying the readopt on every re-pull until it succeeds or "
                f"is resolved by hand."
            ),
        )

    def _record_create_failed_issue(
        self, *, qbo_invoice_line: QboInvoiceLine, invoice_id: int, realm_id: Optional[str], exc: Exception,
    ) -> None:
        """`on_create_failed` (U-361b P2 hardening shape): `resolve_candidate`
        (the fresh-create path) raised. If the underlying INSERT actually
        committed before the failure, there is no candidate reference to
        identify or delete - this is only a DETECTABILITY signal, not a claim
        that a row exists."""
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="ili_line_create_failed",
            entity_type="InvoiceLineItem",
            entity_public_id=None,
            qbo_id=str(qbo_invoice_line.qbo_line_id) if qbo_invoice_line.qbo_line_id else None,
            realm_id=realm_id or "",
            details=(
                f"Creating a new InvoiceLineItem for QboInvoiceLine "
                f"{qbo_invoice_line.qbo_line_id} on Invoice {invoice_id} failed: {exc}. If "
                f"the underlying write actually committed before this failure, an "
                f"unstamped (QboId IS NULL) orphan may exist under this Invoice - not "
                f"confirmed by this record alone, but worth a manual check."
            ),
        )

    @staticmethod
    def _normalize_for_fingerprint(value) -> str:
        """Canonicalize a value for content-fingerprint comparison.

        `+ 0` cancels a negative-zero sign BEFORE formatting (U-362c, Gate-2
        adversarial finding): `Decimal("-0.00").normalize()` formats as
        `"-0"` while `Decimal("0.00").normalize()` formats as `"0"` — two
        numerically-IDENTICAL amounts (`Decimal("-0.00") == Decimal("0.00")`
        is `True`) that would otherwise fingerprint as different strings and
        miss a genuine content match for a real $0.00 line, e.g. a QBO amount
        that round-trips as literal `-0` or a signed source amount netting to
        exactly zero."""
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format((value + 0).normalize(), "f")
        try:
            return format((Decimal(str(value)) + 0).normalize(), "f")
        except Exception:
            pass
        return str(value).strip()
