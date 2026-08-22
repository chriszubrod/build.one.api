# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.invoice.connector.invoice_line_item.business.model import InvoiceLineItemInvoiceLine
from integrations.intuit.qbo.invoice.connector.invoice_line_item.persistence.repo import InvoiceLineItemInvoiceLineRepository
from integrations.intuit.qbo.invoice.business.model import QboInvoiceLine
from entities.invoice_line_item.business.service import InvoiceLineItemService
from entities.invoice_line_item.business.model import InvoiceLineItem
from entities.invoice.business.service import InvoiceService
from integrations.intuit.qbo.base.cache_lookup import cached_or_read
from integrations.intuit.qbo.base.identity_drift import stamp_line_identity_or_warn
from integrations.intuit.qbo.base.identity_fastpath import (
    raise_concurrent_write_race,
    run_line_identity_fastpath,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from shared.database import DatabaseConstraintError

logger = logging.getLogger(__name__)


def _stamp_source_provenance_or_warn(repo, *, qbo_invoice_line: QboInvoiceLine, invoice_line_item_id: int, context: str) -> None:
    """Best-effort dbo source-link provenance mirror (U-272). By every call
    site the line item is already committed, so a stamp failure must never
    abort or roll back otherwise-successful sync work — log and move on, same
    non-blocking shape as stamp_line_identity_or_warn (U-238b). NOTE the blast
    radius differs from that precedent: nothing reads InvoiceLineItem.QboId/
    RealmId yet, but ProposeInvoiceSourceLinks/ReadInvoiceSourceLinkLines read
    THIS table live — a swallowed failure here silently drops that one line
    from source-link proposals until it self-heals on a future QBO pull of the
    same invoice (not guaranteed to be soon; the pull is watermark-incremental)."""
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
    """

    def __init__(
        self,
        mapping_repo: Optional[InvoiceLineItemInvoiceLineRepository] = None,
        invoice_line_item_service: Optional[InvoiceLineItemService] = None,
        invoice_service: Optional[InvoiceService] = None,
        line_mapping_cache: Optional[dict] = None,
        line_item_cache: Optional[dict] = None,
        caches_preloaded: bool = False,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the InvoiceLineItemConnector."""
        self.mapping_repo = mapping_repo or InvoiceLineItemInvoiceLineRepository()
        self.invoice_line_item_service = invoice_line_item_service or InvoiceLineItemService()
        self.invoice_service = invoice_service or InvoiceService()
        # Shared cache from the parent connector: {qbo_invoice_line_id: InvoiceLineItemInvoiceLine}
        self._line_mapping_cache: dict = line_mapping_cache if line_mapping_cache is not None else {}
        # Shared cache from the parent connector: {invoice_line_item_id: InvoiceLineItem}
        self._line_item_cache: dict = line_item_cache if line_item_cache is not None else {}
        self._caches_preloaded = caches_preloaded
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_invoice_line(
        self,
        invoice_id: int,
        invoice_public_id: str,
        qbo_invoice_line: QboInvoiceLine,
        realm_id: Optional[str] = None,
    ) -> InvoiceLineItem:
        """
        Sync data from QboInvoiceLine to InvoiceLineItem module.

        This method:
        1. Checks if a mapping exists
        2. Creates or updates the InvoiceLineItem accordingly

        Args:
            invoice_id: Database ID of the Invoice in our system
            invoice_public_id: Public ID of the Invoice
            qbo_invoice_line: QboInvoiceLine record

        Returns:
            InvoiceLineItem: The synced InvoiceLineItem record
        """
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

        def _apply_line_fields(direct: InvoiceLineItem, *, path_label: str) -> Optional[InvoiceLineItem]:
            """
            Write the QBO-derived fields onto an existing, matched
            InvoiceLineItem. Shared by the fast path and the legacy "mapping
            found" branch (U-293b, mirroring BillLineItemConnector's
            _apply_line_fields) — one update-logic site, not two hand-copies
            that could drift. `direct` plays the role the legacy path's own
            `line_item` used to: its CURRENT stored source_type/amount feed
            the source-reset decision exactly as before.
            """
            existing_source_type = getattr(direct, "source_type", None)
            amount_changed = (
                self._normalize_for_fingerprint(direct.amount)
                != self._normalize_for_fingerprint(amount)
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
                # InvoiceLineItem between the read and this UPDATE.
                logger.error(
                    f"Failed to update InvoiceLineItem {direct.id} from QboInvoiceLine "
                    f"{qbo_invoice_line.id} - update_by_public_id returned None "
                    f"(concurrent write race, {path_label})"
                )
                raise_concurrent_write_race(
                    entity_label="InvoiceLineItem", entity_id=direct.id, path_label=path_label
                )
            # U-238b: dbo line identity dual-write (create+update pairing for U-238c).
            stamp_line_identity_or_warn(
                self.invoice_line_item_service.repo,
                id=int(updated.id),
                qbo_id=qbo_invoice_line.qbo_line_id,
                # U-293-dw fold-in: fall back to the row's own already-stamped
                # realm_id when this call's realm_id is empty (see
                # BillLineItemConnector's identical fallback for the full
                # rationale).
                realm_id=realm_id or getattr(direct, "realm_id", None),
                context=f"Updated InvoiceLineItem {updated.id} ({path_label})",
                enforce_realm_pairing=True,
            )
            # U-272: dbo source-link provenance mirror (create+update pairing).
            _stamp_source_provenance_or_warn(
                self.invoice_line_item_service.repo,
                qbo_invoice_line=qbo_invoice_line,
                invoice_line_item_id=int(updated.id),
                context=f"Updated InvoiceLineItem {updated.id} ({path_label})",
            )
            if self._line_item_cache is not None:
                self._line_item_cache[updated.id] = updated
            return updated

        # Memoized on top of the connector-level preload cache: qbo_invoice_line.id
        # is fixed for this whole call, and both the fast path (via
        # resolve_mapping_state, on a MISSING/CONFLICT classification) and the
        # legacy path just below it (unconditionally, on a fast-path miss) ask
        # this exact same question — mirrors BillLineItemConnector's identical
        # memoization, layered on top of (not replacing) the existing
        # caches_preloaded behavior.
        _qbo_invoice_line_mapping_lookup_cache = {}

        def _read_by_qbo_invoice_line_id_cached(qbo_invoice_line_id):
            if qbo_invoice_line_id not in _qbo_invoice_line_mapping_lookup_cache:
                _qbo_invoice_line_mapping_lookup_cache[qbo_invoice_line_id] = cached_or_read(
                    self._caches_preloaded, self._line_mapping_cache, qbo_invoice_line_id,
                    self.mapping_repo.read_by_qbo_invoice_line_id,
                )
            return _qbo_invoice_line_mapping_lookup_cache[qbo_invoice_line_id]

        # U-293b: resolve identity directly against dbo.InvoiceLineItem's
        # native QboId, scoped to this line's own parent Invoice (U-238b),
        # before falling back to the qbo.InvoiceLineItemInvoiceLine
        # mapping-table hop below. Mirrors BillLineItemConnector's U-293 pilot
        # exactly. conflict->RAISE is structural, never a fall-through to the
        # legacy path.
        outcome = run_line_identity_fastpath(
            parent_local_id=invoice_id,
            qbo_line_id=qbo_invoice_line.qbo_line_id,
            external_id=qbo_invoice_line.id,
            entity_label="InvoiceLineItem",
            external_label="QboInvoiceLine",
            read_direct_by_parent_and_qbo_line_id=self.invoice_line_item_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_invoice_line_item_id,
            read_by_external_id=_read_by_qbo_invoice_line_id_cached,
            external_id_attr="qbo_invoice_line_id",
            record_conflict_issue=lambda entity, by_local, by_external: (
                self._raise_line_identity_mapping_conflict_issue(
                    qbo_invoice_line=qbo_invoice_line,
                    dbo_line_id=coerce_id(entity.id),
                    local_side_mapping=by_local,
                    qbo_side_mapping=by_external,
                    realm_id=realm_id,
                )
            ),
            conflict_message=lambda entity: (
                f"InvoiceLineItemInvoiceLine identity conflict for QboInvoiceLine "
                f"{qbo_invoice_line.qbo_line_id} (id={qbo_invoice_line.id}) on Invoice "
                f"{invoice_id}: dbo.InvoiceLineItem {entity.id} already carries this "
                f"identity but the mapping table disagrees. Not auto-repointed; see "
                f"the recorded reconciliation issue. Skipping until a human resolves it."
            ),
            apply_fields=lambda direct: _apply_line_fields(direct, path_label="line fast path"),
        )
        if outcome.hit:
            return outcome.entity

        # Check for existing mapping (use cache when pre-loaded, else fall back to
        # DB). Memoized above — if the fast path already asked this
        # (MISSING/CONFLICT), this is a cache hit, not a second round trip.
        mapping = _read_by_qbo_invoice_line_id_cached(qbo_invoice_line.id)

        if not mapping:
            # Shape B fallback (task #17): content-fingerprint match when QBO
            # regenerates line IDs. Only applies to Manual-sourced invoice lines;
            # Bill/Expense-sourced lines are matched via their source FKs, not
            # by fingerprint, so we skip them here to avoid double-adoption.
            orphan = self._find_and_match_manual_by_fingerprint(
                invoice_id=invoice_id,
                description=description,
                amount=amount,
            )
            if orphan is not None:
                logger.info(
                    f"Adopting orphaned InvoiceLineItem {orphan.id} for QboInvoiceLine "
                    f"{qbo_invoice_line.id} via content fingerprint match"
                )
                try:
                    mapping = self.create_mapping(
                        invoice_line_item_id=int(orphan.id),
                        qbo_invoice_line_id=qbo_invoice_line.id,
                    )
                except (ValueError, DatabaseConstraintError) as error:
                    # U-247: do NOT fall through to "Create new InvoiceLineItem" below on a
                    # failed adopt — the orphan is a real, already-existing InvoiceLineItem;
                    # minting a NEW one here would be the exact self-amplifying duplication
                    # this unit exists to close. Re-raise so the caller's per-line catch
                    # (InvoiceInvoiceConnector._sync_line_items) logs and retries this QBO
                    # line on the next pass instead of duplicating it on this one.
                    logger.warning(
                        f"Could not adopt orphaned InvoiceLineItem {orphan.id}: {error}"
                    )
                    raise

        if mapping:
            # Found existing mapping - use cached record to avoid DB read
            line_item = cached_or_read(
                self._caches_preloaded, self._line_item_cache, mapping.invoice_line_item_id,
                self.invoice_line_item_service.read_by_id,
            )
            if line_item:
                logger.info(f"Updating existing InvoiceLineItem {line_item.id} from QboInvoiceLine {qbo_invoice_line.id}")
                # U-293b: reuse the SAME _apply_line_fields closure the fast path
                # uses (source-reset decision + update + identity re-stamp +
                # provenance mirror + cache refresh).
                return _apply_line_fields(line_item, path_label="legacy mapping-table path")
            else:
                logger.warning(f"Mapping exists but InvoiceLineItem {mapping.invoice_line_item_id} not found. Creating new.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None

        # Create new InvoiceLineItem
        logger.info(f"Creating new InvoiceLineItem from QboInvoiceLine {qbo_invoice_line.id}")
        line_item = self.invoice_line_item_service.create(
            invoice_public_id=invoice_public_id,
            source_type="Manual",
            description=description,
            amount=amount,
            markup=markup,
            price=price,
            is_draft=False,
        )
        # Add to cache
        if self._line_item_cache is not None:
            self._line_item_cache[line_item.id] = line_item

        # Create mapping
        line_item_id = coerce_id(line_item.id)
        try:
            mapping = self.create_mapping(invoice_line_item_id=line_item_id, qbo_invoice_line_id=qbo_invoice_line.id)
            logger.info(f"Created mapping: InvoiceLineItem {line_item_id} <-> QboInvoiceLine {qbo_invoice_line.id}")
        except (ValueError, DatabaseConstraintError) as e:
            # Compensating delete (U-247): an ILI created above but never mapped is an
            # unmapped-but-Manual phantom row. Delete it and re-raise so the caller's
            # per-line catch (InvoiceInvoiceConnector._sync_line_items) logs and moves on
            # to the next line — this connector has no header-level compensating rollback
            # today (see that method's own "U-006" note on why NOT to add one casually);
            # this stays scoped to the single orphaned line, it does not touch the invoice
            # header.
            logger.warning(f"Could not create mapping for InvoiceLineItem {line_item_id}: {e}")
            try:
                self.invoice_line_item_service.repo.delete_by_id(line_item_id)
            except Exception as cleanup_error:
                logger.error(
                    f"Compensating delete failed for orphan InvoiceLineItem {line_item_id}: {cleanup_error}"
                )
            else:
                if self._line_item_cache is not None:
                    self._line_item_cache.pop(line_item.id, None)
            raise

        # U-238b: dbo line identity dual-write (create+update pairing for U-238c).
        # Mapping is already committed — a stamp failure must NOT roll back the line item.
        stamp_line_identity_or_warn(
            self.invoice_line_item_service.repo,
            id=line_item_id,
            qbo_id=qbo_invoice_line.qbo_line_id,
            realm_id=realm_id,
            context=f"Created mapping for InvoiceLineItem {line_item_id} for QboInvoiceLine {qbo_invoice_line.id}",
            enforce_realm_pairing=True,
        )
        # U-272: dbo source-link provenance mirror (create+update pairing).
        _stamp_source_provenance_or_warn(
            self.invoice_line_item_service.repo,
            qbo_invoice_line=qbo_invoice_line,
            invoice_line_item_id=line_item_id,
            context=f"Created mapping for InvoiceLineItem {line_item_id} for QboInvoiceLine {qbo_invoice_line.id}",
        )

        return line_item

    def _raise_line_identity_mapping_conflict_issue(
        self,
        *,
        qbo_invoice_line: QboInvoiceLine,
        dbo_line_id: int,
        local_side_mapping,
        qbo_side_mapping,
        realm_id: Optional[str] = None,
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by
        run_line_identity_fastpath's resolve_mapping_state. Mirrors
        BillLineItemConnector._raise_line_identity_mapping_conflict_issue
        exactly, scoped to the invoice line level — covers all three conflict
        shapes (qbo-side only, local-side only, or both) in ONE issue, never
        silently dropping either side's blocker.
        """
        parts = [
            f"InvoiceLineItemInvoiceLine identity conflict. dbo.InvoiceLineItem "
            f"{dbo_line_id} carries native QBO identity for QboInvoiceLine "
            f"{qbo_invoice_line.id} (QboLineId={qbo_invoice_line.qbo_line_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboInvoiceLine to a "
                f"DIFFERENT InvoiceLineItem {qbo_side_mapping.invoice_line_item_id} "
                f"(mapping {qbo_side_mapping.id})."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: InvoiceLineItem {dbo_line_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboInvoiceLine "
                f"{local_side_mapping.qbo_invoice_line_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="invoice_line_identity_conflict",
            entity_type="InvoiceLineItem",
            entity_public_id=None,
            qbo_id=str(qbo_invoice_line.qbo_line_id) if qbo_invoice_line.qbo_line_id else None,
            realm_id=realm_id or "",
            details=" ".join(parts),
        )

    # ------------------------------------------------------------------ #
    # Shape B line-matching helpers (task #17)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_for_fingerprint(value) -> str:
        """Canonicalize a value for content-fingerprint comparison."""
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:
            pass
        return str(value).strip()

    def _find_and_match_manual_by_fingerprint(
        self,
        *,
        invoice_id: int,
        description,
        amount,
    ):
        """
        Find an unmapped Manual InvoiceLineItem on this invoice whose content matches.

        Only considers lines with `source_type='Manual'`. Bill- and Expense-sourced
        invoice lines are matched via their source FKs elsewhere; attempting to adopt
        them here would break the source linkage.

        Uses (description, amount) as the fingerprint — qty/rate are less reliable for
        invoice lines because QBO can normalize them during entry. Returns None when no
        match; when multiple unmapped candidates share the fingerprint, deterministically
        adopts the lowest-id candidate (U-247 — returning None on ambiguity caused
        self-amplifying duplicate rows).

        Reads from the connector's pre-loaded ``_line_item_cache`` and
        ``_line_mapping_cache`` when ``caches_preloaded=True`` (U-247 — avoids O(n^2) DB
        round trips on large invoices); falls back to DB reads otherwise.
        """
        from entities.invoice_line_item.business.service import InvoiceLineItemService

        if self._caches_preloaded:
            existing = [
                li
                for li in self._line_item_cache.values()
                if getattr(li, "invoice_id", None) == invoice_id
            ]
            mapped_line_item_ids = {
                m.invoice_line_item_id for m in self._line_mapping_cache.values()
            }
        else:
            existing = InvoiceLineItemService().read_by_invoice_id(invoice_id)
            mapped_line_item_ids = None

        unmapped = []
        for li in existing:
            if getattr(li, "source_type", None) != "Manual":
                continue
            if mapped_line_item_ids is not None:
                if int(li.id) in mapped_line_item_ids:
                    continue
            elif self.mapping_repo.read_by_invoice_line_item_id(int(li.id)):
                continue
            unmapped.append(li)

        target = (
            self._normalize_for_fingerprint(description),
            self._normalize_for_fingerprint(amount),
        )

        matches = []
        for candidate in unmapped:
            candidate_fp = (
                self._normalize_for_fingerprint(getattr(candidate, "description", None)),
                self._normalize_for_fingerprint(getattr(candidate, "amount", None)),
            )
            if candidate_fp == target:
                matches.append(candidate)

        if len(matches) == 0:
            return None
        if len(matches) == 1:
            return matches[0]
        adopted = min(matches, key=lambda li: int(li.id))
        logger.info(
            f"Content-fingerprint match ambiguous: {len(matches)} unmapped "
            f"Manual InvoiceLineItems have identical fingerprint; adopting lowest id "
            f"{adopted.id}"
        )
        return adopted

    def create_mapping(self, invoice_line_item_id: int, qbo_invoice_line_id: int) -> InvoiceLineItemInvoiceLine:
        """
        Create a mapping between InvoiceLineItem and QboInvoiceLine.

        Args:
            invoice_line_item_id: Database ID of InvoiceLineItem record
            qbo_invoice_line_id: Database ID of QboInvoiceLine record

        Returns:
            InvoiceLineItemInvoiceLine: The created mapping record

        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints only when caches_preloaded is False.
        # When caches_preloaded=True, we already checked before calling create_mapping.
        if not self._caches_preloaded:
            existing_by_line_item = self.mapping_repo.read_by_invoice_line_item_id(invoice_line_item_id)
            if existing_by_line_item:
                raise ValueError(
                    f"InvoiceLineItem {invoice_line_item_id} is already mapped to QboInvoiceLine {existing_by_line_item.qbo_invoice_line_id}"
                )
            existing_by_qbo_line = self.mapping_repo.read_by_qbo_invoice_line_id(qbo_invoice_line_id)
            if existing_by_qbo_line:
                raise ValueError(
                    f"QboInvoiceLine {qbo_invoice_line_id} is already mapped to InvoiceLineItem {existing_by_qbo_line.invoice_line_item_id}"
                )

        # Create mapping and update cache
        new_mapping = self.mapping_repo.create(invoice_line_item_id=invoice_line_item_id, qbo_invoice_line_id=qbo_invoice_line_id)
        self._line_mapping_cache[qbo_invoice_line_id] = new_mapping
        return new_mapping

    def get_mapping_by_invoice_line_item_id(self, invoice_line_item_id: int) -> Optional[InvoiceLineItemInvoiceLine]:
        """
        Get mapping by InvoiceLineItem ID.
        """
        return self.mapping_repo.read_by_invoice_line_item_id(invoice_line_item_id)

    def get_mapping_by_qbo_invoice_line_id(self, qbo_invoice_line_id: int) -> Optional[InvoiceLineItemInvoiceLine]:
        """
        Get mapping by QboInvoiceLine ID.
        """
        return self.mapping_repo.read_by_qbo_invoice_line_id(qbo_invoice_line_id)
