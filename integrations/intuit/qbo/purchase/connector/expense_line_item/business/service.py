# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.purchase.connector.expense_line_item.business.model import PurchaseLineExpenseLineItem
from integrations.intuit.qbo.purchase.connector.expense_line_item.persistence.repo import PurchaseLineExpenseLineItemRepository
from integrations.intuit.qbo.purchase.business.model import QboPurchaseLine
from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from services.expense_line_item.business.service import ExpenseLineItemService
from services.expense_line_item.business.model import ExpenseLineItem
from services.expense.business.service import ExpenseService
from services.sub_cost_code.business.service import SubCostCodeService
from services.project.business.service import ProjectService

logger = logging.getLogger(__name__)


class PurchaseLineExpenseLineItemConnector:
    """
    Connector service for synchronization between QboPurchaseLine and ExpenseLineItem.
    """

    def __init__(
        self,
        mapping_repo: Optional[PurchaseLineExpenseLineItemRepository] = None,
        expense_line_item_service: Optional[ExpenseLineItemService] = None,
        expense_service: Optional[ExpenseService] = None,
        item_sub_cost_code_repo: Optional[ItemSubCostCodeRepository] = None,
        qbo_item_repo: Optional[QboItemRepository] = None,
        customer_project_repo: Optional[CustomerProjectRepository] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
    ):
        """Initialize the PurchaseLineExpenseLineItemConnector."""
        self.mapping_repo = mapping_repo or PurchaseLineExpenseLineItemRepository()
        self.expense_line_item_service = expense_line_item_service or ExpenseLineItemService()
        self.expense_service = expense_service or ExpenseService()
        self.item_sub_cost_code_repo = item_sub_cost_code_repo or ItemSubCostCodeRepository()
        self.qbo_item_repo = qbo_item_repo or QboItemRepository()
        self.customer_project_repo = customer_project_repo or CustomerProjectRepository()
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()

    def sync_from_qbo_purchase_line(self, expense_id: int, qbo_line: QboPurchaseLine) -> ExpenseLineItem:
        """
        Sync data from QboPurchaseLine to ExpenseLineItem module.
        
        Args:
            expense_id: Database ID of the Expense
            qbo_line: QboPurchaseLine record
        
        Returns:
            ExpenseLineItem: The synced ExpenseLineItem record
        """
        # Get expense to get public_id
        expense = self.expense_service.read_by_id(expense_id)
        if not expense:
            raise ValueError(f"Expense with ID {expense_id} not found")
        
        # Resolve sub_cost_code from item reference
        sub_cost_code_id = None
        if qbo_line.item_ref_value:
            sub_cost_code_id = self._get_sub_cost_code_id(qbo_line.item_ref_value)
        
        # Resolve project from customer reference
        project_public_id = None
        if qbo_line.customer_ref_value:
            project_public_id = self._get_project_public_id(qbo_line.customer_ref_value)
        
        # Determine billable status
        is_billable = None
        is_billed = None
        if qbo_line.billable_status:
            if qbo_line.billable_status == "Billable":
                is_billable = True
                is_billed = False
            elif qbo_line.billable_status == "HasBeenBilled":
                is_billable = True
                is_billed = True
            elif qbo_line.billable_status == "NotBillable":
                is_billable = False
                is_billed = False
        
        # Calculate markup (convert from percentage to decimal if needed)
        markup = None
        if qbo_line.markup_percent is not None:
            # QBO stores markup as percentage (e.g., 10 for 10%), we store as decimal (e.g., 0.10)
            markup = Decimal(str(qbo_line.markup_percent)) / Decimal('100')
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_purchase_line_id(qbo_line.id)
        
        if mapping:
            # Found existing mapping - update the ExpenseLineItem
            line_item = self.expense_line_item_service.read_by_id(mapping.expense_line_item_id)
            if line_item:
                logger.debug(f"Updating existing ExpenseLineItem {line_item.id} from QboPurchaseLine {qbo_line.id}")
                
                line_item = self.expense_line_item_service.update_by_public_id(
                    line_item.public_id,
                    row_version=line_item.row_version,
                    sub_cost_code_id=sub_cost_code_id,
                    project_public_id=project_public_id,
                    description=qbo_line.description,
                    quantity=int(qbo_line.qty) if qbo_line.qty else None,
                    rate=qbo_line.unit_price,
                    amount=qbo_line.amount,
                    is_billable=is_billable,
                    is_billed=is_billed,
                    markup=markup,
                    is_draft=False,
                )
                
                return line_item
            else:
                # Mapping exists but ExpenseLineItem not found - recreate
                logger.warning(f"Mapping exists but ExpenseLineItem {mapping.expense_line_item_id} not found. Creating new.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new ExpenseLineItem
        logger.debug(f"Creating new ExpenseLineItem from QboPurchaseLine {qbo_line.id}")
        line_item = self.expense_line_item_service.create(
            expense_public_id=expense.public_id,
            sub_cost_code_id=sub_cost_code_id,
            project_public_id=project_public_id,
            description=qbo_line.description,
            quantity=int(qbo_line.qty) if qbo_line.qty else None,
            rate=qbo_line.unit_price,
            amount=qbo_line.amount,
            is_billable=is_billable,
            is_billed=is_billed,
            markup=markup,
            is_draft=False,
        )
        
        # Create mapping
        line_item_id = int(line_item.id) if isinstance(line_item.id, str) else line_item.id
        try:
            mapping = self.create_mapping(expense_line_item_id=line_item_id, qbo_purchase_line_id=qbo_line.id)
            logger.debug(f"Created mapping: ExpenseLineItem {line_item_id} <-> QboPurchaseLine {qbo_line.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        return line_item

    def _get_sub_cost_code_id(self, qbo_item_ref_value: str) -> Optional[int]:
        """
        Get the SubCostCode ID from QBO item reference value.
        
        Args:
            qbo_item_ref_value: QBO item reference value (QBO Item ID)
        
        Returns:
            int: SubCostCode ID or None
        """
        if not qbo_item_ref_value:
            return None
        
        # First find the QboItem by qbo_id
        qbo_item = self.qbo_item_repo.read_by_qbo_id(qbo_item_ref_value)
        if not qbo_item:
            logger.debug(f"QboItem not found for qbo_id: {qbo_item_ref_value}")
            return None
        
        # Then find the ItemSubCostCode mapping
        item_mapping = self.item_sub_cost_code_repo.read_by_qbo_item_id(qbo_item.id)
        if not item_mapping:
            logger.debug(f"ItemSubCostCode mapping not found for QboItem ID: {qbo_item.id}")
            return None
        
        return item_mapping.sub_cost_code_id

    def _get_project_public_id(self, qbo_customer_ref_value: str) -> Optional[str]:
        """
        Get the Project public_id from QBO customer reference value.
        
        Args:
            qbo_customer_ref_value: QBO customer reference value (QBO Customer ID)
        
        Returns:
            str: Project public_id or None
        """
        if not qbo_customer_ref_value:
            return None
        
        # First find the QboCustomer by qbo_id
        qbo_customer = self.qbo_customer_repo.read_by_qbo_id(qbo_customer_ref_value)
        if not qbo_customer:
            logger.debug(f"QboCustomer not found for qbo_id: {qbo_customer_ref_value}")
            return None
        
        # Then find the CustomerProject mapping
        customer_mapping = self.customer_project_repo.read_by_qbo_customer_id(qbo_customer.id)
        if not customer_mapping:
            logger.debug(f"CustomerProject mapping not found for QboCustomer ID: {qbo_customer.id}")
            return None
        
        # Get the Project
        project = ProjectService().read_by_id(customer_mapping.project_id)
        if not project:
            logger.debug(f"Project not found for ID: {customer_mapping.project_id}")
            return None
        
        return project.public_id

    def create_mapping(self, expense_line_item_id: int, qbo_purchase_line_id: int) -> PurchaseLineExpenseLineItem:
        """
        Create a mapping between ExpenseLineItem and QboPurchaseLine.
        
        Args:
            expense_line_item_id: Database ID of ExpenseLineItem record
            qbo_purchase_line_id: Database ID of QboPurchaseLine record
        
        Returns:
            PurchaseLineExpenseLineItem: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_line_item = self.mapping_repo.read_by_expense_line_item_id(expense_line_item_id)
        if existing_by_line_item:
            raise ValueError(
                f"ExpenseLineItem {expense_line_item_id} is already mapped to QboPurchaseLine {existing_by_line_item.qbo_purchase_line_id}"
            )
        
        existing_by_qbo_line = self.mapping_repo.read_by_qbo_purchase_line_id(qbo_purchase_line_id)
        if existing_by_qbo_line:
            raise ValueError(
                f"QboPurchaseLine {qbo_purchase_line_id} is already mapped to ExpenseLineItem {existing_by_qbo_line.expense_line_item_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(expense_line_item_id=expense_line_item_id, qbo_purchase_line_id=qbo_purchase_line_id)

    def get_mapping_by_expense_line_item_id(self, expense_line_item_id: int) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Get mapping by ExpenseLineItem ID.
        """
        return self.mapping_repo.read_by_expense_line_item_id(expense_line_item_id)

    def get_mapping_by_qbo_purchase_line_id(self, qbo_purchase_line_id: int) -> Optional[PurchaseLineExpenseLineItem]:
        """
        Get mapping by QboPurchaseLine ID.
        """
        return self.mapping_repo.read_by_qbo_purchase_line_id(qbo_purchase_line_id)
