# Python Standard Library Imports
import logging
import time
from typing import List, Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.bill.business.model import QboBill, QboBillLine
from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository, QboBillLineRepository
from integrations.intuit.qbo.bill.external.client import QboBillClient
from integrations.intuit.qbo.bill.external.schemas import QboBill as QboBillExternalSchema
from integrations.intuit.qbo.auth.business.service import QboAuthService
from shared.database import with_retry

logger = logging.getLogger(__name__)

# Sync configuration
BATCH_SIZE = 10  # Process bills in batches
BATCH_DELAY = 0.5  # Delay between batches (seconds)
MAX_RETRIES = 3  # Max retries for transient errors
INITIAL_RETRY_DELAY = 2.0  # Initial retry delay (seconds)


class QboBillService:
    """
    Service for QboBill entity business operations.
    """

    def __init__(
        self,
        repo: Optional[QboBillRepository] = None,
        line_repo: Optional[QboBillLineRepository] = None,
    ):
        """Initialize the QboBillService."""
        self.repo = repo or QboBillRepository()
        self.line_repo = line_repo or QboBillLineRepository()

    def sync_from_qbo(
        self,
        realm_id: str,
        last_updated_time: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sync_to_modules: bool = False
    ) -> List[QboBill]:
        """
        Fetch Bills from QBO API and store locally.
        Uses upsert pattern: creates if not exists, updates if exists.
        
        Args:
            realm_id: QBO company realm ID
            last_updated_time: Optional ISO format datetime string. If provided, only fetches
                Bills where Metadata.LastUpdatedTime > last_updated_time.
            start_date: Optional date string (YYYY-MM-DD). If provided, only fetches
                Bills where TxnDate >= start_date.
            end_date: Optional date string (YYYY-MM-DD). If provided, only fetches
                Bills where TxnDate <= end_date.
            sync_to_modules: If True, also sync to Bill/BillLineItem modules
        
        Returns:
            List[QboBill]: The synced bill records
        """
        # Get valid access token
        self._auth_service = QboAuthService()
        self._realm_id = realm_id
        qbo_auth = self._auth_service.ensure_valid_token(realm_id=realm_id)
        
        if not qbo_auth or not qbo_auth.access_token:
            raise ValueError(f"No valid access token found for realm_id: {realm_id}")
        
        # Fetch Bills from QBO API
        with QboBillClient(
            access_token=qbo_auth.access_token,
            realm_id=realm_id
        ) as client:
            qbo_bills: List[QboBillExternalSchema] = client.query_all_bills(
                last_updated_time=last_updated_time,
                start_date=start_date,
                end_date=end_date,
            )
        
        if not qbo_bills:
            logger.info(f"No Bills found since {last_updated_time or 'beginning'}")
            return []
        
        logger.info(f"Retrieved {len(qbo_bills)} bills from QBO")
        
        # Process each bill with retry logic and batch delays
        synced_bills = []
        failed_bills = []
        
        for i, qbo_bill in enumerate(qbo_bills):
            try:
                # Use retry logic for transient database errors
                local_bill = with_retry(
                    self._upsert_bill,
                    qbo_bill,
                    realm_id,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
                synced_bills.append(local_bill)
                logger.debug(f"Upserted bill {qbo_bill.id} ({i + 1}/{len(qbo_bills)})")
            except Exception as e:
                logger.error(f"Failed to upsert bill {qbo_bill.id}: {e}")
                failed_bills.append(qbo_bill.id)
            
            # Add delay between batches to prevent connection exhaustion
            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(qbo_bills):
                logger.debug(f"Processed {i + 1}/{len(qbo_bills)} bills, pausing...")
                time.sleep(BATCH_DELAY)
                # Refresh access token to prevent expiration during long syncs
                self._auth_service.ensure_valid_token(realm_id=self._realm_id)
        
        if failed_bills:
            logger.warning(f"Failed to upsert {len(failed_bills)} bills: {failed_bills}")
        
        # Sync to modules if requested
        if sync_to_modules:
            self._sync_to_bills(synced_bills)
        
        return synced_bills

    def _upsert_bill(self, qbo_bill: QboBillExternalSchema, realm_id: str) -> QboBill:
        """
        Create or update a QboBill record along with its line items.
        
        Args:
            qbo_bill: QBO Bill from external API
            realm_id: QBO realm ID
        
        Returns:
            QboBill: The created or updated record
        """
        # Check if bill already exists
        existing = self.repo.read_by_qbo_id_and_realm_id(qbo_id=qbo_bill.id, realm_id=realm_id)
        
        # Extract reference fields
        vendor_ref_value = qbo_bill.vendor_ref.value if qbo_bill.vendor_ref else None
        vendor_ref_name = qbo_bill.vendor_ref.name if qbo_bill.vendor_ref else None
        ap_account_ref_value = qbo_bill.ap_account_ref.value if qbo_bill.ap_account_ref else None
        ap_account_ref_name = qbo_bill.ap_account_ref.name if qbo_bill.ap_account_ref else None
        sales_term_ref_value = qbo_bill.sales_term_ref.value if qbo_bill.sales_term_ref else None
        sales_term_ref_name = qbo_bill.sales_term_ref.name if qbo_bill.sales_term_ref else None
        currency_ref_value = qbo_bill.currency_ref.value if qbo_bill.currency_ref else None
        currency_ref_name = qbo_bill.currency_ref.name if qbo_bill.currency_ref else None
        department_ref_value = qbo_bill.department_ref.value if qbo_bill.department_ref else None
        department_ref_name = qbo_bill.department_ref.name if qbo_bill.department_ref else None
        
        if existing:
            # Update existing record
            logger.debug(f"Updating existing QBO bill {qbo_bill.id}")
            local_bill = self.repo.update_by_qbo_id(
                qbo_id=qbo_bill.id,
                row_version=existing.row_version_bytes,
                sync_token=qbo_bill.sync_token,
                realm_id=realm_id,
                vendor_ref_value=vendor_ref_value,
                vendor_ref_name=vendor_ref_name,
                txn_date=qbo_bill.txn_date,
                due_date=qbo_bill.due_date,
                doc_number=qbo_bill.doc_number,
                private_note=qbo_bill.private_note,
                total_amt=qbo_bill.total_amt,
                balance=qbo_bill.balance,
                ap_account_ref_value=ap_account_ref_value,
                ap_account_ref_name=ap_account_ref_name,
                sales_term_ref_value=sales_term_ref_value,
                sales_term_ref_name=sales_term_ref_name,
                currency_ref_value=currency_ref_value,
                currency_ref_name=currency_ref_name,
                exchange_rate=qbo_bill.exchange_rate,
                department_ref_value=department_ref_value,
                department_ref_name=department_ref_name,
                global_tax_calculation=qbo_bill.global_tax_calculation,
            )
        else:
            # Create new record
            logger.debug(f"Creating new QBO bill {qbo_bill.id}")
            local_bill = self.repo.create(
                qbo_id=qbo_bill.id,
                sync_token=qbo_bill.sync_token,
                realm_id=realm_id,
                vendor_ref_value=vendor_ref_value,
                vendor_ref_name=vendor_ref_name,
                txn_date=qbo_bill.txn_date,
                due_date=qbo_bill.due_date,
                doc_number=qbo_bill.doc_number,
                private_note=qbo_bill.private_note,
                total_amt=qbo_bill.total_amt,
                balance=qbo_bill.balance,
                ap_account_ref_value=ap_account_ref_value,
                ap_account_ref_name=ap_account_ref_name,
                sales_term_ref_value=sales_term_ref_value,
                sales_term_ref_name=sales_term_ref_name,
                currency_ref_value=currency_ref_value,
                currency_ref_name=currency_ref_name,
                exchange_rate=qbo_bill.exchange_rate,
                department_ref_value=department_ref_value,
                department_ref_name=department_ref_name,
                global_tax_calculation=qbo_bill.global_tax_calculation,
            )
        
        # Upsert line items
        if qbo_bill.line:
            self._upsert_bill_lines(local_bill.id, qbo_bill.line)
        
        return local_bill

    def _upsert_bill_lines(self, qbo_bill_id: int, lines: list) -> None:
        """
        Upsert bill line items.

        After inserting/updating all lines present in the QBO API response,
        any locally-stored QboBillLine whose qbo_line_id is NOT in the current
        response is stale (line was removed in QBO). Stale lines are deleted
        along with their BillLineItemBillLine mappings so that the next
        module sync does not process them as orphaned records.

        Args:
            qbo_bill_id: Database ID of the QboBill
            lines: List of QboBillLine from external API
        """
        current_qbo_line_ids = {line.id for line in lines if line.id}

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
                existing_line = self.line_repo.read_by_qbo_bill_id_and_qbo_line_id(
                    qbo_bill_id=qbo_bill_id,
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
                    qbo_bill_id=qbo_bill_id,
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

        # Delete stale lines — any locally-stored QboBillLine whose qbo_line_id is
        # no longer present in the QBO API response means QBO removed that line.
        # Delete the BillLineItemBillLine mapping first (FK constraint), then the line.
        from integrations.intuit.qbo.bill.connector.bill_line_item.persistence.repo import BillLineItemBillLineRepository
        mapping_repo = BillLineItemBillLineRepository()
        stored_lines = self.line_repo.read_by_qbo_bill_id(qbo_bill_id)
        for stored_line in stored_lines:
            if stored_line.qbo_line_id not in current_qbo_line_ids:
                logger.info(
                    f"Deleting stale QboBillLine id={stored_line.id} "
                    f"qbo_line_id={stored_line.qbo_line_id} (no longer in QBO response)"
                )
                # Delete mapping FIRST — only delete line if mapping cleanup succeeds
                mapping_cleaned = True
                try:
                    stale_mapping = mapping_repo.read_by_qbo_bill_line_id(stored_line.id)
                    if stale_mapping:
                        mapping_repo.delete_by_id(stale_mapping.id)
                        logger.info(f"Deleted stale BillLineItemBillLine mapping id={stale_mapping.id}")
                except Exception as e:
                    mapping_cleaned = False
                    logger.error(f"Could not delete stale mapping for QboBillLine {stored_line.id}: {e} — skipping line deletion to prevent orphan")

                if mapping_cleaned:
                    try:
                        self.line_repo.delete_by_id(stored_line.id)
                    except Exception as e:
                        logger.warning(f"Could not delete stale QboBillLine {stored_line.id}: {e}")

    def _sync_to_bills(self, bills: List[QboBill]) -> None:
        """
        Sync bills to Bill module.
        
        Args:
            bills: List of QboBill records
        """
        if not bills:
            return
        
        # Import here to avoid circular dependencies
        from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
        
        connector = BillBillConnector()
        
        for bill in bills:
            try:
                # Get bill lines for this bill
                bill_lines = self.line_repo.read_by_qbo_bill_id(bill.id)
                bill_module = connector.sync_from_qbo_bill(bill, bill_lines)
                logger.info(f"Synced QboBill {bill.id} to Bill {bill_module.id}")
            except Exception as e:
                logger.error(f"Failed to sync QboBill {bill.id} to Bill: {e}")

    def read_all(self) -> List[QboBill]:
        """
        Read all QboBills.
        """
        return self.repo.read_all()

    def read_by_realm_id(self, realm_id: str) -> List[QboBill]:
        """
        Read all QboBills by realm ID.
        """
        return self.repo.read_by_realm_id(realm_id)

    def read_by_qbo_id(self, qbo_id: str) -> Optional[QboBill]:
        """
        Read a QboBill by QBO ID.
        """
        return self.repo.read_by_qbo_id(qbo_id)

    def read_by_id(self, id: int) -> Optional[QboBill]:
        """
        Read a QboBill by database ID.
        """
        return self.repo.read_by_id(id)

    def read_lines_by_qbo_bill_id(self, qbo_bill_id: int) -> List[QboBillLine]:
        """
        Read all QboBillLines for a QboBill.
        """
        return self.line_repo.read_by_qbo_bill_id(qbo_bill_id)
