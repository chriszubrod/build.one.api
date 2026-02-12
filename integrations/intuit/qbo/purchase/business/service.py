# Python Standard Library Imports
import logging
import time
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.purchase.business.model import QboPurchase, QboPurchaseLine
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository, QboPurchaseLineRepository
from integrations.intuit.qbo.purchase.external.client import QboPurchaseClient
from integrations.intuit.qbo.purchase.external.schemas import QboPurchase as QboPurchaseExternalSchema
from integrations.intuit.qbo.auth.business.service import QboAuthService
from shared.database import with_retry

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process purchases in batches
BATCH_DELAY = 0.5  # Delay between batches (seconds)
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


class QboPurchaseService:
    """
    Service for QboPurchase entity business operations.
    """

    def __init__(
        self,
        repo: Optional[QboPurchaseRepository] = None,
        line_repo: Optional[QboPurchaseLineRepository] = None,
    ):
        """Initialize the QboPurchaseService."""
        self.repo = repo or QboPurchaseRepository()
        self.line_repo = line_repo or QboPurchaseLineRepository()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sync_to_modules: bool = False
    ) -> List[QboPurchase]:
        """
        Fetch Purchases from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Purchases where Metadata.LastUpdatedTime > last_updated_time.
            start_date: Optional date string (YYYY-MM-DD). If provided, only fetches
                Purchases where TxnDate >= start_date.
            end_date: Optional date string (YYYY-MM-DD). If provided, only fetches
                Purchases where TxnDate <= end_date.
            sync_to_modules: If True, also sync to Expense/ExpenseLineItem modules
        
        Returns:
            List[QboPurchase]: The synced purchase records
        """
        # Get valid access token
        auth_service = QboAuthService()
        qbo_auth = auth_service.ensure_valid_token(realm_id=realm_id)
        
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid access token found for realm_id: {realm_id}")
        
        # Fetch Purchases from QBO API
        with QboPurchaseClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            qbo_purchases: List[QboPurchaseExternalSchema] = client.query_all_purchases(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
            )
        
        if not qbo_purchases:
            logger.info(f"No Purchases found since {last_updated_time or 'beginning'}")
            return []
        
        logger.info(f"Retrieved {len(qbo_purchases)} purchases from QBO")
        
        # Process each purchase with retry logic and batch delays
        synced_purchases = []
        failed_purchases = []
        
        for i, qbo_purchase in enumerate(qbo_purchases):
            try:
                # Use retry logic for transient database errors
                local_purchase = with_retry(
                    self._upsert_purchase,
                    qbo_purchase,
                    realm_id,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
                synced_purchases.append(local_purchase)
                logger.debug(f"Upserted purchase {qbo_purchase.id} ({i + 1}/{len(qbo_purchases)})")
            except Exception as e:
                logger.error(f"Failed to upsert purchase {qbo_purchase.id}: {e}")
                failed_purchases.append(qbo_purchase.id)
            
            # Add delay between batches to prevent connection exhaustion
            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(qbo_purchases):
                logger.debug(f"Processed {i + 1}/{len(qbo_purchases)} purchases, pausing...")
                time.sleep(BATCH_DELAY)
        
        if failed_purchases:
            logger.warning(f"Failed to upsert {len(failed_purchases)} purchases: {failed_purchases}")
        
        # Sync to modules if requested
        if sync_to_modules:
            self._sync_to_expenses(synced_purchases)
        
        return synced_purchases

    def _upsert_purchase(self, qbo_purchase: QboPurchaseExternalSchema, realm_id: str) -> QboPurchase:
        """
        Create or update a QboPurchase record along with its line items.
        
        Args:
            qbo_purchase: QBO Purchase from external API
            realm_id: QBO realm ID
        
        Returns:
            QboPurchase: The created or updated record
        """
        # Check if purchase already exists
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_purchase.id, realm_id=realm_id)
        
        # Extract reference fields
        account_ref_value = qbo_purchase.account_ref.value if qbo_purchase.account_ref else None
        account_ref_name = qbo_purchase.account_ref.name if qbo_purchase.account_ref else None
        entity_ref_value = qbo_purchase.entity_ref.value if qbo_purchase.entity_ref else None
        entity_ref_name = qbo_purchase.entity_ref.name if qbo_purchase.entity_ref else None
        currency_ref_value = qbo_purchase.currency_ref.value if qbo_purchase.currency_ref else None
        currency_ref_name = qbo_purchase.currency_ref.name if qbo_purchase.currency_ref else None
        department_ref_value = qbo_purchase.department_ref.value if qbo_purchase.department_ref else None
        department_ref_name = qbo_purchase.department_ref.name if qbo_purchase.department_ref else None
        
        if existing:
            # Update existing record
            logger.debug(f"Updating existing QBO purchase {qbo_purchase.id}")
            local_purchase = self.repo.update_by_qbo_id(
                qbo_id=qbo_purchase.id,
                row_version=existing.row_version_bytes,
                sync_token=qbo_purchase.sync_token,
                realm_id=realm_id,
                payment_type=qbo_purchase.payment_type,
                account_ref_value=account_ref_value,
                account_ref_name=account_ref_name,
                entity_ref_value=entity_ref_value,
                entity_ref_name=entity_ref_name,
                credit=qbo_purchase.credit,
                txn_date=qbo_purchase.txn_date,
                doc_number=qbo_purchase.doc_number,
                private_note=qbo_purchase.private_note,
                total_amt=qbo_purchase.total_amt,
                currency_ref_value=currency_ref_value,
                currency_ref_name=currency_ref_name,
                exchange_rate=qbo_purchase.exchange_rate,
                department_ref_value=department_ref_value,
                department_ref_name=department_ref_name,
                global_tax_calculation=qbo_purchase.global_tax_calculation,
            )
        else:
            # Create new record
            logger.debug(f"Creating new QBO purchase {qbo_purchase.id}")
            local_purchase = self.repo.create(
                qbo_id=qbo_purchase.id,
                sync_token=qbo_purchase.sync_token,
                realm_id=realm_id,
                payment_type=qbo_purchase.payment_type,
                account_ref_value=account_ref_value,
                account_ref_name=account_ref_name,
                entity_ref_value=entity_ref_value,
                entity_ref_name=entity_ref_name,
                credit=qbo_purchase.credit,
                txn_date=qbo_purchase.txn_date,
                doc_number=qbo_purchase.doc_number,
                private_note=qbo_purchase.private_note,
                total_amt=qbo_purchase.total_amt,
                currency_ref_value=currency_ref_value,
                currency_ref_name=currency_ref_name,
                exchange_rate=qbo_purchase.exchange_rate,
                department_ref_value=department_ref_value,
                department_ref_name=department_ref_name,
                global_tax_calculation=qbo_purchase.global_tax_calculation,
            )
        
        # Upsert line items
        if qbo_purchase.line:
            self._upsert_purchase_lines(local_purchase.id, qbo_purchase.line)
        
        return local_purchase

    def _upsert_purchase_lines(self, qbo_purchase_id: int, lines: list) -> None:
        """
        Upsert purchase line items.
        
        Args:
            qbo_purchase_id: Database ID of the QboPurchase
            lines: List of QboPurchaseLine from external API
        """
        for line in lines:
            # Extract detail-specific fields based on detail type
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
            
            if line.detail_type == "ItemBasedExpenseLineDetail" and line.item_based_expense_line_detail:
                detail = line.item_based_expense_line_detail
                item_ref_value = detail.item_ref.value if detail.item_ref else None
                item_ref_name = detail.item_ref.name if detail.item_ref else None
                customer_ref_value = detail.customer_ref.value if detail.customer_ref else None
                customer_ref_name = detail.customer_ref.name if detail.customer_ref else None
                class_ref_value = detail.class_ref.value if detail.class_ref else None
                class_ref_name = detail.class_ref.name if detail.class_ref else None
                billable_status = detail.billable_status
                qty = detail.qty
                unit_price = detail.unit_price
                # Extract markup percent from MarkupInfo
                if detail.markup_info and isinstance(detail.markup_info, dict):
                    markup_percent = detail.markup_info.get("Percent")
            elif line.detail_type == "AccountBasedExpenseLineDetail" and line.account_based_expense_line_detail:
                detail = line.account_based_expense_line_detail
                account_ref_value = detail.account_ref.value if detail.account_ref else None
                account_ref_name = detail.account_ref.name if detail.account_ref else None
                customer_ref_value = detail.customer_ref.value if detail.customer_ref else None
                customer_ref_name = detail.customer_ref.name if detail.customer_ref else None
                class_ref_value = detail.class_ref.value if detail.class_ref else None
                class_ref_name = detail.class_ref.name if detail.class_ref else None
                billable_status = detail.billable_status
                # Extract markup percent from MarkupInfo
                if detail.markup_info and isinstance(detail.markup_info, dict):
                    markup_percent = detail.markup_info.get("Percent")
            
            # Check if line exists
            existing_line = None
            if line.id:
                existing_line = self.line_repo.read_by_qbo_purchase_id_and_qbo_line_id(
                    qbo_purchase_id=qbo_purchase_id,
                    qbo_line_id=line.id
                )
            
            if existing_line:
                # Update existing line
                self.line_repo.update_by_id(
                    id=existing_line.id,
                    row_version=existing_line.row_version_bytes,
                    line_num=line.line_num,
                    description=line.description,
                    amount=line.amount,
                    detail_type=line.detail_type,
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
            else:
                # Create new line
                self.line_repo.create(
                    qbo_purchase_id=qbo_purchase_id,
                    qbo_line_id=line.id,
                    line_num=line.line_num,
                    description=line.description,
                    amount=line.amount,
                    detail_type=line.detail_type,
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

    def _sync_to_expenses(self, purchases: List[QboPurchase]) -> None:
        """
        Sync purchases to Expense module.
        
        Args:
            purchases: List of QboPurchase records
        """
        if not purchases:
            return
        
        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
        
        connector = PurchaseExpenseConnector()
        
        for purchase in purchases:
            try:
                # Get purchase lines for this purchase
                purchase_lines = self.line_repo.read_by_qbo_purchase_id(purchase.id)
                expense = connector.sync_from_qbo_purchase(purchase, purchase_lines)
                logger.info(f"Synced QboPurchase {purchase.id} to Expense {expense.id}")
            except Exception as e:
                logger.error(f"Failed to sync QboPurchase {purchase.id} to Expense: {e}")

    def read_all(self) -> List[QboPurchase]:
        """
        Read all QboPurchases.
        """
        return self.repo.read_all()

    def read_by_realm_id(self, realm_id: str) -> List[QboPurchase]:
        """
        Read all QboPurchases by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboPurchase]:
        """
        Read a QboPurchase by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_id(self, id: int) -> Optional[QboPurchase]:
        """
        Read a QboPurchase by database ID.
        """
        return self.repo.read_by_id(id)

    def read_lines_by_qbo_purchase_id(self, qbo_purchase_id: int) -> List[QboPurchaseLine]:
        """
        Read all QboPurchaseLines for a QboPurchase.
        """
        return self.line_repo.read_by_qbo_purchase_id(qbo_purchase_id)

    def get_lines_needing_update(self, realm_id: Optional[str] = None) -> List[dict]:
        """
        Get purchase lines with AccountRefName = 'NEED TO UPDATE' and no ExpenseLineItem link.
        """
        return self.line_repo.read_lines_needing_update(realm_id=realm_id)
