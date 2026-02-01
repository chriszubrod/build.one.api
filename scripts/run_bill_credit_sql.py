#!/usr/bin/env python3
"""
Run BillCredit and QBO VendorCredit SQL migrations.
Creates tables and stored procedures for BillCredit module and VendorCredit integration.
"""

import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.database import get_connection

# Order: dbo tables first, then qbo cache, then connector mapping tables
SQL_FILES = [
    "services/bill_credit/sql/dbo.bill_credit.sql",
    "services/bill_credit_line_item/sql/dbo.bill_credit_line_item.sql",
    "services/bill_credit_line_item_attachment/sql/dbo.bill_credit_line_item_attachment.sql",
    "integrations/intuit/qbo/vendorcredit/sql/qbo.vendorcredit.sql",
    "integrations/intuit/qbo/vendorcredit/connector/bill_credit/sql/qbo.vendorcredit_bill_credit.sql",
    "integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/sql/qbo.vendorcredit_line_item_bill_credit_line_item.sql",
]


def run_sql_file(cursor, filepath: str) -> bool:
    """Run a SQL file, splitting on GO statements."""
    print(f"\n{'='*60}\nRunning: {filepath}\n{'='*60}")
    try:
        with open(filepath, "r") as f:
            sql_content = f.read()
        batches = re.split(r"^\s*GO\s*$", sql_content, flags=re.MULTILINE | re.IGNORECASE)
        success_count = 0
        error_count = 0
        for i, batch in enumerate(batches, 1):
            batch = batch.strip()
            if not batch:
                continue
            try:
                cursor.execute(batch)
                success_count += 1
            except Exception as e:
                err = str(e)
                if "already exists" in err.lower() or "duplicate" in err.lower():
                    print(f"  [SKIP] Batch {i}: Object already exists")
                else:
                    print(f"  [ERROR] Batch {i}: {err[:120]}")
                    error_count += 1
        print(f"  Completed: {success_count} batches, {error_count} errors")
        return error_count == 0
    except FileNotFoundError:
        print(f"  [ERROR] File not found: {filepath}")
        return False
    except Exception as e:
        print(f"  [ERROR] {e}")
        return False


def main():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print("\n" + "="*60)
    print("BILL CREDIT & VENDOR CREDIT SQL MIGRATIONS")
    print("="*60)
    try:
        with get_connection() as conn:
            conn.autocommit = True  # DDL/GO batches need commit per batch
            cursor = conn.cursor()
            all_ok = True
            for rel_path in SQL_FILES:
                path = os.path.join(project_root, rel_path)
                if not run_sql_file(cursor, path):
                    all_ok = False
            print("\n" + "="*60)
            print("ALL MIGRATIONS COMPLETED SUCCESSFULLY" if all_ok else "MIGRATIONS COMPLETED WITH ERRORS")
            print("="*60 + "\n")
    except Exception as e:
        print(f"\n[FATAL] Database connection failed: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
