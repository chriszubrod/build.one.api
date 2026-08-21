# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_name,
    raise_if_inactive_unmapped,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
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
        # qbo.Term at all. Mirrors VendorCreditBillCreditConnector.sync_from_qbo_vendor_credit
        # (U-278) exactly — including the hard-stop-on-conflict fix baked in from day one.
        #
        # U-287 (the shared integrations/intuit/qbo/base/identity_fastpath.py helper) is
        # ACTIVELY BEING BUILT by a concurrent session in this same working tree as of this
        # unit — confirmed present (uncommitted) with all 6 prior sibling connectors already
        # repointed onto it. It has NOT shipped (no commit exists for it; `git log` has no
        # U-287 entry) — per the standing project convention, a unit must not take a hard
        # dependency on another session's uncommitted work (it could still change, fail its
        # own Gate-2, or not land this session at all), so this hand-rolls its own copy
        # rather than importing the unshipped module — the same call U-279 made under the
        # identical circumstance earlier this session. FOLLOW-UP (book once U-287 ships):
        # migrate this connector onto run_identity_fastpath(), which will then close the
        # LAST of the 7 hand-rolled copies (the other 6 are already migrated, uncommitted).
        #
        # The mapping-table state is checked BEFORE any write, not after: writing to the
        # dbo-identity-matched PaymentTerm first and detecting a conflict afterward would
        # corrupt that PaymentTerm's data in the case where the mapping table — not dbo
        # identity — is actually still the correct side (U-276 round-3 finding). On a
        # detected conflict we record it and RAISE — never fall through to the legacy
        # mapping-table path, which would call set_qbo_identity on a DIFFERENT row with the
        # same (QboId, RealmId) `direct` already holds; SetPaymentTermQboIdentity's own
        # theft-detection UPDATE would then silently NULL `direct`'s identity (the exact
        # bug U-276's pilot shipped and had to be hotfixed live, 2026-08-20 — never
        # re-copy it).
        direct = (
            self.payment_term_service.read_by_qbo_identity(qbo_term.qbo_id, qbo_term.realm_id)
            if qbo_term.qbo_id else None
        )
        if direct:
            state, by_payment_term, by_qbo_term = self._resolve_mapping_state(
                payment_term_id=coerce_id(direct.id), qbo_term=qbo_term
            )
            if state == "conflict":
                self._raise_identity_mapping_conflict_issue(
                    qbo_term=qbo_term,
                    dbo_payment_term_id=coerce_id(direct.id),
                    local_side_mapping=by_payment_term,
                    qbo_side_mapping=by_qbo_term,
                )
                # HARD STOP — do not fall through to the legacy mapping-table path. That
                # path would either (a) update a DIFFERENT PaymentTerm and then call
                # set_qbo_identity(qbo_id=qbo_term.qbo_id, ...) on it, which
                # SetPaymentTermQboIdentity's own theft-detection UPDATE applies against
                # ANY row carrying that (QboId, RealmId) pair regardless of which row this
                # call targets — silently NULLing `direct`'s identity, the exact
                # corruption this check exists to prevent — or (b) mint a duplicate
                # PaymentTerm via the create path when `by_qbo_term` is None (local-side-
                # only conflict), since `direct` already represents this real-world term.
                # Never proceed past a confirmed conflict — a human must resolve which
                # side is correct first; the recorded reconciliation issue is the durable
                # follow-up, this raise is the safety stop.
                raise ValueError(
                    f"TermPaymentTerm identity conflict for QboTerm {qbo_term.qbo_id} "
                    f"(id={qbo_term.id}): dbo.PaymentTerm {direct.id} already carries "
                    f"this identity but the mapping table disagrees. Not auto-repointed; "
                    f"see the recorded reconciliation issue. Skipping until a human "
                    f"resolves it."
                )
            logger.info(
                f"Updating existing PaymentTerm {direct.id} from QboTerm {qbo_term.id} "
                f"(direct dbo identity match)"
            )
            updated = self._apply_payment_term_fields(
                direct, qbo_term=qbo_term, term_name=term_name, description=description
            )
            if state == "missing" and updated is not None:
                try:
                    self.mapping_repo.create(
                        payment_term_id=coerce_id(updated.id),
                        qbo_term_id=qbo_term.id,
                    )
                except Exception as e:
                    # A concurrent sync may have raced this exact QboTerm between the
                    # "missing" check above and this create (mirrors U-276/278 round-4) —
                    # no sp_getapplock serializes mapping create() call sites. Re-check
                    # rather than assume: if it's now a real conflict, record it properly
                    # instead of a bare warning.
                    logger.error(
                        f"TermPaymentTerm mapping create failed for PaymentTerm "
                        f"{updated.id} after a 'missing' pre-check: {e}"
                    )
                    recheck_state, recheck_by_pt, recheck_by_qbo = self._resolve_mapping_state(
                        payment_term_id=coerce_id(updated.id), qbo_term=qbo_term
                    )
                    if recheck_state == "conflict":
                        self._raise_identity_mapping_conflict_issue(
                            qbo_term=qbo_term,
                            dbo_payment_term_id=coerce_id(updated.id),
                            local_side_mapping=recheck_by_pt,
                            qbo_side_mapping=recheck_by_qbo,
                        )
            elif state == "missing" and updated is None:
                # `direct` read empty on write — a concurrent delete between our
                # read_by_qbo_identity fetch and this write. Nothing to map or stamp;
                # let this tick skip and the next pull heal naturally (transient).
                logger.warning(
                    f"PaymentTerm {direct.id} read empty on write (direct-identity fast "
                    f"path) for QboTerm {qbo_term.id} — likely a concurrent delete; "
                    f"skipping this tick."
                )
            return updated

        # Step 2: Check for existing mapping (legacy path — rows that predate identity
        # stamping, or a globally-unmapped QboTerm with no dbo-identity fast-path hit).
        mapping = self.mapping_repo.read_by_qbo_term_id(qbo_term.id)

        if mapping:
            # Found existing mapping - update the PaymentTerm
            payment_term = self.payment_term_service.read_by_id(mapping.payment_term_id)
            if payment_term:
                logger.info(f"Updating existing PaymentTerm {payment_term.id} from QboTerm {qbo_term.id}")
                updated = self._apply_payment_term_fields(
                    payment_term, qbo_term=qbo_term, term_name=term_name, description=description
                )
                if updated:
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
    ) -> Optional[PaymentTerm]:
        """
        Write the QboTerm-derived fields onto an existing PaymentTerm and persist it.
        Shared by the direct dbo-identity fast path (U-282) and the existing
        mapping-table update path so the QboTerm -> PaymentTerm field mapping lives in
        exactly one place (no drift between the two update sites) — mirrors
        VendorCreditBillCreditConnector._apply_bill_credit_fields_and_sync (U-278).

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
        return self.payment_term_service.repo.update_by_id(payment_term)

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

    def _raise_identity_mapping_conflict_issue(
        self,
        *,
        qbo_term: QboTerm,
        dbo_payment_term_id: int,
        local_side_mapping: Optional[TermPaymentTerm],
        qbo_side_mapping: Optional[TermPaymentTerm],
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by _resolve_mapping_state.
        Covers all three shapes in ONE issue: qbo-side only, local-side only, or both
        (the "two-row crossed" case) — never silently dropping either side's blocker.
        Mirrors VendorCreditBillCreditConnector._raise_identity_mapping_conflict_issue
        (U-278). `drift_type` is a literal string (not the drift_types.py constant) to
        match the established connector convention — the AST-discovery test guard
        (tests/test_qbo_reconciliation_recorder.py) only recognizes string-literal
        `record_mapping_issue` kwargs.
        """
        parts = [
            f"TermPaymentTerm identity conflict. dbo.PaymentTerm {dbo_payment_term_id} "
            f"carries native QBO identity for QboTerm {qbo_term.id} "
            f"(QboId={qbo_term.qbo_id}, RealmId={qbo_term.realm_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboTerm to a "
                f"DIFFERENT PaymentTerm {qbo_side_mapping.payment_term_id} (mapping "
                f"{qbo_side_mapping.id})."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: PaymentTerm {dbo_payment_term_id}'s own mapping row "
                f"(mapping {local_side_mapping.id}) still binds it to a DIFFERENT "
                f"QboTerm {local_side_mapping.qbo_term_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="payment_term_identity_conflict",
            entity_type="PaymentTerm",
            entity_public_id=None,
            qbo_id=str(qbo_term.qbo_id) if qbo_term.qbo_id else None,
            realm_id=qbo_term.realm_id or "",
            details=" ".join(parts),
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
