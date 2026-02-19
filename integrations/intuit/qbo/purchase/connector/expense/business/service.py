# Python Standard Library Imports
import logging
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.purchase.connector.expense.business.model import PurchaseExpense
from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import PurchaseExpenseRepository
from integrations.intuit.qbo.purchase.business.model import QboPurchase, QboPurchaseLine
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository, QboPurchaseLineRepository
from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from entities.expense.business.service import ExpenseService
from entities.expense.business.model import Expense
from entities.expense_line_item.business.service import ExpenseLineItemService
from entities.vendor.business.service import VendorService

logger = logging.getLogger(__name__)


class PurchaseExpenseConnector:
    """
    Connector service for synchronization between QboPurchase and Expense modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[PurchaseExpenseRepository] = None,
        expense_service: Optional[ExpenseService] = None,
        vendor_service: Optional[VendorService] = None,
        vendor_vendor_repo: Optional[VendorVendorRepository] = None,
        qbo_vendor_repo: Optional[QboVendorRepository] = None,
        qbo_purchase_repo: Optional[QboPurchaseRepository] = None,
        qbo_purchase_line_repo: Optional[QboPurchaseLineRepository] = None,
    ):
        """Initialize the PurchaseExpenseConnector."""
        self.mapping_repo = mapping_repo or PurchaseExpenseRepository()
        self.expense_service = expense_service or ExpenseService()
        self.vendor_service = vendor_service or VendorService()
        self.vendor_vendor_repo = vendor_vendor_repo or VendorVendorRepository()
        self.qbo_vendor_repo = qbo_vendor_repo or QboVendorRepository()
        self.qbo_purchase_repo = qbo_purchase_repo or QboPurchaseRepository()
        self.qbo_purchase_line_repo = qbo_purchase_line_repo or QboPurchaseLineRepository()

    def sync_from_qbo_purchase(self, qbo_purchase: QboPurchase, qbo_purchase_lines: List[QboPurchaseLine]) -> Expense:
        """
        Sync data from QboPurchase to Expense module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the Expense accordingly
        3. Syncs line items to ExpenseLineItem module
        
        Args:
            qbo_purchase: QboPurchase record
            qbo_purchase_lines: List of QboPurchaseLine records for this purchase
        
        Returns:
            Expense: The synced Expense record
        """
        # Find vendor mapping to get Vendor public_id
        # Purchase uses EntityRef instead of VendorRef
        vendor_public_id = self._get_vendor_public_id(qbo_purchase.entity_ref_value)
        if not vendor_public_id:
            raise ValueError(f"No vendor mapping found for QBO entity ref: {qbo_purchase.entity_ref_value}")
        
        # Map QBO Purchase fields to Expense module fields
        reference_number = qbo_purchase.doc_number or f"QBO-{qbo_purchase.qbo_id}"
        expense_date = qbo_purchase.txn_date or ""
        memo = qbo_purchase.private_note
        total_amount = qbo_purchase.total_amt
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_purchase_id(qbo_purchase.id)
        
        if mapping:
            # Found existing mapping - update the Expense
            expense = self.expense_service.read_by_id(mapping.expense_id)
            if expense:
                logger.info(f"Updating existing Expense {expense.id} from QboPurchase {qbo_purchase.id}")
                
                expense = self.expense_service.update_by_public_id(
                    expense.public_id,
                    row_version=expense.row_version,
                    vendor_public_id=vendor_public_id,
                    expense_date=expense_date,
                    reference_number=reference_number,
                    total_amount=float(total_amount) if total_amount else None,
                    memo=memo,
                    is_draft=True,
                )
                
                # Sync line items for existing expense
                self._sync_line_items(expense.id, qbo_purchase_lines)
                
                return expense
            else:
                # Mapping exists but Expense not found - recreate Expense
                logger.warning(f"Mapping exists but Expense {mapping.expense_id} not found. Creating new Expense.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new Expense
        logger.info(f"Creating new Expense from QboPurchase {qbo_purchase.id}: reference_number={reference_number}")
        expense = self.expense_service.create(
            vendor_public_id=vendor_public_id,
            expense_date=expense_date,
            reference_number=reference_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=True,
        )
        
        # Create mapping
        expense_id = int(expense.id) if isinstance(expense.id, str) else expense.id
        try:
            mapping = self.create_mapping(expense_id=expense_id, qbo_purchase_id=qbo_purchase.id)
            logger.info(f"Created mapping: Expense {expense_id} <-> QboPurchase {qbo_purchase.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        # Sync line items for new expense
        self._sync_line_items(expense_id, qbo_purchase_lines)
        
        return expense

    def _get_vendor_public_id(self, qbo_entity_ref_value: str) -> Optional[str]:
        """
        Get the Vendor public_id from QBO entity reference value.
        
        Args:
            qbo_entity_ref_value: QBO entity reference value (QBO Vendor ID)
        
        Returns:
            str: Vendor public_id or None
        """
        if not qbo_entity_ref_value:
            return None
        
        # First find the QboVendor by qbo_id
        qbo_vendor = self.qbo_vendor_repo.read_by_qbo_id(qbo_entity_ref_value)
        if not qbo_vendor:
            logger.warning(f"QboVendor not found for qbo_id: {qbo_entity_ref_value}")
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

    def _sync_line_items(self, expense_id: int, qbo_purchase_lines: List[QboPurchaseLine]) -> None:
        """
        Sync purchase line items to ExpenseLineItem module.
        
        Args:
            expense_id: Database ID of the Expense
            qbo_purchase_lines: List of QboPurchaseLine records
        """
        if not qbo_purchase_lines:
            return
        
        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.purchase.connector.expense_line_item.business.service import PurchaseLineExpenseLineItemConnector
        
        line_connector = PurchaseLineExpenseLineItemConnector()
        
        for qbo_line in qbo_purchase_lines:
            try:
                line_connector.sync_from_qbo_purchase_line(expense_id, qbo_line)
            except Exception as e:
                logger.error(f"Failed to sync QboPurchaseLine {qbo_line.id} to ExpenseLineItem: {e}")

    def create_mapping(self, expense_id: int, qbo_purchase_id: int) -> PurchaseExpense:
        """
        Create a mapping between Expense and QboPurchase.
        
        Args:
            expense_id: Database ID of Expense record
            qbo_purchase_id: Database ID of QboPurchase record
        
        Returns:
            PurchaseExpense: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_expense = self.mapping_repo.read_by_expense_id(expense_id)
        if existing_by_expense:
            raise ValueError(
                f"Expense {expense_id} is already mapped to QboPurchase {existing_by_expense.qbo_purchase_id}"
            )
        
        existing_by_qbo_purchase = self.mapping_repo.read_by_qbo_purchase_id(qbo_purchase_id)
        if existing_by_qbo_purchase:
            raise ValueError(
                f"QboPurchase {qbo_purchase_id} is already mapped to Expense {existing_by_qbo_purchase.expense_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(expense_id=expense_id, qbo_purchase_id=qbo_purchase_id)

    def get_mapping_by_expense_id(self, expense_id: int) -> Optional[PurchaseExpense]:
        """
        Get mapping by Expense ID.
        """
        return self.mapping_repo.read_by_expense_id(expense_id)

    def get_mapping_by_qbo_purchase_id(self, qbo_purchase_id: int) -> Optional[PurchaseExpense]:
        """
        Get mapping by QboPurchase ID.
        """
        return self.mapping_repo.read_by_qbo_purchase_id(qbo_purchase_id)

    def sync_to_qbo_purchase(self, expense: Expense, realm_id: str) -> QboPurchase:
        """
        Push local Expense changes back to QBO as a Purchase update.
        Converts AccountBasedExpenseLineDetail → ItemBasedExpenseLineDetail.
        
        Args:
            expense: Local Expense record to sync
            realm_id: QBO realm ID for API access
        
        Returns:
            QboPurchase: The updated local QboPurchase record
            
        Raises:
            ValueError: If no mapping exists, QBO record not found, or no valid line items
        """
        expense_id = int(expense.id) if isinstance(expense.id, str) else expense.id
        
        # 1. Get existing mapping (REQUIRED - must already exist from pull)
        mapping = self.mapping_repo.read_by_expense_id(expense_id)
        if not mapping:
            raise ValueError(
                f"No QBO mapping found for Expense {expense_id}. "
                "Cannot push - expense must originate from QBO sync."
            )
        
        # 2. Get local QboPurchase record (has qbo_id, sync_token, payment_type, account_ref)
        local_qbo_purchase = self.qbo_purchase_repo.read_by_id(mapping.qbo_purchase_id)
        if not local_qbo_purchase:
            raise ValueError(f"QboPurchase {mapping.qbo_purchase_id} not found")
        
        # 3. Get expense line items and build QBO lines
        expense_line_item_service = ExpenseLineItemService()
        expense_line_items = expense_line_item_service.read_by_expense_id(expense_id=expense_id)
        
        qbo_lines = []
        skipped_lines = []
        for idx, line_item in enumerate(expense_line_items, start=1):
            qbo_line = self._build_qbo_line(line_item, idx)
            if qbo_line:
                qbo_lines.append(qbo_line)
            else:
                skipped_lines.append(line_item.id)
        
        # QBO requires at least one line item
        if not qbo_lines:
            if expense_line_items:
                raise ValueError(
                    f"Expense has {len(expense_line_items)} line item(s) but none have QBO Item mappings. "
                    f"SubCostCodes must be mapped to QBO Items first. Skipped line item IDs: {skipped_lines}"
                )
            else:
                raise ValueError("Expense has no line items. QBO requires at least one line item.")
        
        # 4. Build QboPurchaseUpdate (preserving PaymentType, AccountRef from original)
        from integrations.intuit.qbo.purchase.external.schemas import (
            QboPurchaseUpdate,
            QboReferenceType,
        )
        
        qbo_purchase_update = QboPurchaseUpdate(
            id=local_qbo_purchase.qbo_id,
            sync_token=local_qbo_purchase.sync_token,
            payment_type=local_qbo_purchase.payment_type,
            account_ref=QboReferenceType(
                value=local_qbo_purchase.account_ref_value,
                name=local_qbo_purchase.account_ref_name
            ) if local_qbo_purchase.account_ref_value else None,
            entity_ref=QboReferenceType(
                value=local_qbo_purchase.entity_ref_value,
                name=local_qbo_purchase.entity_ref_name
            ) if local_qbo_purchase.entity_ref_value else None,
            txn_date=local_qbo_purchase.txn_date,
            doc_number=local_qbo_purchase.doc_number,
            private_note=expense.memo,
            line=qbo_lines,
            currency_ref=QboReferenceType(
                value=local_qbo_purchase.currency_ref_value,
                name=local_qbo_purchase.currency_ref_name
            ) if local_qbo_purchase.currency_ref_value else None,
            department_ref=QboReferenceType(
                value=local_qbo_purchase.department_ref_value,
                name=local_qbo_purchase.department_ref_name
            ) if local_qbo_purchase.department_ref_value else None,
        )
        
        # 5. Get QBO auth and update purchase
        from integrations.intuit.qbo.auth.business.service import QboAuthService
        from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
        
        auth_service = QboAuthService()
        qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid QBO auth found for realm {realm_id}")
        
        logger.info(f"Updating Purchase in QBO for local Expense {expense_id}: qbo_id={local_qbo_purchase.qbo_id}")
        
        with QboPurchaseClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            updated_purchase = client.update_purchase(qbo_purchase_update)
        
        logger.info(f"Updated QBO Purchase {updated_purchase.id} with new SyncToken {updated_purchase.sync_token}")
        
        # 6. Update local QboPurchase record with new sync_token and line items
        updated_local = self.qbo_purchase_repo.update_by_qbo_id(
            qbo_id=local_qbo_purchase.qbo_id,
            row_version=local_qbo_purchase.row_version_bytes,
            sync_token=updated_purchase.sync_token,
            realm_id=realm_id,
            payment_type=local_qbo_purchase.payment_type,
            account_ref_value=local_qbo_purchase.account_ref_value,
            account_ref_name=local_qbo_purchase.account_ref_name,
            entity_ref_value=local_qbo_purchase.entity_ref_value,
            entity_ref_name=local_qbo_purchase.entity_ref_name,
            credit=local_qbo_purchase.credit,
            txn_date=local_qbo_purchase.txn_date,
            doc_number=local_qbo_purchase.doc_number,
            private_note=expense.memo,
            total_amt=updated_purchase.total_amt,
            currency_ref_value=local_qbo_purchase.currency_ref_value,
            currency_ref_name=local_qbo_purchase.currency_ref_name,
            exchange_rate=local_qbo_purchase.exchange_rate,
            department_ref_value=local_qbo_purchase.department_ref_value,
            department_ref_name=local_qbo_purchase.department_ref_name,
            global_tax_calculation=local_qbo_purchase.global_tax_calculation,
        )
        
        # 7. Update local QboPurchaseLine records
        if updated_purchase.line:
            self._update_local_purchase_lines(updated_local.id, updated_purchase.line)
        
        return updated_local

    def _build_qbo_line(self, line_item, line_num: int):
        """
        Build a QBO Purchase line from a local ExpenseLineItem.
        Converts to ItemBasedExpenseLineDetail with ItemRef and CustomerRef.
        
        Args:
            line_item: ExpenseLineItem record
            line_num: Line number
            
        Returns:
            QboPurchaseLine or None if no Item mapping exists
        """
        from integrations.intuit.qbo.purchase.external.schemas import (
            QboPurchaseLine,
            QboReferenceType,
            QboItemBasedExpenseLineDetail,
        )
        from decimal import Decimal
        
        # Get QBO ItemRef from SubCostCode
        item_ref = self._get_qbo_item_ref(line_item.sub_cost_code_id)
        if not item_ref:
            logger.warning(f"No QBO Item mapping for sub_cost_code_id={line_item.sub_cost_code_id}, skipping line {line_item.id}")
            return None
        
        # Get QBO CustomerRef from Project
        customer_ref = self._get_qbo_customer_ref(line_item.project_id) if line_item.project_id else None
        
        # Determine billable status
        billable_status = None
        if line_item.is_billable is True:
            if customer_ref:
                billable_status = "Billable" if not getattr(line_item, 'is_billed', False) else "HasBeenBilled"
            else:
                logger.warning(f"Line item {line_item.id} is billable but no CustomerRef available. Setting to NotBillable.")
                billable_status = "NotBillable"
        elif line_item.is_billable is False:
            billable_status = "NotBillable"
        
        # Calculate amount
        line_amount = line_item.amount
        qty = Decimal(str(line_item.quantity)) if line_item.quantity else None
        unit_price = line_item.rate
        
        if line_amount is None and qty is not None and unit_price is not None:
            line_amount = qty * unit_price
        
        if line_amount is None:
            logger.warning(f"Line item {line_item.id} has no amount, using 0.")
            line_amount = Decimal('0')
        
        # Build ItemBasedExpenseLineDetail
        detail = QboItemBasedExpenseLineDetail(
            item_ref=item_ref,
            customer_ref=customer_ref,
            billable_status=billable_status,
            qty=qty,
            unit_price=unit_price,
        )
        
        return QboPurchaseLine(
            line_num=line_num,
            description=line_item.description,
            amount=line_amount,
            detail_type="ItemBasedExpenseLineDetail",
            item_based_expense_line_detail=detail,
        )

    def _get_qbo_item_ref(self, sub_cost_code_id: int):
        """
        Get QBO ItemRef from local sub_cost_code_id.
        
        Args:
            sub_cost_code_id: Local SubCostCode database ID
            
        Returns:
            QboReferenceType with QBO item value and name, or None
        """
        from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType
        from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
        from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
        
        if not sub_cost_code_id:
            logger.debug("_get_qbo_item_ref called with None sub_cost_code_id")
            return None
        
        item_sub_cost_code_repo = ItemSubCostCodeRepository()
        qbo_item_repo = QboItemRepository()
        
        # Find ItemSubCostCode mapping
        item_mapping = item_sub_cost_code_repo.read_by_sub_cost_code_id(sub_cost_code_id)
        if not item_mapping:
            logger.warning(f"ItemSubCostCode mapping not found for sub_cost_code_id: {sub_cost_code_id}")
            return None
        
        # Get QboItem
        qbo_item = qbo_item_repo.read_by_id(item_mapping.qbo_item_id)
        if not qbo_item or not qbo_item.qbo_id:
            logger.debug(f"QboItem not found for qbo_item_id: {item_mapping.qbo_item_id}")
            return None
        
        return QboReferenceType(value=qbo_item.qbo_id, name=qbo_item.name)

    def _get_qbo_customer_ref(self, project_id: int):
        """
        Get QBO CustomerRef from local project_id.
        
        Args:
            project_id: Local Project database ID
            
        Returns:
            QboReferenceType with QBO customer value and name, or None
        """
        from integrations.intuit.qbo.purchase.external.schemas import QboReferenceType
        from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
        from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
        
        if not project_id:
            return None
        
        customer_project_repo = CustomerProjectRepository()
        qbo_customer_repo = QboCustomerRepository()
        
        # Find CustomerProject mapping
        customer_mapping = customer_project_repo.read_by_project_id(project_id)
        if not customer_mapping:
            logger.debug(f"CustomerProject mapping not found for project_id: {project_id}")
            return None
        
        # Get QboCustomer
        qbo_customer = qbo_customer_repo.read_by_id(customer_mapping.qbo_customer_id)
        if not qbo_customer or not qbo_customer.qbo_id:
            logger.debug(f"QboCustomer not found for qbo_customer_id: {customer_mapping.qbo_customer_id}")
            return None
        
        return QboReferenceType(value=qbo_customer.qbo_id, name=qbo_customer.display_name)

    def _update_local_purchase_lines(self, qbo_purchase_id: int, qbo_lines: list) -> None:
        """
        Update local QboPurchaseLine records after QBO update.
        
        Args:
            qbo_purchase_id: Local QboPurchase database ID
            qbo_lines: List of QboPurchaseLine from API response
        """
        for qbo_line in qbo_lines:
            try:
                # Check if line exists
                existing_line = None
                if qbo_line.id:
                    existing_line = self.qbo_purchase_line_repo.read_by_qbo_purchase_id_and_qbo_line_id(
                        qbo_purchase_id=qbo_purchase_id,
                        qbo_line_id=qbo_line.id
                    )
                
                # Extract references from line detail
                item_ref_value = None
                item_ref_name = None
                customer_ref_value = None
                customer_ref_name = None
                billable_status = None
                qty = None
                unit_price = None
                
                if qbo_line.item_based_expense_line_detail:
                    detail = qbo_line.item_based_expense_line_detail
                    if detail.item_ref:
                        item_ref_value = detail.item_ref.value
                        item_ref_name = detail.item_ref.name
                    if detail.customer_ref:
                        customer_ref_value = detail.customer_ref.value
                        customer_ref_name = detail.customer_ref.name
                    billable_status = detail.billable_status
                    qty = detail.qty
                    unit_price = detail.unit_price
                
                if existing_line:
                    self.qbo_purchase_line_repo.update_by_id(
                        id=existing_line.id,
                        row_version=existing_line.row_version_bytes,
                        line_num=qbo_line.line_num,
                        description=qbo_line.description,
                        amount=qbo_line.amount,
                        detail_type=qbo_line.detail_type,
                        item_ref_value=item_ref_value,
                        item_ref_name=item_ref_name,
                        account_ref_value=None,  # Cleared - now using ItemBasedExpenseLineDetail
                        account_ref_name=None,
                        customer_ref_value=customer_ref_value,
                        customer_ref_name=customer_ref_name,
                        class_ref_value=None,
                        class_ref_name=None,
                        billable_status=billable_status,
                        qty=qty,
                        unit_price=unit_price,
                        markup_percent=None,
                    )
                else:
                    self.qbo_purchase_line_repo.create(
                        qbo_purchase_id=qbo_purchase_id,
                        qbo_line_id=qbo_line.id,
                        line_num=qbo_line.line_num,
                        description=qbo_line.description,
                        amount=qbo_line.amount,
                        detail_type=qbo_line.detail_type,
                        item_ref_value=item_ref_value,
                        item_ref_name=item_ref_name,
                        account_ref_value=None,
                        account_ref_name=None,
                        customer_ref_value=customer_ref_value,
                        customer_ref_name=customer_ref_name,
                        class_ref_value=None,
                        class_ref_name=None,
                        billable_status=billable_status,
                        qty=qty,
                        unit_price=unit_price,
                        markup_percent=None,
                    )
            except Exception as e:
                logger.error(f"Failed to update QboPurchaseLine: {e}")


def sync_purchase_attachments_to_expense_line_items(
    expense_id: int,
    qbo_attachables: list,
) -> int:
    """
    Link QBO attachables (already synced to Attachments) to all ExpenseLineItems for this expense.
    Mirrors Bill _link_attachments_to_bill_line_items: each attachment is linked to each line item.
    Returns count of ExpenseLineItemAttachment links created.
    """
    if not qbo_attachables:
        return 0

    from integrations.intuit.qbo.attachable.connector.attachment.persistence.repo import AttachableAttachmentRepository
    from entities.attachment.business.service import AttachmentService
    from entities.expense_line_item.business.service import ExpenseLineItemService
    from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService

    expense_line_item_service = ExpenseLineItemService()
    expense_line_item_attachment_service = ExpenseLineItemAttachmentService()
    attachment_service = AttachmentService()
    attachable_attachment_repo = AttachableAttachmentRepository()

    line_items = expense_line_item_service.read_by_expense_id(expense_id=expense_id)
    if not line_items:
        logger.debug(f"No ExpenseLineItems found for Expense {expense_id}")
        return 0

    linked = 0
    for qbo_attachable in qbo_attachables:
        mapping = attachable_attachment_repo.read_by_qbo_attachable_id(qbo_attachable.id)
        if not mapping:
            logger.debug(f"No Attachment mapping found for QboAttachable {qbo_attachable.id}")
            continue
        attachment = attachment_service.read_by_id(mapping.attachment_id)
        if not attachment or not attachment.public_id:
            continue
        for line_item in line_items:
            if not line_item.public_id:
                continue
            try:
                expense_line_item_attachment_service.create(
                    expense_line_item_public_id=line_item.public_id,
                    attachment_public_id=attachment.public_id,
                )
                linked += 1
            except Exception as e:
                logger.debug(f"Could not link Attachment {attachment.id} to ExpenseLineItem {line_item.id}: {e}")

    if linked > 0:
        logger.info(f"Created {linked} ExpenseLineItemAttachment links for Expense {expense_id}")
    return linked


def sync_purchase_attachments_to_needing_categorize_lines(
    expense_id: int,
    qbo_purchase_lines: List[QboPurchaseLine],
    qbo_attachables: list,
    realm_id: str,
) -> int:
    """
    Link QBO attachables (already synced to Attachments) to ExpenseLineItem(s)
    that correspond to PurchaseLines with AccountRefName containing 'NEED TO CATEGORIZE' or 'NEED TO UPDATE'.
    Returns count of attachments linked.
    """
    NEED_PATTERNS = ("NEED TO CATEGORIZE", "NEED TO UPDATE")
    needing_lines = [
        pl for pl in qbo_purchase_lines
        if pl.account_ref_name and any(p.upper() in (pl.account_ref_name or "").upper() for p in NEED_PATTERNS)
    ]
    if not needing_lines or not qbo_attachables:
        return 0

    from integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo import (
        PurchaseLineExpenseLineItemRepository,
    )
    from integrations.intuit.qbo.attachable.connector.attachment.business.service import (
        AttachableAttachmentConnector,
    )
    from entities.attachment.business.service import AttachmentService
    from entities.expense_line_item_attachment.business.service import ExpenseLineItemAttachmentService

    mapping_repo = PurchaseLineExpenseLineItemRepository()
    attachment_connector = AttachableAttachmentConnector()
    expense_line_item_service = ExpenseLineItemService()
    expense_line_item_attachment_service = ExpenseLineItemAttachmentService()
    attachment_service = AttachmentService()
    linked = 0

    for i, qbo_line in enumerate(needing_lines):
        if i >= len(qbo_attachables):
            break
        mapping = mapping_repo.read_by_qbo_purchase_line_id(qbo_line.id)
        if not mapping:
            continue
        line_item = expense_line_item_service.read_by_id(mapping.expense_line_item_id)
        if not line_item or not line_item.public_id:
            continue
        existing = expense_line_item_attachment_service.read_by_expense_line_item_id(
            expense_line_item_public_id=line_item.public_id
        )
        if existing:
            continue

        qbo_att = qbo_attachables[i]
        att_mapping = attachment_connector.get_mapping_by_qbo_attachable_id(qbo_att.id)
        if not att_mapping:
            continue
        attachment = attachment_service.read_by_id(att_mapping.attachment_id)
        if not attachment or not attachment.public_id:
            continue
        try:
            expense_line_item_attachment_service.create(
                expense_line_item_public_id=line_item.public_id,
                attachment_public_id=attachment.public_id,
            )
            linked += 1
        except Exception as e:
            logger.error(f"Failed to link attachment to line item: {e}")

    return linked
