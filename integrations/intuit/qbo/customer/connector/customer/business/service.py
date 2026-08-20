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
from integrations.intuit.qbo.customer.connector.customer.business.model import CustomerCustomer
from integrations.intuit.qbo.customer.connector.customer.persistence.repo import CustomerCustomerRepository
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.customer.business.service import CustomerService
from entities.customer.business.model import Customer

logger = logging.getLogger(__name__)


class CustomerCustomerConnector:
    """
    Connector service for synchronization between QboCustomer and Customer modules.
    Handles parent QBO Customers (Job=false) mapping to Customer.
    """

    def __init__(
        self,
        mapping_repo: Optional[CustomerCustomerRepository] = None,
        customer_service: Optional[CustomerService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the CustomerCustomerConnector."""
        self.mapping_repo = mapping_repo or CustomerCustomerRepository()
        self.customer_service = customer_service or CustomerService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_customer(self, qbo_customer: QboCustomer) -> Customer:
        """
        Sync data from QboCustomer to Customer module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Customer accordingly
        
        Args:
            qbo_customer: QboCustomer record (must be a parent customer with Job=false)
        
        Returns:
            Customer: The synced Customer record
        
        Raises:
            ValueError: If the customer has Job=true (is not a parent customer)
        """
        if qbo_customer.is_job:
            raise ValueError(f"QboCustomer {qbo_customer.id} has Job=true and is not a parent customer")
        
        # Map QBO Customer fields to Customer module fields
        customer_name = qbo_customer.display_name or qbo_customer.company_name or ""
        customer_email = qbo_customer.primary_email_addr or ""
        customer_phone = qbo_customer.primary_phone or qbo_customer.mobile or ""

        # U-276 (Phase-4 pilot): resolve identity directly against dbo.Customer's
        # native QboId/RealmId (U-238c) before falling back to the
        # qbo.CustomerCustomer mapping-table hop below. Every Customer synced
        # even once already carries this identity (set_qbo_identity is called
        # on both the update and create paths below), so this covers the
        # steady-state case without touching qbo.Customer at all.
        #
        # The mapping-table state is checked BEFORE any write, not after:
        # writing to the dbo-identity-matched Customer first and detecting a
        # conflict afterward would corrupt that Customer's Name/Email/Phone in
        # the case where the mapping table — not dbo identity — is actually
        # still the correct side (round-3 review finding). On a detected
        # conflict we record it and deliberately do NOT return here; falling
        # through to the pre-existing mapping-table path below is the safe
        # choice, since that path — and CustomerProjectConnector's own
        # parent-Customer lookup — still trusts the mapping table.
        direct = (
            self.customer_service.read_by_qbo_identity(qbo_customer.qbo_id, qbo_customer.realm_id)
            if qbo_customer.qbo_id else None
        )
        if direct:
            state, by_customer, by_qbo_customer = self._resolve_mapping_state(
                customer_id=coerce_id(direct.id), qbo_customer=qbo_customer
            )
            if state == "conflict":
                self._raise_identity_mapping_conflict_issue(
                    qbo_customer=qbo_customer,
                    dbo_customer_id=coerce_id(direct.id),
                    local_side_mapping=by_customer,
                    qbo_side_mapping=by_qbo_customer,
                )
                # HARD STOP — do NOT fall through to the legacy mapping-table path.
                # That path would either update a DIFFERENT Customer and call
                # set_qbo_identity (which SetCustomerQboIdentity's theft-clear UPDATE
                # applies against ANY row carrying that (QboId, RealmId) pair, silently
                # NULLing `direct`'s identity), or mint a DUPLICATE Customer via the
                # CREATE path when no mapping exists for this QboCustomer. Never proceed
                # past a confirmed conflict — a human resolves which side is correct; the
                # recorded reconciliation issue is the durable follow-up (U-276 hotfix,
                # 2026-08-20 — this fall-through identity-theft bug shipped in the pilot
                # and was caught by U-278's review of the mirrored vendorcredit unit).
                raise ValueError(
                    f"CustomerCustomer identity conflict for QboCustomer "
                    f"{qbo_customer.qbo_id} (id={qbo_customer.id}): dbo.Customer "
                    f"{direct.id} already carries this identity but the mapping table "
                    f"disagrees. Not auto-repointed; see the recorded reconciliation "
                    f"issue. Skipping until a human resolves it."
                )
            else:
                logger.info(
                    f"Updating existing Customer {direct.id} from QboCustomer {qbo_customer.id} "
                    f"(direct dbo identity match)"
                )
                direct.name = preserve_human_edited_name(direct.name, customer_name)
                direct.email = customer_email
                direct.phone = customer_phone
                customer = self.customer_service.repo.update_by_id(direct)
                if state == "missing":
                    try:
                        self.mapping_repo.create(
                            customer_id=coerce_id(customer.id), qbo_customer_id=qbo_customer.id
                        )
                    except Exception as e:
                        # A concurrent sync may have raced this exact QboCustomer between
                        # the "missing" check above and this create (round-4 review) — no
                        # sp_getapplock serializes create_mapping()'s call sites (a known,
                        # pre-existing gap tracked in TODO.md's U-238a follow-ups). Re-check
                        # rather than assume: if it's now a real conflict, record it properly
                        # instead of a bare warning; otherwise surface the raw failure loud.
                        logger.error(
                            f"CustomerCustomer mapping create failed for Customer "
                            f"{customer.id} after a 'missing' pre-check: {e}"
                        )
                        recheck_state, recheck_by_customer, recheck_by_qbo_customer = (
                            self._resolve_mapping_state(
                                customer_id=coerce_id(customer.id), qbo_customer=qbo_customer
                            )
                        )
                        if recheck_state == "conflict":
                            self._raise_identity_mapping_conflict_issue(
                                qbo_customer=qbo_customer,
                                dbo_customer_id=coerce_id(customer.id),
                                local_side_mapping=recheck_by_customer,
                                qbo_side_mapping=recheck_by_qbo_customer,
                            )
                return customer

        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_customer_id(qbo_customer.id)
        
        if mapping:
            # Found existing mapping - update the Customer
            customer = self.customer_service.read_by_id(mapping.customer_id)
            if customer:
                logger.info(f"Updating existing Customer {customer.id} from QboCustomer {qbo_customer.id}")
                customer.name = preserve_human_edited_name(customer.name, customer_name)
                customer.email = customer_email
                customer.phone = customer_phone
                customer = self.customer_service.repo.update_by_id(customer)
                self.customer_service.repo.set_qbo_identity(
                    id=coerce_id(customer.id),
                    qbo_id=qbo_customer.qbo_id,
                    realm_id=qbo_customer.realm_id,
                )
                return customer
            else:
                # Mapping exists but Customer not found - recreate Customer
                logger.warning(f"Mapping exists but Customer {mapping.customer_id} not found. Creating new Customer.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new Customer
        # Deactivation guard (U-219): no adopt path; directly before create.
        raise_if_inactive_unmapped(
            qbo_customer.active,
            qbo_label="QboCustomer",
            qbo_id=qbo_customer.id,
            target="Customer",
        )
        logger.info(f"Creating new Customer from QboCustomer {qbo_customer.id}: name={customer_name}")
        customer = self.customer_service.create(
            name=customer_name,
            email=customer_email,
            phone=customer_phone
        )
        
        # Create mapping
        customer_id = coerce_id(customer.id)
        try:
            mapping = self.create_mapping(
                customer_id=customer_id,
                qbo_customer_id=qbo_customer.id,
                qbo_id=qbo_customer.qbo_id,
                realm_id=qbo_customer.realm_id,
            )
            logger.info(f"Created mapping: Customer {customer_id} <-> QboCustomer {qbo_customer.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        return customer

    def _resolve_mapping_state(self, *, customer_id: int, qbo_customer: QboCustomer):
        """
        Read-only check of the CustomerCustomer mapping table against a
        dbo-identity match, BEFORE any write happens (U-276 fast path). Must
        run before the Name/Email/Phone update — writing to the
        dbo-identity-matched Customer first and detecting a conflict
        afterward would corrupt that Customer's data in the case where the
        mapping table, not dbo identity, is actually still the correct side
        (round-3 review finding).

        Checks BOTH directions like create_mapping's own 1:1 guards — a
        customer_id-only check would miss a stale mapping still binding this
        qbo_customer_id to a DIFFERENT Customer (left behind by an earlier
        identity "theft" — see SetCustomerQboIdentity's own theft-clear
        UPDATE, which does not clean up the mapping table). A stale entry
        here also feeds CustomerProjectConnector's parent-Customer lookup, so
        leaving it undetected can bind job Projects to the wrong Customer.

        Returns (state, by_customer, by_qbo_customer) — see
        CustomerProjectConnector._resolve_mapping_state for the state
        semantics (this mirrors it exactly).

        Only reads read_by_qbo_customer_id when by_customer doesn't already
        settle it — QboCustomerId is unique on the mapping table, so a
        by_customer row whose qbo_customer_id matches IS the row
        read_by_qbo_customer_id would return; fetching it again would be a
        wasted round trip on the common (steady-state, consistent) path,
        which is exactly the path this whole fast path exists to keep cheap.
        """
        by_customer = self.mapping_repo.read_by_customer_id(customer_id)
        if by_customer and by_customer.qbo_customer_id == qbo_customer.id:
            return "consistent", by_customer, by_customer
        by_qbo_customer = self.mapping_repo.read_by_qbo_customer_id(qbo_customer.id)
        if not by_customer and not by_qbo_customer:
            return "missing", by_customer, by_qbo_customer
        return "conflict", by_customer, by_qbo_customer

    def _raise_identity_mapping_conflict_issue(
        self,
        *,
        qbo_customer: QboCustomer,
        dbo_customer_id: int,
        local_side_mapping: Optional[CustomerCustomer],
        qbo_side_mapping: Optional[CustomerCustomer],
    ) -> None:
        """
        Record a dbo-identity <-> mapping-table split found by
        _resolve_mapping_state. Mirrors CustomerProjectConnector's identically
        named/shaped method — covers all three conflict shapes (qbo-side only,
        local-side only, or both) in ONE issue, never silently dropping
        either side's blocker.
        """
        parts = [
            f"CustomerCustomer identity conflict. dbo.Customer {dbo_customer_id} carries native "
            f"QBO identity for QboCustomer {qbo_customer.id} (QboId={qbo_customer.qbo_id}, "
            f"RealmId={qbo_customer.realm_id})."
        ]
        if qbo_side_mapping:
            parts.append(
                f"qbo-side: the mapping table still binds that same QboCustomer to a DIFFERENT "
                f"Customer {qbo_side_mapping.customer_id} (mapping {qbo_side_mapping.id}) — "
                f"CustomerProjectConnector's parent-Customer lookup will keep resolving to Customer "
                f"{qbo_side_mapping.customer_id}, not {dbo_customer_id}, until repointed."
            )
        if local_side_mapping:
            parts.append(
                f"local-side: Customer {dbo_customer_id}'s own mapping row (mapping "
                f"{local_side_mapping.id}) still binds it to a DIFFERENT QboCustomer "
                f"{local_side_mapping.qbo_customer_id}."
            )
        parts.append("Not auto-repointed — investigate which side is correct.")
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="customer_identity_conflict",
            entity_type="Customer",
            entity_public_id=None,
            qbo_id=str(qbo_customer.qbo_id) if qbo_customer.qbo_id else None,
            realm_id=qbo_customer.realm_id or "",
            details=" ".join(parts),
        )

    def create_mapping(
        self,
        customer_id: int,
        qbo_customer_id: int,
        *,
        qbo_id: Optional[str],
        realm_id: Optional[str],
    ) -> CustomerCustomer:
        """
        Create a mapping between Customer and QboCustomer.
        
        Args:
            customer_id: Database ID of Customer record
            qbo_customer_id: Database ID of QboCustomer record
        
        Returns:
            CustomerCustomer: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_customer = self.mapping_repo.read_by_customer_id(customer_id)
        if existing_by_customer:
            raise ValueError(
                f"Customer {customer_id} is already mapped to QboCustomer {existing_by_customer.qbo_customer_id}"
            )
        
        existing_by_qbo_customer = self.mapping_repo.read_by_qbo_customer_id(qbo_customer_id)
        if existing_by_qbo_customer:
            raise ValueError(
                f"QboCustomer {qbo_customer_id} is already mapped to Customer {existing_by_qbo_customer.customer_id}"
            )
        
        self.customer_service.repo.set_qbo_identity(
            id=customer_id,
            qbo_id=qbo_id,
            realm_id=realm_id,
        )
        return self.mapping_repo.create(customer_id=customer_id, qbo_customer_id=qbo_customer_id)

    def get_mapping_by_customer_id(self, customer_id: int) -> Optional[CustomerCustomer]:
        """
        Get mapping by Customer ID.
        """
        return self.mapping_repo.read_by_customer_id(customer_id)

    def get_mapping_by_qbo_customer_id(self, qbo_customer_id: int) -> Optional[CustomerCustomer]:
        """
        Get mapping by QboCustomer ID.
        """
        return self.mapping_repo.read_by_qbo_customer_id(qbo_customer_id)
