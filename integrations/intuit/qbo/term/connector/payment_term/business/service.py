# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_name,
    raise_if_inactive_unmapped,
)
from integrations.intuit.qbo.base.identity_fastpath import (
    raise_concurrent_write_race,
    run_identity_fastpath,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import (
    record_identity_mapping_conflict,
)
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from integrations.intuit.qbo.term.connector.payment_term.business.model import TermPaymentTerm
from integrations.intuit.qbo.term.connector.payment_term.persistence.repo import TermPaymentTermRepository
from integrations.intuit.qbo.term.business.model import QboTerm
from entities.payment_term.business.service import PaymentTermService
from entities.payment_term.business.model import PaymentTerm

logger = logging.getLogger(__name__)


class TermPaymentTermConnector:
    """
    Connector service for synchronization between QboTerm and PaymentTerm modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[TermPaymentTermRepository] = None,
        payment_term_service: Optional[PaymentTermService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the TermPaymentTermConnector."""
        self.mapping_repo = mapping_repo or TermPaymentTermRepository()
        self.payment_term_service = payment_term_service or PaymentTermService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_term(self, qbo_term: QboTerm) -> Optional[PaymentTerm]:
        """
        Sync data from QboTerm to PaymentTerm module.

        Process:
        1. Resolve identity directly against dbo.PaymentTerm's native QboId/RealmId
           (U-282 fast path); fall back to the qbo.TermPaymentTerm mapping table
        2. Create or update the PaymentTerm accordingly

        Args:
            qbo_term: QboTerm record

        Returns:
            PaymentTerm: The synced PaymentTerm record
        """
        # Map QBO Term fields to PaymentTerm module fields
        term_name = qbo_term.name or ""

        # Build description from term details
        description_parts = []
        if qbo_term.type:
            description_parts.append(f"Type: {qbo_term.type}")
        if qbo_term.due_days is not None:
            description_parts.append(f"Due in {qbo_term.due_days} days")
        if qbo_term.day_of_month_due is not None:
            description_parts.append(f"Due on day {qbo_term.day_of_month_due} of month")
        description = "; ".join(description_parts) if description_parts else None

        # U-282 (Phase-4 repoint): resolve identity directly against dbo.PaymentTerm's
        # native QboId/RealmId (U-238c) before falling back to the qbo.TermPaymentTerm
        # mapping-table hop below. Every PaymentTerm synced even once already carries this
        # identity (set_qbo_identity is called on both the create path and the legacy
        # update path below), so this covers the steady-state case without touching
        # qbo.Term at all.
        #
        # U-291: migrated onto the shared `run_identity_fastpath()` helper (U-287, shipped
        # `edcf1f31`) — this was the 7th/last hand-rolled copy, booked for this migration by
        # U-282's own comment the moment U-287 shipped. conflict->RAISE is now structural
        # (base/identity_fastpath.py), not something this connector hand-maintains. Note:
        # deliberately does NOT refresh QboActive on a fast-path hit — PaymentTerm accepted
        # a staleness tradeoff here (U-282), unlike Vendor/SubCostCode's "refresh every hit"
        # pattern; that gap is its own separate open item, not touched by this migration.
        # _apply_payment_term_fields raises internally (via raise_concurrent_write_race,
        # U-291) on a ROWVERSION-race/concurrent-delete update failure — one guard inside
        # the shared helper protects every caller (this fast path, and the legacy branch
        # below) rather than each call site needing its own. Replaces this connector's own
        # pre-migration behavior, which only logged a warning and let the None flow through
        # as a silent success.
        outcome = run_identity_fastpath(
            qbo_id=qbo_term.qbo_id,
            realm_id=qbo_term.realm_id,
            external_id=qbo_term.id,
            entity_label="PaymentTerm",
            external_label="QboTerm",
            mapping_label="TermPaymentTerm",
            read_direct_by_qbo_identity=self.payment_term_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_payment_term_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_term_id,
            external_id_attr="qbo_term_id",
            record_conflict_issue=lambda entity, by_local, by_external: (
                self._record_identity_mapping_conflict_issue(
                    qbo_term=qbo_term,
                    dbo_payment_term_id=coerce_id(entity.id),
                    local_side_mapping=by_local,
                    qbo_side_mapping=by_external,
                )
            ),
            conflict_message=lambda entity: (
                f"TermPaymentTerm identity conflict for QboTerm {qbo_term.qbo_id} "
                f"(id={qbo_term.id}): dbo.PaymentTerm {entity.id} already carries "
                f"this identity but the mapping table disagrees. Not auto-repointed; "
                f"see the recorded reconciliation issue. Skipping until a human "
                f"resolves it."
            ),
            create_mapping=lambda local_id: self.mapping_repo.create(
                payment_term_id=local_id, qbo_term_id=qbo_term.id
            ),
            apply_fields=lambda entity: self._apply_payment_term_fields(
                entity, qbo_term=qbo_term, term_name=term_name, description=description
            ),
        )
        if outcome.hit:
            return outcome.entity

        # Step 2: Check for existing mapping (legacy path — rows that predate identity
        # stamping, or a globally-unmapped QboTerm with no dbo-identity fast-path hit).
        mapping = self.mapping_repo.read_by_qbo_term_id(qbo_term.id)

        if mapping:
            # Found existing mapping - update the PaymentTerm
            payment_term = self.payment_term_service.read_by_id(mapping.payment_term_id)
            if payment_term:
                logger.info(f"Updating existing PaymentTerm {payment_term.id} from QboTerm {qbo_term.id}")
                updated = self._apply_payment_term_fields(
                    payment_term, qbo_term=qbo_term, term_name=term_name, description=description,
                    path_label="legacy mapping-table path",
                )
                self.payment_term_service.repo.set_qbo_identity(
                    id=coerce_id(updated.id),
                    qbo_id=qbo_term.qbo_id,
                    realm_id=qbo_term.realm_id,
                    active=qbo_term.active,
                )
                return updated
            else:
                # Mapping exists but PaymentTerm not found - recreate PaymentTerm
                logger.warning(f"Mapping exists but PaymentTerm {mapping.payment_term_id} not found. Creating new PaymentTerm.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None

        # Create new PaymentTerm
        # Deactivation guard (U-219): no adopt path; directly before create.
        raise_if_inactive_unmapped(
            qbo_term.active, qbo_label="QboTerm", qbo_id=qbo_term.id, target="PaymentTerm"
        )
        logger.info(f"Creating new PaymentTerm from QboTerm {qbo_term.id}: name={term_name}")
        payment_term = self.payment_term_service.create(
            name=term_name,
            description=description,
            discount_percent=float(qbo_term.discount_percent) if qbo_term.discount_percent else None,
            discount_days=qbo_term.discount_days,
            due_days=qbo_term.due_days,
        )

        # Create mapping
        payment_term_id = coerce_id(payment_term.id)
        try:
            mapping = self.create_mapping(
                payment_term_id=payment_term_id,
                qbo_term_id=qbo_term.id,
                qbo_id=qbo_term.qbo_id,
                realm_id=qbo_term.realm_id,
                active=qbo_term.active,
            )
            logger.info(f"Created mapping: PaymentTerm {payment_term_id} <-> QboTerm {qbo_term.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")

        return payment_term

    def _apply_payment_term_fields(
        self,
        payment_term: PaymentTerm,
        *,
        qbo_term: QboTerm,
        term_name: str,
        description: Optional[str],
        path_label: str = "fast path",
    ) -> PaymentTerm:
        """
        Write the QboTerm-derived fields onto an existing PaymentTerm and persist it.
        Shared by the direct dbo-identity fast path (U-282) and the existing
        mapping-table update path so the QboTerm -> PaymentTerm field mapping lives in
        exactly one place (no drift between the two update sites) AND both get the
        same ROWVERSION-race guard for free (U-291): a None `update_by_id` return is
        raised here, not returned, so a caller cannot forget to check for it.
        `path_label` names which caller hit the race, for the log trail.

        Deliberately does NOT stamp dbo-native identity or QboActive — the fast-path
        caller's row already carries it by construction (that's how `read_by_qbo_identity`
        found it in the first place; re-stamping there would be a wasted round trip on the
        steady-state path this whole feature exists to keep cheap). Only the legacy
        mapping-table path may be updating a row that predates identity stamping, so IT
        calls `set_qbo_identity` itself after this returns.
        """
        payment_term.name = preserve_human_edited_name(payment_term.name, term_name)
        payment_term.description = description
        payment_term.discount_percent = float(qbo_term.discount_percent) if qbo_term.discount_percent else None
        payment_term.discount_days = qbo_term.discount_days
        payment_term.due_days = qbo_term.due_days
        updated = self.payment_term_service.repo.update_by_id(payment_term)
        if updated is None:
            raise_concurrent_write_race(
                entity_label="PaymentTerm", entity_id=payment_term.id, path_label=path_label
            )
        return updated

    def _resolve_mapping_state(self, *, payment_term_id: int, qbo_term: QboTerm):
        """
        Read-only check of the TermPaymentTerm mapping table against a dbo-identity
        match, BEFORE any write happens (U-282 fast path). Must run before
        `_apply_payment_term_fields` — writing to the dbo-identity-matched PaymentTerm
        first and detecting a conflict afterward would corrupt that PaymentTerm's data in
        the case where the mapping table, not dbo identity, is actually still the correct
        side (U-276 round-3 finding).

        Checks BOTH directions like create_mapping's own 1:1 guards — a
        payment_term_id-only check would miss a stale mapping still binding this
        qbo_term_id to a DIFFERENT PaymentTerm (left behind by an earlier identity
        "theft" — SetPaymentTermQboIdentity's own theft-clear UPDATE does not clean up
        the mapping table).

        NOTE (U-291): no production caller — `sync_from_qbo_term` now passes these same
        accessors straight to `run_identity_fastpath`, which calls the shared
        `resolve_mapping_state` itself (this connector's own migration onto the shared
        helper, the 7th and last hand-rolled copy). Retained as the per-family test seam
        for this file's suite, which calls it by name — mirrors the disposition U-287
        already gave the other 6 families' equivalent wrappers. Disposition booked in
        TODO.md.

        Returns (state, by_payment_term, by_qbo_term) where state is one of:
          "consistent" — a mapping row exists and agrees; caller writes freely.
          "missing"    — no mapping row on either side; caller writes and creates one.
          "conflict"   — the two sides disagree (one or both directions); caller must
                         NOT write to the dbo-identity-matched row — record the conflict
                         and hard-stop instead.

        Only reads read_by_qbo_term_id when by_payment_term doesn't already settle it —
        QboTermId is unique on the mapping table, so a by_payment_term row whose
        qbo_term_id matches IS the row read_by_qbo_term_id would return; fetching it
        again would be a wasted round trip on the common (steady-state, consistent) path.
        """
        by_payment_term = self.mapping_repo.read_by_payment_term_id(payment_term_id)
        if by_payment_term and by_payment_term.qbo_term_id == qbo_term.id:
            return "consistent", by_payment_term, by_payment_term
        by_qbo_term = self.mapping_repo.read_by_qbo_term_id(qbo_term.id)
        if not by_payment_term and not by_qbo_term:
            return "missing", by_payment_term, by_qbo_term
        return "conflict", by_payment_term, by_qbo_term

    def _record_identity_mapping_conflict_issue(
        self,
        *,
        qbo_term: QboTerm,
        dbo_payment_term_id: int,
        local_side_mapping: Optional[TermPaymentTerm],
        qbo_side_mapping: Optional[TermPaymentTerm],
    ) -> None:
        record_identity_mapping_conflict(
            self.reconciliation_repo,
            drift_type="payment_term_identity_conflict",
            entity_type="PaymentTerm",
            mapping_label="TermPaymentTerm",
            qbo_label="QboTerm",
            dbo_id=dbo_payment_term_id,
            qbo_row_id=qbo_term.id,
            raw_qbo_id=qbo_term.qbo_id,
            raw_realm_id=qbo_term.realm_id,
            realm_id=qbo_term.realm_id,
            local_side_mapping=local_side_mapping,
            qbo_side_mapping=qbo_side_mapping,
            qbo_side_local_fk_attr="payment_term_id",
            local_side_qbo_fk_attr="qbo_term_id",
        )

    def create_mapping(
        self,
        payment_term_id: int,
        qbo_term_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
        active: Optional[bool] = None,
    ) -> TermPaymentTerm:
        """
        Create a mapping between PaymentTerm and QboTerm.
        
        Args:
            payment_term_id: Database ID of PaymentTerm record
            qbo_term_id: Database ID of QboTerm record
        
        Returns:
            TermPaymentTerm: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_payment_term = self.mapping_repo.read_by_payment_term_id(payment_term_id)
        if existing_by_payment_term:
            raise ValueError(
                f"PaymentTerm {payment_term_id} is already mapped to QboTerm {existing_by_payment_term.qbo_term_id}"
            )
        
        existing_by_qbo_term = self.mapping_repo.read_by_qbo_term_id(qbo_term_id)
        if existing_by_qbo_term:
            raise ValueError(
                f"QboTerm {qbo_term_id} is already mapped to PaymentTerm {existing_by_qbo_term.payment_term_id}"
            )
        
        self.payment_term_service.repo.set_qbo_identity(
            id=payment_term_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
            active=active,
        )
        return self.mapping_repo.create(payment_term_id=payment_term_id, qbo_term_id=qbo_term_id)

    def get_mapping_by_payment_term_id(self, payment_term_id: int) -> Optional[TermPaymentTerm]:
        """
        Get mapping by PaymentTerm ID.
        """
        return self.mapping_repo.read_by_payment_term_id(payment_term_id)

    def get_mapping_by_qbo_term_id(self, qbo_term_id: int) -> Optional[TermPaymentTerm]:
        """
        Get mapping by QboTerm ID.
        """
        return self.mapping_repo.read_by_qbo_term_id(qbo_term_id)
