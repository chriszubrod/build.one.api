#!/usr/bin/env python3
"""
Reconcile QBO billable items against the local database for project BR-MAIN - 7550C Buffalo Road.
READ-ONLY — no changes are made.

Usage:
    cd /Users/chris/Applications/build.one
    python scripts/reconcile_qbo_billable.py
"""

import os
import sys
import json
import logging
import requests
from decimal import Decimal
from collections import defaultdict

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from integrations.intuit.qbo.auth.business.service import QboAuthService
from shared.database import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
REALM_ID = '9130353016965726'
CUSTOMER_REF_VALUE = '356'
PROJECT_ID = 64
PROJECT_PUBLIC_ID = 'A18A71A4-AFE9-4C9E-98DD-8B7A583B0727'
QBO_BASE_URL = f'https://quickbooks.api.intuit.com/v3/company/{REALM_ID}'


def get_qbo_headers():
    """Get authenticated QBO headers."""
    auth_service = QboAuthService()
    qbo_auth = auth_service.ensure_valid_token(REALM_ID)
    if not qbo_auth:
        raise Exception("Failed to get valid QBO token")
    return {
        'Authorization': f'Bearer {qbo_auth.access_token}',
        'Accept': 'application/json',
    }


def qbo_query(headers, query_str):
    """Execute a QBO query and return all results with pagination."""
    all_entities = []
    start = 1
    page_size = 1000
    while True:
        paged_query = f"{query_str} STARTPOSITION {start} MAXRESULTS {page_size}"
        url = f"{QBO_BASE_URL}/query?query={requests.utils.quote(paged_query)}"
        resp = requests.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        qr = data.get('QueryResponse', {})
        # Determine entity type from query
        entity_type = None
        for key in qr:
            if key not in ('startPosition', 'maxResults', 'totalCount'):
                entity_type = key
                break
        if not entity_type:
            break
        entities = qr.get(entity_type, [])
        if not entities:
            break
        all_entities.extend(entities)
        if len(entities) < page_size:
            break
        start += page_size
    return all_entities


def extract_billable_lines(entity, entity_type):
    """Extract lines from a QBO Bill or Purchase that are Billable for customer 356."""
    billable_lines = []
    doc_number = entity.get('DocNumber', '')
    qbo_id = entity.get('Id', '')
    vendor_name = ''
    txn_date = entity.get('TxnDate', '')

    if entity_type == 'Bill':
        vendor_ref = entity.get('VendorRef', {})
        vendor_name = vendor_ref.get('name', '')
    elif entity_type == 'Purchase':
        entity_ref = entity.get('EntityRef', {})
        vendor_name = entity_ref.get('name', '')

    for line in entity.get('Line', []):
        detail_type = line.get('DetailType', '')
        detail = None
        if detail_type == 'ItemBasedExpenseLineDetail':
            detail = line.get('ItemBasedExpenseLineDetail', {})
        elif detail_type == 'AccountBasedExpenseLineDetail':
            detail = line.get('AccountBasedExpenseLineDetail', {})

        if not detail:
            continue

        customer_ref = detail.get('CustomerRef', {})
        customer_value = str(customer_ref.get('value', ''))
        billable_status = detail.get('BillableStatus', '')

        if customer_value == CUSTOMER_REF_VALUE and billable_status == 'Billable':
            billable_lines.append({
                'entity_type': entity_type,
                'qbo_id': qbo_id,
                'doc_number': doc_number,
                'vendor_name': vendor_name,
                'txn_date': txn_date,
                'line_id': str(line.get('Id', '')),
                'line_num': line.get('LineNum'),
                'amount': Decimal(str(line.get('Amount', 0))),
                'description': line.get('Description', ''),
                'detail_type': detail_type,
                'billable_status': billable_status,
                'item_ref': detail.get('ItemRef', {}),
            })

    return billable_lines


def extract_has_been_billed_lines(entity, entity_type):
    """Extract lines from a QBO Bill or Purchase that are HasBeenBilled for customer 356."""
    lines = []
    doc_number = entity.get('DocNumber', '')
    qbo_id = entity.get('Id', '')

    for line in entity.get('Line', []):
        detail_type = line.get('DetailType', '')
        detail = None
        if detail_type == 'ItemBasedExpenseLineDetail':
            detail = line.get('ItemBasedExpenseLineDetail', {})
        elif detail_type == 'AccountBasedExpenseLineDetail':
            detail = line.get('AccountBasedExpenseLineDetail', {})

        if not detail:
            continue

        customer_ref = detail.get('CustomerRef', {})
        customer_value = str(customer_ref.get('value', ''))
        billable_status = detail.get('BillableStatus', '')

        if customer_value == CUSTOMER_REF_VALUE and billable_status == 'HasBeenBilled':
            lines.append({
                'entity_type': entity_type,
                'qbo_id': qbo_id,
                'doc_number': doc_number,
                'line_id': str(line.get('Id', '')),
                'amount': Decimal(str(line.get('Amount', 0))),
                'billable_status': billable_status,
            })

    return lines


def extract_not_billable_lines(entity, entity_type):
    """Extract lines from a QBO Bill or Purchase that are NotBillable for customer 356."""
    lines = []
    doc_number = entity.get('DocNumber', '')
    qbo_id = entity.get('Id', '')

    for line in entity.get('Line', []):
        detail_type = line.get('DetailType', '')
        detail = None
        if detail_type == 'ItemBasedExpenseLineDetail':
            detail = line.get('ItemBasedExpenseLineDetail', {})
        elif detail_type == 'AccountBasedExpenseLineDetail':
            detail = line.get('AccountBasedExpenseLineDetail', {})

        if not detail:
            continue

        customer_ref = detail.get('CustomerRef', {})
        customer_value = str(customer_ref.get('value', ''))
        billable_status = detail.get('BillableStatus', '')

        if customer_value == CUSTOMER_REF_VALUE and billable_status == 'NotBillable':
            lines.append({
                'entity_type': entity_type,
                'qbo_id': qbo_id,
                'doc_number': doc_number,
                'line_id': str(line.get('Id', '')),
                'amount': Decimal(str(line.get('Amount', 0))),
                'billable_status': billable_status,
            })

    return lines


def check_bill_line_mapping(cursor, doc_number, line_amount, line_id):
    """
    Check if a QBO bill line has a mapping to a local BillLineItem.
    Returns dict with mapping info.
    """
    result = {
        'has_qbo_mapping': False,
        'has_fallback_match': False,
        'local_items': [],
        'has_attachment': False,
        'duplicate_count': 0,
    }

    # Check via QBO staging tables
    cursor.execute("""
        SELECT dbl.Id, dbl.PublicId, dbl.Amount, dbl.Price, dbl.Description,
               dbl.IsBilled, dbl.IsBillable, dbl.IsDraft,
               bl.QboLineId, bl.Amount as QboAmount
        FROM qbo.BillLine bl
        JOIN qbo.Bill b ON bl.QboBillId = b.Id
        LEFT JOIN qbo.BillLineItemBillLine blibll ON blibll.QboBillLineId = bl.Id
        LEFT JOIN dbo.BillLineItem dbl ON dbl.Id = blibll.BillLineItemId
        WHERE b.DocNumber = ? AND bl.CustomerRefValue = ? AND bl.BillableStatus = 'Billable'
    """, doc_number, CUSTOMER_REF_VALUE)

    rows = cursor.fetchall()
    for row in rows:
        if row.Id is not None:
            result['has_qbo_mapping'] = True
            result['local_items'].append({
                'id': row.Id,
                'public_id': str(row.PublicId) if row.PublicId else None,
                'amount': row.Amount,
                'price': row.Price,
                'description': row.Description,
                'is_billed': row.IsBilled,
                'is_billable': row.IsBillable,
                'is_draft': row.IsDraft,
                'qbo_line_id': row.QboLineId,
                'qbo_amount': row.QboAmount,
            })

    # If no QBO mapping, try fallback match by bill number + amount
    if not result['has_qbo_mapping']:
        cursor.execute("""
            SELECT dbl.Id, dbl.PublicId, dbl.Amount, dbl.Price, dbl.Description,
                   dbl.IsBilled, dbl.IsBillable, dbl.IsDraft
            FROM dbo.BillLineItem dbl
            JOIN dbo.Bill dbill ON dbl.BillId = dbill.Id
            WHERE dbill.BillNumber = ? AND dbl.ProjectId = ? AND ABS(dbl.Amount - ?) < 0.01
        """, doc_number, PROJECT_ID, float(line_amount))

        rows = cursor.fetchall()
        for row in rows:
            result['has_fallback_match'] = True
            result['local_items'].append({
                'id': row.Id,
                'public_id': str(row.PublicId) if row.PublicId else None,
                'amount': row.Amount,
                'price': row.Price,
                'description': row.Description,
                'is_billed': row.IsBilled,
                'is_billable': row.IsBillable,
                'is_draft': row.IsDraft,
                'match_type': 'fallback',
            })

    # Check attachment chain for each local item
    for item in result['local_items']:
        if item['id']:
            cursor.execute("""
                SELECT blia.Id, a.BlobUrl
                FROM dbo.BillLineItemAttachment blia
                JOIN dbo.Attachment a ON blia.AttachmentId = a.Id
                WHERE blia.BillLineItemId = ?
            """, item['id'])
            att_rows = cursor.fetchall()
            if att_rows:
                item['has_attachment'] = True
                item['attachment_count'] = len(att_rows)
                item['has_blob'] = any(r.BlobUrl for r in att_rows)
            else:
                item['has_attachment'] = False

    result['duplicate_count'] = len(result['local_items'])
    if result['local_items']:
        result['has_attachment'] = any(i.get('has_attachment', False) for i in result['local_items'])

    return result


def check_purchase_line_mapping(cursor, doc_number, line_amount, qbo_purchase_id):
    """
    Check if a QBO purchase line has a mapping to a local ExpenseLineItem.
    Returns dict with mapping info.
    """
    result = {
        'has_qbo_mapping': False,
        'has_fallback_match': False,
        'local_items': [],
        'has_attachment': False,
        'duplicate_count': 0,
    }

    # Check via QBO staging tables
    cursor.execute("""
        SELECT eli.Id, eli.PublicId, eli.Amount, eli.Description,
               eli.IsBilled, eli.IsBillable, eli.IsDraft,
               pl.QboLineId, pl.Amount as QboAmount
        FROM qbo.PurchaseLine pl
        JOIN qbo.Purchase p ON pl.QboPurchaseId = p.Id
        LEFT JOIN qbo.PurchaseLineExpenseLineItem pleli ON pleli.QboPurchaseLineId = pl.Id
        LEFT JOIN dbo.ExpenseLineItem eli ON eli.Id = pleli.ExpenseLineItemId
        WHERE p.DocNumber = ? AND pl.CustomerRefValue = ? AND pl.BillableStatus = 'Billable'
    """, doc_number, CUSTOMER_REF_VALUE)

    rows = cursor.fetchall()
    for row in rows:
        if row.Id is not None:
            result['has_qbo_mapping'] = True
            result['local_items'].append({
                'id': row.Id,
                'public_id': str(row.PublicId) if row.PublicId else None,
                'amount': row.Amount,
                'description': row.Description,
                'is_billed': row.IsBilled,
                'is_billable': row.IsBillable,
                'is_draft': row.IsDraft,
                'qbo_line_id': row.QboLineId,
                'qbo_amount': row.QboAmount,
            })

    # Fallback match by amount + project
    if not result['has_qbo_mapping']:
        cursor.execute("""
            SELECT eli.Id, eli.PublicId, eli.Amount, eli.Description,
                   eli.IsBilled, eli.IsBillable, eli.IsDraft
            FROM dbo.ExpenseLineItem eli
            JOIN dbo.Expense e ON eli.ExpenseId = e.Id
            WHERE eli.ProjectId = ? AND ABS(eli.Amount - ?) < 0.01
        """, PROJECT_ID, float(line_amount))

        rows = cursor.fetchall()
        for row in rows:
            result['has_fallback_match'] = True
            result['local_items'].append({
                'id': row.Id,
                'public_id': str(row.PublicId) if row.PublicId else None,
                'amount': row.Amount,
                'description': row.Description,
                'is_billed': row.IsBilled,
                'is_billable': row.IsBillable,
                'is_draft': row.IsDraft,
                'match_type': 'fallback',
            })

    # Check attachment chain
    for item in result['local_items']:
        if item['id']:
            cursor.execute("""
                SELECT elia.Id, a.BlobUrl
                FROM dbo.ExpenseLineItemAttachment elia
                JOIN dbo.Attachment a ON elia.AttachmentId = a.Id
                WHERE elia.ExpenseLineItemId = ?
            """, item['id'])
            att_rows = cursor.fetchall()
            if att_rows:
                item['has_attachment'] = True
                item['attachment_count'] = len(att_rows)
                item['has_blob'] = any(r.BlobUrl for r in att_rows)
            else:
                item['has_attachment'] = False

    result['duplicate_count'] = len(result['local_items'])
    if result['local_items']:
        result['has_attachment'] = any(i.get('has_attachment', False) for i in result['local_items'])

    return result


def check_stale_bill_items(cursor, qbo_not_billable_doc_numbers):
    """
    Find local BillLineItems that are marked billable but the QBO line is NotBillable or HasBeenBilled.
    """
    cursor.execute("""
        SELECT dbl.Id, dbl.PublicId, dbl.Amount, dbl.Price, dbl.Description,
               dbl.IsBilled, dbl.IsBillable, dbl.IsDraft,
               dbill.BillNumber, dbill.PublicId as BillPublicId
        FROM dbo.BillLineItem dbl
        JOIN dbo.Bill dbill ON dbl.BillId = dbill.Id
        WHERE dbl.ProjectId = ? AND dbl.IsBillable = 1
              AND (dbl.IsBilled = 0 OR dbl.IsBilled IS NULL)
              AND (dbl.IsDraft = 0 OR dbl.IsDraft IS NULL)
    """, PROJECT_ID)

    return cursor.fetchall()


def check_stale_expense_items(cursor):
    """
    Find local ExpenseLineItems that are marked billable but may be stale.
    """
    cursor.execute("""
        SELECT eli.Id, eli.PublicId, eli.Amount, eli.Description,
               eli.IsBilled, eli.IsBillable, eli.IsDraft,
               e.ExpenseNumber, e.PublicId as ExpensePublicId
        FROM dbo.ExpenseLineItem eli
        JOIN dbo.Expense e ON eli.ExpenseId = e.Id
        WHERE eli.ProjectId = ? AND eli.IsBillable = 1
              AND (eli.IsBilled = 0 OR eli.IsBilled IS NULL)
              AND (eli.IsDraft = 0 OR eli.IsDraft IS NULL)
    """, PROJECT_ID)

    return cursor.fetchall()


def main():
    print("=" * 100)
    print("QBO BILLABLE RECONCILIATION REPORT")
    print(f"Project: BR-MAIN - 7550C Buffalo Road (ProjectId={PROJECT_ID})")
    print(f"QBO CustomerRef: {CUSTOMER_REF_VALUE}")
    print(f"Date: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 100)

    # Step 1: Fetch all QBO Bills and Purchases
    print("\n[1] Fetching QBO data...")
    headers = get_qbo_headers()

    print("  Querying QBO Bills...")
    all_bills = qbo_query(headers, "SELECT * FROM Bill")
    print(f"  Found {len(all_bills)} total QBO Bills")

    print("  Querying QBO Purchases...")
    all_purchases = qbo_query(headers, "SELECT * FROM Purchase")
    print(f"  Found {len(all_purchases)} total QBO Purchases")

    # Step 2: Filter for Billable lines for customer 356
    all_billable_lines = []
    all_has_been_billed_lines = []
    all_not_billable_lines = []

    bills_with_billable = 0
    for bill in all_bills:
        billable = extract_billable_lines(bill, 'Bill')
        hbb = extract_has_been_billed_lines(bill, 'Bill')
        nb = extract_not_billable_lines(bill, 'Bill')
        if billable:
            bills_with_billable += 1
        all_billable_lines.extend(billable)
        all_has_been_billed_lines.extend(hbb)
        all_not_billable_lines.extend(nb)

    purchases_with_billable = 0
    for purchase in all_purchases:
        billable = extract_billable_lines(purchase, 'Purchase')
        hbb = extract_has_been_billed_lines(purchase, 'Purchase')
        nb = extract_not_billable_lines(purchase, 'Purchase')
        if billable:
            purchases_with_billable += 1
        all_billable_lines.extend(billable)
        all_has_been_billed_lines.extend(hbb)
        all_not_billable_lines.extend(nb)

    bill_billable = [l for l in all_billable_lines if l['entity_type'] == 'Bill']
    purchase_billable = [l for l in all_billable_lines if l['entity_type'] == 'Purchase']

    print(f"\n  QBO Billable lines for customer {CUSTOMER_REF_VALUE}:")
    print(f"    Bills: {len(bill_billable)} lines across {bills_with_billable} bills")
    print(f"    Purchases: {len(purchase_billable)} lines across {purchases_with_billable} purchases")
    print(f"    Total Billable: {len(all_billable_lines)}")
    print(f"    HasBeenBilled: {len(all_has_been_billed_lines)}")
    print(f"    NotBillable: {len(all_not_billable_lines)}")
    total_billable_amount = sum(l['amount'] for l in all_billable_lines)
    print(f"    Total Billable Amount: ${total_billable_amount:,.2f}")

    # Step 3: Check local database mappings
    print("\n[2] Checking local database mappings...")

    # Track results
    correct_mapping = []
    missing_qbo_mapping = []
    missing_attachment = []
    duplicates = []
    fallback_only = []

    with get_connection() as conn:
        cursor = conn.cursor()

        # Check each QBO Billable bill line
        print("\n  --- BILL LINE ITEMS ---")
        for line in bill_billable:
            check = check_bill_line_mapping(cursor, line['doc_number'], line['amount'], line['line_id'])
            line['check'] = check

            if check['has_qbo_mapping'] and check['duplicate_count'] == 1 and check['has_attachment']:
                correct_mapping.append(line)
            elif check['has_qbo_mapping'] and check['duplicate_count'] == 1 and not check['has_attachment']:
                missing_attachment.append(line)
            elif check['has_qbo_mapping'] and check['duplicate_count'] > 1:
                duplicates.append(line)
            elif not check['has_qbo_mapping'] and check['has_fallback_match']:
                fallback_only.append(line)
            else:
                missing_qbo_mapping.append(line)

        # Check each QBO Billable purchase line
        print("  --- PURCHASE/EXPENSE LINE ITEMS ---")
        correct_purchase_mapping = []
        missing_purchase_mapping = []
        missing_purchase_attachment = []
        purchase_duplicates = []
        purchase_fallback_only = []

        for line in purchase_billable:
            check = check_purchase_line_mapping(cursor, line['doc_number'], line['amount'], line['qbo_id'])
            line['check'] = check

            if check['has_qbo_mapping'] and check['duplicate_count'] == 1 and check['has_attachment']:
                correct_purchase_mapping.append(line)
            elif check['has_qbo_mapping'] and check['duplicate_count'] == 1 and not check['has_attachment']:
                missing_purchase_attachment.append(line)
            elif check['has_qbo_mapping'] and check['duplicate_count'] > 1:
                purchase_duplicates.append(line)
            elif not check['has_qbo_mapping'] and check['has_fallback_match']:
                purchase_fallback_only.append(line)
            else:
                missing_purchase_mapping.append(line)

        # Step 4: Check for stale local items
        print("\n  --- STALE LOCAL ITEMS CHECK ---")

        # Get all QBO doc numbers that have HasBeenBilled or NotBillable lines
        hbb_doc_numbers = set(l['doc_number'] for l in all_has_been_billed_lines)
        nb_doc_numbers = set(l['doc_number'] for l in all_not_billable_lines)

        # Billable QBO doc numbers (for cross-reference)
        billable_bill_doc_numbers = set(l['doc_number'] for l in bill_billable)
        billable_purchase_doc_numbers = set(l['doc_number'] for l in purchase_billable)

        stale_bill_items = check_stale_bill_items(cursor, nb_doc_numbers)
        stale_expense_items = check_stale_expense_items(cursor)

        # For stale checks, cross-reference with QBO
        # A local item is stale if it's billable locally but the QBO line is HasBeenBilled or NotBillable
        # We need to check each local billable item against QBO

        # Get all local billable bill line items with their QBO mapping
        cursor.execute("""
            SELECT dbl.Id, dbl.PublicId, dbl.Amount, dbl.Price, dbl.Description,
                   dbl.IsBilled, dbl.IsBillable, dbl.IsDraft,
                   dbill.BillNumber, dbill.PublicId as BillPublicId,
                   bl.BillableStatus as QboBillableStatus,
                   bl.QboLineId
            FROM dbo.BillLineItem dbl
            JOIN dbo.Bill dbill ON dbl.BillId = dbill.Id
            LEFT JOIN qbo.BillLineItemBillLine blibll ON blibll.BillLineItemId = dbl.Id
            LEFT JOIN qbo.BillLine bl ON bl.Id = blibll.QboBillLineId
            WHERE dbl.ProjectId = ? AND dbl.IsBillable = 1
                  AND (dbl.IsBilled = 0 OR dbl.IsBilled IS NULL)
                  AND (dbl.IsDraft = 0 OR dbl.IsDraft IS NULL)
        """, PROJECT_ID)

        local_billable_bills = cursor.fetchall()
        stale_bills = []
        orphaned_bills = []

        for row in local_billable_bills:
            if row.QboBillableStatus and row.QboBillableStatus != 'Billable':
                stale_bills.append(row)
            elif row.QboBillableStatus is None:
                # No QBO mapping at all — check if QBO even has this bill
                orphaned_bills.append(row)

        # Same for expenses
        cursor.execute("""
            SELECT eli.Id, eli.PublicId, eli.Amount, eli.Description,
                   eli.IsBilled, eli.IsBillable, eli.IsDraft,
                   e.ExpenseNumber, e.PublicId as ExpensePublicId,
                   pl.BillableStatus as QboBillableStatus,
                   pl.QboLineId
            FROM dbo.ExpenseLineItem eli
            JOIN dbo.Expense e ON eli.ExpenseId = e.Id
            LEFT JOIN qbo.PurchaseLineExpenseLineItem pleli ON pleli.ExpenseLineItemId = eli.Id
            LEFT JOIN qbo.PurchaseLine pl ON pl.Id = pleli.QboPurchaseLineId
            WHERE eli.ProjectId = ? AND eli.IsBillable = 1
                  AND (eli.IsBilled = 0 OR eli.IsBilled IS NULL)
                  AND (eli.IsDraft = 0 OR eli.IsDraft IS NULL)
        """, PROJECT_ID)

        local_billable_expenses = cursor.fetchall()
        stale_expenses = []
        orphaned_expenses = []

        for row in local_billable_expenses:
            if row.QboBillableStatus and row.QboBillableStatus != 'Billable':
                stale_expenses.append(row)
            elif row.QboBillableStatus is None:
                orphaned_expenses.append(row)

    # ==========================================
    # REPORT
    # ==========================================
    print("\n" + "=" * 100)
    print("RECONCILIATION RESULTS")
    print("=" * 100)

    # --- BILLS ---
    print("\n" + "-" * 80)
    print("BILLS (QBO Bill -> Local BillLineItem)")
    print("-" * 80)

    print(f"\nTotal QBO Billable Bill Lines: {len(bill_billable)}")
    print(f"  Correct mapping + attachment:  {len(correct_mapping)}")
    print(f"  Missing QBO mapping:           {len(missing_qbo_mapping)}")
    print(f"  Missing attachment chain:      {len(missing_attachment)}")
    print(f"  Duplicates:                    {len(duplicates)}")
    print(f"  Fallback match only (no QBO mapping): {len(fallback_only)}")

    if missing_qbo_mapping:
        print(f"\n  MISSING QBO MAPPING ({len(missing_qbo_mapping)} lines):")
        for line in missing_qbo_mapping:
            print(f"    Bill #{line['doc_number']} | {line['vendor_name']} | {line['txn_date']} | "
                  f"Line {line['line_id']} | ${line['amount']:,.2f} | {line['description'][:60]}")

    if missing_attachment:
        print(f"\n  MISSING ATTACHMENT CHAIN ({len(missing_attachment)} lines):")
        for line in missing_attachment:
            item = line['check']['local_items'][0] if line['check']['local_items'] else {}
            print(f"    Bill #{line['doc_number']} | {line['vendor_name']} | ${line['amount']:,.2f} | "
                  f"LocalId={item.get('id')} | {line['description'][:60]}")

    if duplicates:
        print(f"\n  DUPLICATES ({len(duplicates)} lines):")
        for line in duplicates:
            print(f"    Bill #{line['doc_number']} | {line['vendor_name']} | ${line['amount']:,.2f} | "
                  f"Count={line['check']['duplicate_count']}")
            for item in line['check']['local_items']:
                print(f"      -> LocalId={item['id']} Amount=${item['amount']} IsBilled={item.get('is_billed')}")

    if fallback_only:
        print(f"\n  FALLBACK MATCH ONLY ({len(fallback_only)} lines):")
        for line in fallback_only:
            print(f"    Bill #{line['doc_number']} | {line['vendor_name']} | ${line['amount']:,.2f}")
            for item in line['check']['local_items']:
                print(f"      -> LocalId={item['id']} Amount=${item['amount']} (matched by bill# + amount)")

    # --- PURCHASES/EXPENSES ---
    print("\n" + "-" * 80)
    print("PURCHASES/EXPENSES (QBO Purchase -> Local ExpenseLineItem)")
    print("-" * 80)

    print(f"\nTotal QBO Billable Purchase Lines: {len(purchase_billable)}")
    print(f"  Correct mapping + attachment:  {len(correct_purchase_mapping)}")
    print(f"  Missing QBO mapping:           {len(missing_purchase_mapping)}")
    print(f"  Missing attachment chain:      {len(missing_purchase_attachment)}")
    print(f"  Duplicates:                    {len(purchase_duplicates)}")
    print(f"  Fallback match only:           {len(purchase_fallback_only)}")

    if missing_purchase_mapping:
        print(f"\n  MISSING QBO MAPPING ({len(missing_purchase_mapping)} lines):")
        for line in missing_purchase_mapping:
            print(f"    Purchase #{line['doc_number']} | {line['vendor_name']} | {line['txn_date']} | "
                  f"Line {line['line_id']} | ${line['amount']:,.2f} | {line['description'][:60]}")

    if missing_purchase_attachment:
        print(f"\n  MISSING ATTACHMENT CHAIN ({len(missing_purchase_attachment)} lines):")
        for line in missing_purchase_attachment:
            item = line['check']['local_items'][0] if line['check']['local_items'] else {}
            print(f"    Purchase #{line['doc_number']} | {line['vendor_name']} | ${line['amount']:,.2f} | "
                  f"LocalId={item.get('id')}")

    if purchase_duplicates:
        print(f"\n  DUPLICATES ({len(purchase_duplicates)} lines):")
        for line in purchase_duplicates:
            print(f"    Purchase #{line['doc_number']} | {line['vendor_name']} | ${line['amount']:,.2f} | "
                  f"Count={line['check']['duplicate_count']}")

    if purchase_fallback_only:
        print(f"\n  FALLBACK MATCH ONLY ({len(purchase_fallback_only)} lines):")
        for line in purchase_fallback_only:
            print(f"    Purchase #{line['doc_number']} | {line['vendor_name']} | ${line['amount']:,.2f}")

    # --- STALE ITEMS ---
    print("\n" + "-" * 80)
    print("STALE LOCAL ITEMS (Billable locally, but NOT Billable in QBO)")
    print("-" * 80)

    if stale_bills:
        print(f"\n  Stale Bill Line Items ({len(stale_bills)}):")
        for row in stale_bills:
            print(f"    Bill #{row.BillNumber} | LocalId={row.Id} | ${row.Amount:,.2f} | "
                  f"QBO Status={row.QboBillableStatus} | {(row.Description or '')[:60]}")
    else:
        print("\n  No stale bill line items found.")

    if stale_expenses:
        print(f"\n  Stale Expense Line Items ({len(stale_expenses)}):")
        for row in stale_expenses:
            print(f"    Expense #{row.ExpenseNumber} | LocalId={row.Id} | ${row.Amount:,.2f} | "
                  f"QBO Status={row.QboBillableStatus}")
    else:
        print("\n  No stale expense line items found.")

    # --- ORPHANED ITEMS ---
    print("\n" + "-" * 80)
    print("ORPHANED LOCAL ITEMS (No QBO mapping at all)")
    print("-" * 80)

    if orphaned_bills:
        print(f"\n  Orphaned Bill Line Items ({len(orphaned_bills)}):")
        for row in orphaned_bills:
            print(f"    Bill #{row.BillNumber} | LocalId={row.Id} | PublicId={row.PublicId} | "
                  f"${row.Amount:,.2f} | {(row.Description or '')[:60]}")
    else:
        print("\n  No orphaned bill line items found.")

    if orphaned_expenses:
        print(f"\n  Orphaned Expense Line Items ({len(orphaned_expenses)}):")
        for row in orphaned_expenses:
            print(f"    Expense #{row.ExpenseNumber} | LocalId={row.Id} | PublicId={row.PublicId} | "
                  f"${row.Amount:,.2f} | {(row.Description or '')[:60]}")
    else:
        print("\n  No orphaned expense line items found.")

    # --- SUMMARY ---
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)

    total_qbo = len(all_billable_lines)
    total_correct = len(correct_mapping) + len(correct_purchase_mapping)
    total_missing_map = len(missing_qbo_mapping) + len(missing_purchase_mapping)
    total_missing_att = len(missing_attachment) + len(missing_purchase_attachment)
    total_dups = len(duplicates) + len(purchase_duplicates)
    total_fallback = len(fallback_only) + len(purchase_fallback_only)
    total_stale = len(stale_bills) + len(stale_expenses)
    total_orphaned = len(orphaned_bills) + len(orphaned_expenses)

    print(f"\n  QBO Billable Lines Total:        {total_qbo}")
    print(f"    Bills:                           {len(bill_billable)}")
    print(f"    Purchases:                       {len(purchase_billable)}")
    print(f"  Total Billable Amount:             ${total_billable_amount:,.2f}")
    print(f"\n  Correct (mapping + attachment):    {total_correct}")
    print(f"  Missing QBO mapping:               {total_missing_map}")
    print(f"  Missing attachment chain:          {total_missing_att}")
    print(f"  Duplicates:                        {total_dups}")
    print(f"  Fallback match only:               {total_fallback}")
    print(f"  Stale local items:                 {total_stale}")
    print(f"  Orphaned local items:              {total_orphaned}")

    has_been_billed_amount = sum(l['amount'] for l in all_has_been_billed_lines)
    print(f"\n  QBO HasBeenBilled Lines:           {len(all_has_been_billed_lines)} (${has_been_billed_amount:,.2f})")
    not_billable_amount = sum(l['amount'] for l in all_not_billable_lines)
    print(f"  QBO NotBillable Lines:             {len(all_not_billable_lines)} (${not_billable_amount:,.2f})")

    print("\n" + "=" * 100)
    print("END OF REPORT")
    print("=" * 100)


if __name__ == '__main__':
    main()
