# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.customer.connector.project.business.model import CustomerProject
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from integrations.intuit.qbo.customer.connector.customer.persistence.repo import CustomerCustomerRepository
from integrations.intuit.qbo.customer.business.model import QboCustomer
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.physical_address.connector.business.service import PhysicalAddressAddressConnector
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.project.business.service import ProjectService
from entities.project.business.model import Project
from entities.project_address.business.service import ProjectAddressService

logger = logging.getLogger(__name__)

# Address type IDs (these would typically come from a lookup table)
ADDRESS_TYPE_BILLING = 1
ADDRESS_TYPE_SHIPPING = 2


class CustomerProjectConnector:
    """
    Connector service for synchronization between QboCustomer and Project modules.
    Handles job/sub-customer QBO Customers (Job=true) mapping to Project.
    
    Also syncs addresses from QboPhysicalAddress to Address via ProjectAddress.
    """

    def __init__(
        self,
        mapping_repo: Optional[CustomerProjectRepository] = None,
        project_service: Optional[ProjectService] = None,
        project_address_service: Optional[ProjectAddressService] = None,
        address_connector: Optional[PhysicalAddressAddressConnector] = None,
        customer_mapping_repo: Optional[CustomerCustomerRepository] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the CustomerProjectConnector."""
        self.mapping_repo = mapping_repo or CustomerProjectRepository()
        self.project_service = project_service or ProjectService()
        self.project_address_service = project_address_service or ProjectAddressService()
        self.address_connector = address_connector or PhysicalAddressAddressConnector()
        self.customer_mapping_repo = customer_mapping_repo or CustomerCustomerRepository()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_customer(self, qbo_customer: QboCustomer) -> Project:
        """
        Sync data from QboCustomer to Project module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Project accordingly
        3. Syncs addresses (BillAddr, ShipAddr) to Address via ProjectAddress
        
        Args:
            qbo_customer: QboCustomer record (must be a job/sub-customer with Job=true)
        
        Returns:
            Project: The synced Project record
        
        Raises:
            ValueError: If the customer has Job=false (is not a job/sub-customer)
        """
        if not qbo_customer.is_job:
            raise ValueError(f"QboCustomer {qbo_customer.id} has Job=false and is not a job/sub-customer")
        
        # Map QBO Customer fields to Project module fields
        project_name = qbo_customer.display_name or qbo_customer.company_name or ""
        project_description = qbo_customer.notes or ""
        project_status = "active" if qbo_customer.active else "inactive"
        
        # Find the parent Customer ID if this job has a parent
        customer_id = None
        if qbo_customer.parent_ref_value:
            # Look up the parent QboCustomer and its mapping to Customer
            qbo_customer_repo = QboCustomerRepository()
            parent_qbo_customer = qbo_customer_repo.read_by_qbo_id(qbo_customer.parent_ref_value)
            if parent_qbo_customer:
                parent_mapping = self.customer_mapping_repo.read_by_qbo_customer_id(parent_qbo_customer.id)
                if parent_mapping:
                    customer_id = parent_mapping.customer_id
                    logger.debug(f"Found parent Customer {customer_id} for Project")
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_customer_id(qbo_customer.id)

        if mapping:
            # Found existing mapping - update the Project
            project = self.project_service.read_by_id(mapping.project_id)
            if project:
                logger.info(f"Updating existing Project {project.id} from QboCustomer {qbo_customer.id}")
                return self._apply_project_fields_and_sync(
                    project,
                    qbo_customer=qbo_customer,
                    name=project_name,
                    description=project_description,
                    status=project_status,
                    customer_id=customer_id,
                )
            else:
                # Mapping exists but the bound Project is missing (a transient empty-read,
                # or a renamed/deleted project). HEAL in place — do NOT delete-then-maybe-
                # -create: a delete here plus a name-miss below would MINT A DUPLICATE
                # dbo.Project and orphan the real row (the 2026-07-08 OHR2-CHAPEL failure).
                # Re-resolve by name FIRST; repoint the stale mapping in place (via update,
                # never delete) only once a replacement binding is confirmed.
                replacement = self.project_service.read_by_name(project_name)
                if replacement:
                    existing_map_for_replacement = self.mapping_repo.read_by_project_id(replacement.id)
                    if existing_map_for_replacement and existing_map_for_replacement.qbo_customer_id != qbo_customer.id:
                        # Replacement Project is already bound to a DIFFERENT QboCustomer — a
                        # genuine QBO-side duplicate sub-customer. Do NOT repoint (would break
                        # the 1:1 project<->customer mapping). Record the issue, mutate nothing.
                        self._raise_duplicate_qbo_customer_issue(
                            qbo_customer=qbo_customer,
                            local_project=replacement,
                            existing_mapping=existing_map_for_replacement,
                        )
                        return replacement
                    # Replacement is unbound (or already bound to THIS QboCustomer) — repoint
                    # the stale mapping to it IN PLACE (no delete, no window).
                    if mapping.project_id != replacement.id:
                        old_project_id = mapping.project_id
                        mapping.project_id = replacement.id
                        self.mapping_repo.update_by_id(mapping)
                        logger.info(
                            f'Healed CustomerProject mapping {mapping.id}: repointed QboCustomer '
                            f'{qbo_customer.id} from missing Project {old_project_id} to Project '
                            f'{replacement.id} ({project_name})'
                        )
                    return self._apply_project_fields_and_sync(
                        replacement,
                        qbo_customer=qbo_customer,
                        name=project_name,
                        description=project_description,
                        status=project_status,
                        customer_id=customer_id,
                    )
                # No replacement Project resolvable — a transient empty-read must NOT mint a
                # duplicate. Do NOT delete the mapping, do NOT create a Project. Record a
                # reconciliation issue and raise so the caller's per-item handler logs + skips
                # and the row retries next tick (heals naturally if the read was transient).
                self._raise_missing_project_issue(qbo_customer=qbo_customer, mapping=mapping)
                raise ValueError(
                    f'CustomerProject mapping {mapping.id} points at missing Project '
                    f'{mapping.project_id} and no local Project named "{project_name}" could be '
                    f'resolved for QboCustomer {qbo_customer.id}; preserving mapping, skipping.'
                )

        # No mapping. Before creating a fresh Project, look for a matching local row
        # by exact (case-insensitive — SQL Server default collation) Name. This
        # catches the original-import-time gap where dbo.Project rows exist with
        # no qbo.CustomerProject paired row (e.g. 10 of 11 known dup-set names
        # as of 2026-05-28). See docs/dedupe-project-rows.md.
        existing_local = self.project_service.read_by_name(project_name)
        if existing_local:
            existing_mapping_for_local = self.mapping_repo.read_by_project_id(existing_local.id)
            if existing_mapping_for_local:
                # Local Project is already bound to a different QboCustomer. This
                # is a genuine QBO-side duplicate sub-customer — do not create a
                # new local row and do not auto-rebind. Raise a reconciliation
                # issue so a human can resolve it (typically by merging the
                # duplicate in QBO).
                self._raise_duplicate_qbo_customer_issue(
                    qbo_customer=qbo_customer,
                    local_project=existing_local,
                    existing_mapping=existing_mapping_for_local,
                )
                return existing_local

            # Local Project exists with no QBO mapping — bind it.
            logger.info(
                f"Binding existing local Project {existing_local.id} ({project_name}) "
                f"to QboCustomer {qbo_customer.id} by name match"
            )
            self.create_mapping(project_id=existing_local.id, qbo_customer_id=qbo_customer.id)
            self._sync_addresses(qbo_customer, existing_local.id)
            return existing_local

        # No existing local Project — create one and pair it with a mapping.
        logger.info(f"Creating new Project from QboCustomer {qbo_customer.id}: name={project_name}")
        project = self.project_service.create(
            name=project_name,
            description=project_description,
            status=project_status,
            customer_id=customer_id
        )

        project_id = int(project.id) if isinstance(project.id, str) else project.id
        # The mapping MUST land for the Project to be reachable on the next sync.
        # Previously this swallowed ValueError and left the Project orphaned,
        # producing a fresh duplicate on every subsequent run. Now we surface
        # the failure so the caller's per-item handler logs + skips and the
        # row can be retried next tick (with the orphaned Project now visible
        # to the name-match branch above).
        try:
            mapping = self.create_mapping(project_id=project_id, qbo_customer_id=qbo_customer.id)
            logger.info(f"Created mapping: Project {project_id} <-> QboCustomer {qbo_customer.id}")
        except ValueError as e:
            logger.error(
                f"Mapping creation failed after Project {project_id} create "
                f"(QboCustomer {qbo_customer.id}): {e}. Leaving Project for "
                f"name-match rebind on next sync."
            )
            raise

        self._sync_addresses(qbo_customer, project_id)

        return project

    def _apply_project_fields_and_sync(
        self,
        project: Project,
        *,
        qbo_customer: QboCustomer,
        name: str,
        description: str,
        status: str,
        customer_id: Optional[int],
    ) -> Project:
        """
        Write the QboCustomer-derived fields onto an existing Project, persist it,
        and sync its addresses. Shared by the normal existing-mapping update path
        and the heal-in-place repoint path so the QboCustomer->Project field mapping
        lives in exactly one place (no drift between the two update sites).
        """
        project.name = name
        project.description = description
        project.status = status
        project.customer_id = customer_id
        updated = self.project_service.repo.update_by_id(project)
        self._sync_addresses(qbo_customer, updated.id)
        return updated

    def _record_reconciliation_issue(
        self,
        *,
        drift_type: str,
        entity_public_id: Optional[str],
        qbo_customer: QboCustomer,
        details: str,
    ) -> None:
        """
        Insert a critical qbo.ReconciliationIssue for a manual-review Project-mapping
        drift, failure-isolated: a failed insert is logged loud but never breaks the
        sync. Shared scaffold for the two detectors below (duplicate-sub-customer and
        orphaned-mapping) — only drift_type / entity_public_id / details vary.
        """
        try:
            self.reconciliation_repo.create(
                drift_type=drift_type,
                severity="critical",
                action="manual_review",
                entity_type="Project",
                entity_public_id=entity_public_id,
                qbo_id=str(qbo_customer.qbo_id) if qbo_customer.qbo_id else None,
                realm_id=qbo_customer.realm_id or "",
                details=details,
            )
            logger.warning(details)
        except Exception as exc:
            # Don't break the sync because reconciliation insert failed. Log loud.
            logger.error(f"Failed to record reconciliation issue: {exc}. Details: {details}")

    def _raise_duplicate_qbo_customer_issue(
        self,
        *,
        qbo_customer: QboCustomer,
        local_project: Project,
        existing_mapping: CustomerProject,
    ) -> None:
        """
        Record a duplicate-sub-customer detection on qbo.ReconciliationIssue.

        Triggered when a fresh QboCustomer pull finds an existing local Project
        by exact name match but that Project is already bound to a different
        QboCustomer. Treated as critical because every subsequent sync will
        re-detect it until resolved upstream in QBO.
        """
        details = (
            f"Duplicate QBO sub-customer detected. QboCustomer {qbo_customer.id} "
            f"(QboId={qbo_customer.qbo_id}, DisplayName='{qbo_customer.display_name}') "
            f"name-matches local Project {local_project.id} which is already bound to "
            f"QboCustomer {existing_mapping.qbo_customer_id}. Resolve by merging or "
            f"renaming one of the QBO sub-customers."
        )
        self._record_reconciliation_issue(
            drift_type="duplicate_qbo_customer",
            entity_public_id=str(local_project.public_id) if local_project.public_id else None,
            qbo_customer=qbo_customer,
            details=details,
        )

    def _raise_missing_project_issue(self, *, qbo_customer: QboCustomer, mapping: CustomerProject) -> None:
        """
        Record an orphaned-mapping detection on qbo.ReconciliationIssue.

        Triggered when a CustomerProject mapping exists but its bound Project is
        missing AND no local Project can be resolved by name to repoint it to.
        We deliberately do NOT delete the mapping or create a Project here (a
        transient empty-read would otherwise mint a duplicate); the row is left
        intact for a human to resolve / the next tick to heal.
        """
        details = (
            f"Orphaned CustomerProject mapping. Mapping {mapping.id} (QboCustomer "
            f"{qbo_customer.id}, QboId={qbo_customer.qbo_id}, DisplayName="
            f"'{qbo_customer.display_name}') points at Project {mapping.project_id} which no "
            f"longer reads, and no local Project name-matches to repoint it. Mapping preserved; "
            f"no Project created. Investigate whether the Project was deleted/renamed."
        )
        self._record_reconciliation_issue(
            drift_type="orphaned_customer_project_mapping",
            entity_public_id=None,
            qbo_customer=qbo_customer,
            details=details,
        )

    def _sync_addresses(self, qbo_customer: QboCustomer, project_id: int) -> None:
        """
        Sync billing and shipping addresses from QboCustomer to ProjectAddress/Address.
        
        Args:
            qbo_customer: QboCustomer with bill_addr_id and ship_addr_id
            project_id: Database ID of the Project
        """
        # Sync billing address
        if qbo_customer.bill_addr_id:
            try:
                address = self.address_connector.sync_from_qbo_to_address(qbo_customer.bill_addr_id)
                address_id = int(address.id) if isinstance(address.id, str) else address.id
                self._ensure_project_address(project_id, address_id, ADDRESS_TYPE_BILLING)
                logger.debug(f"Synced billing address {address_id} for Project {project_id}")
            except Exception as e:
                logger.error(f"Failed to sync billing address for Project {project_id}: {e}")
        
        # Sync shipping address
        if qbo_customer.ship_addr_id:
            try:
                address = self.address_connector.sync_from_qbo_to_address(qbo_customer.ship_addr_id)
                address_id = int(address.id) if isinstance(address.id, str) else address.id
                self._ensure_project_address(project_id, address_id, ADDRESS_TYPE_SHIPPING)
                logger.debug(f"Synced shipping address {address_id} for Project {project_id}")
            except Exception as e:
                logger.error(f"Failed to sync shipping address for Project {project_id}: {e}")

    def _ensure_project_address(self, project_id: int, address_id: int, address_type_id: int) -> None:
        """
        Ensure a ProjectAddress record exists linking Project to Address.
        Creates if not exists, updates if exists with different address.
        
        Args:
            project_id: Database ID of the Project
            address_id: Database ID of the Address
            address_type_id: Type of address (billing/shipping)
        """
        # Check for existing ProjectAddress by project_id and address_type
        existing_addresses = self.project_address_service.read_by_project_id(project_id)
        existing = None
        for pa in existing_addresses:
            if pa.address_type_id == address_type_id:
                existing = pa
                break
        
        if existing:
            if existing.address_id != address_id:
                # Update with new address
                existing.address_id = address_id
                self.project_address_service.repo.update_by_id(existing)
                logger.debug(f"Updated ProjectAddress {existing.id} with new address {address_id}")
        else:
            # Create new ProjectAddress
            self.project_address_service.create(
                project_id=project_id,
                address_id=address_id,
                address_type_id=address_type_id
            )
            logger.debug(f"Created ProjectAddress for Project {project_id}, Address {address_id}, Type {address_type_id}")

    def create_mapping(self, project_id: int, qbo_customer_id: int) -> CustomerProject:
        """
        Create a mapping between Project and QboCustomer.
        
        Args:
            project_id: Database ID of Project record
            qbo_customer_id: Database ID of QboCustomer record
        
        Returns:
            CustomerProject: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_project = self.mapping_repo.read_by_project_id(project_id)
        if existing_by_project:
            raise ValueError(
                f"Project {project_id} is already mapped to QboCustomer {existing_by_project.qbo_customer_id}"
            )
        
        existing_by_qbo_customer = self.mapping_repo.read_by_qbo_customer_id(qbo_customer_id)
        if existing_by_qbo_customer:
            raise ValueError(
                f"QboCustomer {qbo_customer_id} is already mapped to Project {existing_by_qbo_customer.project_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(project_id=project_id, qbo_customer_id=qbo_customer_id)

    def get_mapping_by_project_id(self, project_id: int) -> Optional[CustomerProject]:
        """
        Get mapping by Project ID.
        """
        return self.mapping_repo.read_by_project_id(project_id)

    def get_mapping_by_qbo_customer_id(self, qbo_customer_id: int) -> Optional[CustomerProject]:
        """
        Get mapping by QboCustomer ID.
        """
        return self.mapping_repo.read_by_qbo_customer_id(qbo_customer_id)

    def heal_missing_mapping(self, qbo_customer) -> Optional[Project]:
        '''
        Auto-heal a MISSING CustomerProject mapping for a QboCustomer by binding an
        existing local Project matched EXACTLY by name. NEVER creates a new Project.

        Returns the bound Project, or None when no local Project can be resolved
        (callers must fail loud rather than mint). Shared by the invoice-pull
        connector to close the no-invoice window on a (possibly transient) missing
        mapping without duplicating the bind recipe.
        '''
        # Only job/sub-customers map to Projects (parity with sync_from_qbo_customer's
        # is_job gate at the top of this class). A non-job (top-level) customer must NOT be
        # name-bound to a Project — return None so the invoice caller fails loud instead of
        # wrong-binding an invoice onto an unrelated Project that merely shares a name.
        if not qbo_customer.is_job:
            return None
        project_name = qbo_customer.display_name or qbo_customer.company_name or ''
        if not project_name:
            return None
        existing_local = self.project_service.read_by_name(project_name)
        if not existing_local:
            return None
        existing_mapping_for_local = self.mapping_repo.read_by_project_id(existing_local.id)
        if existing_mapping_for_local:
            if existing_mapping_for_local.qbo_customer_id == qbo_customer.id:
                return existing_local  # already correctly mapped
            # Bound to a DIFFERENT QboCustomer — genuine duplicate; do NOT rebind.
            self._raise_duplicate_qbo_customer_issue(
                qbo_customer=qbo_customer,
                local_project=existing_local,
                existing_mapping=existing_mapping_for_local,
            )
            return None
        self.create_mapping(project_id=existing_local.id, qbo_customer_id=qbo_customer.id)
        self._sync_addresses(qbo_customer, existing_local.id)
        logger.info(
            f'Auto-healed missing CustomerProject mapping: bound Project {existing_local.id} '
            f'({project_name}) to QboCustomer {qbo_customer.id} by name match'
        )
        return existing_local
