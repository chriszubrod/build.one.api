#!/usr/bin/env python3
"""
Re-run the SharePoint upload step for a specific completed invoice.
Usage:
    python scripts/upload_invoice_sharepoint.py 14B17DB4-650C-46E1-98DB-6591ABB52CE3
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entities.invoice.business.service import InvoiceService
from entities.invoice_line_item.business.service import InvoiceLineItemService

public_id = sys.argv[1] if len(sys.argv) > 1 else None
if not public_id:
    print("Usage: python scripts/upload_invoice_sharepoint.py <invoice_public_id>")
    sys.exit(1)

service = InvoiceService()
invoice = service.read_by_public_id(public_id=public_id)
if not invoice:
    print(f"Invoice not found: {public_id}")
    sys.exit(1)

print(f"Invoice: {invoice.invoice_number} (id={invoice.id})")

line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)
print(f"Line items: {len(line_items)}")

result = service._upload_to_sharepoint(invoice=invoice, line_items=line_items)
print(f"\nResult: {'SUCCESS' if result.get('success') else 'FAILED'}")
print(f"Message: {result.get('message')}")
print(f"Synced: {result.get('synced_count', 0)}")
if result.get('errors'):
    print("Errors:")
    for e in result['errors']:
        print(f"  - {e}")
