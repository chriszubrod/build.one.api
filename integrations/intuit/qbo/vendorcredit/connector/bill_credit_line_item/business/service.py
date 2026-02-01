# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.vendorcredit.business.model import QboVendorCreditLine
from entities.bill_credit_line_item.business.service import BillCreditLineItemService
from entities.bill_credit_line_item.business.model import BillCreditLineItem
from entities.project.business.service import ProjectService
from entities.sub_cost_code.business.service import SubCostCodeService

logger = logging.getLogger(__name__)


class VendorCreditLineItemConnector:
    """Connector for syncing QBO VendorCredit lines to BillCreditLineItems."""

    def __init__(self):
        self.bill_credit_line_item_service = BillCreditLineItemService()
        self.project_service = ProjectService()
        self.sub_cost_code_service = SubCostCodeService()

    def sync_from_qbo_line(
        self,
        bill_credit_public_id: str,
        qbo_line: QboVendorCreditLine,
    ) -> Optional[BillCreditLineItem]:
        """
        Sync a QBO VendorCredit line to a BillCreditLineItem.
        
        Prefers ItemBasedExpenseLineDetail when available.
        """
        try:
            # Resolve project from CustomerRef (if billable)
            project_public_id = None
            if qbo_line.customer_ref_value:
                project_public_id = self._get_project_public_id(qbo_line.customer_ref_value)
            
            # Resolve sub_cost_code from ItemRef
            sub_cost_code_id = None
            if qbo_line.item_ref_value:
                sub_cost_code_id = self._get_sub_cost_code_id(qbo_line.item_ref_value)
            
            # Determine billable status
            is_billable = qbo_line.billable_status in ("Billable", "HasBeenBilled")
            
            # Calculate billable amount (for now, same as amount if billable)
            billable_amount = qbo_line.amount if is_billable else None
            
            # Create line item
            line_item = self.bill_credit_line_item_service.create(
                bill_credit_public_id=bill_credit_public_id,
                sub_cost_code_id=sub_cost_code_id,
                project_public_id=project_public_id,
                description=qbo_line.description,
                quantity=qbo_line.qty,
                unit_price=qbo_line.unit_price,
                amount=qbo_line.amount,
                is_billable=is_billable,
                billable_amount=billable_amount,
                is_draft=True,
            )
            
            return line_item
            
        except Exception as e:
            logger.error(f"Error syncing QBO line {qbo_line.qbo_line_id}: {e}")
            return None

    def _get_project_public_id(self, qbo_customer_ref_value: str) -> Optional[str]:
        """Resolve QBO customer ref to local project public_id (QboCustomer by qbo_id -> CustomerProject by qbo_customer_id)."""
        try:
            from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
            from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository

            qbo_customer_repo = QboCustomerRepository()
            customer_project_repo = CustomerProjectRepository()
            qbo_customer = qbo_customer_repo.read_by_qbo_id(qbo_customer_ref_value)
            if not qbo_customer:
                return None
            mapping = customer_project_repo.read_by_qbo_customer_id(qbo_customer.id)
            if not mapping or not mapping.project_id:
                return None
            project = self.project_service.read_by_id(id=str(mapping.project_id))
            return project.public_id if project else None
        except Exception as e:
            logger.warning(f"Error resolving customer ref {qbo_customer_ref_value}: {e}")
            return None

    def _get_sub_cost_code_id(self, qbo_item_ref_value: str) -> Optional[int]:
        """Resolve QBO item ref to local sub_cost_code_id (QboItem by qbo_id -> ItemSubCostCode by qbo_item_id)."""
        try:
            from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
            from integrations.intuit.qbo.item.persistence.repo import QboItemRepository

            qbo_item_repo = QboItemRepository()
            item_scc_repo = ItemSubCostCodeRepository()
            qbo_item = qbo_item_repo.read_by_qbo_id(qbo_item_ref_value)
            if not qbo_item:
                return None
            mapping = item_scc_repo.read_by_qbo_item_id(qbo_item.id)
            return mapping.sub_cost_code_id if mapping else None
        except Exception as e:
            logger.warning(f"Error resolving item ref {qbo_item_ref_value}: {e}")
            return None
