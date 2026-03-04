#!/usr/bin/env python3
"""
Diagnose bill attachment linkage: show each Bill, its BillLineItems,
and the Attachment linked to each via BillLineItemAttachment.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entities.bill.business.service import BillService
from entities.bill_line_item.business.service import BillLineItemService
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
from entities.attachment.business.service import AttachmentService
from entities.vendor.business.service import VendorService
from entities.project.business.service import ProjectService


def main():
    bill_service = BillService()
    bli_service = BillLineItemService()
    blia_service = BillLineItemAttachmentService()
    att_service = AttachmentService()
    vendor_service = VendorService()
    project_service = ProjectService()

    vendors = {v.id: v.name for v in vendor_service.read_all()}
    projects = {p.id: p.name for p in project_service.read_all()}

    bills = bill_service.read_all()
    draft_bills = [b for b in bills if b.is_draft]

    print(f"Found {len(draft_bills)} draft bills\n")

    for bill in draft_bills:
        vendor_name = vendors.get(bill.vendor_id, "?")
        print(f"Bill {bill.id} | {bill.bill_number} | vendor={vendor_name} | total={bill.total_amount}")

        line_items = bli_service.read_by_bill_id(bill_id=bill.id)
        for li in line_items:
            project_name = projects.get(li.project_id, "?")
            print(f"  BLI {li.id} (pub={li.public_id[:8]}...) | project={project_name} | desc={li.description[:40] if li.description else ''} | price={li.price}")

            link = blia_service.read_by_bill_line_item_id(bill_line_item_public_id=li.public_id)
            if link:
                att = att_service.read_by_id(id=link.attachment_id)
                if att:
                    print(f"    -> Attachment {att.id} | filename={att.filename} | blob_url={att.blob_url}")
                else:
                    print(f"    -> Attachment {link.attachment_id} NOT FOUND (orphan link)")
            else:
                print(f"    -> No BillLineItemAttachment")
        print()


if __name__ == "__main__":
    main()
