# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from shared.api.money import to_decimal_or_none
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCredit, QboVendorCreditLine
from integrations.intuit.qbo.vendorcredit.connector.bill_credit.persistence.repo import (
    VendorCreditBillCreditMappingRepository,
    VendorCreditBillCreditMapping,
)
from integrations.intuit.qbo.vendorcredit.connector.bill_credit_line_item.business.service import (
    VendorCreditLineItemConnector,
)
from entities.bill_credit.business.service import BillCreditService
from entities.bill_credit.business.model import BillCredit
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.vendor.business.service import VendorService
from integrations.intuit.qbo.base.pull_race import guard_lines_present
from integrations.intuit.qbo.base.compensation import rollback_orphan_header
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.base.field_ownership import preserve_human_edited_ref, qbo_ref_or_placeholder
from integrations.intuit.qbo.base.identity_fastpath import (
    raise_concurrent_write_race,
    resolve_mapping_state,
    run_identity_fastpath,
)
from integrations.intuit.qbo.base.ids import coerce_id
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
        line_item_connector: Optional[VendorCreditLineItemConnector] = None,
    ):
        self.mapping_repo = mapping_repo or VendorCreditBillCreditMappingRepository()
        self.bill_credit_service = bill_credit_service or BillCreditService()
        self.bill_credit_line_item_service = bill_credit_line_item_service or BillCreditLineItemService()
        self.vendor_service = vendor_service or VendorService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()
        # Run-scoped (U-229): built once here and reused across every credit processed by
        # this connector instance, so VendorCreditLineItemConnector._sub_cost_code_cache
        # persists for the whole pull run instead of resetting per credit. Mirrors
        # PurchaseExpenseConnector's identical hoist of PurchaseLineExpenseLineItemConnector.
        self.line_item_connector = line_item_connector or VendorCreditLineItemConnector()

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

            # U-278 (Phase-4, header/reference repoint): resolve identity directly against
            # dbo.BillCredit's native QboId/RealmId (Phase 2) before falling back to the
            # qbo.VendorCreditBillCredit mapping-table hop below. Every BillCredit synced
            # even once already carries this identity (set_qbo_identity is called on both
            # the create path and _apply_bill_credit_fields_and_sync below), so this covers
            # the steady-state case without touching qbo.VendorCredit at all. Mirrors
            # CustomerCustomerConnector.sync_from_qbo_customer (U-276) exactly.
            #
            # The mapping-table state is checked BEFORE any write, not after: writing to
            # the dbo-identity-matched BillCredit first and detecting a conflict afterward
            # would corrupt that BillCredit's data in the case where the mapping table —
            # not dbo identity — is actually still the correct side (U-276 round-3 finding).
            # On a detected conflict we record it and RAISE — never fall through to the
            # legacy mapping-table path, which would call set_qbo_identity on a DIFFERENT
            # row with the same (QboId, RealmId) `direct` already holds; the sproc's own
            # theft-detection UPDATE would then silently NULL `direct`'s identity, and a
            # local-side-only conflict could additionally mint a duplicate BillCredit via
            # Step 3 CREATE. See the raise below for the full rationale.
            # _apply_bill_credit_fields_and_sync raises internally (via
            # raise_concurrent_write_race, U-291) on a ROWVERSION-race/concurrent-delete
            # update failure — one guard inside the shared helper protects every caller
            # (this fast path, and the legacy branch below) rather than each call site
            # needing its own. Replaces this connector's own pre-migration behavior,
            # which only logged a warning and let the None flow through as a silent
            # success (`FastPathOutcome(hit=True, entity=None)`, which the caller
            # returned straight through to project_records — counted as a projected
            # SUCCESS with the BillCredit never actually written).
            outcome = run_identity_fastpath(
                qbo_id=qbo_vc.qbo_id,
                realm_id=qbo_vc.realm_id,
                external_id=qbo_vc.id,
                entity_label="BillCredit",
                external_label="QboVendorCredit",
                mapping_label="VendorCreditBillCredit",
                read_direct_by_qbo_identity=self.bill_credit_service.read_by_qbo_identity,
                read_by_local_id=self.mapping_repo.read_by_bill_credit_id,
                read_by_external_id=self.mapping_repo.read_by_qbo_vendor_credit_id,
                external_id_attr="qbo_vendor_credit_id",
                record_conflict_issue=lambda entity, by_local, by_external: (
                    self._raise_identity_mapping_conflict_issue(
                        qbo_vc=qbo_vc,
                        dbo_bill_credit_id=coerce_id(entity.id),
                        local_side_mapping=by_local,
                        qbo_side_mapping=by_external,
                    )
                ),
                conflict_message=lambda entity: (
                    f"VendorCreditBillCredit identity conflict for QboVendorCredit "
                    f"{qbo_vc.qbo_id} (id={qbo_vc.id}): dbo.BillCredit {entity.id} "
                    f"already carries this identity but the mapping table "
                    f"disagrees. Not auto-repointed; see the recorded "
                    f"reconciliation issue. Skipping until a human resolves it."
                ),
                create_mapping=lambda local_id: self.mapping_repo.create(
                    qbo_vendor_credit_id=qbo_vc.id,
                    bill_credit_id=local_id,
                ),
                apply_fields=lambda entity: self._apply_bill_credit_fields_and_sync(
                    entity,
                    qbo_vc=qbo_vc,
                    vendor_public_id=vendor_public_id,
                    credit_number=credit_number,
                    qbo_lines=qbo_lines,
                ),
            )
            if outcome.hit:
                return outcome.entity

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

                # U-027 (rule of three) + line sync live in the shared helper now (U-278)
                # so this path and the direct-identity fast path above apply the exact
                # same QboVendorCredit->BillCredit field mapping. This (legacy) path may
                # be updating a row that predates identity stamping, so — unlike the fast
                # path — it (re-)stamps dbo-native identity itself after the helper
                # returns. See _apply_bill_credit_fields_and_sync for the full rationale.
                updated = self._apply_bill_credit_fields_and_sync(
                    bill_credit,
                    qbo_vc=qbo_vc,
                    vendor_public_id=vendor_public_id,
                    credit_number=credit_number,
                    qbo_lines=qbo_lines,
                    path_label="legacy mapping-table path",
                )
                self.bill_credit_service.repo.set_qbo_identity(
                    id=coerce_id(updated.id),
                    qbo_id=qbo_vc.qbo_id,
                    realm_id=qbo_vc.realm_id,
                )
                return updated

            # Step 3: Create new BillCredit
            bill_credit = self.bill_credit_service.create(
                vendor_public_id=vendor_public_id,
                credit_date=qbo_vc.txn_date,
                credit_number=credit_number,
                total_amount=to_decimal_or_none(qbo_vc.total_amt),
                memo=qbo_vc.private_note,
                is_draft=False,
            )
            
            if bill_credit:
                self.bill_credit_service.repo.set_qbo_identity(
                    id=coerce_id(bill_credit.id),
                    qbo_id=qbo_vc.qbo_id,
                    realm_id=qbo_vc.realm_id,
                )
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
                        on_header_delete_failed=lambda exc: self._record_orphan_header_issue(
                            bill_credit=bill_credit, qbo_vc=qbo_vc, exc=exc
                        ),
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

    def _apply_bill_credit_fields_and_sync(
        self,
        bill_credit: BillCredit,
        *,
        qbo_vc: QboVendorCredit,
        vendor_public_id: str,
        credit_number: str,
        qbo_lines: List[QboVendorCreditLine],
        path_label: str = "fast path",
    ) -> BillCredit:
        """
        Write the QboVendorCredit-derived fields onto an existing BillCredit, persist it,
        and sync its line items. Shared by the direct dbo-identity fast path (U-278) and
        the existing mapping-table update path so the QboVendorCredit -> BillCredit field
        mapping lives in exactly one place (no drift between the two update sites) AND
        both get the same ROWVERSION-race guard for free (U-291): a None
        `update_by_public_id` return is raised here, not returned, so a caller cannot
        forget to check for it. `path_label` names which caller hit the race, for the
        log trail. Mirrors CustomerProjectConnector._apply_project_fields_and_sync
        (U-276).

        Deliberately does NOT stamp dbo-native identity — the fast-path caller's row
        already carries it by construction (that's how `read_by_qbo_identity` found it in
        the first place; re-stamping there would be a wasted round trip on the steady-state
        path this whole feature exists to keep cheap). Only the legacy mapping-table path
        may be updating a row that predates identity stamping, so IT calls
        `set_qbo_identity` itself after this returns — same asymmetry as
        `_apply_project_fields_and_sync` (U-276), which carries no `set_qbo_identity` call
        at all for the identical reason.

        U-027 (rule of three): never clobber a human-corrected credit_number on re-pull.
        Preserve the stored value unless it is empty/null or the QBO-<id> placeholder
        (which still upgrades to a real doc_number). The CREATE path is unchanged. See
        base.field_ownership. ACCEPTED RESIDUAL: same as the Bill sibling — a preserved
        credit_number diverges from the QBO number, so IF this credit's mapping is later
        lost while it persists (abnormal), the CREATE path's
        UQ_BillCredit_VendorId_CreditNumber dedup keys on the QBO number and won't match
        -> possible duplicate. Adopt-style recovery is a separate reviewed unit (TODO.md).
        """
        effective_credit_number = preserve_human_edited_ref(
            bill_credit.credit_number, credit_number, qbo_vc.qbo_id
        )
        updated = self.bill_credit_service.update_by_public_id(
            public_id=bill_credit.public_id,
            row_version=bill_credit.row_version,
            vendor_public_id=vendor_public_id,
            credit_date=qbo_vc.txn_date,
            credit_number=effective_credit_number,
            total_amount=to_decimal_or_none(qbo_vc.total_amt),
            memo=qbo_vc.private_note,
        )
        if updated is None:
            raise_concurrent_write_race(
                entity_label="BillCredit", entity_id=bill_credit.id, path_label=path_label
            )
        self._sync_line_items(updated.id, updated.public_id, qbo_lines, qbo_vc.realm_id)
        return updated

    def _resolve_mapping_state(self, *, bill_credit_id: int, qbo_vc: QboVendorCredit):
        """
        Read-only check of the VendorCreditBillCredit mapping table against a
        dbo-identity match, BEFORE any write happens (U-278 fast path). Must run before
        `_apply_bill_credit_fields_and_sync` — writing to the dbo-identity-matched
        BillCredit first and detecting a conflict afterward would corrupt that
        BillCredit's data in the case where the mapping table, not dbo identity, is
        actually still the correct side (U-276 round-3 finding).

        Checks BOTH directions like create_mapping's own 1:1 guards — a
        bill_credit_id-only check would miss a stale mapping still binding this
        qbo_vendor_credit_id to a DIFFERENT BillCredit (left behind by an earlier
        identity "theft" — see SetBillCreditQboIdentity's own theft-clear UPDATE, which
        does not clean up the mapping table). A stale entry here also feeds the
        invoice-side LinkedTxn Tier-0 resolver (ProposeInvoiceSourceLinks), which reads
        this same mapping table, so leaving it undetected can misroute LinkedTxn
        resolution to the wrong BillCredit.

        NOTE (U-287): no production caller — `sync_from_qbo_*` passes these same
        accessors straight to `run_identity_fastpath`, which calls the shared
        `resolve_mapping_state` itself. Retained as the per-family test seam for the
        U-276/277/278/279 suites, which call this by name. Disposition booked in TODO.md.

        Returns (state, by_bill_credit, by_qbo_vc) — see
        base.identity_fastpath.resolve_mapping_state, which owns the algorithm and
        documents the "consistent"/"missing"/"conflict" semantics (U-287); this is the
        VendorCreditBillCredit binding of it.
        """
        return resolve_mapping_state(
            local_id=bill_credit_id,
            external_id=qbo_vc.id,
            read_by_local_id=self.mapping_repo.read_by_bill_credit_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_vendor_credit_id,
            external_id_attr="qbo_vendor_credit_id",
        )

    def _raise_identity_mapping_conflict_issue(
        self,
        *,
        qbo_vc: QboVendorCredit,
        dbo_bill_credit_id: int,
        local_side_mapping: Optional[VendorCreditBillCreditMapping],
        qbo_side_mapping: Optional[VendorCreditBillCreditMapping],
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by _resolve_mapping_state.
        Distinct from `_record_missing_bill_credit_issue` (a bound-row-read-empty
        detection) — this is a post-hoc drift between two already-established identity
        sources, most plausibly left behind by an identity "theft" event
        (SetBillCreditQboIdentity's theft-clear UPDATE clears the losing row's
        QboId/RealmId but does not touch the mapping table). Covers all three shapes in
        ONE issue: qbo-side only, local-side only, or both (the "two-row crossed"
        case) — never silently dropping either side's blocker. Mirrors
        CustomerCustomerConnector._raise_identity_mapping_conflict_issue (U-276).
        """
        parts = [
            f"VendorCreditBillCredit identity conflict. dbo.BillCredit {dbo_bill_credit_id} "
            f"carries native QBO identity for QboVendorCredit {qbo_vc.id} "
            f"(QboId={qbo_vc.qbo_id}, RealmId={qbo_vc.realm_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboVendorCredit to a "
                f"DIFFERENT BillCredit {qbo_side_mapping.bill_credit_id} (mapping "
                f"{qbo_side_mapping.id}) — the invoice-side LinkedTxn Tier-0 resolver "
                f"(ProposeInvoiceSourceLinks) reading this mapping table will keep "
                f"resolving to BillCredit {qbo_side_mapping.bill_credit_id}, not "
                f"{dbo_bill_credit_id}, until repointed."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: BillCredit {dbo_bill_credit_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboVendorCredit "
                f"{local_side_mapping.qbo_vendor_credit_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="vendorcredit_identity_conflict",
            entity_type="BillCredit",
            entity_public_id=None,
            qbo_id=str(qbo_vc.qbo_id) if qbo_vc.qbo_id else None,
            realm_id=qbo_vc.realm_id or "",
            details=" ".join(parts),
        )

    def _record_orphan_header_issue(
        self,
        *,
        bill_credit: BillCredit,
        qbo_vc: QboVendorCredit,
        exc: Exception,
    ) -> None:
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphan_billcredit_header",
            entity_type="BillCredit",
            entity_public_id=str(bill_credit.public_id) if bill_credit.public_id else None,
            qbo_id=str(qbo_vc.qbo_id) if qbo_vc.qbo_id else None,
            realm_id=qbo_vc.realm_id or "",
            details=(
                f"Compensating rollback deleted VendorCreditBillCredit mapping but "
                f"failed to delete orphan BillCredit {bill_credit.id} "
                f"({bill_credit.public_id}): {exc}. Header blocks re-pull until "
                f"manually resolved."
            ),
        )

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
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="orphaned_vc_billcredit_mapping",
            entity_type="BillCredit",
            entity_public_id=None,
            qbo_id=str(qbo_vc.qbo_id) if qbo_vc.qbo_id else None,
            realm_id=qbo_vc.realm_id or "",
            details=details,
        )

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
                self.line_item_connector.sync_from_qbo_line(bill_credit_id, bill_credit_public_id, line, realm_id)
            except Exception as e:
                logger.error(f"Error syncing line item {line.qbo_line_id}: {e}")
                failed.append((line.qbo_line_id, str(e)))
        if failed:
            raise RuntimeError(
                f"{len(failed)} of {len(qbo_lines)} credit line(s) failed to project for "
                f"bill_credit_id={bill_credit_id}: {failed}"
            )
