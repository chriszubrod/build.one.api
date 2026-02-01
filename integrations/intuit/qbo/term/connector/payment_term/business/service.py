# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
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
    ):
        """Initialize the TermPaymentTermConnector."""
        self.mapping_repo = mapping_repo or TermPaymentTermRepository()
        self.payment_term_service = payment_term_service or PaymentTermService()

    def sync_from_qbo_term(self, qbo_term: QboTerm) -> PaymentTerm:
        """
        Sync data from QboTerm to PaymentTerm module.
        
        This method:
        1. Checks if a mapping exists
        2. Creates or updates the PaymentTerm accordingly
        
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
        
        # Check for existing mapping
        mapping = self.mapping_repo.read_by_qbo_term_id(qbo_term.id)
        
        if mapping:
            # Found existing mapping - update the PaymentTerm
            payment_term = self.payment_term_service.read_by_id(mapping.payment_term_id)
            if payment_term:
                logger.info(f"Updating existing PaymentTerm {payment_term.id} from QboTerm {qbo_term.id}")
                payment_term.name = term_name
                payment_term.description = description
                payment_term.discount_percent = float(qbo_term.discount_percent) if qbo_term.discount_percent else None
                payment_term.discount_days = qbo_term.discount_days
                payment_term.due_days = qbo_term.due_days
                payment_term = self.payment_term_service.repo.update_by_id(payment_term)
                return payment_term
            else:
                # Mapping exists but PaymentTerm not found - recreate PaymentTerm
                logger.warning(f"Mapping exists but PaymentTerm {mapping.payment_term_id} not found. Creating new PaymentTerm.")
                self.mapping_repo.delete_by_id(mapping.id)
                mapping = None
        
        # Create new PaymentTerm
        logger.info(f"Creating new PaymentTerm from QboTerm {qbo_term.id}: name={term_name}")
        payment_term = self.payment_term_service.create(
            name=term_name,
            description=description,
            discount_percent=float(qbo_term.discount_percent) if qbo_term.discount_percent else None,
            discount_days=qbo_term.discount_days,
            due_days=qbo_term.due_days,
        )
        
        # Create mapping
        payment_term_id = int(payment_term.id) if isinstance(payment_term.id, str) else payment_term.id
        try:
            mapping = self.create_mapping(payment_term_id=payment_term_id, qbo_term_id=qbo_term.id)
            logger.info(f"Created mapping: PaymentTerm {payment_term_id} <-> QboTerm {qbo_term.id}")
        except ValueError as e:
            logger.warning(f"Could not create mapping: {e}")
        
        return payment_term

    def create_mapping(self, payment_term_id: int, qbo_term_id: int) -> TermPaymentTerm:
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
        
        # Create mapping
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
