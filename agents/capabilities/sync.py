# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from agents.capabilities.base import Capability, CapabilityResult

logger = logging.getLogger(__name__)


class SyncCapabilities(Capability):
    """
    External system sync capabilities.
    
    Provides sync operations to QuickBooks Online (QBO).
    """
    
    @property
    def name(self) -> str:
        return "sync"
    
    def __init__(self):
        self._qbo_bill_service = None
    
    def _get_qbo_bill_service(self):
        """Lazy load the QBO bill service."""
        if self._qbo_bill_service is None:
            from integrations.intuit.qbo.bill.business.service import QboBillService
            self._qbo_bill_service = QboBillService()
        return self._qbo_bill_service
    
    def push_bill_to_qbo(
        self,
        bill_id: int,
        realm_id: str,
        access_token: str,
    ) -> CapabilityResult:
        """
        Push a bill to QuickBooks Online.
        
        This operation is idempotent - if the bill already exists in QBO,
        it will update rather than create a duplicate.
        
        Args:
            bill_id: Local bill ID
            realm_id: QBO realm/company ID
            access_token: QBO access token
            
        Returns:
            CapabilityResult with QBO bill ID
        """
        self._log_call("push_bill_to_qbo", bill_id=bill_id, realm_id=realm_id)
        
        try:
            # Get the local bill
            from modules.bill.business.service import BillService
            bill_service = BillService()
            
            bill_result = bill_service.read_by_id(bill_id)
            if bill_result.get("status_code") != 200:
                return CapabilityResult.fail(error="Bill not found")
            
            bill = bill_result.get("bill", {})
            
            # Check if already synced
            qbo_id = bill.get("qbo_id")
            if qbo_id:
                logger.info(f"Bill {bill_id} already synced to QBO as {qbo_id}")
                return CapabilityResult.ok(
                    data={
                        "qbo_id": qbo_id,
                        "already_synced": True,
                    },
                )
            
            # Get vendor QBO ID
            vendor_id = bill.get("vendor_id")
            from modules.vendor.business.service import VendorService
            vendor_service = VendorService()
            
            vendor_result = vendor_service.read_by_id(vendor_id)
            if vendor_result.get("status_code") != 200:
                return CapabilityResult.fail(error="Vendor not found")
            
            vendor = vendor_result.get("vendor", {})
            vendor_qbo_id = vendor.get("qbo_id")
            
            if not vendor_qbo_id:
                return CapabilityResult.fail(
                    error="Vendor not synced to QBO. Please sync vendor first.",
                )
            
            # Create bill in QBO
            from integrations.intuit.qbo.bill.external.client import QboBillClient
            qbo_client = QboBillClient()
            
            qbo_bill_data = {
                "VendorRef": {"value": vendor_qbo_id},
                "TxnDate": bill.get("invoice_date"),
                "DueDate": bill.get("due_date"),
                "DocNumber": bill.get("invoice_number"),
                "Line": self._build_qbo_line_items(bill.get("line_items", [])),
            }
            
            create_result = qbo_client.create(
                realm_id=realm_id,
                access_token=access_token,
                bill_data=qbo_bill_data,
            )
            
            if create_result.get("status_code") not in (200, 201):
                return CapabilityResult.fail(
                    error=f"QBO sync failed: {create_result.get('message')}",
                    qbo_error=create_result.get("error"),
                )
            
            qbo_bill = create_result.get("bill", {})
            new_qbo_id = qbo_bill.get("Id")
            
            # Update local bill with QBO ID
            bill_service.update_qbo_id(
                bill_id=bill_id,
                qbo_id=new_qbo_id,
            )
            
            result = CapabilityResult.ok(
                data={
                    "qbo_id": new_qbo_id,
                    "already_synced": False,
                },
            )
            self._log_result("push_bill_to_qbo", result)
            return result
            
        except Exception as e:
            logger.exception("Error in push_bill_to_qbo")
            return CapabilityResult.fail(error=str(e))
    
    def _build_qbo_line_items(self, line_items: list) -> list:
        """Convert local line items to QBO format."""
        qbo_lines = []
        for item in line_items:
            qbo_lines.append({
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": item.get("amount", 0),
                "Description": item.get("description", ""),
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": item.get("qbo_account_id", "1")},
                },
            })
        
        # If no line items, create a single line with total
        if not qbo_lines:
            qbo_lines.append({
                "DetailType": "AccountBasedExpenseLineDetail",
                "Amount": 0,
                "Description": "Bill",
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": "1"},
                },
            })
        
        return qbo_lines
    
    def check_qbo_connection(
        self,
        realm_id: str,
        access_token: str,
    ) -> CapabilityResult:
        """
        Check if QBO connection is valid.
        
        Args:
            realm_id: QBO realm/company ID
            access_token: QBO access token
            
        Returns:
            CapabilityResult with connection status
        """
        self._log_call("check_qbo_connection", realm_id=realm_id)
        
        try:
            from integrations.intuit.qbo.company.external.client import QboCompanyClient
            client = QboCompanyClient()
            
            result = client.get_company_info(
                realm_id=realm_id,
                access_token=access_token,
            )
            
            if result.get("status_code") != 200:
                return CapabilityResult.fail(
                    error="QBO connection failed",
                    details=result.get("message"),
                )
            
            company = result.get("company_info", {})
            return CapabilityResult.ok(
                data={
                    "connected": True,
                    "company_name": company.get("CompanyName"),
                    "realm_id": realm_id,
                },
            )
            
        except Exception as e:
            logger.exception("Error in check_qbo_connection")
            return CapabilityResult.fail(error=str(e))
