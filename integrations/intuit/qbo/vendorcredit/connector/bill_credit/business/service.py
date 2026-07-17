# Python Standard Library Imports
import logging
from typing import List, Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit, QboVendorCreditLine
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo import (
    VendorCreditBillCreditMappingRepository,
    VendorCreditBillCreditMapping,
)
from entities.bill_credit.business.service import BillCreditService
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.vendor.business.service import VendorService
from integrations.intuit.qbo.base.pull_race import guard_lines_present
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.base.field_ownership import preserve_human_edited_ref, qbo_ref_or_placeholder
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository

logger = logging.getLogger(__name__)


class VendorCreditBillCreditConnector:
    """Connector service for syncing QBO VendorCredits to BillCredits."""

    def __init__(
        self,
        mapping_repo: Optional[VendorCreditBillCreditMappingRepository] = None,
        bill_credit_service: Optional[BillCreditService] = None,
        bill_credit_line_item_service: Optional[BillCreditLineItemService] = None,
        vendor_service: Optional[VendorService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        self.mapping_repo = mapping_repo or VendorCreditBillCreditMappingRepository()
        self.bill_credit_service = bill_credit_service or BillCreditService()
        self.bill_credit_line_item_service = bill_credit_line_item_service or BillCreditLineItemService()
        self.vendor_service = vendor_service or VendorService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_vendor_credit(
        self,
        qbo_vc: QboVendorCredit,
        qbo_lines: List[QboVendorCreditLine],
    ) -> Optional[BillCredit]:
        """
        Sync a QBO VendorCredit to BillCredit module.
        
        Process:
        1. Resolve vendor via VendorRefValue -> QboVendor -> VendorVendor -> Vendor
        2. Check if BillCredit already exists via mapping table
        3. Create or update BillCredit
        4. Create mapping record
        5. Sync line items
        """
        # Last-resort guard against the QBO pull-race that mints half-built credits (see
        # base.pull_race). Placed BEFORE the try so the RuntimeError isn't swallowed by the
        # inner except. Pull scripts pre-read past the race; this protects every other caller.
        guard_lines_present(
            qbo_lines, qbo_vc.total_amt,
            entity_label="QboVendorCredit", entity_id=qbo_vc.id, qbo_id=qbo_vc.qbo_id,
        )

        try:
            # Step 1: Resolve vendor
            vendor_public_id = self._get_vendor_public_id(qbo_vc.vendor_ref_value)
            if not vendor_public_id:
                # Permanent data issue — raise (don't silently return None) so the
                # caller classifies it as a skip that doesn't block the watermark,
                # consistent with the Bill and Purchase connectors.
                raise ValueError(
                    f"No vendor mapping found for QBO vendor ref: {qbo_vc.vendor_ref_value}"
                )
            
            # QBO-derived credit number (real DocNumber or the QBO-<id> placeholder).
            # Hoisted once and reused on both the UPDATE and CREATE paths, mirroring the
            # Bill/Expense siblings.
            credit_number = qbo_ref_or_placeholder(qbo_vc.doc_number, qbo_vc.qbo_id)

            # Step 2: Check for existing mapping
            existing_mapping = self.mapping_repo.read_by_qbo_vendor_credit_id(qbo_vc.id)

            if existing_mapping:
                # Found existing mapping. Resolve the BillCredit to update. HEAL-don't-
                # delete (U-031, mirroring U-029 Purchase->Expense): the empty-read branch
                # must NEVER fall through to Step 3 CREATE — that mints a DUPLICATE
                # BillCredit. (This connector's flavor of the bug: it didn't delete the
                # mapping, it just silently fell through to create when the read came back
                # empty.)
                bill_credit = self.bill_credit_service.read_by_id(existing_mapping.bill_credit_id)
                if not bill_credit:
                    # Bound BillCredit read empty. Re-resolve by the natural
                    # (credit_number, vendor) fingerprint and heal ONLY when it re-binds the
                    # SAME BillCredit the mapping already targets (a confirmed transient
                    # empty-read). The fingerprint keys on the QBO-derived credit_number
                    # (what CREATE writes); the same-id gate makes a wrong/duplicate row safe
                    # under a non-TOP-1 fingerprint proc (id != mapping → record+raise).
                    replacement = self.bill_credit_service.read_by_credit_number_and_vendor_public_id(
                        credit_number, vendor_public_id
                    )
                    if replacement and replacement.id == existing_mapping.bill_credit_id:
                        logger.warning(
                            f"BillCredit {existing_mapping.bill_credit_id} read empty for "
                            f"QboVendorCredit {qbo_vc.qbo_id} but re-resolved by "
                            f"(credit_number, vendor) — transient empty-read; healing in "
                            f"place, not recreating."
                        )
                        bill_credit = replacement
                    else:
                        # No fingerprint match, or a match under a DIFFERENT id we cannot
                        # safely repoint to (no mapping-update sproc): preserve the mapping,
                        # create nothing, record a critical reconciliation issue, and RAISE.
                        # The pull caller treats this ValueError as a per-item skip; the
                        # issue is the durable follow-up record.
                        self._record_missing_bill_credit_issue(
                            qbo_vc=qbo_vc, mapping=existing_mapping, fingerprint=replacement
                        )
                        raise ValueError(
                            f"VendorCreditBillCredit mapping {existing_mapping.id} points at "
                            f"missing BillCredit {existing_mapping.bill_credit_id} and no "
                            f"local BillCredit fingerprinted by credit_number "
                            f"'{credit_number}' + vendor resolves to it for QboVendorCredit "
                            f"{qbo_vc.qbo_id}; preserving mapping, skipping."
                        )

                # U-027 (rule of three): never clobber a human-corrected credit_number on
                # re-pull. Preserve the stored value unless it is empty/null or the QBO-<id>
                # placeholder (which still upgrades to a real doc_number). The CREATE path
                # below is unchanged. See base.field_ownership.
                # ACCEPTED RESIDUAL: same as the Bill sibling — a preserved credit_number
                # diverges from the QBO number, so IF this credit's mapping is later lost
                # while it persists (abnormal), the CREATE path's UQ_BillCredit_VendorId_
                # CreditNumber dedup keys on the QBO number and won't match → possible
                # duplicate. Adopt-style recovery is a separate reviewed unit (see TODO.md).
                effective_credit_number = preserve_human_edited_ref(
                    bill_credit.credit_number, credit_number, qbo_vc.qbo_id
                )
                updated = self.bill_credit_service.update_by_public_id(
                    public_id=bill_credit.public_id,
                    row_version=bill_credit.row_version,
                    vendor_public_id=vendor_public_id,
                    credit_date=qbo_vc.txn_date,
                    credit_number=effective_credit_number,
                    total_amount=Decimal(str(qbo_vc.total_amt)) if qbo_vc.total_amt else None,
                    memo=qbo_vc.private_note,
                )
                if updated:
                    # Sync line items
                    self._sync_line_items(updated.id, updated.public_id, qbo_lines, qbo_vc.realm_id)
                return updated

            # Step 3: Create new BillCredit
            bill_credit = self.bill_credit_service.create(
                vendor_public_id=vendor_public_id,
                credit_date=qbo_vc.txn_date,
                credit_number=credit_number,
                total_amount=Decimal(str(qbo_vc.total_amt)) if qbo_vc.total_amt else None,
                memo=qbo_vc.private_note,
                is_draft=False,
            )
            
            if bill_credit:
                # Step 4: Create mapping
                self.mapping_repo.create(
                    qbo_vendor_credit_id=qbo_vc.id,
                    bill_credit_id=bill_credit.id,
                )
                
                # Step 5: Sync line items
                # Compensating rollback — a permanent line failure must not leave a header-only
                # zombie; delete the just-created header + mapping and re-raise (watermark holds;
                # re-pull is idempotent).
                try:
                    self._sync_line_items(bill_credit.id, bill_credit.public_id, qbo_lines, qbo_vc.realm_id)
                except Exception:
                    rollback_orphan_header(
                        delete_header=lambda: self.bill_credit_service.delete_by_public_id(bill_credit.public_id),
                        delete_mapping=lambda: self.mapping_repo.delete_by_qbo_vendor_credit_id(qbo_vc.id),
                        entity_label='BillCredit', entity_id=bill_credit.id,
                    )
                    raise
                
                logger.info(f"Created BillCredit {bill_credit.public_id} from VendorCredit {qbo_vc.qbo_id}")
            
            return bill_credit
            
        except ValueError:
            # Permanent data issue — propagate for the caller to classify as a skip.
            raise
        except Exception as e:
            # Transient error (DB, connection, etc.) — propagate so the caller can
            # block the watermark and retry next run, instead of silently dropping it.
            logger.error(f"Error syncing VendorCredit {qbo_vc.qbo_id} to BillCredit: {e}")
            raise

    def _record_missing_bill_credit_issue(
        self,
        *,
        qbo_vc: QboVendorCredit,
        mapping: VendorCreditBillCreditMapping,
        fingerprint: Optional[BillCredit] = None,
    ) -> None:
        """
        Record an orphaned-mapping detection on qbo.ReconciliationIssue, failure-
        isolated: a failed insert is logged loud but never breaks the sync (mirrors
        the Purchase/CustomerProject connectors' recorders).

        Triggered when a VendorCreditBillCredit mapping exists but its bound BillCredit
        read empty AND the (credit_number, vendor) fingerprint did not re-resolve to
        that same BillCredit. We deliberately do NOT create a BillCredit or drop the
        mapping here — a transient empty-read would otherwise mint a duplicate; the
        mapping is preserved for the next tick / a human to resolve.
        """
        if fingerprint is not None:
            fingerprint_note = (
                f" A different BillCredit {fingerprint.id} matches the (credit_number, "
                f"vendor) fingerprint but is not the mapped row; not repointing (no "
                f"mapping-update path)."
            )
        else:
            fingerprint_note = (
                " No local BillCredit matches the (credit_number, vendor) fingerprint."
            )
        details = (
            f"Orphaned VendorCreditBillCredit mapping. Mapping {mapping.id} "
            f"(QboVendorCredit {qbo_vc.id}, QboId={qbo_vc.qbo_id}) points at BillCredit "
            f"{mapping.bill_credit_id} which no longer reads.{fingerprint_note} Mapping "
            f"preserved; no BillCredit created. Investigate whether the BillCredit was "
            f"deleted/renumbered."
        )
        try:
            self.reconciliation_repo.create(
                drift_type="orphaned_vendorcredit_billcredit_mapping",
                severity="critical",
                action="manual_review",
                entity_type="BillCredit",
                entity_public_id=None,
                qbo_id=str(qbo_vc.qbo_id) if qbo_vc.qbo_id else None,
                realm_id=qbo_vc.realm_id or "",
                details=details,
            )
            logger.warning(details)
        except Exception as exc:
            # Don't break the sync because the reconciliation insert failed. Log loud.
            logger.error(f"Failed to record reconciliation issue: {exc}. Details: {details}")

    def _get_vendor_public_id(self, qbo_vendor_ref_value: Optional[str]) -> Optional[str]:
        """Resolve QBO vendor ref (QBO API string ID) to local Vendor public_id.
        Same two-step lookup as PurchaseExpenseConnector: QboVendor by qbo_id, then VendorVendor by QboVendor.Id.
        """
        if not qbo_vendor_ref_value:
            return None
        
        try:
            from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
            from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
            
            qbo_vendor_repo = QboVendorRepository()
            vendor_vendor_repo = VendorVendorRepository()
            
            # Step 1: Find local QboVendor by QBO API vendor ID (string)
            qbo_vendor = qbo_vendor_repo.read_by_qbo_id(qbo_vendor_ref_value)
            if not qbo_vendor or not qbo_vendor.id:
                logger.warning(f"QboVendor not found for qbo_id: {qbo_vendor_ref_value}")
                return None
            
            # Step 2: Find VendorVendor mapping by local QboVendor.Id (integer)
            mapping = vendor_vendor_repo.read_by_qbo_vendor_id(qbo_vendor.id)
            if not mapping or not mapping.vendor_id:
                logger.warning(f"VendorVendor mapping not found for QboVendor ID: {qbo_vendor.id}")
                return None
            
            vendor = self.vendor_service.read_by_id(id=mapping.vendor_id)
            if not vendor:
                logger.warning(f"Vendor not found for ID: {mapping.vendor_id}")
                return None
            
            return vendor.public_id
        except Exception as e:
            logger.warning(f"Error resolving vendor ref {qbo_vendor_ref_value}: {e}")
            return None

    def _sync_line_items(
        self,
        bill_credit_id: int,
        bill_credit_public_id: str,
        qbo_lines: List[QboVendorCreditLine],
        realm_id: Optional[str] = None,
    ) -> None:
        """
        Sync line items from QBO VendorCredit to BillCreditLineItems by UPSERTING
        each line in place (parity with Bill's _sync_line_items).

        The snapshot layer (_upsert_vendor_credit_lines) keeps qbo.VendorCreditLine
        PKs stable across re-pulls, so the VendorCreditLineItemBillCreditLineItem
        mapping survives and each BillCreditLineItem is updated in place rather than
        deleted+recreated. This preserves the BillCreditLineItem PK, its attachments,
        and any InvoiceLineItem -> credit-line FK, and removes the old duplication
        vector entirely (an invoice-referenced line is updated, never re-created).
        Stale-line cleanup (lines QBO removed) is handled in the snapshot layer.
        """
        from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import VendorCreditLineItemConnector

        connector = VendorCreditLineItemConnector()

        # Upsert each QBO line in place. No delete-then-recreate: stale-line cleanup
        # lives in the snapshot layer, and the connector matches existing
        # BillCreditLineItems via the (now-stable) line mapping (with a content
        # fingerprint fallback for QBO line-id regeneration).
        # Attempt EVERY line, collect failures, then RAISE if any failed — never leave
        # a BillCredit whose header total doesn't match its lines. Raising marks the
        # whole credit failed so the pull watermark holds and it retries (idempotent).
        failed = []
        for line in qbo_lines:
            try:
                connector.sync_from_qbo_line(bill_credit_id, bill_credit_public_id, line, realm_id)
            except Exception as e:
                logger.error(f"Error syncing line item {line.qbo_line_id}: {e}")
                failed.append((line.qbo_line_id, str(e)))
        if failed:
            raise RuntimeError(
                f"{len(failed)} of {len(qbo_lines)} credit line(s) failed to project for "
                f"bill_credit_id={bill_credit_id}: {failed}"
            )
