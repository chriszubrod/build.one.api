#!/usr/bin/env python3
"""
Fix contract labor bills that were created with incorrect BillLineItem prices.

Resets billed ContractLabor entries to 'ready' so Generate Bills can be re-run
from the UI. The edit path will find existing bills, replace BillLineItems with
correct values, re-upload PDFs, and re-mark entries as billed.

Usage:
    python scripts/fix_contract_labor_bills.py [--vendor-id VENDOR_ID] [--dry-run]

If --vendor-id is not specified, resets ALL billed entries.
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entities.contract_labor.business.service import ContractLaborService
from entities.contract_labor.persistence.repo import ContractLaborRepository
from entities.vendor.business.service import VendorService


def main():
    parser = argparse.ArgumentParser(description="Reset billed contract labor entries to ready")
    parser.add_argument("--vendor-id", type=int, help="Only reset entries for this vendor ID")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be reset without making changes")
    args = parser.parse_args()

    cl_service = ContractLaborService()
    cl_repo = ContractLaborRepository()
    vendor_service = VendorService()

    billed_entries = cl_service.read_by_status(status="billed")

    if args.vendor_id:
        billed_entries = [e for e in billed_entries if e.bill_vendor_id == args.vendor_id]
        vendor = vendor_service.read_by_id(id=args.vendor_id)
        vendor_name = vendor.name if vendor else f"ID {args.vendor_id}"
        print(f"Vendor: {vendor_name}")

    print(f"Found {len(billed_entries)} billed entries to reset")

    if not billed_entries:
        print("Nothing to do.")
        return

    for entry in billed_entries:
        label = f"  ContractLabor {entry.id} (public_id={entry.public_id}, employee={entry.employee_name})"
        if args.dry_run:
            print(f"  [DRY RUN] Would reset: {label}")
        else:
            entry.status = "ready"
            entry.bill_line_item_id = None
            updated = cl_repo.update_by_id(entry)
            if updated:
                print(f"  Reset: {label}")
            else:
                print(f"  FAILED to reset: {label}")

    if args.dry_run:
        print(f"\nDry run complete. Re-run without --dry-run to apply.")
    else:
        print(f"\nReset {len(billed_entries)} entries to 'ready'.")
        print("Now go to the Contract Labor Bills page and click 'Generate Bills' for each vendor.")
        print("The edit path will fix existing Bills and BillLineItems with correct prices.")


if __name__ == "__main__":
    main()
