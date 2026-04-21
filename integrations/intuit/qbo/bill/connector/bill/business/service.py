# Python Standard Library Imports
import logging
from typing import List, Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.bill.connector.bill.business.model import BillBill
from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository
from integrations.intuit.qbo.bill.business.model import QboBill, QboBillLine
from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository, QboBillLineRepository
from integrations.intuit.qbo.bill.external.client import QboBillClient
from integrations.intuit.qbo.bill.external.schemas import (
    QboBillCreate,
    QboBillLine as QboBillLineSchema,
    QboReferenceType,
    QboItemBasedExpenseLineDetail,
    QboAccountBasedExpenseLineDetail,
)
from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.account.persistence.repo import QboAccountRepository
from integrations.intuit.qbo.term.connector.payment_term.persistence.repo import TermPaymentTermRepository
from integrations.intuit.qbo.term.persistence.repo import QboTermRepository
from entities.bill.business.service import BillService
from entities.bill.business.model import Bill
from entities.bill_line_item.business.service import BillLineItemService
from entities.vendor.business.service import VendorService

logger = logging.getLogger(__name__)


class BillBillConnector:
    """
    Connector service for synchronization between QboBill and Bill modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[BillBillRepository] = None,
        bill_service: Optional[BillService] = None,
        vendor_service: Optional[VendorService] = None,
        vendor_vendor_repo: Optional[VendorVendorRepository] = None,
        qbo_vendor_repo: Optional[QboVendorRepository] = None,
        qbo_bill_repo: Optional[QboBillRepository] = None,
        qbo_bill_line_repo: Optional[QboBillLineRepository] = None,
        bill_line_item_service: Optional[BillLineItemService] = None,
        item_sub_cost_code_repo: Optional[ItemSubCostCodeRepository] = None,
        qbo_item_repo: Optional[QboItemRepository] = None,
        customer_project_repo: Optional[CustomerProjectRepository] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        qbo_account_repo: Optional[QboAccountRepository] = None,
        term_payment_term_repo: Optional[TermPaymentTermRepository] = None,
        qbo_term_repo: Optional[QboTermRepository] = None,
    ):
        """Initialize the BillBillConnector."""
        self.mapping_repo = mapping_repo or BillBillRepository()
        self.bill_service = bill_service or BillService()
        self.vendor_service = vendor_service or VendorService()
        self.vendor_vendor_repo = vendor_vendor_repo or VendorVendorRepository()
        self.qbo_vendor_repo = qbo_vendor_repo or QboVendorRepository()
        self.qbo_bill_repo = qbo_bill_repo or QboBillRepository()
        self.qbo_bill_line_repo = qbo_bill_line_repo or QboBillLineRepository()
        self.bill_line_item_service = bill_line_item_service or BillLineItemService()
        self.item_sub_cost_code_repo = item_sub_cost_code_repo or ItemSubCostCodeRepository()
        self.qbo_item_repo = qbo_item_repo or QboItemRepository()
        self.customer_project_repo = customer_project_repo or CustomerProjectRepository()
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        self.qbo_account_repo = qbo_account_repo or QboAccountRepository()
        self.term_payment_term_repo = term_payment_term_repo or TermPaymentTermRepository()
        self.qbo_term_repo = qbo_term_repo or QboTermRepository()

    def sync_from_qbo_bill(self, qbo_bill: QboBill, qbo_bill_lines: List[QboBillLine]) -> Bill:
        """
        Sync data from QboBill to Bill module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Bill accordingly
        3. Syncs line items to BillLineItem module
        
        Args:
            qbo_bill: QboBill record
            qbo_bill_lines: List of QboBillLine records for this bill
        
        Returns:
            Bill: The synced Bill record
        """
        # Find vendor mapping to get Vendor public_id
        vendor_public_id = self._get_vendor_public_id(qbo_bill.vendor_ref_value)
        if not vendor_public_id:
            raise ValueError(f"No vendor mapping found for QBO vendor ref: {qbo_bill.vendor_ref_value}")
        
        # Map QBO Bill fields to Bill module fields
        bill_number = qbo_bill.doc_number or f"QBO-{qbo_bill.qbo_id}"
        bill_date = qbo_bill.txn_date or ""
        due_date = qbo_bill.due_date or ""
        memo = qbo_bill.private_note
        total_amount = qbo_bill.total_amt
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_bill_id(qbo_bill.id)
        
        if mapping:
            # Found existing mapping - update the Bill
            bill = self.bill_service.read_by_id(mapping.bill_id)
            if bill:
                logger.info(f"Updating existing Bill {bill.id} from QboBill {qbo_bill.id}")
                
                bill = self.bill_service.update_by_public_id(
                    bill.public_id,
                    vendor_public_id=vendor_public_id,
                    bill_date=bill_date,
                    due_date=due_date,
                    bill_number=bill_number,
                    total_amount=total_amount,
                    memo=memo,
                    is_draft=False,
                    row_version=bill.row_version,
                )
                
                # Sync line items for existing bill
                self._sync_line_items(bill.id, qbo_bill_lines)
                
                return bill
            else:
                # Mapping exists but Bill not found - recreate Bill
                logger.warning(f"Mapping exists but Bill {mapping.bill_id} not found. Creating new Bill.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new Bill
        logger.info(f"Creating new Bill from QboBill {qbo_bill.id}: bill_number={bill_number}")
        bill = self.bill_service.create(
            vendor_public_id=vendor_public_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=False
        )
        
        # Create mapping
        bill_id = int(bill.id) if isinstance(bill.id, str) else bill.id
        try:
            mapping = self.create_mapping(bill_id=bill_id, qbo_bill_id=qbo_bill.id)
            logger.info(f"Created mapping: Bill {bill_id} <-> QboBill {qbo_bill.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        # Sync line items for new bill
        self._sync_line_items(bill_id, qbo_bill_lines)
        
        return bill

    def _get_vendor_public_id(self, qbo_vendor_ref_value: str) -> Optional[str]:
        """
        Get the Vendor public_id from QBO vendor reference value.
        
        Args:
            qbo_vendor_ref_value: QBO vendor reference value (QBO Vendor ID)
        
        Returns:
            str: Vendor public_id or None
        """
        if not qbo_vendor_ref_value:
            return None
        
        # First find the QboVendor by qbo_id
        qbo_vendor = self.qbo_vendor_repo.read_by_qbo_id(qbo_vendor_ref_value)
        if not qbo_vendor:
            logger.warning(f"QboVendor not found for qbo_id: {qbo_vendor_ref_value}")
            return None
        
        # Then find the VendorVendor mapping
        vendor_mapping = self.vendor_vendor_repo.read_by_qbo_vendor_id(qbo_vendor.id)
        if not vendor_mapping:
            logger.warning(f"VendorVendor mapping not found for QboVendor ID: {qbo_vendor.id}")
            return None
        
        # Get the Vendor
        vendor = self.vendor_service.read_by_id(vendor_mapping.vendor_id)
        if not vendor:
            logger.warning(f"Vendor not found for ID: {vendor_mapping.vendor_id}")
            return None
        
        return vendor.public_id

    def _sync_line_items(self, bill_id: int, qbo_bill_lines: List[QboBillLine]) -> None:
        """
        Sync bill line items to BillLineItem module.
        
        Args:
            bill_id: Database ID of the Bill
            qbo_bill_lines: List of QboBillLine records
        """
        if not qbo_bill_lines:
            return
        
        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector
        
        line_connector = BillLineItemConnector()
        
        for qbo_line in qbo_bill_lines:
            try:
                line_connector.sync_from_qbo_bill_line(bill_id, qbo_line)
            except Exception as e:
                logger.error(f"Failed to sync QboBillLine {qbo_line.id} to BillLineItem: {e}")

    def create_mapping(self, bill_id: int, qbo_bill_id: int) -> BillBill:
        """
        Create a mapping between Bill and QboBill.
        
        Args:
            bill_id: Database ID of Bill record
            qbo_bill_id: Database ID of QboBill record
        
        Returns:
            BillBill: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_bill = self.mapping_repo.read_by_bill_id(bill_id)
        if existing_by_bill:
            raise ValueError(
                f"Bill {bill_id} is already mapped to QboBill {existing_by_bill.qbo_bill_id}"
            )
        
        existing_by_qbo_bill = self.mapping_repo.read_by_qbo_bill_id(qbo_bill_id)
        if existing_by_qbo_bill:
            raise ValueError(
                f"QboBill {qbo_bill_id} is already mapped to Bill {existing_by_qbo_bill.bill_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(bill_id=bill_id, qbo_bill_id=qbo_bill_id)

    def get_mapping_by_bill_id(self, bill_id: int) -> Optional[BillBill]:
        """
        Get mapping by Bill ID.
        """
        return self.mapping_repo.read_by_bill_id(bill_id)

    def get_mapping_by_qbo_bill_id(self, qbo_bill_id: int) -> Optional[BillBill]:
        """
        Get mapping by QboBill ID.
        """
        return self.mapping_repo.read_by_qbo_bill_id(qbo_bill_id)

    def sync_to_qbo_bill(self, bill: Bill, realm_id: str) -> QboBill:
        """
        Sync a local Bill to QuickBooks Online.
        
        This method:
        1. Checks if a mapping already exists (skip if already synced)
        2. Looks up vendor mapping to get QBO vendor reference
        3. Builds QBO Bill payload with line items
        4. Creates Bill in QBO via API
        5. Stores QboBill locally and creates mapping
        
        Args:
            bill: Local Bill record to sync
            realm_id: QBO realm ID for API access
        
        Returns:
            QboBill: The local QboBill record created
            
        Raises:
            ValueError: If mapping lookup fails (vendor not mapped, etc.)
        """
        bill_id = int(bill.id) if isinstance(bill.id, str) else bill.id
        
        # Check if already mapped
        existing_mapping = self.mapping_repo.read_by_bill_id(bill_id)
        if existing_mapping:
            logger.info(f"Bill {bill_id} is already mapped to QboBill {existing_mapping.qbo_bill_id}")
            return self.qbo_bill_repo.read_by_id(existing_mapping.qbo_bill_id)
        
        # Require bill_number — QBO DocNumber must be present; exclude_none=True would silently drop it
        if not bill.bill_number:
            raise ValueError(f"Bill {bill_id} has no bill_number. Set a bill number before syncing to QBO.")

        # Require bill_date — TxnDate must be present; without it QBO silently uses today's date
        if not bill.bill_date:
            raise ValueError(f"Bill {bill_id} has no bill_date. Set a bill date before syncing to QBO.")

        # Get QBO vendor reference
        qbo_vendor_ref = self._get_qbo_vendor_ref(bill.vendor_id)
        if not qbo_vendor_ref:
            raise ValueError(f"No QBO vendor mapping found for vendor_id: {bill.vendor_id}")
        
        # Get bill line items
        bill_line_items = self.bill_line_item_service.read_by_bill_id(bill_id=bill_id)
        
        # Build QBO line items — all line items must have valid mappings.
        # A partial sync is not allowed; if any line item cannot be mapped, the entire sync fails.
        qbo_lines = []
        line_num_to_line_item_id = {}

        if not bill_line_items:
            raise ValueError("Bill has no line items. QBO requires at least one line item.")

        for idx, line_item in enumerate(bill_line_items, start=1):
            qbo_line = self._build_qbo_line(line_item, idx)
            qbo_lines.append(qbo_line)
            line_num_to_line_item_id[idx] = line_item.id
        
        # Get AP Account reference
        ap_account_ref = self._get_ap_account_ref(realm_id)

        # Get SalesTerm reference from PaymentTerm mapping
        sales_term_ref = self._get_qbo_sales_term_ref(bill.payment_term_id)

        # Build QBO Bill create payload
        qbo_bill_create = QboBillCreate(
            vendor_ref=qbo_vendor_ref,
            ap_account_ref=ap_account_ref,
            sales_term_ref=sales_term_ref,
            txn_date=bill.bill_date[:10] if bill.bill_date else None,  # YYYY-MM-DD
            due_date=bill.due_date[:10] if bill.due_date else None,
            doc_number=bill.bill_number,
            private_note=bill.memo,
            line=qbo_lines,
        )
        
        logger.info(f"Creating Bill in QBO for local Bill {bill_id}: doc_number={bill.bill_number}")

        # Log payload for debugging
        payload_dict = qbo_bill_create.model_dump(by_alias=True, exclude_none=True)
        logger.info(f"QBO Bill payload: {payload_dict}")

        from integrations.intuit.qbo.base.errors import QboValidationError

        # QboHttpClient (via QboBillClient) resolves and refreshes the access token
        # lazily, so no upfront auth call is needed here.
        with QboBillClient(realm_id=realm_id) as client:
            try:
                created_bill = client.create_bill(qbo_bill_create)
            except QboValidationError as e:
                if "duplicate" in str(e).lower():
                    # Bill already exists in QBO — query for it and create mapping
                    logger.warning(
                        f"Bill {bill_id} doc_number={bill.bill_number} already exists in QBO. "
                        f"Querying for existing bill to create mapping."
                    )
                    return self._recover_duplicate_qbo_bill(
                        client=client,
                        bill=bill,
                        bill_id=bill_id,
                        realm_id=realm_id,
                        qbo_vendor_ref=qbo_vendor_ref,
                        line_num_to_line_item_id=line_num_to_line_item_id,
                    )
                raise

        logger.info(f"Created QBO Bill {created_bill.id} with SyncToken {created_bill.sync_token}")
        
        # Get vendor name for storage
        vendor = self.vendor_service.read_by_id(bill.vendor_id) if bill.vendor_id else None
        vendor_name = vendor.name if vendor else None
        
        # Store QboBill locally
        local_qbo_bill = self.qbo_bill_repo.create(
            qbo_id=created_bill.id,
            sync_token=created_bill.sync_token,
            realm_id=realm_id,
            vendor_ref_value=qbo_vendor_ref.value,
            vendor_ref_name=vendor_name,
            txn_date=created_bill.txn_date,
            due_date=created_bill.due_date,
            doc_number=created_bill.doc_number,
            private_note=created_bill.private_note,
            total_amt=created_bill.total_amt,
            balance=created_bill.balance,
            ap_account_ref_value=created_bill.ap_account_ref.value if created_bill.ap_account_ref else None,
            ap_account_ref_name=created_bill.ap_account_ref.name if created_bill.ap_account_ref else None,
            sales_term_ref_value=created_bill.sales_term_ref.value if created_bill.sales_term_ref else None,
            sales_term_ref_name=created_bill.sales_term_ref.name if created_bill.sales_term_ref else None,
            currency_ref_value=created_bill.currency_ref.value if created_bill.currency_ref else None,
            currency_ref_name=created_bill.currency_ref.name if created_bill.currency_ref else None,
            exchange_rate=created_bill.exchange_rate,
            department_ref_value=created_bill.department_ref.value if created_bill.department_ref else None,
            department_ref_name=created_bill.department_ref.name if created_bill.department_ref else None,
            global_tax_calculation=created_bill.global_tax_calculation,
        )
        
        logger.info(f"Stored local QboBill {local_qbo_bill.id}")
        
        # Store QboBillLines locally and create line item mappings
        if created_bill.line:
            from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector
            line_connector = BillLineItemConnector()

            for qbo_line in created_bill.line:
                stored_line = self._store_qbo_bill_line(local_qbo_bill.id, qbo_line)

                # Create BillLineItem <-> QboBillLine mapping using line_num match
                if stored_line and qbo_line.line_num and qbo_line.line_num in line_num_to_line_item_id:
                    bill_line_item_id = line_num_to_line_item_id[qbo_line.line_num]
                    stored_line_id = int(stored_line.id) if isinstance(stored_line.id, str) else stored_line.id
                    try:
                        line_connector.create_mapping(
                            bill_line_item_id=bill_line_item_id,
                            qbo_bill_line_id=stored_line_id,
                        )
                        logger.info(f"Created line mapping: BillLineItem {bill_line_item_id} <-> QboBillLine {stored_line_id}")
                    except ValueError as e:
                        logger.warning(f"Could not create line mapping: {e}")
        
        # Create mapping
        qbo_bill_id = int(local_qbo_bill.id) if isinstance(local_qbo_bill.id, str) else local_qbo_bill.id
        try:
            mapping = self.create_mapping(bill_id=bill_id, qbo_bill_id=qbo_bill_id)
            logger.info(f"Created mapping: Bill {bill_id} <-> QboBill {qbo_bill_id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        return local_qbo_bill

    def _recover_duplicate_qbo_bill(
        self,
        client: QboBillClient,
        bill: Bill,
        bill_id: int,
        realm_id: str,
        qbo_vendor_ref,
        line_num_to_line_item_id: dict,
    ) -> Optional[QboBill]:
        """
        Handle duplicate DocNumber: query QBO for the existing bill,
        store it locally, and create the Bill<->QboBill mapping.
        """
        # Query QBO for bills with this doc number
        query = f"SELECT * FROM Bill WHERE DocNumber = '{bill.bill_number}'"
        path = "/query"
        params = {"query": query}
        data = client._request("GET", path, params=params)

        qbo_bills = data.get("QueryResponse", {}).get("Bill", [])
        if not qbo_bills:
            raise ValueError(
                f"QBO reported duplicate DocNumber '{bill.bill_number}' but query returned no matching bills."
            )

        # Use the first match
        from integrations.intuit.qbo.bill.external.schemas import QboBill as QboBillSchema
        existing_qbo_bill = QboBillSchema.model_validate(qbo_bills[0])

        logger.info(f"Found existing QBO Bill {existing_qbo_bill.id} for DocNumber '{bill.bill_number}'")

        # Get vendor name
        vendor = self.vendor_service.read_by_id(bill.vendor_id) if bill.vendor_id else None
        vendor_name = vendor.name if vendor else None

        # Store locally
        local_qbo_bill = self.qbo_bill_repo.create(
            qbo_id=existing_qbo_bill.id,
            sync_token=existing_qbo_bill.sync_token,
            realm_id=realm_id,
            vendor_ref_value=qbo_vendor_ref.value,
            vendor_ref_name=vendor_name,
            txn_date=existing_qbo_bill.txn_date,
            due_date=existing_qbo_bill.due_date,
            doc_number=existing_qbo_bill.doc_number,
            private_note=existing_qbo_bill.private_note,
            total_amt=existing_qbo_bill.total_amt,
            balance=existing_qbo_bill.balance,
            ap_account_ref_value=existing_qbo_bill.ap_account_ref.value if existing_qbo_bill.ap_account_ref else None,
            ap_account_ref_name=existing_qbo_bill.ap_account_ref.name if existing_qbo_bill.ap_account_ref else None,
            sales_term_ref_value=existing_qbo_bill.sales_term_ref.value if existing_qbo_bill.sales_term_ref else None,
            sales_term_ref_name=existing_qbo_bill.sales_term_ref.name if existing_qbo_bill.sales_term_ref else None,
            currency_ref_value=existing_qbo_bill.currency_ref.value if existing_qbo_bill.currency_ref else None,
            currency_ref_name=existing_qbo_bill.currency_ref.name if existing_qbo_bill.currency_ref else None,
            exchange_rate=existing_qbo_bill.exchange_rate,
            department_ref_value=existing_qbo_bill.department_ref.value if existing_qbo_bill.department_ref else None,
            department_ref_name=existing_qbo_bill.department_ref.name if existing_qbo_bill.department_ref else None,
            global_tax_calculation=existing_qbo_bill.global_tax_calculation,
        )

        logger.info(f"Stored local QboBill {local_qbo_bill.id} from existing QBO Bill {existing_qbo_bill.id}")

        # Store lines and create mappings
        if existing_qbo_bill.line:
            from integrations.intuit.qbo.bill.connector.bill_line_item.business.service import BillLineItemConnector
            line_connector = BillLineItemConnector()

            for qbo_line in existing_qbo_bill.line:
                stored_line = self._store_qbo_bill_line(local_qbo_bill.id, qbo_line)
                if stored_line and qbo_line.line_num and qbo_line.line_num in line_num_to_line_item_id:
                    bill_line_item_id = line_num_to_line_item_id[qbo_line.line_num]
                    stored_line_id = int(stored_line.id) if isinstance(stored_line.id, str) else stored_line.id
                    try:
                        line_connector.create_mapping(
                            bill_line_item_id=bill_line_item_id,
                            qbo_bill_line_id=stored_line_id,
                        )
                        logger.info(f"Created line mapping: BillLineItem {bill_line_item_id} <-> QboBillLine {stored_line_id}")
                    except ValueError as e:
                        logger.warning(f"Could not create line mapping: {e}")

        # Create bill mapping
        qbo_bill_id = int(local_qbo_bill.id) if isinstance(local_qbo_bill.id, str) else local_qbo_bill.id
        try:
            self.create_mapping(bill_id=bill_id, qbo_bill_id=qbo_bill_id)
            logger.info(f"Created mapping: Bill {bill_id} <-> QboBill {qbo_bill_id} (recovered from duplicate)")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")

        return local_qbo_bill

    def update_has_been_billed_in_qbo(self, bill_id: int, realm_id: str) -> None:
        """
        Re-push a QBO Bill with updated BillableStatus = HasBeenBilled on billed line items.
        Called after invoice completion to reflect the billed state in QBO.
        """
        from integrations.intuit.qbo.bill.external.schemas import QboBillUpdate

        mapping = self.mapping_repo.read_by_bill_id(bill_id)
        if not mapping:
            logger.debug(f"No QBO mapping for bill_id={bill_id}, skipping HasBeenBilled update")
            return

        local_qbo_bill = self.qbo_bill_repo.read_by_id(mapping.qbo_bill_id)
        if not local_qbo_bill or not local_qbo_bill.qbo_id:
            return

        # Auth is resolved lazily inside QboHttpClient when the bill client makes a request.
        # Rebuild all bill lines — _build_qbo_line reads is_billed from local DB,
        # so billed items will now get BillableStatus = "HasBeenBilled".
        # Use sequential line_nums with no gaps to match QBO's numbering.
        bill_line_items = self.bill_line_item_service.read_by_bill_id(bill_id=bill_id)
        qbo_lines = []
        seq = 0
        for line_item in bill_line_items:
            qbo_line = self._build_qbo_line(line_item, seq + 1)
            if qbo_line:
                seq += 1
                qbo_lines.append(qbo_line)

        if not qbo_lines:
            logger.warning(f"No QBO lines could be built for bill_id={bill_id}, skipping update")
            return

        vendor_ref = QboReferenceType(
            value=local_qbo_bill.vendor_ref_value,
            name=local_qbo_bill.vendor_ref_name,
        )

        # QBO Bill updates are full-replace — any field not included is cleared.
        # Re-send all header fields from the locally stored QboBill to preserve them.
        ap_account_ref = (
            QboReferenceType(value=local_qbo_bill.ap_account_ref_value, name=local_qbo_bill.ap_account_ref_name)
            if local_qbo_bill.ap_account_ref_value else None
        )
        sales_term_ref = (
            QboReferenceType(value=local_qbo_bill.sales_term_ref_value, name=local_qbo_bill.sales_term_ref_name)
            if local_qbo_bill.sales_term_ref_value else None
        )
        currency_ref = (
            QboReferenceType(value=local_qbo_bill.currency_ref_value, name=local_qbo_bill.currency_ref_name)
            if local_qbo_bill.currency_ref_value else None
        )
        department_ref = (
            QboReferenceType(value=local_qbo_bill.department_ref_value, name=local_qbo_bill.department_ref_name)
            if local_qbo_bill.department_ref_value else None
        )

        with QboBillClient(realm_id=realm_id) as client:
            fresh = client.get_bill(local_qbo_bill.qbo_id)
            qbo_bill_update = QboBillUpdate(
                id=local_qbo_bill.qbo_id,
                sync_token=fresh.sync_token,
                vendor_ref=vendor_ref,
                ap_account_ref=ap_account_ref,
                sales_term_ref=sales_term_ref,
                currency_ref=currency_ref,
                department_ref=department_ref,
                txn_date=local_qbo_bill.txn_date,
                due_date=local_qbo_bill.due_date,
                doc_number=local_qbo_bill.doc_number,
                private_note=local_qbo_bill.private_note,
                exchange_rate=local_qbo_bill.exchange_rate,
                global_tax_calculation=local_qbo_bill.global_tax_calculation,
                line=qbo_lines,
            )
            updated = client.update_bill(qbo_bill_update)

        logger.info(f"Updated QBO Bill {local_qbo_bill.qbo_id} — billed line items now HasBeenBilled")

        # Persist the new SyncToken locally
        self.qbo_bill_repo.update_by_qbo_id(
            qbo_id=local_qbo_bill.qbo_id,
            row_version=local_qbo_bill.row_version_bytes,
            sync_token=updated.sync_token,
            realm_id=realm_id,
            vendor_ref_value=local_qbo_bill.vendor_ref_value,
            vendor_ref_name=local_qbo_bill.vendor_ref_name,
            txn_date=local_qbo_bill.txn_date,
            due_date=local_qbo_bill.due_date,
            doc_number=local_qbo_bill.doc_number,
            private_note=local_qbo_bill.private_note,
            total_amt=updated.total_amt,
            balance=updated.balance,
            ap_account_ref_value=local_qbo_bill.ap_account_ref_value,
            ap_account_ref_name=local_qbo_bill.ap_account_ref_name,
            sales_term_ref_value=local_qbo_bill.sales_term_ref_value,
            sales_term_ref_name=local_qbo_bill.sales_term_ref_name,
            currency_ref_value=local_qbo_bill.currency_ref_value,
            currency_ref_name=local_qbo_bill.currency_ref_name,
            exchange_rate=local_qbo_bill.exchange_rate,
            department_ref_value=local_qbo_bill.department_ref_value,
            department_ref_name=local_qbo_bill.department_ref_name,
            global_tax_calculation=local_qbo_bill.global_tax_calculation,
        )

    def _get_qbo_vendor_ref(self, vendor_id: int) -> Optional[QboReferenceType]:
        """
        Get QBO VendorRef from local vendor_id.
        
        Args:
            vendor_id: Local vendor database ID
            
        Returns:
            QboReferenceType with QBO vendor value and name, or None
        """
        if not vendor_id:
            return None
        
        # Find VendorVendor mapping
        vendor_mapping = self.vendor_vendor_repo.read_by_vendor_id(vendor_id)
        if not vendor_mapping:
            logger.warning(f"VendorVendor mapping not found for vendor_id: {vendor_id}")
            return None
        
        # Get QboVendor
        qbo_vendor = self.qbo_vendor_repo.read_by_id(vendor_mapping.qbo_vendor_id)
        if not qbo_vendor or not qbo_vendor.qbo_id:
            logger.warning(f"QboVendor not found for qbo_vendor_id: {vendor_mapping.qbo_vendor_id}")
            return None
        
        # Get vendor name
        vendor = self.vendor_service.read_by_id(vendor_id)
        vendor_name = vendor.name if vendor else None
        
        return QboReferenceType(value=qbo_vendor.qbo_id, name=vendor_name)

    def _get_qbo_item_ref(self, sub_cost_code_id: int) -> Optional[QboReferenceType]:
        """
        Get QBO ItemRef from local sub_cost_code_id.
        
        Args:
            sub_cost_code_id: Local SubCostCode database ID
            
        Returns:
            QboReferenceType with QBO item value and name, or None
        """
        if not sub_cost_code_id:
            logger.debug("_get_qbo_item_ref called with None sub_cost_code_id")
            return None
        
        # Find ItemSubCostCode mapping
        logger.debug(f"Looking up ItemSubCostCode mapping for sub_cost_code_id: {sub_cost_code_id}")
        item_mapping = self.item_sub_cost_code_repo.read_by_sub_cost_code_id(sub_cost_code_id)
        if not item_mapping:
            logger.warning(f"ItemSubCostCode mapping not found for sub_cost_code_id: {sub_cost_code_id}")
            return None
        
        # Get QboItem
        qbo_item = self.qbo_item_repo.read_by_id(item_mapping.qbo_item_id)
        if not qbo_item or not qbo_item.qbo_id:
            logger.debug(f"QboItem not found for qbo_item_id: {item_mapping.qbo_item_id}")
            return None
        
        return QboReferenceType(value=qbo_item.qbo_id, name=qbo_item.name)

    def _get_qbo_customer_ref(self, project_id: int) -> Optional[QboReferenceType]:
        """
        Get QBO CustomerRef from local project_id.
        
        Args:
            project_id: Local Project database ID
            
        Returns:
            QboReferenceType with QBO customer value and name, or None
        """
        if not project_id:
            return None
        
        # Find CustomerProject mapping
        customer_mapping = self.customer_project_repo.read_by_project_id(project_id)
        if not customer_mapping:
            logger.debug(f"CustomerProject mapping not found for project_id: {project_id}")
            return None
        
        # Get QboCustomer
        qbo_customer = self.qbo_customer_repo.read_by_id(customer_mapping.qbo_customer_id)
        if not qbo_customer or not qbo_customer.qbo_id:
            logger.debug(f"QboCustomer not found for qbo_customer_id: {customer_mapping.qbo_customer_id}")
            return None
        
        return QboReferenceType(value=qbo_customer.qbo_id, name=qbo_customer.display_name)

    def _get_ap_account_ref(self, realm_id: str) -> Optional[QboReferenceType]:
        """
        Get the Accounts Payable account reference for a realm.
        
        Args:
            realm_id: QBO realm ID
            
        Returns:
            QboReferenceType with AP account value and name, or None
        """
        # Get all accounts for this realm and find the AP account
        accounts = self.qbo_account_repo.read_by_realm_id(realm_id)
        for account in accounts:
            if account.account_type == "Accounts Payable":
                return QboReferenceType(value=account.qbo_id, name=account.name)
        
        logger.warning(f"No Accounts Payable account found for realm_id: {realm_id}")
        return None

    def _get_qbo_sales_term_ref(self, payment_term_id: int) -> Optional[QboReferenceType]:
        """
        Get QBO SalesTermRef from local payment_term_id.

        Args:
            payment_term_id: Local PaymentTerm database ID

        Returns:
            QboReferenceType with QBO term value and name, or None
        """
        if not payment_term_id:
            return None

        # Find TermPaymentTerm mapping
        term_mapping = self.term_payment_term_repo.read_by_payment_term_id(payment_term_id)
        if not term_mapping:
            logger.debug(f"TermPaymentTerm mapping not found for payment_term_id: {payment_term_id}")
            return None

        # Get QboTerm
        qbo_term = self.qbo_term_repo.read_by_id(term_mapping.qbo_term_id)
        if not qbo_term or not qbo_term.qbo_id:
            logger.debug(f"QboTerm not found for qbo_term_id: {term_mapping.qbo_term_id}")
            return None

        return QboReferenceType(value=qbo_term.qbo_id, name=qbo_term.name)

    def _build_qbo_line(self, line_item, line_num: int) -> Optional[QboBillLineSchema]:
        """
        Build a QBO Bill line from a local BillLineItem.
        
        Args:
            line_item: BillLineItem record
            line_num: Line number
            
        Returns:
            QboBillLineSchema or None
        """
        logger.debug(f"Building QBO line for BillLineItem {line_item.id}: sub_cost_code_id={line_item.sub_cost_code_id}, project_id={line_item.project_id}")
        
        # Get QBO references — all line items must have valid mappings
        item_ref = None
        if line_item.sub_cost_code_id:
            item_ref = self._get_qbo_item_ref(line_item.sub_cost_code_id)
            if not item_ref:
                raise ValueError(
                    f"BillLineItem {line_item.id}: no QBO Item mapping for sub_cost_code_id={line_item.sub_cost_code_id}. "
                    f"Map the SubCostCode to a QBO Item before syncing."
                )
        else:
            raise ValueError(f"BillLineItem {line_item.id} has no sub_cost_code_id. All line items require a SubCostCode for QBO sync.")
        
        customer_ref = self._get_qbo_customer_ref(line_item.project_id) if line_item.project_id else None
        
        # Determine billable status.
        # is_billable=None means default billable (treat same as True).
        # is_billable=False means explicitly not billable.
        # Note: If BillableStatus is "Billable", CustomerRef is REQUIRED by QBO.
        if line_item.is_billable is not False:
            if customer_ref:
                billable_status = "HasBeenBilled" if line_item.is_billed is True else "Billable"
            else:
                logger.warning(
                    f"Line item {line_item.id} is billable but no CustomerRef available "
                    f"(project_id={line_item.project_id}). Setting to NotBillable."
                )
                billable_status = "NotBillable"
        else:
            billable_status = "NotBillable"
        
        # Calculate markup percent (convert from decimal like 0.10 to percentage like 10)
        markup_percent = None
        if line_item.markup is not None:
            markup_percent = line_item.markup * Decimal('100')
        
        # Item-based expense line
        # Ensure we have an amount - QBO requires either Amount or (Qty + UnitPrice)
        line_amount = line_item.amount
        qty = Decimal(str(line_item.quantity)) if line_item.quantity else None
        unit_price = line_item.rate

        # If no amount, try to calculate from qty * rate
        if line_amount is None and qty is not None and unit_price is not None:
            line_amount = qty * unit_price

        # If still no amount, use 0 as fallback
        if line_amount is None:
            logger.warning(f"Line item {line_item.id} has no amount, qty, or rate. Using 0.")
            line_amount = Decimal('0')

        detail = QboItemBasedExpenseLineDetail(
            item_ref=item_ref,
            customer_ref=customer_ref,
            billable_status=billable_status,
            qty=qty,
            unit_price=unit_price,
            # float() is acceptable here: percentage value inside Dict[str, Any] needs
            # JSON-serializable numeric type; Pydantic won't auto-convert Decimal in dicts.
            markup_info={"Percent": float(markup_percent)} if markup_percent is not None else None,
        )
        return QboBillLineSchema(
            line_num=line_num,
            description=line_item.description,
            amount=line_amount,
            detail_type="ItemBasedExpenseLineDetail",
            item_based_expense_line_detail=detail,
        )

    def _store_qbo_bill_line(self, qbo_bill_id: int, qbo_line: QboBillLineSchema):
        """
        Store a QBO Bill line locally.

        Args:
            qbo_bill_id: Local QboBill database ID
            qbo_line: QBO Bill line from API response

        Returns:
            QboBillLine: The created local record, or None on failure
        """
        try:
            # Extract references from line detail
            item_ref_value = None
            item_ref_name = None
            account_ref_value = None
            account_ref_name = None
            customer_ref_value = None
            customer_ref_name = None
            class_ref_value = None
            class_ref_name = None
            billable_status = None
            qty = None
            unit_price = None
            markup_percent = None
            
            if qbo_line.item_based_expense_line_detail:
                detail = qbo_line.item_based_expense_line_detail
                if detail.item_ref:
                    item_ref_value = detail.item_ref.value
                    item_ref_name = detail.item_ref.name
                if detail.customer_ref:
                    customer_ref_value = detail.customer_ref.value
                    customer_ref_name = detail.customer_ref.name
                if detail.class_ref:
                    class_ref_value = detail.class_ref.value
                    class_ref_name = detail.class_ref.name
                billable_status = detail.billable_status
                qty = detail.qty
                unit_price = detail.unit_price
                if detail.markup_info and isinstance(detail.markup_info, dict):
                    raw_pct = detail.markup_info.get("Percent") or detail.markup_info.get("percent")
                    if raw_pct is not None:
                        markup_percent = Decimal(str(raw_pct))
            elif qbo_line.account_based_expense_line_detail:
                detail = qbo_line.account_based_expense_line_detail
                if detail.account_ref:
                    account_ref_value = detail.account_ref.value
                    account_ref_name = detail.account_ref.name
                if detail.customer_ref:
                    customer_ref_value = detail.customer_ref.value
                    customer_ref_name = detail.customer_ref.name
                if detail.class_ref:
                    class_ref_value = detail.class_ref.value
                    class_ref_name = detail.class_ref.name
                billable_status = detail.billable_status
            
            return self.qbo_bill_line_repo.create(
                qbo_bill_id=qbo_bill_id,
                qbo_line_id=qbo_line.id,
                line_num=qbo_line.line_num,
                description=qbo_line.description,
                amount=qbo_line.amount,
                detail_type=qbo_line.detail_type,
                item_ref_value=item_ref_value,
                item_ref_name=item_ref_name,
                account_ref_value=account_ref_value,
                account_ref_name=account_ref_name,
                customer_ref_value=customer_ref_value,
                customer_ref_name=customer_ref_name,
                class_ref_value=class_ref_value,
                class_ref_name=class_ref_name,
                billable_status=billable_status,
                qty=qty,
                unit_price=unit_price,
                markup_percent=markup_percent,
            )
        except Exception as e:
            logger.error(f"Failed to store QboBillLine: {e}")
            return None
