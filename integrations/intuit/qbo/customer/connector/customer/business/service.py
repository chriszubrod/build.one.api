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
    resolve_mapping_state,
    run_identity_fastpath,
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
        # conflict the helper records the issue and RAISES — it never falls
        # through to the mapping-table path below. See base/identity_fastpath.py
        # (U-287): falling through was the 2026-08-20 live-prod P0, because that
        # path would either set_qbo_identity on a DIFFERENT Customer (whose
        # theft-clear UPDATE nulls this one's identity) or mint a duplicate.
        def _apply_customer_fields(entity: Customer) -> Optional[Customer]:
            entity.name = preserve_human_edited_name(entity.name, customer_name)
            entity.email = customer_email
            entity.phone = customer_phone
            return self.customer_service.repo.update_by_id(entity)

        def _on_update_empty(entity: Customer) -> None:
            """
            ROWVERSION race: UpdateCustomerById affected 0 rows, so there is no row to
            map. This MUST fail loud — a silent `return None` reaches project_records,
            which counts a None return as a projected SUCCESS and lets the watermark
            advance past a Customer whose fields were never written and whose mapping
            row was never created (base/sync_outcome.py::project_records).

            RuntimeError, deliberately NOT ValueError: record_projection_error's rule 2
            classifies a plain ValueError as a permanent SKIP (which still advances the
            watermark); rule 3 sends everything else to failure/hold. A ROWVERSION race
            is transient, so hold-and-retry is the correct classification — and it is
            what this path did before U-287, when the missing `None` guard made it blow
            up with an AttributeError here.
            """
            raise RuntimeError(
                f"Failed to update Customer {entity.id} via fast path - update_by_id "
                f"returned None (concurrent write race); holding for retry."
            )

        outcome = run_identity_fastpath(
            qbo_id=qbo_customer.qbo_id,
            realm_id=qbo_customer.realm_id,
            external_id=qbo_customer.id,
            entity_label="Customer",
            external_label="QboCustomer",
            mapping_label="CustomerCustomer",
            read_direct_by_qbo_identity=self.customer_service.read_by_qbo_identity,
            read_by_local_id=self.mapping_repo.read_by_customer_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_customer_id,
            external_id_attr="qbo_customer_id",
            record_conflict_issue=lambda entity, by_local, by_external: (
                self._raise_identity_mapping_conflict_issue(
                    qbo_customer=qbo_customer,
                    dbo_customer_id=coerce_id(entity.id),
                    local_side_mapping=by_local,
                    qbo_side_mapping=by_external,
                )
            ),
            conflict_message=lambda entity: (
                f"CustomerCustomer identity conflict for QboCustomer "
                f"{qbo_customer.qbo_id} (id={qbo_customer.id}): dbo.Customer "
                f"{entity.id} already carries this identity but the mapping table "
                f"disagrees. Not auto-repointed; see the recorded reconciliation "
                f"issue. Skipping until a human resolves it."
            ),
            create_mapping=lambda local_id: self.mapping_repo.create(
                customer_id=local_id, qbo_customer_id=qbo_customer.id
            ),
            apply_fields=_apply_customer_fields,
            on_apply_returned_none=_on_update_empty,
        )
        if outcome.hit:
            return outcome.entity

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

        NOTE (U-287): no production caller — `sync_from_qbo_*` passes these same
        accessors straight to `run_identity_fastpath`, which calls the shared
        `resolve_mapping_state` itself. Retained as the per-family test seam for the
        U-276/277/278/279 suites, which call this by name. Disposition booked in TODO.md.

        Returns (state, by_customer, by_qbo_customer) — see
        base.identity_fastpath.resolve_mapping_state, which owns the algorithm
        (U-287); this is the CustomerCustomer binding of it.
        """
        return resolve_mapping_state(
            local_id=customer_id,
            external_id=qbo_customer.id,
            read_by_local_id=self.mapping_repo.read_by_customer_id,
            read_by_external_id=self.mapping_repo.read_by_qbo_customer_id,
            external_id_attr="qbo_customer_id",
        )

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
