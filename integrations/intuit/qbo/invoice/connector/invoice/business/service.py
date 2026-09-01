# Python Standard Library Imports
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.invoice.connector.invoice_line_item.persistence.repo import InvoiceLineItemInvoiceLineRepository
from integrations.intuit.qbo.invoice.business.model import QboInvoice, QboInvoiceLine
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from entities.invoice.business.service import InvoiceService
from entities.invoice.business.model import Invoice
from entities.project.business.service import ProjectService
from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_ref,
    qbo_ref_or_placeholder,
)
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.base.identity_fastpath import (
    run_identity_fastpath_dbo_only,
    stamp_dbo_identity_with_lock,
)
from integrations.intuit.qbo.base.reconciliation_recorder import (
    build_duplicate_qbo_identity_conflict_desc,
    record_duplicate_identity_conflict,
    record_mapping_issue,
)
from integrations.intuit.qbo.base.cost_code_resolver import resolve_qbo_item_ref
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from integrations.intuit.qbo.base.ids import coerce_id
from entities.sub_cost_code.business.service import SubCostCodeService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _InvoiceCandidate:
    """`resolve_candidate` -> `stamp_identity` handoff for the dbo-only fast path's
    MISS branch (U-356): which local Invoice to bind, and whether it is a
    pre-existing row ADOPTED by side-channel key (stamped under its own candidate
    lock; never rolled back on a stamp failure; never line-synced — see
    `_adopt_invoice_identity`) or a fresh header this sync just minted (rolled
    back on a stamp / line-sync failure). `id` is what
    `run_identity_fastpath_dbo_only` reads off the candidate for its own
    concurrent-write-race message."""

    invoice: Invoice
    adopted: bool

    @property
    def id(self):
        return self.invoice.id


class InvoiceInvoiceConnector:
    """
    Connector service for synchronization between QboInvoice and Invoice modules.
    """

    def __init__(
        self,
        line_mapping_repo: Optional[InvoiceLineItemInvoiceLineRepository] = None,
        invoice_service: Optional[InvoiceService] = None,
        project_service: Optional[ProjectService] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        customer_project_repo=None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
    ):
        """Initialize the InvoiceInvoiceConnector.

        U-356: the header mapping repo (qbo.InvoiceInvoice) is retired —
        dbo.Invoice.QboId/RealmId is the sole header identity store. The LINE
        mapping repo (qbo.InvoiceLineItemInvoiceLine, `line_mapping_repo`) is a
        separate family (U-358) and is deliberately untouched here.
        """
        self.line_mapping_repo = line_mapping_repo or InvoiceLineItemInvoiceLineRepository()
        self.invoice_service = invoice_service or InvoiceService()
        self.project_service = project_service or ProjectService()
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        # U-314 dropped qbo.CustomerProject entirely -- _get_project_public_id
        # below already binds via a fresh CustomerProjectConnector() instance,
        # never self.customer_project_repo, so this was already dead. Kept as
        # an untyped, unconstructed constructor param so existing test kwargs
        # don't need touching.
        self.customer_project_repo = customer_project_repo
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # U-307b: only ever passed to cost_code_resolver.resolve_qbo_item_ref, never
        # used directly here. Kept as an injectable constructor param (not defaulted
        # inline) so tests can inject a fake exactly as they did before this repoint.
        self.sub_cost_code_service = sub_cost_code_service

        # In-memory caches to avoid repeated DB lookups across invoice syncs
        self._project_cache: dict = {}          # {(realm_id, qbo_customer_ref_value): project_public_id}
        self._line_mapping_cache: dict = {}     # {qbo_invoice_line_id: InvoiceLineItemInvoiceLine}
        self._invoice_cache: dict = {}          # {invoice_id: Invoice}
        self._line_item_cache: dict = {}        # {invoice_line_item_id: InvoiceLineItem}
        self._caches_preloaded: bool = False

    def preload_caches(self) -> None:
        """
        Pre-load all mapping and module record caches from the database.
        Call once before processing a large batch to eliminate per-invoice DB lookups.
        """
        from entities.invoice_line_item.business.service import InvoiceLineItemService

        # U-356: no header-mapping preload — header identity is resolved per
        # invoice straight off dbo.Invoice.QboId/RealmId (an indexed point read),
        # not from a bulk qbo.InvoiceInvoice snapshot. The LINE mapping preload
        # below is a different family (U-358) and stays.
        logger.info("Pre-loading InvoiceLineItemInvoiceLine mapping cache...")
        all_line_mappings = self.line_mapping_repo.read_all()
        self._line_mapping_cache = {m.qbo_invoice_line_id: m for m in all_line_mappings}
        logger.info(f"Pre-loaded {len(self._line_mapping_cache)} InvoiceLineItemInvoiceLine mappings")

        logger.info("Pre-loading Invoice module records...")
        all_invoices = self.invoice_service.read_all()
        self._invoice_cache = {inv.id: inv for inv in all_invoices}
        logger.info(f"Pre-loaded {len(self._invoice_cache)} Invoice records")

        logger.info("Pre-loading InvoiceLineItem module records...")
        line_item_service = InvoiceLineItemService()
        all_line_items = line_item_service.read_all()
        self._line_item_cache = {item.id: item for item in all_line_items}
        logger.info(f"Pre-loaded {len(self._line_item_cache)} InvoiceLineItem records")

        self._caches_preloaded = True

    def sync_from_qbo_invoice(self, qbo_invoice: QboInvoice, qbo_invoice_lines: List[QboInvoiceLine]) -> Invoice:
        """
        Sync a QBO Invoice to the Invoice module, via the dbo-only identity fast
        path (U-356 — qbo.InvoiceInvoice is retired; dbo.Invoice.QboId/RealmId,
        U-238a, is the sole identity store).

        This method:
        1. Resolves the QBO CustomerRef to a Project public_id (dbo-native)
        2. Direct dbo.Invoice.QboId/RealmId hit -> update in place + sync lines
        3. Genuine miss -> gap-detect/adopt a mapping-lost local Invoice by
           (project, number) or header fingerprint (U-034), else create a new
           Invoice (suffixing a colliding number); stamp identity; sync lines

        Args:
            qbo_invoice: QboInvoice record
            qbo_invoice_lines: List of QboInvoiceLine records for this invoice

        Returns:
            Invoice: The synced Invoice record
        """
        # Find project mapping from QBO CustomerRef. U-311: covers a dbo-direct
        # miss, a failed dbo-only identity verification, and a failed
        # heal-by-name — not just "no mapping row" any more (Codex xhigh P3).
        project_public_id = self._get_project_public_id(qbo_invoice.customer_ref_value, qbo_invoice.realm_id)
        if not project_public_id:
            raise ValueError(
                f"No project mapping found for QBO customer ref: {qbo_invoice.customer_ref_value}"
            )

        # Map QBO Invoice fields to Invoice module fields
        invoice_number = qbo_ref_or_placeholder(qbo_invoice.doc_number, qbo_invoice.qbo_id)
        invoice_date = qbo_invoice.txn_date or ""
        due_date = qbo_invoice.due_date or ""
        memo = qbo_invoice.private_note
        total_amount = qbo_invoice.total_amt

        def _write_qbo_fields(target: Invoice) -> Optional[Invoice]:
            """
            Write the QBO-derived fields onto an existing Invoice (no identity
            stamp, no line sync). Shared by the fast path's HIT `apply_fields`
            and the MISS branch's adopt path (`_adopt_invoice_identity`, where it
            runs inside the candidate's own stamp lock) so the QboInvoice->Invoice
            field mapping lives in exactly one place — mirrors
            BillBillConnector's `_apply_bill_fields`.

            U-027/U-034 (rule of three, completed for Invoice): never clobber a
            human-corrected invoice_number on re-pull. Preserve the stored value
            unless it is empty/null or the QBO-<id> placeholder (which still
            upgrades to a real doc_number) — exactly like the Bill/BillCredit/
            Expense siblings. This is safe because the lost-mapping gap-detect/
            adopt path no longer keys ONLY on the QBO-derived number: when a
            preserved (divergent) number makes the number lookup miss, a header
            fingerprint (total + txn_date + project) re-adopts the renamed invoice
            instead of minting a phantom -N duplicate. CREATE path unchanged.

            Returns None on a ROWVERSION-race / concurrent-delete
            `update_by_public_id` miss (U-291) — both callers hand that None to a
            shared helper whose own `raise_concurrent_write_race` is the ONE place
            the guarantee lives (`run_identity_fastpath_dbo_only._apply` on the
            HIT path, `stamp_dbo_identity_with_lock` step 5 on the adopt path).
            """
            effective_invoice_number = preserve_human_edited_ref(
                target.invoice_number, invoice_number, qbo_invoice.qbo_id
            )
            updated = self.invoice_service.update_by_public_id(
                target.public_id,
                row_version=target.row_version,
                project_public_id=project_public_id,
                invoice_date=invoice_date,
                due_date=due_date,
                invoice_number=effective_invoice_number,
                total_amount=Decimal(str(total_amount)) if total_amount is not None else None,
                memo=memo,
                is_draft=False,
            )
            if updated is None:
                logger.error(
                    f"Failed to update Invoice {target.id} from QboInvoice {qbo_invoice.id} - "
                    f"update_by_public_id returned None (concurrent write race)"
                )
                return None
            if self._invoice_cache is not None:
                self._invoice_cache[updated.id] = updated
            return updated

        def _apply_invoice_fields(direct: Invoice) -> Optional[Invoice]:
            """
            `apply_fields` for the dbo-only fast path's HIT branch: write the
            QBO-derived fields, re-stamp identity, sync line items. Covers both a
            plain direct hit and a race-resolved hit
            (`run_identity_fastpath_dbo_only` calls this for both).

            Invoice carries SyncToken as part of its identity (like Bill/Expense) —
            this re-stamp is NOT redundant even when QboId/RealmId are already
            correct-by-construction (the fast path only found `direct` because
            they already match): it refreshes SyncToken on every pull, matching
            the pre-U-356 behavior exactly.
            """
            updated = _write_qbo_fields(direct)
            if updated is None:
                return None
            invoice_id = coerce_id(updated.id)
            self._set_invoice_qbo_identity(invoice_id, qbo_invoice)
            self._sync_line_items(
                invoice_id, updated.public_id, qbo_invoice_lines, qbo_invoice.realm_id
            )
            return updated

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_invoice.qbo_id,
            realm_id=qbo_invoice.realm_id,
            entity_label="Invoice",
            external_label="QboInvoice",
            lock_resource_label="Invoice",
            read_direct_by_qbo_identity=self.invoice_service.read_by_qbo_identity,
            apply_fields=_apply_invoice_fields,
            resolve_candidate=lambda: self._resolve_invoice_candidate(
                qbo_invoice=qbo_invoice,
                project_public_id=project_public_id,
                invoice_number=invoice_number,
                invoice_date=invoice_date,
                due_date=due_date,
                memo=memo,
                total_amount=total_amount,
            ),
            stamp_identity=lambda candidate: (
                self._adopt_invoice_identity(
                    candidate, qbo_invoice=qbo_invoice, write_fields=_write_qbo_fields,
                )
                if candidate.adopted
                else self._stamp_fresh_invoice_identity(
                    candidate.invoice, qbo_invoice=qbo_invoice, qbo_invoice_lines=qbo_invoice_lines,
                )
            ),
        )
        if outcome.entity is None:
            # No longer race-reachable in practice (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a directly
            # invoked falsy qbo_invoice.qbo_id, mirroring every sibling
            # connector's identical guard (U-350/U-353/U-354/U-355).
            raise RuntimeError(
                f"Failed to resolve Invoice for QboInvoice {qbo_invoice.id} "
                f"(qbo_id={qbo_invoice.qbo_id}) via the dbo-only identity fast path"
            )
        return outcome.entity

    def _resolve_invoice_candidate(
        self,
        *,
        qbo_invoice: QboInvoice,
        project_public_id: str,
        invoice_number: str,
        invoice_date: str,
        due_date: str,
        memo,
        total_amount,
    ) -> _InvoiceCandidate:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-356):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once a
        genuine miss is confirmed (no dbo.Invoice currently holds this identity,
        including the re-read under lock).

        Gap-detect / adopt first (prevents phantom "-N" duplicate invoices): if a
        local Invoice already exists for this (project, number) — or, when a
        human RENAMED it, matches the header fingerprint (U-034) — and carries
        no QBO identity of its own, it is almost certainly the same invoice
        whose identity was lost (or a manual original). ADOPT it (the stamp
        happens in `_adopt_invoice_identity`, under the candidate's own lock)
        rather than minting a phantom suffixed duplicate via the create loop
        below. A candidate already bound to a DIFFERENT QBO invoice is a
        genuine number collision: recorded as `duplicate_qbo_invoice_number`
        (U-334) and never stolen — falls through to a real, suffixed CREATE.

        The dbo-only "already bound elsewhere?" check replaces the retired
        mapping-table read. A number-matched row is re-read BY ID first:
        ReadInvoiceByInvoiceNumberAndProjectId does not select QboId/RealmId
        (only ReadInvoiceById / ReadInvoiceByQboIdAndRealmId do — see
        entities/invoice/business/model.py), so the lookup row's `qbo_id` is
        always None and must not be trusted for this decision. A fingerprint-
        matched row needs no re-read: `_find_adoptable_invoice_by_fingerprint`
        already returns the by-id row it vetted. Cold path only (MISS branch),
        one by-id read per adopt candidate — the same cost profile as the
        per-candidate mapping read this replaces.
        """
        proj = self.project_service.read_by_public_id(project_public_id) if project_public_id else None
        existing_local = (
            self.invoice_service.repo.read_by_invoice_number_and_project_id(invoice_number, proj.id)
            if proj else None
        )
        if existing_local:
            existing_local = self.invoice_service.read_by_id(int(existing_local.id))
        # U-034 (completes the rule of three for Invoice): if the QBO-derived-number
        # lookup missed, the local invoice may have been RENAMED by a human — its
        # number now diverges from QBO's (the HIT path preserves that edit) and its
        # identity has since been lost. Fall back to a header fingerprint (total +
        # txn_date + project) so the renamed invoice is RE-ADOPTED here rather than
        # missed and phantom-duplicated by the suffix-CREATE loop below (the "46
        # phantom -N invoices" bug this whole path exists to prevent).
        if not existing_local and proj:
            existing_local = self._find_adoptable_invoice_by_fingerprint(
                proj.id, total_amount, invoice_date, qbo_invoice.qbo_id, qbo_invoice.realm_id
            )
        if existing_local:
            if self._carries_different_qbo_identity(
                existing_local, qbo_invoice.qbo_id, qbo_invoice.realm_id
            ):
                logger.warning(
                    f"Invoice number '{invoice_number}' (project {proj.id}) is already bound to a "
                    f"DIFFERENT QBO invoice (QboId={existing_local.qbo_id}, "
                    f"RealmId={existing_local.realm_id}); QboInvoice {qbo_invoice.id} "
                    f"will create a suffixed invoice (genuine number collision)."
                )
                record_mapping_issue(
                    self.reconciliation_repo,
                    drift_type="duplicate_qbo_invoice_number",
                    entity_type="Invoice",
                    entity_public_id=str(existing_local.public_id) if existing_local.public_id else None,
                    qbo_id=str(existing_local.qbo_id),
                    realm_id=qbo_invoice.realm_id or "",
                    details=(
                        f"Invoice number '{invoice_number}' (project {proj.id}) collides: local Invoice "
                        f"{existing_local.id} (number '{existing_local.invoice_number}') already carries "
                        f"dbo-native QBO identity QboId={existing_local.qbo_id} "
                        f"(RealmId={existing_local.realm_id}); incoming QboInvoice {qbo_invoice.id} "
                        f"(QboId={qbo_invoice.qbo_id}) will create a suffixed invoice instead of "
                        f"overwriting the existing identity."
                    ),
                    severity="critical",
                )
            else:
                # Positive-evidence guard: a (project, number) match alone is NOT enough to adopt.
                # Local-origin / manual invoices are normally UNSTAMPED (QBO push is disabled) and a
                # local invoice can legitimately share a number with a DIFFERENT QboInvoice; adopting
                # one would overwrite its header and double-add lines. Only adopt when the header
                # fingerprint confirms the SAME invoice whose identity was lost: total within $0.01 AND
                # same txn date. Otherwise fall through to the safe (non-destructive) suffix CREATE.
                same_invoice = self._header_fingerprint_matches(
                    total_amount, invoice_date,
                    getattr(existing_local, "total_amount", None),
                    getattr(existing_local, "invoice_date", None),
                )
                # Provenance guard (Pass-1 P2): only re-adopt an invoice that QBO actually
                # materialized (its line items carry InvoiceLineItemInvoiceLine mappings).
                # A manual/local-origin invoice — which, since QBO invoice push is disabled,
                # is exactly what stays unstamped — must never be adopted even on an exact
                # (number, total, date) match; that would bind it to QBO and overwrite its
                # header. Gates BOTH the number-matched and fingerprint-fallback adopt routes,
                # which converge here (the fingerprint helper also pre-filters on provenance
                # so its single-match disambiguation is correct).
                has_qbo_provenance = self._has_qbo_line_provenance(int(existing_local.id))
                if not same_invoice or not has_qbo_provenance:
                    logger.warning(
                        f"Local Invoice {existing_local.id} shares (project {proj.id}, number "
                        f"'{invoice_number}') with QboInvoice {qbo_invoice.id} but is NOT an "
                        f"adoptable identity-lost QBO invoice (fingerprint_match={same_invoice}, "
                        f"qbo_provenance={has_qbo_provenance}) — NOT adopting; will create a "
                        f"suffixed invoice."
                    )
                else:
                    logger.info(
                        f"Adopting existing Invoice {existing_local.id} for QboInvoice {qbo_invoice.id} "
                        f"(number '{invoice_number}', fingerprint match) instead of minting a phantom"
                    )
                    return _InvoiceCandidate(existing_local, adopted=True)

        # Create new Invoice, handling duplicate invoice numbers
        logger.info(f"Creating new Invoice from QboInvoice {qbo_invoice.id}: invoice_number={invoice_number}")
        create_number = invoice_number
        for attempt in range(10):
            try:
                invoice = self.invoice_service.create(
                    project_public_id=project_public_id,
                    invoice_date=invoice_date,
                    due_date=due_date,
                    invoice_number=create_number,
                    total_amount=total_amount,
                    memo=memo,
                    is_draft=False,
                )
                break
            except ValueError as e:
                if "already exists" in str(e):
                    create_number = f"{invoice_number}-{attempt + 2}"
                    logger.info(f"Duplicate invoice number, retrying with: {create_number}")
                else:
                    raise
        else:
            # Every suffix variant is taken. Record it (it IS a number collision,
            # ten times over — the same drift_type the single-collision branch
            # above records; without a durable row the un-projected QBO invoice
            # would be invisible to the daily reconcile, which only scans
            # dbo.Invoice rows WITH identity), then fail loud with the same
            # ValueError class the loop's own "already exists" branch carries (a
            # permanent per-item skip for the pull caller), instead of the
            # UnboundLocalError the pre-U-356 loop fell into here.
            record_mapping_issue(
                self.reconciliation_repo,
                drift_type="duplicate_qbo_invoice_number",
                entity_type="Invoice",
                entity_public_id=None,
                qbo_id=str(qbo_invoice.qbo_id) if qbo_invoice.qbo_id else None,
                realm_id=qbo_invoice.realm_id or "",
                details=(
                    f"Invoice number '{invoice_number}' (project {proj.id if proj else None}) and "
                    f"all of its suffix variants ('{invoice_number}-2'..'{invoice_number}-11') already "
                    f"exist; QboInvoice {qbo_invoice.id} (QboId={qbo_invoice.qbo_id}) could not be "
                    f"projected and will be skipped until a human frees a number."
                ),
                severity="critical",
            )
            raise ValueError(
                f"Invoice number '{invoice_number}' (project {proj.id if proj else None}) and all "
                f"of its suffix variants already exist; cannot create Invoice for QboInvoice "
                f"{qbo_invoice.id}"
            )

        # Update invoice cache with newly created record
        if self._invoice_cache is not None:
            self._invoice_cache[invoice.id] = invoice
        return _InvoiceCandidate(invoice, adopted=False)

    def _stamp_fresh_invoice_identity(
        self,
        invoice: Invoice,
        *,
        qbo_invoice: QboInvoice,
        qbo_invoice_lines: List[QboInvoiceLine],
    ) -> Optional[Invoice]:
        """
        `stamp_identity` for a FRESH candidate on the dbo-only fast path's MISS
        branch (U-356) — a row `_resolve_invoice_candidate`'s create loop just
        minted (an ADOPTED candidate goes to `_adopt_invoice_identity` instead;
        `sync_from_qbo_invoice`'s `stamp_identity` lambda dispatches on
        `_InvoiceCandidate.adopted`).

        The row is uniquely ours, so — like Bill (U-355) — there is no
        concurrent-different-qbo_id race to guard with extra locking; the fast
        path's own create lock already serializes two syncs of the SAME
        QboInvoice. On a failure in EITHER the identity stamp or the line sync,
        best-effort deletes the just-created header via `rollback_orphan_header`
        so a bad create never strands a header-only zombie — the identity-stamp
        rollback race fix (U-354/U-355 pattern): without it, a transient
        `set_qbo_identity` failure during CREATE mints an unstamped orphan
        Invoice that `read_direct_by_qbo_identity` can never find again (it
        carries no QboId), and the next pull tick mints a genuine duplicate —
        the exact "phantom -N invoice" class this connector's adopt path exists
        to prevent. Both steps share ONE try/except so a stamp failure gets the
        same cleanup as a line-sync failure (`_sync_line_items` swallows per-line
        failures today, so in practice only the stamp can raise here — but the
        rollback now stands regardless, closing the U-006 landmine that note
        used to carry).

        Re-reads and returns the row after stamping (mirrors Bill/Expense):
        `set_qbo_identity` is a void DB write that never mutates `invoice` in
        memory, so returning it as-is would hand the caller an Invoice whose
        `qbo_id`/`realm_id` still read as their pre-stamp `None`.
        """
        invoice_id = coerce_id(invoice.id)
        try:
            self._set_invoice_qbo_identity(invoice_id, qbo_invoice)
            self._sync_line_items(
                invoice_id, invoice.public_id, qbo_invoice_lines, qbo_invoice.realm_id
            )
        except Exception:
            rollback_orphan_header(
                delete_header=lambda: self.invoice_service.delete_by_public_id(invoice.public_id),
                delete_mapping=lambda: None,
                entity_label="Invoice",
                entity_id=invoice_id,
                on_header_delete_failed=lambda exc: self._record_orphan_header_issue(
                    invoice=invoice, qbo_invoice=qbo_invoice, exc=exc
                ),
            )
            raise

        refreshed = self.invoice_service.read_by_id(invoice_id)
        if refreshed is not None and self._invoice_cache is not None:
            self._invoice_cache[invoice_id] = refreshed
        return refreshed

    def _adopt_invoice_identity(
        self,
        candidate: _InvoiceCandidate,
        *,
        qbo_invoice: QboInvoice,
        write_fields,
    ) -> Optional[Invoice]:
        """
        `stamp_identity` for an ADOPTED (pre-existing, side-channel-keyed)
        candidate, delegating the row-scoped lock + theft-guard + write sequence
        to the shared `stamp_dbo_identity_with_lock` (U-328/U-331) — see that
        function's docstring for why a SECOND lock, keyed on the candidate's own
        id, is needed: `_resolve_invoice_candidate` binds by (project, number) or
        header fingerprint, so two DIFFERENT incoming QBO invoices could resolve
        to the SAME local Invoice concurrently. Mirrors
        `CompanyInfoCompanyConnector._stamp_company_identity` (U-350). Never
        rolled back on a failure: it is a human's / an earlier pull's live
        financial record, not something this sync minted.

        `apply_fields` is the shared `_write_qbo_fields` closure (preserving a
        human-corrected number — re-adopting a RENAMED invoice must not overwrite
        its number back to the QBO-derived value, that would re-clobber the very
        edit the fingerprint let us find), run AFTER the theft-guard, inside the
        lock, against the fresh under-lock re-read (so its ROWVERSION is current).
        `on_conflict` records `invoice_identity_conflict` (the DriftType the
        retired mapping-table-era recorder used to emit) — the raise itself lives
        in the shared helper.

        Lines are NEVER re-projected onto an adopted invoice. An adoptable
        candidate must pass `_has_qbo_line_provenance`, which is only true when
        the invoice already carries ≥1 QBO-mapped line item — so an adopted
        invoice is populated by construction, and re-syncing onto a populated
        invoice would DOUBLE-ADD every QBO line as a phantom Manual duplicate
        (its source-backed Bill/Expense lines aren't matched by the Manual-only
        line fingerprinter). Re-establishing the header identity is enough: future
        HIT pulls reconcile via the existing line maps. (The pre-U-356 "re-sync
        only when the adopted invoice is EMPTY" branch was unreachable for the
        same reason — the provenance gate already implied non-empty.)
        """
        stamped = stamp_dbo_identity_with_lock(
            candidate_id=coerce_id(candidate.invoice.id),
            entity_label="Invoice",
            qbo_id=qbo_invoice.qbo_id,
            realm_id=qbo_invoice.realm_id,
            read_by_id=self.invoice_service.read_by_id,
            apply_fields=write_fields,
            write_identity=lambda current: self._set_invoice_qbo_identity(
                coerce_id(current.id), qbo_invoice
            ),
            on_conflict=lambda current: self._record_duplicate_qbo_invoice_issue(
                qbo_invoice=qbo_invoice, local_invoice=current
            ),
        )
        if stamped is not None and self._invoice_cache is not None:
            self._invoice_cache[stamped.id] = stamped
        return stamped

    def _set_invoice_qbo_identity(self, invoice_id: int, qbo_invoice: QboInvoice) -> None:
        """The one `SetInvoiceQboIdentity` call shape shared by the HIT re-stamp,
        the fresh-create stamp and the adopt-path `write_identity` (U-356)."""
        self.invoice_service.repo.set_qbo_identity(
            id=invoice_id,
            qbo_id=qbo_invoice.qbo_id,
            realm_id=qbo_invoice.realm_id,
            sync_token=getattr(qbo_invoice, "sync_token", None),
        )

    @staticmethod
    def _carries_different_qbo_identity(local_invoice, qbo_id: Optional[str], realm_id: Optional[str]) -> bool:
        """True when `local_invoice` already carries a dbo-native QBO identity
        that is NOT this exact (qbo_id, realm_id) pair — i.e. it belongs to a
        DIFFERENT QBO invoice and must never be adopted/re-pointed. The dbo-only
        replacement for the retired "mapped to a different QboInvoice" check.
        Checking QboId alone would miss a same-QboId-different-realm collision
        (QBO ids are only unique WITHIN a realm) — same predicate the shared
        `stamp_dbo_identity_with_lock` theft-guard applies at stamp time."""
        existing_qbo_id = getattr(local_invoice, "qbo_id", None)
        if not existing_qbo_id:
            return False
        return not (
            existing_qbo_id == qbo_id
            and (getattr(local_invoice, "realm_id", None) or "") == (realm_id or "")
        )

    def _find_adoptable_invoice_by_fingerprint(
        self, project_id, total_amount, invoice_date, qbo_id, realm_id
    ) -> Optional[Invoice]:
        """Find an identity-lost local Invoice for this project whose header fingerprint
        matches the incoming QBO invoice, so a human-RENAMED invoice (whose number no
        longer equals the QBO-derived value and is therefore missed by
        read_by_invoice_number_and_project_id) is still RE-ADOPTED instead of phantom-
        duplicated. This fingerprint fallback is what makes preserving invoice_number
        safe (U-034 completes the rule of three for Invoice).

        Fingerprint = same project AND total within $0.01 AND same non-blank txn date
        (first 10 chars) — identical to the positive-evidence guard the number-matched
        adopt path already applies. Returns the SINGLE unambiguous adoptable candidate,
        or None (no match, ambiguous multi-match, or a missing signal) so the caller
        falls through to the safe suffix-CREATE.

        Adoptable = has QBO line-mapping provenance (see _has_qbo_line_provenance) AND
        carries no dbo-native QBO identity, or already carries exactly THIS one. A
        candidate bound to a DIFFERENT QBO invoice is a distinct invoice and is never
        returned (genuine-collision case); a candidate with NO QBO provenance is a
        distinct manual invoice and is never returned either (closes the Pass-1 P2
        false-adopt path). RESIDUAL: two distinct QBO-derived invoices in one project
        sharing (total, date) with one's identity lost is ambiguous — the single-match
        rule falls through to suffix-CREATE rather than risk a wrong QBO-to-QBO bind.

        Candidate source is the preloaded _invoice_cache (production batch path) or a full
        read when caches_preloaded=False. Pure in-memory scan; the per-candidate by-id
        re-read (ReadInvoices does not select QboId/RealmId — see
        `_resolve_invoice_candidate`) only runs for the tiny subset that already passed
        the fingerprint filter, replacing the per-candidate mapping read one-for-one.
        """
        qbo_date = str(invoice_date or "").strip()[:10]
        if total_amount is None or not qbo_date:
            # Need both signals; a blank date is not a fingerprint (matches the guard
            # in the adopt block below).
            return None

        candidates = (
            list(self._invoice_cache.values())
            if self._caches_preloaded
            else self.invoice_service.read_all()
        )
        matches = []
        for inv in candidates:
            if getattr(inv, "project_id", None) != project_id:
                continue
            if not self._header_fingerprint_matches(
                total_amount, invoice_date,
                getattr(inv, "total_amount", None), getattr(inv, "invoice_date", None),
            ):
                continue
            current = self.invoice_service.read_by_id(int(inv.id))
            if current is None:
                continue  # deleted between the scan and this re-read
            if self._carries_different_qbo_identity(current, qbo_id, realm_id):
                continue  # bound to a DIFFERENT QBO invoice -> genuine separate invoice
            if not self._has_qbo_line_provenance(int(inv.id)):
                # No QBO line-mapping provenance -> a distinct local/manual invoice that
                # merely shares the fingerprint (QBO invoice push is disabled, so manual
                # invoices are exactly the unstamped ones). Never adopt it (Pass-1 P2).
                continue
            matches.append(current)

        if len(matches) == 1:
            logger.info(
                f"Fingerprint (total {total_amount}, date {qbo_date}, project {project_id}) "
                f"re-adopting identity-lost local Invoice {matches[0].id} for QBO invoice "
                f"{qbo_id} (number diverged via a human edit)."
            )
            return matches[0]
        if len(matches) > 1:
            logger.warning(
                f"Fingerprint (total {total_amount}, date {qbo_date}, project {project_id}) "
                f"matched {len(matches)} adoptable local invoices for QBO invoice "
                f"{qbo_id}; ambiguous -- NOT adopting, will suffix-CREATE."
            )
        return None

    def _has_qbo_line_provenance(self, invoice_id: int) -> bool:
        """True if any of the invoice's line items carries an InvoiceLineItemInvoiceLine
        mapping — i.e. it was materialized by a QBO pull. A local-origin/manual invoice
        has none (and since QBO invoice push is disabled, manual invoices are exactly the
        ones that stay unstamped), so this separates an identity-LOST QBO invoice — the
        only thing we may re-adopt — from a distinct manual invoice that merely shares the
        header fingerprint. Closes the Pass-1 P2 false-adopt path.

        Cost is confined to the tiny set of fingerprint-matched candidates. Residual: if a
        QBO invoice's line mappings were ALSO wiped (or it had no lines), provenance can't
        be proven and the caller falls through to the safe suffix-CREATE — the accepted,
        VISIBLE degradation (never a silent wrong bind onto a human's manual invoice).
        """
        from entities.invoice_line_item.business.service import InvoiceLineItemService

        line_items = InvoiceLineItemService().read_by_invoice_id(int(invoice_id))
        for li in line_items or []:
            if self.line_mapping_repo.read_by_invoice_line_item_id(int(li.id)):
                return True
        return False

    @staticmethod
    def _header_fingerprint_matches(
        total_amount, invoice_date, other_total, other_invoice_date
    ) -> bool:
        """True if an incoming QBO header (total + txn_date) matches a local invoice's
        header within the adopt fingerprint: both totals present AND within $0.01 AND the
        same NON-BLANK txn date (first 10 chars). Single source for the money-tolerance +
        date rule shared by the number-matched adopt guard (`same_invoice`) and the
        fingerprint-fallback scan, so the $0.01 / date[:10] rule lives in exactly one place.
        Pure; no I/O.
        """
        qbo_date = str(invoice_date or "").strip()[:10]
        other_date = str(other_invoice_date or "").strip()[:10]
        if total_amount is None or other_total is None or not qbo_date:
            return False
        if qbo_date != other_date:
            return False
        return abs(Decimal(str(total_amount)) - Decimal(str(other_total))) <= Decimal("0.01")

    def _record_duplicate_qbo_invoice_issue(self, *, qbo_invoice: QboInvoice, local_invoice) -> None:
        """Adopt-time theft-guard conflict (U-356): the candidate resolved as this
        QBO invoice's identity-lost local counterpart turned out, on the fresh
        under-lock re-read, to already carry a DIFFERENT QBO identity. Records
        the same `invoice_identity_conflict` DriftType the retired mapping-table
        recorder emitted; `stamp_dbo_identity_with_lock` raises right after.
        `realm_id` is the caller's effective realm (U-350 P2), never the possibly
        stale row value."""
        record_duplicate_identity_conflict(
            self.reconciliation_repo,
            drift_type="invoice_identity_conflict",
            entity_type="Invoice",
            qbo_id=str(qbo_invoice.qbo_id) if qbo_invoice.qbo_id else None,
            realm_id=qbo_invoice.realm_id or "",
            entity_public_id=(
                str(local_invoice.public_id) if getattr(local_invoice, "public_id", None) else None
            ),
            details=(
                f"Invoice {local_invoice.id} (number "
                f"{getattr(local_invoice, 'invoice_number', None)!r}) was resolved as the "
                f"identity-lost local counterpart of QboInvoice {qbo_invoice.id} "
                f"(QboId={qbo_invoice.qbo_id}, RealmId={qbo_invoice.realm_id}) but by stamp time "
                f"already carried "
                + build_duplicate_qbo_identity_conflict_desc(
                    existing_qbo_id=local_invoice.qbo_id,
                    incoming_qbo_id=qbo_invoice.qbo_id,
                    existing_realm_id=getattr(local_invoice, "realm_id", None),
                    incoming_realm_id=qbo_invoice.realm_id,
                )
                + ". Not re-pointed — investigate which side is correct."
            ),
        )

    def _record_orphan_header_issue(self, *, invoice: Invoice, qbo_invoice: QboInvoice, exc: Exception) -> None:
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphan_invoice_header",
            entity_type="Invoice",
            entity_public_id=str(invoice.public_id) if invoice.public_id else None,
            qbo_id=str(qbo_invoice.qbo_id) if qbo_invoice.qbo_id else None,
            realm_id=qbo_invoice.realm_id or "",
            details=(
                f"Compensating rollback failed to delete orphan Invoice {invoice.id} "
                f"({invoice.public_id}): {exc}. Header blocks re-pull until manually resolved."
            ),
        )

    # One of FOUR near-identical QBO customer-ref -> Project resolvers (invoice /
    # purchase / vendorcredit / bill). All four are realm-scoped as of U-060; they
    # still diverge on heal (invoice only) and caching (invoice + purchase only).
    # Lift into one shared resolver when multi-realm lands — see TODO.md.
    def _get_project_public_id(self, qbo_customer_ref_value: str, realm_id: Optional[str] = None) -> Optional[str]:
        """
        Get the Project public_id from QBO customer reference value.
        Results are cached to avoid repeated DB lookups for the same customer.

        U-311 (Wave-5, scope expansion — this resolver was missed by
        `docs/design/wave5.md` §4's own consumer sweep; found + fixed in-unit,
        same class of gap Codex caught for U-312's `duplicate_project` column):
        tries dbo.Project's native QboId/RealmId directly first via
        `verify_identity_dbo_only` before falling back to the QboCustomer ->
        heal-by-name path below. `qbo.Customer` (the raw staging mirror, NOT
        one of Wave 5's 3 retiring junction tables — see wave5.md's own scope
        note) stays as the lookup key for the heal fallback; only the
        `qbo.CustomerProject` mapping-table hop is removed.

        A direct hit that FAILS verification (the identity was reassigned
        between the read and this call) returns None outright, same as the
        other 6 repointed sites — it must NOT fall through to the heal-by-name
        path below (Codex xhigh P1, U-311): heal-by-name is keyed purely on
        QboCustomer.DisplayName, independent of the failed verify, so falling
        through could silently bind the invoice line to a DIFFERENT Project
        than the one verify just refused to trust, and `heal_missing_mapping`
        can itself create/stamp a mapping — exactly the kind of action a
        refused verify exists to prevent. The heal fallback is reserved for a
        genuine MISS (no direct hit at all).

        Args:
            qbo_customer_ref_value: QBO customer reference value (QBO Customer ID)
            realm_id: Optional QBO realm ID for realm-scoped customer lookup

        Returns:
            str: Project public_id or None
        """
        if not qbo_customer_ref_value:
            return None

        cache_key = (realm_id, qbo_customer_ref_value)

        # Return cached result if available (including None for known misses)
        if cache_key in self._project_cache:
            return self._project_cache[cache_key]

        direct_project = self.project_service.read_by_qbo_identity(qbo_customer_ref_value, realm_id)
        if direct_project:
            verified_qbo_id = verify_identity_dbo_only(
                direct_project,
                read_direct_by_qbo_identity=self.project_service.read_by_qbo_identity,
            )
            if verified_qbo_id:
                logger.debug(f"Found Project {direct_project.id} via direct dbo QboId lookup")
                self._project_cache[cache_key] = direct_project.public_id
                return direct_project.public_id
            logger.warning(
                f"Project {direct_project.id} failed dbo-only identity verification for "
                f"QboCustomer ref {qbo_customer_ref_value} — refusing to trust it or fall "
                f"through to heal-by-name."
            )
            self._project_cache[cache_key] = None
            return None

        # Genuine miss (no direct hit at all). Find the QboCustomer staging row
        # by qbo_id (qbo.Customer — not retiring) so the heal-by-name fallback
        # below has a QboCustomer to bind from.
        if realm_id:
            qbo_customer = self.qbo_customer_repo.read_by_qbo_id_and_realm_id(qbo_customer_ref_value, realm_id)
        else:
            qbo_customer = self.qbo_customer_repo.read_by_qbo_id(qbo_customer_ref_value)
        if not qbo_customer:
            logger.warning(f"QboCustomer not found for qbo_id: {qbo_customer_ref_value}")
            self._project_cache[cache_key] = None
            return None

        # Auto-heal by binding an existing local Project by name (never
        # creating one). Closes the no-invoice window where a (possibly
        # transient) not-yet-dbo-stamped Project fails the entire invoice
        # pull. If it genuinely cannot resolve a local Project, fall through
        # to return None so the caller still raises (fail loud — never
        # silently skip the project binding).
        from integrations.intuit.qbo.customer.connector.project.business.service import (
            CustomerProjectConnector,
        )
        healed_project = CustomerProjectConnector().heal_missing_mapping(qbo_customer)
        if healed_project:
            self._project_cache[cache_key] = healed_project.public_id
            return healed_project.public_id
        logger.warning(f'Project not resolvable (dbo miss, and unhealable by name) for QboCustomer ID: {qbo_customer.id}')
        self._project_cache[cache_key] = None
        return None

    def _sync_line_items(
        self,
        invoice_id: int,
        invoice_public_id: str,
        qbo_invoice_lines: List[QboInvoiceLine],
        realm_id: Optional[str] = None,
    ) -> None:
        """
        Sync invoice line items to InvoiceLineItem module.

        Args:
            invoice_id: Database ID of the Invoice
            invoice_public_id: Public ID of the Invoice
            qbo_invoice_lines: List of QboInvoiceLine records
            realm_id: QBO realm ID from the parent staging header
        """
        if not qbo_invoice_lines:
            return

        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.service import InvoiceLineItemConnector

        line_connector = InvoiceLineItemConnector(
            line_mapping_cache=self._line_mapping_cache,
            line_item_cache=self._line_item_cache,
            caches_preloaded=self._caches_preloaded,
        )

        for qbo_line in qbo_invoice_lines:
            try:
                line_connector.sync_from_qbo_invoice_line(
                    invoice_id, invoice_public_id, qbo_line, realm_id
                )
            except Exception as e:
                # LOAD-BEARING: swallowing (not raising) is what keeps the NEW-invoice path
                # zombie-safe without a compensating rollback (see the create-path note above /
                # U-006). Do not change this to raise without adding rollback_orphan_header there.
                logger.error(f"Failed to sync QboInvoiceLine {qbo_line.id} to InvoiceLineItem: {e}")

    # ------------------------------------------------------------------
    # Local → QBO direction
    # ------------------------------------------------------------------

    def sync_to_qbo_invoice(self, invoice: Invoice, realm_id: str):
        """
        Push a local Invoice to QuickBooks Online.

        Creates the invoice in QBO if not yet synced. If already synced
        (dbo.Invoice.QboId/RealmId — the sole identity store since U-356),
        updates the existing QBO invoice with the current local data.

        DORMANT PATH (U-356): Invoice push is disabled — `complete_invoice`
        never enqueues, the `/sync/invoice/{id}/qbo` router is a no-op stub,
        and the `sync_invoice_to_qbo` outbox Kind has had ZERO rows ever. The
        retired qbo.InvoiceInvoice reads here were repointed mechanically onto
        dbo-native identity (mirroring `BillBillConnector.sync_to_qbo_bill`,
        U-355) so the path stays coherent and mapping-free; unlike Bill's live
        push there was no traffic to equivalence-prove the repoint against.

        Args:
            invoice: Local Invoice record
            realm_id: QBO realm ID

        Returns:
            QboInvoice: The local QboInvoice mirror record

        Raises:
            ValueError: If CustomerRef cannot be resolved or no valid lines exist
        """
        from integrations.intuit.qbo.invoice.persistence.repo import QboInvoiceRepository, QboInvoiceLineRepository
        from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient
        from integrations.intuit.qbo.invoice.external.schemas import (
            QboInvoiceCreate as QboInvoiceCreateSchema,
            QboInvoiceUpdate as QboInvoiceUpdateSchema,
            QboReferenceType,
        )
        from entities.invoice_line_item.business.service import InvoiceLineItemService

        qbo_invoice_repo = QboInvoiceRepository()
        qbo_invoice_line_repo = QboInvoiceLineRepository()

        invoice_id = coerce_id(invoice.id)

        # Resolve QBO CustomerRef from project_id
        customer_ref = self._get_qbo_customer_ref(invoice.project_id)
        if not customer_ref:
            raise ValueError(f"No QBO customer mapping found for project_id: {invoice.project_id}")

        # QboHttpClient resolves and refreshes the access token lazily on every request,
        # so we don't need an upfront auth check here.

        # Fetch ReimburseCharge lookup so we can link invoice lines to the QBO
        # intermediate records that QBO created when bills/purchases were marked Billable.
        # Falls back to Bill/Purchase TxnType if the lookup cannot be built.
        reimburse_charge_lookup = {}
        try:
            reimburse_charge_lookup = self._build_reimburse_charge_lookup(
                customer_ref_value=customer_ref.value,
                realm_id=realm_id,
            )
            logger.info(f"ReimburseCharge lookup: {len(reimburse_charge_lookup)} entries for customer {customer_ref.value}")
        except Exception as e:
            logger.warning(f"Could not build ReimburseCharge lookup (LinkedTxn will fall back to Bill/Purchase): {e}")

        # Get invoice line items
        invoice_line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id)

        # Build QBO line items
        qbo_lines = []
        skipped_lines = []
        invoice_linked_txn_rc_ids = []  # ReimburseCharge IDs for invoice-level LinkedTxn
        for line_item in invoice_line_items:
            qbo_line = self._build_qbo_invoice_line(line_item, reimburse_charge_lookup, realm_id)
            if qbo_line:
                linked = qbo_line.linked_txn[0] if qbo_line.linked_txn else None
                logger.info(
                    f"InvoiceLineItem {line_item.id} (source={line_item.source_type}) → "
                    f"LinkedTxn={linked.txn_type + ':' + linked.txn_id if linked else 'NONE'}"
                )
                # Collect ReimburseCharge IDs for the invoice-level LinkedTxn array
                if linked and linked.txn_type == "ReimburseCharge" and linked.txn_id:
                    if linked.txn_id not in invoice_linked_txn_rc_ids:
                        invoice_linked_txn_rc_ids.append(linked.txn_id)
                qbo_lines.append(qbo_line)
            else:
                skipped_lines.append(line_item.id)

        # Invoice-level LinkedTxn: one entry per ReimburseCharge (TxnId = rc_id)
        from integrations.intuit.qbo.invoice.external.schemas import QboLinkedTxn as QboLinkedTxnSchema
        invoice_linked_txns = [
            QboLinkedTxnSchema(txn_id=rc_id, txn_type="ReimburseCharge")
            for rc_id in invoice_linked_txn_rc_ids
        ] or None

        if not qbo_lines:
            if invoice_line_items:
                raise ValueError(
                    f"Invoice {invoice_id} has {len(invoice_line_items)} line item(s) but none could be "
                    f"built for QBO. Manual lines require a SubCostCode mapped to a QBO Item. "
                    f"Skipped line item IDs: {skipped_lines}"
                )
            raise ValueError("Invoice has no line items. QBO requires at least one line item.")

        # UPDATE path — invoice already synced to QBO (dbo-native identity, verified;
        # U-356). `invoice` may come from a by-public-id read (the outbox worker's
        # _handle_sync_invoice) and ReadInvoiceByPublicId does NOT select
        # QboId/RealmId (only ReadInvoiceById / ReadInvoiceByQboIdAndRealmId do —
        # see entities/invoice/business/model.py), so the identity is re-read by
        # id here rather than trusted off the caller's row. A truthy qbo_id is
        # then re-verified via verify_identity_dbo_only before being used for the
        # qbo.Invoice staging-cache lookup — a stale/reassigned identity must never
        # resolve to the WRONG cached QboInvoice (mirrors sync_to_qbo_bill, U-355).
        from integrations.intuit.qbo.base.errors import QboError, QboNotFoundError
        current = self.invoice_service.read_by_id(invoice_id)
        if current is None:
            # Deleted locally between the caller's by-public-id read and this
            # re-read. A fall-through would take the CREATE branch (the caller's
            # row carries no qbo_id) and mint a QBO Invoice for a row that no
            # longer exists — refuse instead (same shape as the outbox handler's
            # own not-found raise).
            raise ValueError(
                f"Invoice {invoice_id} no longer exists locally (deleted between the caller's read "
                f"and the push); refusing to create a QBO Invoice for a missing row."
            )
        local_qbo_invoice = None
        if getattr(current, "qbo_id", None):
            verified_qbo_id = verify_identity_dbo_only(
                current, read_direct_by_qbo_identity=self.invoice_service.read_by_qbo_identity,
            )
            if not verified_qbo_id:
                record_mapping_issue(
                    self.reconciliation_repo,
                    drift_type="invoice_identity_conflict",
                    entity_type="Invoice",
                    entity_public_id=str(invoice.public_id) if invoice.public_id else None,
                    qbo_id=str(current.qbo_id),
                    realm_id=realm_id or "",
                    details=(
                        f"Push refused for Invoice {invoice_id}: dbo.Invoice.QboId={current.qbo_id!r} "
                        f"no longer resolves back to this Invoice on a fresh dbo-only read (see "
                        f"verify_identity_dbo_only) — the identity was reassigned to a different "
                        f"Invoice. Investigate which side is correct."
                    ),
                )
                raise ValueError(
                    f"Invoice {invoice_id} carries a QBO identity (qbo_id={current.qbo_id!r}) that no "
                    f"longer resolves back to it on a fresh dbo-only read — refusing to push a "
                    f"possibly stolen/reassigned identity. See verify_identity_dbo_only."
                )
            local_qbo_invoice = qbo_invoice_repo.read_by_qbo_id_and_realm_id(
                verified_qbo_id, current.realm_id
            )
            if not local_qbo_invoice or not local_qbo_invoice.qbo_id:
                # dbo.Invoice carries a verified QboId but the local qbo.Invoice staging
                # cache has no row for it — a genuine data-integrity anomaly (the stamp
                # and the staging-cache write happen together at the end of this
                # method), not the ordinary "never pushed" case. Refuse rather than
                # risk pushing a DUPLICATE Invoice into QBO.
                record_mapping_issue(
                    self.reconciliation_repo,
                    drift_type="invoice_staging_row_missing",
                    entity_type="Invoice",
                    entity_public_id=str(invoice.public_id) if invoice.public_id else None,
                    qbo_id=str(verified_qbo_id),
                    realm_id=realm_id or "",
                    details=(
                        f"Push refused for Invoice {invoice_id}: dbo.Invoice carries a verified QboId "
                        f"({verified_qbo_id!r}, realm_id={current.realm_id!r}) but no local qbo.Invoice "
                        f"staging row exists for it. Investigate the missing staging row before "
                        f"retrying — pushing again risks creating a duplicate Invoice in QBO."
                    ),
                )
                raise ValueError(
                    f"Invoice {invoice_id} carries a verified QBO identity (qbo_id={verified_qbo_id!r}, "
                    f"realm_id={current.realm_id!r}) but no local qbo.Invoice staging row exists for "
                    f"it — refusing to push a possible duplicate. Investigate the missing staging row "
                    f"before retrying."
                )
        if local_qbo_invoice:
            logger.info(f"Updating existing QBO Invoice {local_qbo_invoice.qbo_id} for local Invoice {invoice_id}")

            # Fetch fresh SyncToken. If the invoice was deleted in QBO, clear the stale mapping
            # and fall through to the CREATE path below.
            try:
                with QboInvoiceClient(realm_id=realm_id) as client:
                    fresh = client.get_invoice(local_qbo_invoice.qbo_id)
                    qbo_invoice_update = QboInvoiceUpdateSchema(
                        id=local_qbo_invoice.qbo_id,
                        sync_token=fresh.sync_token,
                        customer_ref=QboReferenceType(value=customer_ref.value, name=customer_ref.name),
                        txn_date=invoice.invoice_date[:10] if invoice.invoice_date else None,
                        due_date=invoice.due_date[:10] if invoice.due_date else None,
                        doc_number=invoice.invoice_number,
                        private_note=invoice.memo,
                        line=qbo_lines,
                        linked_txn=invoice_linked_txns,
                    )
                    updated = client.update_invoice(qbo_invoice_update)

                logger.info(f"Updated QBO Invoice {updated.id} (SyncToken={updated.sync_token})")

                qbo_invoice_repo.update_by_qbo_id(
                    qbo_id=local_qbo_invoice.qbo_id,
                    row_version=local_qbo_invoice.row_version_bytes,
                    sync_token=updated.sync_token,
                    realm_id=realm_id,
                    customer_ref_value=customer_ref.value,
                    customer_ref_name=customer_ref.name,
                    txn_date=updated.txn_date,
                    due_date=updated.due_date,
                    ship_date=None,
                    doc_number=updated.doc_number,
                    private_note=updated.private_note,
                    customer_memo=None,
                    bill_email=None,
                    total_amt=updated.total_amt,
                    balance=updated.balance,
                    deposit=None,
                    sales_term_ref_value=None,
                    sales_term_ref_name=None,
                    currency_ref_value=updated.currency_ref.value if updated.currency_ref else None,
                    currency_ref_name=updated.currency_ref.name if updated.currency_ref else None,
                    exchange_rate=updated.exchange_rate,
                    department_ref_value=None,
                    department_ref_name=None,
                    class_ref_value=None,
                    class_ref_name=None,
                    ship_method_ref_value=None,
                    ship_method_ref_name=None,
                    tracking_num=None,
                    print_status=None,
                    email_status=None,
                    allow_online_ach_payment=None,
                    allow_online_credit_card_payment=None,
                    apply_tax_after_discount=None,
                    global_tax_calculation=None,
                )
                return qbo_invoice_repo.read_by_id(local_qbo_invoice.id)

            except QboError as e:
                msg = str(e).lower()
                if "not found" in msg or "inactive" in msg:
                    # Nothing to clear (U-356): the CREATE path below re-stamps
                    # dbo.Invoice.QboId/RealmId with the NEW QBO id via
                    # SetInvoiceQboIdentity's unconditional non-NULL overwrite,
                    # exactly where the retired mapping row used to be deleted
                    # and re-created. The stale qbo.Invoice staging row is left
                    # in place, as before.
                    logger.warning(
                        f"QBO Invoice {local_qbo_invoice.qbo_id} is gone or inactive in QBO. "
                        f"Re-creating; dbo identity is re-stamped below. Error: {e}"
                    )
                else:
                    raise

        # CREATE path — first sync
        logger.info(f"Creating Invoice in QBO for local Invoice {invoice_id}: doc_number={invoice.invoice_number}")

        qbo_invoice_create = QboInvoiceCreateSchema(
            customer_ref=QboReferenceType(value=customer_ref.value, name=customer_ref.name),
            txn_date=invoice.invoice_date[:10] if invoice.invoice_date else None,
            due_date=invoice.due_date[:10] if invoice.due_date else None,
            doc_number=invoice.invoice_number,
            private_note=invoice.memo,
            line=qbo_lines,
            linked_txn=invoice_linked_txns,
        )

        with QboInvoiceClient(realm_id=realm_id) as client:
            created_invoice = client.create_invoice(qbo_invoice_create)

        logger.info(f"Created QBO Invoice {created_invoice.id} (SyncToken={created_invoice.sync_token})")

        # Store local QboInvoice mirror — reuse on retry if a prior attempt already persisted it
        existing_local_qbo_invoice = qbo_invoice_repo.read_by_qbo_id_and_realm_id(
            created_invoice.id, realm_id
        )
        if existing_local_qbo_invoice:
            # U-356: dbo.Invoice.QboId/RealmId (unique per realm) is the sole
            # "already bound" store — a DIFFERENT local Invoice holding this
            # identity means this retry must not stamp onto it.
            holder = self.invoice_service.read_by_qbo_identity(created_invoice.id, realm_id)
            if holder is not None and coerce_id(holder.id) != invoice_id:
                raise ValueError(
                    f"QboInvoice {existing_local_qbo_invoice.id} (QboId={created_invoice.id}) is already mapped to a "
                    f"different Invoice {holder.id} (dbo.Invoice.QboId/RealmId); cannot push Invoice {invoice_id} "
                    f"onto it. This indicates a race between this push retry and an independent pull, or a "
                    f"duplicate local Invoice. Manual investigation required."
                )
            local_qbo_invoice = existing_local_qbo_invoice
            logger.info(
                f"QboInvoice already stored locally for QboId {created_invoice.id} "
                f"(retry after prior partial success) — reusing local record {local_qbo_invoice.id}"
            )
        else:
            local_qbo_invoice = qbo_invoice_repo.create(
                qbo_id=created_invoice.id,
                sync_token=created_invoice.sync_token,
                realm_id=realm_id,
                customer_ref_value=customer_ref.value,
                customer_ref_name=customer_ref.name,
                txn_date=created_invoice.txn_date,
                due_date=created_invoice.due_date,
                ship_date=None,
                doc_number=created_invoice.doc_number,
                private_note=created_invoice.private_note,
                customer_memo=None,
                bill_email=None,
                total_amt=created_invoice.total_amt,
                balance=created_invoice.balance,
                deposit=None,
                sales_term_ref_value=None,
                sales_term_ref_name=None,
                currency_ref_value=created_invoice.currency_ref.value if created_invoice.currency_ref else None,
                currency_ref_name=created_invoice.currency_ref.name if created_invoice.currency_ref else None,
                exchange_rate=created_invoice.exchange_rate,
                department_ref_value=None,
                department_ref_name=None,
                class_ref_value=None,
                class_ref_name=None,
                ship_method_ref_value=None,
                ship_method_ref_name=None,
                tracking_num=None,
                print_status=None,
                email_status=None,
                allow_online_ach_payment=None,
                allow_online_credit_card_payment=None,
                apply_tax_after_discount=None,
                global_tax_calculation=None,
            )
            logger.info(f"Stored local QboInvoice {local_qbo_invoice.id}")

        # Store local QboInvoiceLine mirrors
        if created_invoice.line:
            existing_lines_by_qbo_line_id = {
                line.qbo_line_id: line
                for line in qbo_invoice_line_repo.read_by_qbo_invoice_id(local_qbo_invoice.id)
                if line.qbo_line_id
            }

            for qbo_line in created_invoice.line:
                if qbo_line.detail_type != "SalesItemLineDetail":
                    continue
                try:
                    if qbo_line.id and existing_lines_by_qbo_line_id.get(qbo_line.id):
                        continue
                    detail = qbo_line.sales_item_line_detail
                    qbo_invoice_line_repo.create(
                        qbo_invoice_id=local_qbo_invoice.id,
                        qbo_line_id=qbo_line.id,
                        line_num=qbo_line.line_num,
                        description=qbo_line.description,
                        amount=qbo_line.amount,
                        detail_type=qbo_line.detail_type,
                        item_ref_value=detail.item_ref.value if detail and detail.item_ref else None,
                        item_ref_name=detail.item_ref.name if detail and detail.item_ref else None,
                        class_ref_value=detail.class_ref.value if detail and detail.class_ref else None,
                        class_ref_name=detail.class_ref.name if detail and detail.class_ref else None,
                        qty=detail.qty if detail else None,
                        unit_price=detail.unit_price if detail else None,
                        tax_code_ref_value=detail.tax_code_ref.value if detail and detail.tax_code_ref else None,
                        tax_code_ref_name=detail.tax_code_ref.name if detail and detail.tax_code_ref else None,
                        service_date=detail.service_date if detail else None,
                        discount_rate=None,
                        discount_amt=None,
                    )
                except Exception as e:
                    logger.warning(f"Could not store QboInvoiceLine for QBO line {qbo_line.id}: {e}")

        # Stamp dbo-native identity — the sole identity store (U-356); there is
        # no mapping row to write. SetInvoiceQboIdentity's theft-clear + the
        # UQ_Invoice_QboId_RealmId index guarantee at most one holder. The local
        # qbo.Invoice mirror carries (qbo_id, realm_id, sync_token) in the same
        # attribute shape as a pulled QboInvoice, so the one shared stamp call
        # serves the push too (its realm_id is always this push's `realm_id` —
        # it was created or looked up with it above).
        self._set_invoice_qbo_identity(invoice_id, local_qbo_invoice)
        logger.info(f"Stamped dbo identity: Invoice {invoice_id} <-> QBO Invoice {local_qbo_invoice.qbo_id}")

        return local_qbo_invoice

    def _get_qbo_customer_ref(self, project_id: int):
        """
        Resolve local project_id to a QBO CustomerRef (value=qbo_id, name=name).

        U-276 (Phase-4 pilot): reads dbo.Project.Name/.QboId directly (native
        since U-238a) instead of hopping qbo.CustomerProject -> qbo.Customer
        for DisplayName. Returns None if the Project has never been QBO-synced
        (no QboId stamped) — same "can't resolve" contract as before. U-311
        (Wave-5 Option A): the dbo identity is verified via
        `verify_identity_dbo_only` (a plain re-read of dbo.Project by its own
        (qbo_id, realm_id), trusted only when it still resolves back to this
        same row) — dbo-internal uniqueness alone doesn't guarantee the row
        wasn't reassigned between the read above and this call.
        """
        from integrations.intuit.qbo.invoice.external.schemas import QboReferenceType

        if not project_id:
            return None

        project = self.project_service.read_by_id(project_id)
        if not project or not project.qbo_id:
            logger.warning(f"Project {project_id} has no QBO identity (QboId) stamped")
            return None

        verified_qbo_id = verify_identity_dbo_only(
            project,
            read_direct_by_qbo_identity=self.project_service.read_by_qbo_identity,
        )
        if not verified_qbo_id:
            return None

        return QboReferenceType(value=verified_qbo_id, name=project.name)

    def _build_reimburse_charge_lookup(self, customer_ref_value: str, realm_id: str, access_token: Optional[str] = None) -> dict:
        """
        Query QBO for ReimburseCharge records for a customer and build a lookup dict.

        Each ReimburseCharge carries a LinkedTxn back to its source Bill/Purchase. We
        capture both so the invoice line can be built with the correct QBO payload:
            Line.LinkedTxn[0].TxnId       = source Purchase/Bill QBO ID
            Line.LinkedTxn[0].TxnType     = "ReimburseCharge"
            Line.LinkedTxn[0].TxnLineId   = ReimburseCharge ID

        Returns:
            dict: {(qbo_item_id_str, amount_rounded_str): {"rc_id": str, "source_txn_id": str|None}}
        """
        from integrations.intuit.qbo.invoice.external.client import QboInvoiceClient

        lookup = {}
        with QboInvoiceClient(realm_id=realm_id) as client:
            records = client.query_reimburse_charges(customer_ref_value)
            for rc in records:
                rc_id = rc.get("Id")
                amount = rc.get("Amount")

                # Extract the source Purchase/Bill ID from the ReimburseCharge's own LinkedTxn
                source_txn_id = None
                rc_linked = rc.get("LinkedTxn", [])
                if isinstance(rc_linked, dict):
                    rc_linked = [rc_linked]
                for lt in rc_linked:
                    if lt.get("TxnType") in ("Purchase", "Bill"):
                        source_txn_id = str(lt.get("TxnId")) if lt.get("TxnId") else None
                        break

                lines = rc.get("Line", [])
                if isinstance(lines, dict):
                    lines = [lines]
                for line in lines:
                    detail = line.get("ReimburseLineDetail", {})
                    item_ref = detail.get("ItemRef", {})
                    item_ref_value = item_ref.get("value")
                    if rc_id and amount is not None and item_ref_value:
                        key = (str(item_ref_value), str(round(float(amount), 2)))
                        lookup[key] = {"rc_id": str(rc_id), "source_txn_id": source_txn_id}

        return lookup

    def _build_qbo_invoice_line(self, line_item, reimburse_charge_lookup: dict = None, realm_id: Optional[str] = None):
        """
        Build a QBO SalesItemLine from a local InvoiceLineItem.

        Returns None if the line cannot be resolved (no amount, no ItemRef).
        """
        from integrations.intuit.qbo.invoice.external.schemas import (
            QboInvoiceLine as QboInvoiceLineSchema,
            QboSalesItemLineDetail,
            QboReferenceType,
        )

        # Amount charged on the invoice: prefer price (billable), fall back to cost amount
        amount = line_item.price if line_item.price is not None else line_item.amount
        if amount is None:
            logger.warning(f"InvoiceLineItem {line_item.id} has no price or amount, skipping")
            return None

        # Resolve ItemRef — required by QBO for SalesItemLine
        item_ref = self._get_qbo_item_ref_for_line(line_item, realm_id)
        if not item_ref:
            logger.warning(
                f"InvoiceLineItem {line_item.id} (source_type={line_item.source_type}) "
                f"has no QBO Item mapping, skipping"
            )
            return None

        # QBO requires UnitPrice + Qty + TaxCodeRef on every SalesItemLine to properly
        # link the invoice line to its ReimburseCharge (flipping BillableStatus to
        # HasBeenBilled on the source Bill/Purchase). Without these, QBO accepts the
        # LinkedTxn reference but silently discards it and recreates the ReimburseCharge.
        # For Manual lines use the stored quantity/rate; for source-backed lines default
        # to Qty=1 / UnitPrice=amount (matching what QBO stores on the ReimburseCharge).
        if line_item.source_type == "Manual":
            qty = line_item.quantity if line_item.quantity is not None else Decimal("1")
            unit_price = line_item.rate if line_item.rate is not None else amount
        else:
            qty = Decimal("1")
            unit_price = amount

        detail = QboSalesItemLineDetail(
            item_ref=item_ref,
            qty=qty,
            unit_price=unit_price,
            tax_code_ref=QboReferenceType(value="NON"),
        )

        # Resolve LinkedTxn — link to the QBO ReimburseCharge so QBO recognises the
        # line as covering that billable transaction (flips HasBeenInvoiced on the
        # ReimburseCharge and removes it from "Suggested Transactions").
        linked_txn = self._resolve_linked_txn_for_line(line_item, reimburse_charge_lookup, realm_id)

        return QboInvoiceLineSchema(
            description=line_item.description,
            amount=amount,
            detail_type="SalesItemLineDetail",
            sales_item_line_detail=detail,
            linked_txn=[linked_txn] if linked_txn else None,
        )

    def _get_qbo_item_ref_for_line(self, line_item, realm_id: Optional[str] = None):
        """
        Resolve the QBO ItemRef for a line item by walking:
          Manual           → InvoiceLineItem.sub_cost_code_id
          BillLineItem     → BillLineItem.sub_cost_code_id
          ExpenseLineItem  → ExpenseLineItem.sub_cost_code_id
          BillCreditLineItem → BillCreditLineItem.sub_cost_code_id

        Then (U-307b): sub_cost_code_id -> dbo-native SubCostCode.QboId direct via
        cost_code_resolver.resolve_qbo_item_ref -- no qbo.Item hop, realm-verified
        (see that module for the resolution/realm-matching contract).
        """
        from integrations.intuit.qbo.invoice.external.schemas import QboReferenceType

        sub_cost_code_id = None

        if line_item.source_type == "Manual":
            sub_cost_code_id = line_item.sub_cost_code_id

        elif line_item.source_type == "BillLineItem" and line_item.bill_line_item_id:
            from entities.bill_line_item.business.service import BillLineItemService
            bill_li = BillLineItemService().read_by_id(line_item.bill_line_item_id)
            sub_cost_code_id = bill_li.sub_cost_code_id if bill_li else None

        elif line_item.source_type == "ExpenseLineItem" and line_item.expense_line_item_id:
            from entities.expense_line_item.business.service import ExpenseLineItemService
            expense_li = ExpenseLineItemService().read_by_id(line_item.expense_line_item_id)
            sub_cost_code_id = expense_li.sub_cost_code_id if expense_li else None

        elif line_item.source_type == "BillCreditLineItem" and line_item.bill_credit_line_item_id:
            from entities.bill_credit_line_item.business.service import BillCreditLineItemService
            credit_li = BillCreditLineItemService().read_by_id(line_item.bill_credit_line_item_id)
            sub_cost_code_id = credit_li.sub_cost_code_id if credit_li else None

        if not sub_cost_code_id:
            return None

        item_ref = resolve_qbo_item_ref(
            sub_cost_code_id,
            realm_id,
            sub_cost_code_service=self.sub_cost_code_service,
        )
        if item_ref is None:
            return None

        return QboReferenceType(value=item_ref.value, name=item_ref.name)

    def _resolve_linked_txn_for_line(self, line_item, reimburse_charge_lookup: dict = None, realm_id: Optional[str] = None):
        """
        Resolve the QBO LinkedTxn for a source-backed line item.

        Prefers ReimburseCharge linking when a lookup dict is provided, which is the
        correct QBO mechanism for linking invoice lines back to billable transactions
        (causes QBO to flip HasBeenInvoiced on the ReimburseCharge and removes the
        item from "Suggested Transactions"). Falls back to Bill/Purchase/VendorCredit
        TxnType if the ReimburseCharge cannot be resolved.

        Walk the mapping chain:
          BillLineItem     → BillBill → QboBill.qbo_id → (ReimburseCharge or "Bill")
          ExpenseLineItem  → dbo.Expense.QboId (U-354 dbo-native) → "Purchase"
          BillCreditLineItem → dbo.BillCredit.QboId (U-353 dbo-native) → "VendorCredit"
          Manual           → None (no linked transaction)

        Returns None if the chain cannot be resolved or for Manual lines.
        """
        from integrations.intuit.qbo.invoice.external.schemas import QboLinkedTxn

        try:
            # For all source-backed lines, try ReimburseCharge matching.
            # QBO correctly stores the LinkedTxn in the invoice for both Bill and Expense lines.
            # Bill line BillableStatus is updated separately via _mark_source_bills_as_billed().
            if reimburse_charge_lookup:
                item_ref = self._get_qbo_item_ref_for_line(line_item, realm_id)
                amount = line_item.price if line_item.price is not None else line_item.amount
                if item_ref and amount is not None:
                    key = (str(item_ref.value), str(round(float(amount), 2)))
                    rc_entry = reimburse_charge_lookup.get(key)
                    if rc_entry:
                        rc_id = rc_entry["rc_id"]
                        source_txn_id = rc_entry.get("source_txn_id")
                        # QBO expects: TxnId=ReimburseCharge ID, TxnType="ReimburseCharge", TxnLineId="1"
                        return QboLinkedTxn(
                            txn_id=rc_id,
                            txn_type="ReimburseCharge",
                            txn_line_id="1",
                        )

            elif line_item.source_type == "ExpenseLineItem" and line_item.expense_line_item_id:
                from entities.expense_line_item.business.service import ExpenseLineItemService
                from entities.expense.business.service import ExpenseService

                expense_li = ExpenseLineItemService().read_by_id(line_item.expense_line_item_id)
                if not expense_li or not expense_li.expense_id:
                    return None

                # U-354: dbo.Expense.QboId is the sole identity store — no more
                # qbo.PurchaseExpense mapping row to hop through. read_by_id (not
                # read_by_public_id) — ReadExpenseByPublicId does not select QboId.
                expense = ExpenseService().read_by_id(expense_li.expense_id)
                if not expense or not expense.qbo_id:
                    logger.debug(f"No dbo-native QBO identity for expense_id={expense_li.expense_id}")
                    return None

                return QboLinkedTxn(txn_id=expense.qbo_id, txn_type="Purchase")

            elif line_item.source_type == "BillCreditLineItem" and line_item.bill_credit_line_item_id:
                from entities.bill_credit_line_item.business.service import BillCreditLineItemService
                from entities.bill_credit.business.service import BillCreditService

                credit_li = BillCreditLineItemService().read_by_id(line_item.bill_credit_line_item_id)
                if not credit_li or not credit_li.bill_credit_id:
                    return None

                # U-353: dbo.BillCredit.QboId is the sole identity store — no more
                # qbo.VendorCreditBillCredit mapping row to hop through.
                bill_credit = BillCreditService().read_by_id(credit_li.bill_credit_id)
                if not bill_credit or not bill_credit.qbo_id:
                    logger.debug(f"No dbo-native QBO identity for bill_credit_id={credit_li.bill_credit_id}")
                    return None

                return QboLinkedTxn(txn_id=bill_credit.qbo_id, txn_type="VendorCredit")

        except Exception as e:
            logger.warning(f"Error resolving LinkedTxn for InvoiceLineItem {line_item.id}: {e}")

        return None
