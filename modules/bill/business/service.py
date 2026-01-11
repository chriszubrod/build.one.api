# Python Standard Library Imports
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from modules.bill.business.model import Bill
from modules.bill.persistence.repo import BillRepository
from modules.vendor.business.service import VendorService


class BillService:
    """
    Service for Bill entity business operations.
    """

    def __init__(self, repo: Optional[BillRepository] = None):
        """Initialize the BillService."""
        self.repo = repo or BillRepository()

    def create(self, *, vendor_public_id: str, terms_id: Optional[int] = None, bill_date: str, due_date: str, bill_number: str, total_amount: Optional[Decimal] = None, memo: Optional[str] = None, is_draft: bool = True) -> Bill:
        """
        Create a new bill.
        """
        if not vendor_public_id:
            raise ValueError("Vendor is required.")
        if not bill_date:
            raise ValueError("Bill date is required.")
        if not due_date:
            raise ValueError("Due date is required.")
        if not bill_number:
            raise ValueError("Bill number is required.")
        
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{vendor_public_id}' not found.")
        vendor_id = vendor.id
        
        # Check if a bill with the same BillNumber and VendorId already exists
        existing = self.repo.read_by_bill_number_and_vendor_id(bill_number=bill_number, vendor_id=vendor_id)
        if existing:
            raise ValueError(f"A bill with BillNumber '{bill_number}' already exists for this vendor. Please update the existing bill instead of creating a new one.")
        
        return self.repo.create(
            vendor_id=vendor_id,
            terms_id=terms_id,
            bill_date=bill_date,
            due_date=due_date,
            bill_number=bill_number,
            total_amount=total_amount,
            memo=memo,
            is_draft=is_draft,
        )

    def read_all(self) -> list[Bill]:
        """
        Read all bills.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[Bill]:
        """
        Read a bill by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Bill]:
        """
        Read a bill by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_bill_number(self, bill_number: str) -> Optional[Bill]:
        """
        Read a bill by bill number.
        """
        return self.repo.read_by_bill_number(bill_number)

    def read_by_bill_number_and_vendor_public_id(self, bill_number: str, vendor_public_id: str) -> Optional[Bill]:
        """
        Read a bill by bill number and vendor public ID.
        """
        vendor = VendorService().read_by_public_id(public_id=vendor_public_id)
        if not vendor:
            return None
        return self.repo.read_by_bill_number_and_vendor_id(bill_number=bill_number, vendor_id=vendor.id)

    def update_by_public_id(self, public_id: str, bill) -> Optional[Bill]:
        """
        Update a bill by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None
        
        if not hasattr(bill, 'vendor_public_id') or not bill.vendor_public_id:
            raise ValueError("Vendor is required.")
        if not hasattr(bill, 'bill_date') or not bill.bill_date:
            raise ValueError("Bill date is required.")
        if not hasattr(bill, 'due_date') or not bill.due_date:
            raise ValueError("Due date is required.")
        if not hasattr(bill, 'bill_number') or not bill.bill_number:
            raise ValueError("Bill number is required.")
        
        existing.row_version = bill.row_version
        
        # Convert vendor_public_id to vendor_id
        vendor = VendorService().read_by_public_id(public_id=bill.vendor_public_id)
        if not vendor:
            raise ValueError(f"Vendor with public_id '{bill.vendor_public_id}' not found.")
        existing.vendor_id = vendor.id
        
        existing.terms_id = bill.terms_id
        existing.bill_date = bill.bill_date
        existing.due_date = bill.due_date
        existing.bill_number = bill.bill_number
        existing.total_amount = bill.total_amount
        existing.memo = bill.memo
        # Only update is_draft if explicitly provided
        if hasattr(bill, 'is_draft') and bill.is_draft is not None:
            existing.is_draft = bill.is_draft
        
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Bill]:
        """
        Delete a bill by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
