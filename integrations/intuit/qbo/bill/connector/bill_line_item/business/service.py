# Python Standard Library Imports
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.bill.connector.bill_line_item.business.model import BillLineItemBillLine
from integrations.intuit.qbo.bill.connector.bill_line_item.persistence.repo import BillLineItemBillLineRepository
from integrations.intuit.qbo.bill.business.model import QboBillLine
from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository
from integrations.intuit.qbo.bill.persistence.repo import QboBillLineRepository
from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import ItemSubCostCodeRepository
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item.business.model import BillLineItem
from entities.bill.business.service import BillService
from entities.project.business.service import ProjectService

logger = logging.getLogger(__name__)


class BillLineItemConnector:
    """
    Connector service for synchronization between QboBillLine and BillLineItem modules.
    """

    def __init__(
        self,
        mapping_repo: Optional[BillLineItemBillLineRepository] = None,
        bill_line_item_service: Optional[BillLineItemService] = None,
        bill_service: Optional[BillService] = None,
        bill_bill_repo: Optional[BillBillRepository] = None,
        qbo_item_repo: Optional[QboItemRepository] = None,
        qbo_bill_line_repo: Optional[QboBillLineRepository] = None,
        item_sub_cost_code_repo: Optional[ItemSubCostCodeRepository] = None,
        qbo_customer_repo: Optional[QboCustomerRepository] = None,
        customer_project_repo: Optional[CustomerProjectRepository] = None,
        project_service: Optional[ProjectService] = None,
    ):
        """Initialize the BillLineItemConnector."""
        self.mapping_repo = mapping_repo or BillLineItemBillLineRepository()
        self.bill_line_item_service = bill_line_item_service or BillLineItemService()
        self.bill_service = bill_service or BillService()
        self.bill_bill_repo = bill_bill_repo or BillBillRepository()
        self.qbo_item_repo = qbo_item_repo or QboItemRepository()
        self.qbo_bill_line_repo = qbo_bill_line_repo or QboBillLineRepository()
        self.item_sub_cost_code_repo = item_sub_cost_code_repo or ItemSubCostCodeRepository()
        self.qbo_customer_repo = qbo_customer_repo or QboCustomerRepository()
        self.customer_project_repo = customer_project_repo or CustomerProjectRepository()
        self.project_service = project_service or ProjectService()

    def sync_from_qbo_bill_line(self, bill_id: int, qbo_bill_line: QboBillLine) -> BillLineItem:
        """
        Sync data from QboBillLine to BillLineItem module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the BillLineItem accordingly
        
        Args:
            bill_id: Database ID of the Bill in our system
            qbo_bill_line: QboBillLine record
        
        Returns:
            BillLineItem: The synced BillLineItem record
        """
        # Get the Bill public_id from bill_id
        bill = self.bill_service.read_by_id(bill_id)
        if not bill:
            raise ValueError(f"Bill with id {bill_id} not found")
        
        bill_public_id = bill.public_id
        
        # Map QBO BillLine fields to BillLineItem module fields
        description = qbo_bill_line.description
        amount = qbo_bill_line.amount
        qty = int(qbo_bill_line.qty) if qbo_bill_line.qty else None
        rate = qbo_bill_line.unit_price
        
        # Map markup from QBO (QBO uses percentage like 10 for 10%, convert to decimal 0.10)
        markup = None
        if qbo_bill_line.markup_percent is not None:
            markup = qbo_bill_line.markup_percent / Decimal('100')
        
        # Calculate price as UnitPrice * (1 + MarkupPercent/100)
        price = None
        if qbo_bill_line.unit_price is not None and qbo_bill_line.markup_percent is not None:
            price = qbo_bill_line.unit_price * (Decimal('1') + qbo_bill_line.markup_percent / Decimal('100'))
        
        # Determine billable and billed status from QBO BillableStatus
        # QBO BillableStatus values: "Billable" (not yet invoiced), "HasBeenBilled" (already invoiced), "NotBillable"
        # - is_billable: True if marked billable (regardless of whether already billed)
        # - is_billed: True if the expense has already been invoiced to a customer
        is_billable = None
        is_billed = None
        if qbo_bill_line.billable_status:
            is_billable = qbo_bill_line.billable_status in ("Billable", "HasBeenBilled")
            is_billed = qbo_bill_line.billable_status == "HasBeenBilled"
        
        # Look up SubCostCode from QBO Item reference
        sub_cost_code_id = None
        if qbo_bill_line.item_ref_value:
            # Find the QboItem by its QboId
            qbo_item = self.qbo_item_repo.read_by_qbo_id(qbo_bill_line.item_ref_value)
            if qbo_item:
                # Look up the SubCostCode mapping for this QboItem
                item_sub_cost_code = self.item_sub_cost_code_repo.read_by_qbo_item_id(qbo_item.id)
                if item_sub_cost_code:
                    sub_cost_code_id = item_sub_cost_code.sub_cost_code_id
                    logger.debug(f"Found SubCostCode {sub_cost_code_id} for QboItem {qbo_item.id}")
                else:
                    logger.debug(f"No SubCostCode mapping found for QboItem {qbo_item.id}")
            else:
                logger.debug(f"QboItem with QboId {qbo_bill_line.item_ref_value} not found in local database")
        
        # Look up Project from QBO Customer reference (customer_ref can be a job/sub-customer which maps to Project)
        project_public_id = None
        if qbo_bill_line.customer_ref_value:
            # Find the QboCustomer by its QboId
            qbo_customer = self.qbo_customer_repo.read_by_qbo_id(qbo_bill_line.customer_ref_value)
            if qbo_customer:
                # Look up the Project mapping for this QboCustomer
                customer_project = self.customer_project_repo.read_by_qbo_customer_id(qbo_customer.id)
                if customer_project:
                    # Get the Project to retrieve its public_id
                    project = self.project_service.read_by_id(customer_project.project_id)
                    if project:
                        project_public_id = project.public_id
                        logger.debug(f"Found Project {project.id} for QboCustomer {qbo_customer.id}")
                    else:
                        logger.debug(f"Project {customer_project.project_id} not found in database")
                else:
                    logger.debug(f"No Project mapping found for QboCustomer {qbo_customer.id}")
            else:
                logger.debug(f"QboCustomer with QboId {qbo_bill_line.customer_ref_value} not found in local database")
        
        # Check for existing mapping by current qbo_bill_line.id.
        mapping = self.mapping_repo.read_by_qbo_bill_line_id(qbo_bill_line.id)

        if not mapping:
            # Shape B fallback (task #17): when no direct mapping exists, look for
            # an orphaned BillLineItem on this bill whose content matches this QBO
            # line. This handles the case where QBO regenerates line IDs after a
            # bill edit — the old QboBillLine row is deleted by stale-line cleanup,
            # leaving the local BillLineItem unmapped. Matching by content
            # (description, amount, qty, rate) re-adopts the correct local line
            # rather than creating a duplicate.
            unmapped = self._find_unmapped_line_items(bill_id)
            orphan = self._match_by_fingerprint(
                unmapped=unmapped,
                description=description,
                amount=amount,
                qty=qty,
                rate=rate,
            )
            if orphan is not None:
                logger.info(
                    f"Adopting orphaned BillLineItem {orphan.id} for QboBillLine {qbo_bill_line.id} "
                    f"via content fingerprint match (QBO line ID regenerated or re-ordered)"
                )
                try:
                    mapping = self.mapping_repo.create(
                        bill_line_item_id=orphan.id,
                        qbo_bill_line_id=qbo_bill_line.id,
                    )
                except ValueError as error:
                    logger.warning(
                        f"Could not adopt orphaned BillLineItem {orphan.id}: {error}"
                    )

        if mapping:
            # Found existing mapping - update the BillLineItem
            line_item = self.bill_line_item_service.read_by_id(mapping.bill_line_item_id)
            if line_item:
                logger.info(f"Updating existing BillLineItem {line_item.id} from QboBillLine {qbo_bill_line.id}")

                line_item = self.bill_line_item_service.update_by_public_id(
                    line_item.public_id,
                    bill_public_id=bill_public_id,
                    sub_cost_code_id=sub_cost_code_id,
                    project_public_id=project_public_id,
                    description=description,
                    quantity=qty,
                    rate=rate,
                    amount=amount,
                    is_billable=is_billable,
                    is_billed=is_billed,
                    markup=markup,
                    price=price,
                    is_draft=False,
                    row_version=line_item.row_version,
                )
                return line_item
            else:
                # Mapping exists but BillLineItem not found - recreate
                logger.warning(f"Mapping exists but BillLineItem {mapping.bill_line_item_id} not found. Creating new.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new BillLineItem
        logger.info(f"Creating new BillLineItem from QboBillLine {qbo_bill_line.id}")
        line_item = self.bill_line_item_service.create(
            bill_public_id=bill_public_id,
            sub_cost_code_id=sub_cost_code_id,
            project_public_id=project_public_id,
            description=description,
            quantity=qty,
            rate=rate,
            amount=amount,
            is_billable=is_billable,
            is_billed=is_billed,
            markup=markup,
            price=price,
            is_draft=False
        )
        
        # Create mapping
        line_item_id = int(line_item.id) if isinstance(line_item.id, str) else line_item.id
        try:
            mapping = self.create_mapping(bill_line_item_id=line_item_id, qbo_bill_line_id=qbo_bill_line.id)
            logger.info(f"Created mapping: BillLineItem {line_item_id} <-> QboBillLine {qbo_bill_line.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        return line_item

    # ------------------------------------------------------------------ #
    # Shape B line-matching helpers (task #17)
    # ------------------------------------------------------------------ #

    def _find_unmapped_line_items(self, bill_id: int):
        """Return BillLineItems on this bill that have no QboBillLine mapping."""
        existing = self.bill_line_item_service.read_by_bill_id(bill_id)
        return [
            li for li in existing
            if not self.mapping_repo.read_by_bill_line_item_id(li.id)
        ]

    @staticmethod
    def _normalize_for_fingerprint(value) -> str:
        """Canonicalize a value for content-fingerprint comparison."""
        if value is None:
            return ""
        # Decimals and floats: normalize to a fixed-precision string so 10 == 10.00.
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except Exception:
            pass
        return str(value).strip()

    def _match_by_fingerprint(
        self,
        *,
        unmapped,
        description,
        amount,
        qty,
        rate,
    ):
        """
        Find at most one unmapped line item whose content fingerprint matches.

        The fingerprint is `(description, amount, quantity, rate)` — the four
        fields most likely to uniquely identify a line. Exact match on all four
        is required. If zero or multiple candidates match, returns None — the
        caller falls through to creating a new BillLineItem rather than risk
        adopting the wrong one.
        """
        target = (
            self._normalize_for_fingerprint(description),
            self._normalize_for_fingerprint(amount),
            self._normalize_for_fingerprint(qty),
            self._normalize_for_fingerprint(rate),
        )

        matches = []
        for candidate in unmapped:
            candidate_fp = (
                self._normalize_for_fingerprint(getattr(candidate, "description", None)),
                self._normalize_for_fingerprint(getattr(candidate, "amount", None)),
                self._normalize_for_fingerprint(getattr(candidate, "quantity", None)),
                self._normalize_for_fingerprint(getattr(candidate, "rate", None)),
            )
            if candidate_fp == target:
                matches.append(candidate)

        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.info(
                f"Content-fingerprint match ambiguous: {len(matches)} unmapped "
                f"BillLineItems have identical (description, amount, qty, rate); "
                f"creating new line rather than guessing"
            )
        return None

    def create_mapping(self, bill_line_item_id: int, qbo_bill_line_id: int) -> BillLineItemBillLine:
        """
        Create a mapping between BillLineItem and QboBillLine.
        
        Args:
            bill_line_item_id: Database ID of BillLineItem record
            qbo_bill_line_id: Database ID of QboBillLine record
        
        Returns:
            BillLineItemBillLine: The created mapping record
        
        Raises:
            ValueError: If mapping already exists or validation fails
        """
        # Validate 1:1 constraints
        existing_by_line_item = self.mapping_repo.read_by_bill_line_item_id(bill_line_item_id)
        if existing_by_line_item:
            raise ValueError(
                f"BillLineItem {bill_line_item_id} is already mapped to QboBillLine {existing_by_line_item.qbo_bill_line_id}"
            )
        
        existing_by_qbo_line = self.mapping_repo.read_by_qbo_bill_line_id(qbo_bill_line_id)
        if existing_by_qbo_line:
            raise ValueError(
                f"QboBillLine {qbo_bill_line_id} is already mapped to BillLineItem {existing_by_qbo_line.bill_line_item_id}"
            )
        
        # Create mapping
        return self.mapping_repo.create(bill_line_item_id=bill_line_item_id, qbo_bill_line_id=qbo_bill_line_id)

    def get_mapping_by_bill_line_item_id(self, bill_line_item_id: int) -> Optional[BillLineItemBillLine]:
        """
        Get mapping by BillLineItem ID.
        """
        return self.mapping_repo.read_by_bill_line_item_id(bill_line_item_id)

    def get_mapping_by_qbo_bill_line_id(self, qbo_bill_line_id: int) -> Optional[BillLineItemBillLine]:
        """
        Get mapping by QboBillLine ID.
        """
        return self.mapping_repo.read_by_qbo_bill_line_id(qbo_bill_line_id)
