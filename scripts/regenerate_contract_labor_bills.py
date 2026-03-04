#!/usr/bin/env python3
"""
Reset billed contract labor entries and regenerate all bills.
Calls the same service code as the UI's Generate Bills button.

Usage:
    python scripts/regenerate_contract_labor_bills.py [--dry-run]
"""
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entities.contract_labor.business.service import ContractLaborService
from entities.contract_labor.business.bill_service import ContractLaborBillService
from entities.contract_labor.persistence.repo import ContractLaborRepository
from entities.vendor.business.service import VendorService


def main():
    parser = argparse.ArgumentParser(description="Reset and regenerate contract labor bills")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    args = parser.parse_args()

    cl_service = ContractLaborService()
    cl_repo = ContractLaborRepository()
    vendor_service = VendorService()
    bill_service = ContractLaborBillService()

    # Step 1: Reset billed entries
    billed_entries = cl_service.read_by_status(status="billed")
    print(f"Step 1: Found {len(billed_entries)} billed entries to reset")

    if args.dry_run:
        vendor_ids = set()
        for entry in billed_entries:
            if entry.bill_vendor_id:
                vendor_ids.add(entry.bill_vendor_id)
        vendors = {v.id: v.name for v in vendor_service.read_all()}
        print(f"  Vendors to regenerate: {[vendors.get(vid, vid) for vid in vendor_ids]}")
        print(f"\nDry run complete. Re-run without --dry-run to apply.")
        return

    vendor_ids = set()
    for entry in billed_entries:
        entry.status = "ready"
        entry.bill_line_item_id = None
        updated = cl_repo.update_by_id(entry)
        if updated and entry.bill_vendor_id:
            vendor_ids.add(entry.bill_vendor_id)

    print(f"  Reset {len(billed_entries)} entries to 'ready'")

    # Step 2: Generate bills for each vendor
    vendors = {v.id: v.name for v in vendor_service.read_all()}
    print(f"\nStep 2: Generating bills for {len(vendor_ids)} vendor(s)")

    for vendor_id in sorted(vendor_ids):
        vendor_name = vendors.get(vendor_id, f"ID {vendor_id}")
        print(f"\n  Vendor: {vendor_name} (ID {vendor_id})")
        try:
            result = bill_service.generate_bills_for_vendor(vendor_id=vendor_id)
            new = result.get("bills_created", 0)
            updated = result.get("bills_updated", 0)
            items = result.get("line_items_created", 0)
            billed = result.get("entries_billed", 0)
            pdfs = len(result.get("pdf_urls", []))
            errors = result.get("errors", [])
            print(f"    {new} new, {updated} updated bill(s), {items} line items, {billed} billed, {pdfs} PDF(s)")
            if errors:
                for err in errors:
                    print(f"    ERROR: {err}")
        except Exception as e:
            print(f"    FAILED: {e}")

    print("\nDone.")


if __name__ == "__main__":
    main()
