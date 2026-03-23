#!/usr/bin/env python3
"""
Three-way reconciliation: Build One DB <-> QBO <-> SharePoint Excel.
DB is the middle layer — all sync flows through DB.

Four directions:

  1. DB -> QBO   Bills missing a BillBill QBO mapping are pushed via sync_to_qbo_bill().
                 BillLineItems missing BillLineItemBillLine mappings are repaired by
                 matching to QboBillLines by description + amount.

  2. QBO -> DB   Local qbo.Bill / qbo.Purchase records whose lines reference the project
                 but have no BillBill / PurchaseExpense mapping are pulled via
                 sync_from_qbo_bill() / sync_from_qbo_purchase().
                 (Run scripts/sync_qbo_bill.py first to refresh local qbo tables from QBO API.)

  3. DB -> Excel DB line items whose public_id is not in Excel col Z are written using
                 the existing sync_to_excel_workbook() from BillService / ExpenseService.
                 Only the missing line items are written (avoids duplicating existing rows).

  4. Excel -> DB Rows with col Z: public_id is verified against DB.
                 Rows without col Z: fuzzy-matched by vendor / ref# / description / price.
                   - If matched: col Z is backfilled (--write only).
                   - If no bill/expense exists for vendor+ref#: a draft Bill + BillLineItem
                     is created for manual review (--write only).
                   - Unresolvable rows (ambiguous match, vendor not found, etc.) are logged.

Usage:
  python scripts/reconcile_project.py --project-id 128         # dry run
  python scripts/reconcile_project.py --project-id 128 --write # apply all repairs
  python scripts/reconcile_project.py                           # all projects (dry run)
  python scripts/reconcile_project.py --write                   # all projects, apply
"""

import argparse
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Set, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entities.bill.business.service import BillService
from entities.bill.persistence.repo import BillRepository
from entities.bill_line_item.persistence.repo import BillLineItemRepository
from entities.expense.business.service import ExpenseService
from entities.expense.persistence.repo import ExpenseRepository
from entities.expense_line_item.persistence.repo import ExpenseLineItemRepository
from entities.vendor.persistence.repo import VendorRepository
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository
from integrations.intuit.qbo.bill.connector.bill_line_item.persistence.repo import BillLineItemBillLineRepository
from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository, QboBillLineRepository
from integrations.intuit.qbo.customer.connector.project.persistence.repo import CustomerProjectRepository
from integrations.intuit.qbo.customer.persistence.repo import QboCustomerRepository
from integrations.intuit.qbo.purchase.connector.expense.business.service import PurchaseExpenseConnector
from integrations.intuit.qbo.purchase.connector.expense.persistence.repo import PurchaseExpenseRepository
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseRepository, QboPurchaseLineRepository
from integrations.ms.sharepoint.driveitem.connector.project_excel.persistence.repo import DriveItemProjectExcelRepository
from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
from integrations.ms.sharepoint.external.client import (
    get_excel_used_range_values,
    update_excel_range,
)
from shared.database import get_connection

# ── Excel column indices (0-based from col A) ──
# Range is always fetched as A1:Z{lastRow} so index 0 = col A.
COL_H = 7    # Draw Request      (H)
COL_I = 8    # Date              (I)
COL_J = 9    # Vendor Name       (J)
COL_K = 10   # Bill / Ref Number (K)
COL_L = 11   # Description       (L)
COL_N = 13   # Price             (N)
COL_Z = 25   # public_id key     (Z)

PRICE_TOLERANCE = Decimal("0.01")

_VENDOR_SUFFIX_RE = re.compile(
    r"[,.]?\s*(llc|inc|corp|ltd|co|company|limited|incorporated)\.?$",
    re.IGNORECASE,
)


def _normalize_vendor(name: str) -> str:
    """Lowercase and strip common legal suffixes for fuzzy vendor matching."""
    return _VENDOR_SUFFIX_RE.sub("", name.strip().lower()).strip().rstrip(",").strip()


# ── Helpers ─────────────────────────────────────────────────────

def _cell(row: List[Any], idx: int) -> str:
    """Return stripped string value at index, or '' if out of range."""
    if idx < len(row):
        v = row[idx]
        return str(v).strip() if v is not None else ""
    return ""


def _parse_date(value) -> Optional[str]:
    """
    Parse an Excel cell value to YYYY-MM-DD string.
    Handles Excel serial numbers (int/float) and common string formats.
    Returns None if unparsable.
    """
    if value is None or value == "":
        return None
    from datetime import datetime, timedelta
    # Excel serial number
    try:
        serial = int(float(str(value)))
        if 20000 < serial < 60000:  # sanity check: roughly 1954–2064
            return (datetime(1899, 12, 30) + timedelta(days=serial)).strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        pass
    # String date formats
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _safe_decimal(value) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _find_line_item_match(
    description: str,
    price: Optional[Decimal],
    line_items: List[Any],
) -> Tuple[Optional[Any], str]:
    """
    Match an Excel row to a list of bill/expense line items by description + price.
    Returns (line_item, status) where status is 'match', 'ambiguous', or 'no_match'.
    """
    candidates = []
    for li in line_items:
        li_desc = (li.description or "").strip()
        li_price = _safe_decimal(li.price)
        desc_match = li_desc.lower() == description.lower()
        price_match = (
            price is not None
            and li_price is not None
            and abs(li_price - price) <= PRICE_TOLERANCE
        )
        if desc_match and price_match:
            candidates.append(li)
    if len(candidates) == 1:
        return candidates[0], "match"
    if len(candidates) > 1:
        return None, "ambiguous"
    return None, "no_match"


def _get_realm_id(auth_service: QboAuthService) -> Optional[str]:
    """Return the first available QBO realm_id, or None."""
    auths = auth_service.read_all()
    for auth in auths:
        if auth.realm_id:
            return auth.realm_id
    return None


def _get_project_qbo_customer_ref(
    project_id: int,
    customer_project_repo: CustomerProjectRepository,
    qbo_customer_repo: QboCustomerRepository,
) -> Optional[str]:
    """Return QboCustomer.qbo_id for a project, or None if not mapped."""
    customer_project = customer_project_repo.read_by_project_id(project_id)
    if not customer_project:
        return None
    qbo_customer = qbo_customer_repo.read_by_id(customer_project.qbo_customer_id)
    if not qbo_customer:
        return None
    return qbo_customer.qbo_id


# ── Data loaders ─────────────────────────────────────────────────

def _load_bills_for_project(
    project_id: int,
    bill_repo: BillRepository,
    bill_line_item_repo: BillLineItemRepository,
) -> Tuple[Dict, Dict, Dict]:
    """
    Return (bills_by_id, line_items_by_bill_id, all_line_items_by_id) for
    non-draft bills that have at least one non-draft line item for this project.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT b.Id
            FROM dbo.Bill b
            INNER JOIN dbo.BillLineItem bli ON bli.BillId = b.Id
            WHERE bli.ProjectId = ? AND b.IsDraft = 0
              AND YEAR(b.BillDate) = 2026
              AND bli.IsBilled = 0
            """,
            (project_id,),
        )
        bill_ids = [row[0] for row in cursor.fetchall()]
        cursor.close()

    bills_by_id: Dict[int, Any] = {}
    line_items_by_bill_id: Dict[int, List[Any]] = {}
    all_line_items_by_id: Dict[int, Any] = {}

    for bill_id in bill_ids:
        bill = bill_repo.read_by_id(bill_id)
        if not bill:
            continue
        bills_by_id[bill_id] = bill
        line_items = bill_line_item_repo.read_by_bill_id(bill_id)
        project_lines = [
            li for li in line_items
            if li.project_id == project_id and not li.is_draft
        ]
        line_items_by_bill_id[bill_id] = project_lines
        for li in project_lines:
            all_line_items_by_id[li.id] = li

    return bills_by_id, line_items_by_bill_id, all_line_items_by_id


def _load_expenses_for_project(
    project_id: int,
    expense_repo: ExpenseRepository,
    expense_line_item_repo: ExpenseLineItemRepository,
) -> Tuple[Dict, Dict, Dict]:
    """
    Return (expenses_by_id, line_items_by_expense_id, all_line_items_by_id) for
    non-draft expenses that have at least one non-draft line item for this project.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT e.Id
            FROM dbo.Expense e
            INNER JOIN dbo.ExpenseLineItem eli ON eli.ExpenseId = e.Id
            WHERE eli.ProjectId = ? AND e.IsDraft = 0
              AND YEAR(e.ExpenseDate) = 2026
              AND eli.IsBilled = 0
            """,
            (project_id,),
        )
        expense_ids = [row[0] for row in cursor.fetchall()]
        cursor.close()

    expenses_by_id: Dict[int, Any] = {}
    line_items_by_expense_id: Dict[int, List[Any]] = {}
    all_line_items_by_id: Dict[int, Any] = {}

    for expense_id in expense_ids:
        expense = expense_repo.read_by_id(expense_id)
        if not expense:
            continue
        expenses_by_id[expense_id] = expense
        line_items = expense_line_item_repo.read_by_expense_id(expense_id)
        project_lines = [
            li for li in line_items
            if li.project_id == project_id and not li.is_draft
        ]
        line_items_by_expense_id[expense_id] = project_lines
        for li in project_lines:
            all_line_items_by_id[li.id] = li

    return expenses_by_id, line_items_by_expense_id, all_line_items_by_id


# ── Reconciliation checks ────────────────────────────────────────

def check_db_to_qbo(
    bills_by_id: Dict,
    line_items_by_bill_id: Dict,
    bill_bill_repo: BillBillRepository,
    bill_line_item_bill_line_repo: BillLineItemBillLineRepository,
) -> List[str]:
    """Check every non-draft bill and line item has a QBO mapping."""
    issues = []
    for bill_id, bill in bills_by_id.items():
        if not bill_bill_repo.read_by_bill_id(bill_id):
            issues.append(
                f"  [DB->QBO] Bill #{bill.bill_number} (id={bill_id}) — missing BillBill QBO mapping"
            )
        for li in line_items_by_bill_id.get(bill_id, []):
            if not bill_line_item_bill_line_repo.read_by_bill_line_item_id(li.id):
                issues.append(
                    f"  [DB->QBO] BillLineItem id={li.id} '{li.description}' "
                    f"(bill #{bill.bill_number}) — missing BillLineItemBillLine QBO mapping"
                )
    return issues


def repair_db_to_qbo_bills(
    bills_by_id: Dict,
    bill_bill_repo: BillBillRepository,
    connector: BillBillConnector,
    realm_id: str,
    dry_run: bool,
) -> Tuple[List[str], int]:
    """Push bills with no BillBill mapping to QBO via sync_to_qbo_bill()."""
    issues = []
    repairs_count = 0

    for bill_id, bill in bills_by_id.items():
        if bill_bill_repo.read_by_bill_id(bill_id):
            continue  # Already mapped

        print(
            f"  [DB->QBO] Bill #{bill.bill_number} (id={bill_id}) — pushing to QBO"
        )
        if dry_run:
            print(f"    DRY RUN: would call sync_to_qbo_bill()")
            repairs_count += 1
            continue

        try:
            connector.sync_to_qbo_bill(bill=bill, realm_id=realm_id)
            print(f"    Pushed to QBO")
            repairs_count += 1
        except Exception as e:
            issues.append(
                f"  [DB->QBO] Bill #{bill.bill_number} (id={bill_id}): push failed: {e}"
            )

    return issues, repairs_count


def repair_qbo_line_item_mappings(
    bills_by_id: Dict,
    line_items_by_bill_id: Dict,
    bill_bill_repo: BillBillRepository,
    bill_line_item_bill_line_repo: BillLineItemBillLineRepository,
    qbo_bill_line_repo: QboBillLineRepository,
    dry_run: bool,
) -> Tuple[List[str], int]:
    """
    For each bill that exists in QBO (has a BillBill mapping) but whose line items
    are missing BillLineItemBillLine records, match each BillLineItem to its
    QboBillLine by description + amount and create the missing mapping.

    Bills with no BillBill mapping at all are skipped (handled by repair_db_to_qbo_bills).

    Returns (issues, repairs_count).
    """
    issues = []
    repairs_count = 0

    for bill_id, bill in bills_by_id.items():
        bill_bill = bill_bill_repo.read_by_bill_id(bill_id)
        if not bill_bill:
            continue

        unlinked = [
            li for li in line_items_by_bill_id.get(bill_id, [])
            if not bill_line_item_bill_line_repo.read_by_bill_line_item_id(li.id)
        ]
        if not unlinked:
            continue

        qbo_lines = qbo_bill_line_repo.read_by_qbo_bill_id(bill_bill.qbo_bill_id)
        if not qbo_lines:
            issues.append(
                f"  [QBO repair] Bill #{bill.bill_number}: no QboBillLines found "
                f"for qbo_bill_id={bill_bill.qbo_bill_id} — cannot repair"
            )
            continue

        for li in unlinked:
            li_desc = (li.description or "").strip().lower()
            li_amount = _safe_decimal(li.amount)

            candidates = [
                ql for ql in qbo_lines
                if (ql.description or "").strip().lower() == li_desc
                and li_amount is not None
                and _safe_decimal(ql.amount) is not None
                and abs(_safe_decimal(ql.amount) - li_amount) <= PRICE_TOLERANCE
            ]

            if len(candidates) == 0:
                issues.append(
                    f"  [QBO repair] BillLineItem id={li.id} '{li.description}' "
                    f"(bill #{bill.bill_number}) — no matching QboBillLine found "
                    f"(desc+amount match failed)"
                )
                continue

            if len(candidates) > 1:
                issues.append(
                    f"  [QBO repair] BillLineItem id={li.id} '{li.description}' "
                    f"(bill #{bill.bill_number}) — ambiguous: {len(candidates)} QboBillLines match"
                )
                continue

            matched_qbo_line = candidates[0]

            existing_mapping = bill_line_item_bill_line_repo.read_by_qbo_bill_line_id(matched_qbo_line.id)
            if existing_mapping and existing_mapping.bill_line_item_id != li.id:
                issues.append(
                    f"  [QBO repair] BillLineItem id={li.id} '{li.description}' "
                    f"(bill #{bill.bill_number}) — QboBillLine id={matched_qbo_line.id} is already "
                    f"mapped to BillLineItem id={existing_mapping.bill_line_item_id} "
                    f"(possible duplicate from sync_from_qbo)"
                )
                continue

            print(
                f"  [QBO repair] BillLineItem id={li.id} '{li.description}' "
                f"-> QboBillLine id={matched_qbo_line.id} (line_num={matched_qbo_line.line_num})"
            )

            if dry_run:
                print(f"    DRY RUN: would create BillLineItemBillLine mapping")
                repairs_count += 1
                continue

            try:
                bill_line_item_bill_line_repo.create(
                    bill_line_item_id=li.id,
                    qbo_bill_line_id=matched_qbo_line.id,
                )
                print(f"    Created BillLineItemBillLine mapping")
                repairs_count += 1
            except Exception as e:
                issues.append(
                    f"  [QBO repair] BillLineItem id={li.id}: create mapping failed: {e}"
                )

    return issues, repairs_count


def sync_qbo_to_db_bills(
    qbo_customer_ref: str,
    realm_id: str,
    bill_bill_repo: BillBillRepository,
    qbo_bill_repo: QboBillRepository,
    qbo_bill_line_repo: QboBillLineRepository,
    connector: BillBillConnector,
    dry_run: bool,
) -> Tuple[List[str], int]:
    """
    Check local qbo.Bill records whose lines reference this project but have
    no BillBill mapping, and pull them into DB via sync_from_qbo_bill().

    Note: Run scripts/sync_qbo_bill.py first to refresh local qbo tables from QBO API.
    """
    issues = []
    synced_count = 0

    # Filter at SQL level — only bills with a line for this project, this year
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT b.Id
            FROM qbo.Bill b
            INNER JOIN qbo.BillLine bl ON bl.QboBillId = b.Id
            WHERE b.RealmId = ?
              AND bl.CustomerRefValue = ?
              AND YEAR(b.TxnDate) = 2026
            """,
            (realm_id, qbo_customer_ref),
        )
        candidate_ids = [row[0] for row in cursor.fetchall()]
        cursor.close()

    for bill_id in candidate_ids:
        qbo_bill = qbo_bill_repo.read_by_id(bill_id)
        if not qbo_bill:
            continue

        if bill_bill_repo.read_by_qbo_bill_id(qbo_bill.id):
            continue  # Already mapped to a DB bill

        lines = qbo_bill_line_repo.read_by_qbo_bill_id(qbo_bill.id)
        print(
            f"  [QBO->DB] QBO Bill #{qbo_bill.doc_number} "
            f"(qbo_id={qbo_bill.qbo_id}) — not in DB"
        )
        if dry_run:
            print(f"    DRY RUN: would call sync_from_qbo_bill()")
            synced_count += 1
            continue

        try:
            connector.sync_from_qbo_bill(qbo_bill=qbo_bill, qbo_bill_lines=lines)
            print(f"    Synced to DB")
            synced_count += 1
        except Exception as e:
            issues.append(
                f"  [QBO->DB] QBO Bill #{qbo_bill.doc_number}: sync failed: {e}"
            )

    return issues, synced_count


def sync_qbo_to_db_expenses(
    qbo_customer_ref: str,
    purchase_expense_repo: PurchaseExpenseRepository,
    qbo_purchase_repo: QboPurchaseRepository,
    qbo_purchase_line_repo: QboPurchaseLineRepository,
    connector: PurchaseExpenseConnector,
    dry_run: bool,
) -> Tuple[List[str], int]:
    """
    Check local qbo.Purchase records whose lines reference this project but have
    no PurchaseExpense mapping, and pull them into DB via sync_from_qbo_purchase().
    """
    issues = []
    synced_count = 0

    # Filter at SQL level — only purchases with a line for this project, this year
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT DISTINCT p.Id
            FROM qbo.Purchase p
            INNER JOIN qbo.PurchaseLine pl ON pl.QboPurchaseId = p.Id
            WHERE pl.CustomerRefValue = ?
              AND YEAR(p.TxnDate) = 2026
            """,
            (qbo_customer_ref,),
        )
        candidate_ids = [row[0] for row in cursor.fetchall()]
        cursor.close()

    for purchase_id in candidate_ids:
        qbo_purchase = qbo_purchase_repo.read_by_id(purchase_id)
        if not qbo_purchase:
            continue

        if purchase_expense_repo.read_by_qbo_purchase_id(qbo_purchase.id):
            continue  # Already mapped to a DB expense

        lines = qbo_purchase_line_repo.read_by_qbo_purchase_id(qbo_purchase.id)
        print(
            f"  [QBO->DB] QBO Purchase #{qbo_purchase.doc_number} "
            f"(qbo_id={qbo_purchase.qbo_id}) — not in DB"
        )
        if dry_run:
            print(f"    DRY RUN: would call sync_from_qbo_purchase()")
            synced_count += 1
            continue

        try:
            connector.sync_from_qbo_purchase(
                qbo_purchase=qbo_purchase, qbo_purchase_lines=lines
            )
            print(f"    Synced to DB")
            synced_count += 1
        except Exception as e:
            issues.append(
                f"  [QBO->DB] QBO Purchase #{qbo_purchase.doc_number}: sync failed: {e}"
            )

    return issues, synced_count


def repair_db_to_excel(
    bills_by_id: Dict,
    line_items_by_bill_id: Dict,
    expenses_by_id: Dict,
    line_items_by_expense_id: Dict,
    excel_public_ids: Set[str],
    project_id: int,
    bill_service: BillService,
    expense_service: ExpenseService,
    dry_run: bool,
) -> Tuple[List[str], int]:
    """
    Write DB line items missing from Excel using sync_to_excel_workbook().
    Only missing line items are passed to avoid duplicating existing rows.
    """
    issues = []
    synced_count = 0

    for bill_id, bill in bills_by_id.items():
        line_items = line_items_by_bill_id.get(bill_id, [])
        missing = [
            li for li in line_items
            if not li.public_id or str(li.public_id) not in excel_public_ids
        ]
        if not missing:
            continue

        print(
            f"  [DB->Excel] Bill #{bill.bill_number} (id={bill_id}) "
            f"— {len(missing)} line item(s) missing, writing to Excel"
        )
        if dry_run:
            for li in missing:
                print(f"    DRY RUN: would write '{li.description}' (id={li.id})")
            synced_count += len(missing)
            continue

        try:
            result = bill_service.sync_to_excel_workbook(
                bill=bill, line_items=missing, project_id=project_id
            )
            if result.get("errors"):
                for err in result["errors"]:
                    issues.append(f"  [DB->Excel] Bill #{bill.bill_number}: {err}")
            else:
                print(f"    Written {len(missing)} line item(s) to Excel")
                synced_count += len(missing)
        except Exception as e:
            issues.append(
                f"  [DB->Excel] Bill #{bill.bill_number} (id={bill_id}): sync failed: {e}"
            )

    for expense_id, expense in expenses_by_id.items():
        line_items = line_items_by_expense_id.get(expense_id, [])
        missing = [
            li for li in line_items
            if not li.public_id or str(li.public_id) not in excel_public_ids
        ]
        if not missing:
            continue

        print(
            f"  [DB->Excel] Expense #{expense.reference_number} (id={expense_id}) "
            f"— {len(missing)} line item(s) missing, writing to Excel"
        )
        if dry_run:
            for li in missing:
                print(f"    DRY RUN: would write '{li.description}' (id={li.id})")
            synced_count += len(missing)
            continue

        try:
            result = expense_service.sync_to_excel_workbook(
                expense=expense, line_items=missing, project_id=project_id
            )
            if result.get("errors"):
                for err in result["errors"]:
                    issues.append(
                        f"  [DB->Excel] Expense #{expense.reference_number}: {err}"
                    )
            else:
                print(f"    Written {len(missing)} line item(s) to Excel")
                synced_count += len(missing)
        except Exception as e:
            issues.append(
                f"  [DB->Excel] Expense #{expense.reference_number} (id={expense_id}): sync failed: {e}"
            )

    return issues, synced_count


def check_db_to_excel(
    all_bill_li_by_id: Dict,
    all_exp_li_by_id: Dict,
    excel_public_ids: Set[str],
) -> List[str]:
    """Check every non-draft DB line item public_id appears in Excel col Z."""
    issues = []
    for li in all_bill_li_by_id.values():
        pid = str(li.public_id) if li.public_id else None
        if not pid or pid not in excel_public_ids:
            issues.append(
                f"  [DB->Excel] BillLineItem id={li.id} '{li.description}' "
                f"— public_id not found in Excel col Z"
            )
    for li in all_exp_li_by_id.values():
        pid = str(li.public_id) if li.public_id else None
        if not pid or pid not in excel_public_ids:
            issues.append(
                f"  [DB->Excel] ExpenseLineItem id={li.id} '{li.description}' "
                f"— public_id not found in Excel col Z"
            )
    return issues


def check_excel_to_db(
    data_rows: List[List[Any]],
    all_bill_public_id_map: Dict[str, Any],
    all_expense_public_id_map: Dict[str, Any],
    vendor_map: Dict[str, Any],
    vendor_norm_map: Dict[str, Any],
    bill_repo: BillRepository,
    expense_repo: ExpenseRepository,
    bill_line_item_repo: BillLineItemRepository,
    expense_line_item_repo: ExpenseLineItemRepository,
    dry_run: bool,
    drive_id: str,
    item_id: str,
    worksheet_name: str,
    project_id: int = None,
) -> Tuple[List[str], int, int, Set[str]]:
    """
    Step 8 — Match scoped Excel rows to DB records.

    Scope: 2026 rows where col H (DRAW REQUEST) is empty (step 7).

    - Rows with col Z: verify public_id exists in the scoped DB set.
      Orphaned (not found) → clear col Z in write mode so the row
      re-enters matching logic on the next run.
    - Rows without col Z: attempt exact match on all five fields —
        date + vendor (fuzzy) + bill/ref number + description + amount.
        Unambiguous match → backfill col Z (write mode).
        Any mismatch or ambiguous → flag for manual review.

    No automatic DB record creation (step 10 is manual review only).

    Returns (issues, backfill_count, orphan_clear_count, backfilled_public_ids).
    """
    issues = []
    backfill_count = 0
    orphan_clear_count = 0
    backfilled_public_ids: Set[str] = set()

    for row_idx, row in enumerate(data_rows):
        excel_row_num = row_idx + 2  # +2: row 1 is header, rows are 1-based
        col_z_val = _cell(row, COL_Z)
        draw_request = _cell(row, COL_H)
        date_str = _cell(row, COL_I)
        vendor_name = _cell(row, COL_J)
        ref_number = _cell(row, COL_K)
        description = _cell(row, COL_L)
        price = _safe_decimal(_cell(row, COL_N))

        # ── Skip the secondary header row (Excel row 2) ─────────
        if excel_row_num == 2:
            continue

        # ── Step 7 scope filter ──────────────────────────────────
        # Only reconcile 2026 rows that have not yet been billed
        # (col H "DRAW REQUEST" is empty).
        if draw_request:
            continue
        parsed_date = _parse_date(date_str)
        if not parsed_date or not parsed_date.startswith("2026"):
            continue

        # ── Rows with col Z: direct lookup ──────────────────────
        if col_z_val:
            found = (
                col_z_val in all_bill_public_id_map
                or col_z_val in all_expense_public_id_map
            )
            if not found:
                print(
                    f"  [Excel->DB] Row {excel_row_num}: col Z='{col_z_val}' "
                    f"— orphaned (not in DB)"
                )
                if dry_run:
                    print(f"    DRY RUN: would clear col Z")
                    orphan_clear_count += 1
                else:
                    clear_result = update_excel_range(
                        drive_id, item_id, worksheet_name,
                        f"Z{excel_row_num}", [[""]]
                    )
                    if clear_result.get("status_code") in (200, 204):
                        print(f"    Cleared col Z")
                        orphan_clear_count += 1
                    else:
                        issues.append(
                            f"  [Excel->DB] Row {excel_row_num}: failed to clear col Z: "
                            f"{clear_result.get('message')}"
                        )
            continue

        # ── Rows without col Z: skip if no vendor or ref# ───────
        if not vendor_name or not ref_number:
            continue

        # ── Vendor lookup (exact then normalized) ────────────────
        vendor = vendor_map.get(vendor_name.lower())
        if not vendor:
            vendor = vendor_norm_map.get(_normalize_vendor(vendor_name))
        if not vendor:
            issues.append(
                f"  [Excel->DB] Row {excel_row_num}: vendor '{vendor_name}' not in DB — skipped"
            )
            continue

        matched_li = None
        source_type = None

        # Try bill first — match on all five fields: vendor (fuzzy, already
        # resolved), ref number, date, description, amount.
        bill = bill_repo.read_by_bill_number_and_vendor_id(ref_number, vendor.id)
        if bill:
            bill_date_str = str(bill.bill_date)[:10] if bill.bill_date else None
            if bill_date_str != parsed_date:
                issues.append(
                    f"  [Excel->DB] Row {excel_row_num}: date mismatch for bill "
                    f"#{ref_number} (Excel={parsed_date}, DB={bill_date_str}) — review manually"
                )
                continue
            bill_line_items = bill_line_item_repo.read_by_bill_id(bill.id)
            if project_id is not None:
                bill_line_items = [li for li in bill_line_items if li.project_id == project_id and not li.is_draft]
            matched, status = _find_line_item_match(description, price, bill_line_items)
            if status == "match":
                matched_li = matched
                source_type = "bill"
            elif status == "ambiguous":
                issues.append(
                    f"  [Excel->DB] Row {excel_row_num}: ambiguous match among "
                    f"BillLineItems for bill #{ref_number} — review manually"
                )
                continue

        # Try expense if no bill match
        if matched_li is None:
            expense = expense_repo.read_by_reference_number_and_vendor_id(ref_number, vendor.id)
            if expense:
                exp_date_str = str(expense.expense_date)[:10] if expense.expense_date else None
                if exp_date_str != parsed_date:
                    issues.append(
                        f"  [Excel->DB] Row {excel_row_num}: date mismatch for expense "
                        f"#{ref_number} (Excel={parsed_date}, DB={exp_date_str}) — review manually"
                    )
                    continue
                expense_line_items = expense_line_item_repo.read_by_expense_id(expense.id)
                if project_id is not None:
                    expense_line_items = [li for li in expense_line_items if li.project_id == project_id and not li.is_draft]
                matched, status = _find_line_item_match(description, price, expense_line_items)
                if status == "match":
                    matched_li = matched
                    source_type = "expense"
                elif status == "ambiguous":
                    issues.append(
                        f"  [Excel->DB] Row {excel_row_num}: ambiguous match among "
                        f"ExpenseLineItems for ref #{ref_number} — review manually"
                    )
                    continue

        if matched_li is None:
            # No match on all five fields — flag for manual review (step 10).
            issues.append(
                f"  [Excel->DB] Row {excel_row_num}: no DB match "
                f"(vendor='{vendor_name}', ref='{ref_number}', "
                f"date='{parsed_date}', desc='{description}', amount={price}) — review manually"
            )
            continue

        # ── Matched: backfill col Z ──────────────────────────────
        public_id_str = str(matched_li.public_id) if matched_li.public_id else None
        if not public_id_str:
            issues.append(
                f"  [Excel->DB] Row {excel_row_num}: matched {source_type} line item "
                f"id={matched_li.id} has no public_id"
            )
            continue

        print(
            f"  [Excel->DB] Row {excel_row_num}: matched {source_type} line item "
            f"id={matched_li.id} '{description}' -> public_id={public_id_str}"
        )

        if dry_run:
            print(f"    DRY RUN: would write public_id to col Z, row {excel_row_num}")
            backfill_count += 1
            backfilled_public_ids.add(public_id_str)
            continue

        # Re-read and validate before writing
        reread = get_excel_used_range_values(drive_id, item_id, worksheet_name)
        if reread.get("status_code") != 200:
            issues.append(
                f"  [Excel->DB] Row {excel_row_num}: re-read failed before write: {reread.get('message')}"
            )
            continue

        reread_rows = reread.get("range", {}).get("values", [])
        reread_data = reread_rows[1:] if len(reread_rows) > 1 else []

        if row_idx >= len(reread_data):
            issues.append(
                f"  [Excel->DB] Row {excel_row_num}: row no longer exists after re-read"
            )
            continue

        reread_row = reread_data[row_idx]
        if _cell(reread_row, COL_J) != vendor_name or _cell(reread_row, COL_K) != ref_number:
            issues.append(
                f"  [Excel->DB] Row {excel_row_num}: validation failed after re-read "
                f"(row may have shifted) — skipped"
            )
            continue

        if _cell(reread_row, COL_Z):
            print(f"    Row {excel_row_num}: col Z populated since snapshot — skipping")
            continue

        write_result = update_excel_range(
            drive_id, item_id, worksheet_name, f"Z{excel_row_num}", [[public_id_str]]
        )
        if write_result.get("status_code") in (200, 204):
            print(f"    Wrote public_id to Z{excel_row_num}")
            backfill_count += 1
            backfilled_public_ids.add(public_id_str)
        else:
            issues.append(
                f"  [Excel->DB] Row {excel_row_num}: write failed: {write_result.get('message')}"
            )

    return issues, backfill_count, orphan_clear_count, backfilled_public_ids


# ── Per-project entry point ──────────────────────────────────────

def process_project(
    project_id: int,
    dry_run: bool,
    vendor_map: Dict[str, Any],
    vendor_norm_map: Dict[str, Any],
    bill_repo: BillRepository,
    bill_line_item_repo: BillLineItemRepository,
    expense_repo: ExpenseRepository,
    expense_line_item_repo: ExpenseLineItemRepository,
    bill_bill_repo: BillBillRepository,
    bill_line_item_bill_line_repo: BillLineItemBillLineRepository,
    qbo_bill_repo: QboBillRepository,
    qbo_bill_line_repo: QboBillLineRepository,
    qbo_purchase_repo: QboPurchaseRepository,
    qbo_purchase_line_repo: QboPurchaseLineRepository,
    purchase_expense_repo: PurchaseExpenseRepository,
    bill_bill_connector: BillBillConnector,
    purchase_expense_connector: PurchaseExpenseConnector,
    customer_project_repo: CustomerProjectRepository,
    qbo_customer_repo: QboCustomerRepository,
    auth_service: QboAuthService,
    bill_service: BillService,
    expense_service: ExpenseService,
    driveitem_repo: MsDriveItemRepository,
    drive_repo: MsDriveRepository,
    excel_mapping_repo: DriveItemProjectExcelRepository,
) -> Dict:
    stats = {
        "db_to_qbo_issues": 0,
        "qbo_repairs": 0,
        "db_pushes": 0,
        "qbo_to_db_synced": 0,
        "qbo_to_db_issues": 0,
        "db_to_excel_issues": 0,
        "db_to_excel_written": 0,
        "excel_to_db_issues": 0,
        "backfills": 0,
        "orphan_clears": 0,
    }

    mapping = excel_mapping_repo.read_by_project_id(project_id)
    if not mapping:
        print("  No linked Excel workbook — skipping.")
        return stats

    all_driveitems = driveitem_repo.read_all()
    driveitem = next((d for d in all_driveitems if d.id == mapping.ms_driveitem_id), None)
    if not driveitem:
        print(f"  DriveItem id={mapping.ms_driveitem_id} not found — skipping.")
        return stats

    drive = drive_repo.read_by_id(driveitem.ms_drive_id)
    if not drive:
        print(f"  Drive not found for driveitem id={driveitem.id} — skipping.")
        return stats

    drive_id = drive.drive_id
    item_id = driveitem.item_id
    worksheet_name = mapping.worksheet_name

    # ── Load DB data ─────────────────────────────────────────────
    print("  Loading DB bills...")
    bills_by_id, line_items_by_bill_id, all_bill_li_by_id = _load_bills_for_project(
        project_id, bill_repo, bill_line_item_repo
    )
    print(f"    {len(bills_by_id)} bills, {len(all_bill_li_by_id)} non-draft line items")

    print("  Loading DB expenses...")
    expenses_by_id, line_items_by_expense_id, all_exp_li_by_id = _load_expenses_for_project(
        project_id, expense_repo, expense_line_item_repo
    )
    print(f"    {len(expenses_by_id)} expenses, {len(all_exp_li_by_id)} non-draft line items")

    # Build public_id lookup maps for Excel->DB check
    all_bill_public_id_map: Dict[str, Any] = {
        str(li.public_id): li
        for li in all_bill_li_by_id.values()
        if li.public_id
    }
    all_expense_public_id_map: Dict[str, Any] = {
        str(li.public_id): li
        for li in all_exp_li_by_id.values()
        if li.public_id
    }

    # ── Get QBO context ──────────────────────────────────────────
    realm_id = _get_realm_id(auth_service)
    qbo_customer_ref = _get_project_qbo_customer_ref(
        project_id, customer_project_repo, qbo_customer_repo
    )

    # ── Read Excel worksheet ─────────────────────────────────────
    print(f"  Reading worksheet '{worksheet_name}'...")
    excel_result = get_excel_used_range_values(drive_id, item_id, worksheet_name)
    if excel_result.get("status_code") != 200:
        print(f"  ERROR reading worksheet: {excel_result.get('message')}")
        return stats

    all_rows = excel_result.get("range", {}).get("values", [])
    data_rows = all_rows[1:] if len(all_rows) > 1 else []
    print(f"  Worksheet has {len(all_rows)} rows (including header), {len(data_rows)} data rows")

    # Collect col Z values already in Excel
    excel_public_ids: Set[str] = set()
    for row in data_rows:
        z = _cell(row, COL_Z)
        if z:
            excel_public_ids.add(z)

    # ── [1/4] DB -> QBO ──────────────────────────────────────────
    print(f"\n  [1/4] DB -> QBO")
    if not bills_by_id:
        print("  No completed bills — skipping DB->QBO check.")
    else:
        qbo_issues = check_db_to_qbo(
            bills_by_id, line_items_by_bill_id,
            bill_bill_repo, bill_line_item_bill_line_repo,
        )
        for issue in qbo_issues:
            print(issue)
        stats["db_to_qbo_issues"] = len(qbo_issues)
        if not qbo_issues:
            total_lines = sum(len(v) for v in line_items_by_bill_id.values())
            print(f"  OK — all {len(bills_by_id)} bills and {total_lines} line items have QBO mappings")

        # Push bills missing BillBill mapping
        if realm_id:
            push_issues, push_count = repair_db_to_qbo_bills(
                bills_by_id=bills_by_id,
                bill_bill_repo=bill_bill_repo,
                connector=bill_bill_connector,
                realm_id=realm_id,
                dry_run=dry_run,
            )
            for issue in push_issues:
                print(issue)
            stats["db_pushes"] = push_count
        else:
            print("  No QBO auth found — skipping DB->QBO push repair.")

        # Repair BillLineItemBillLine mappings
        repair_issues, repairs_count = repair_qbo_line_item_mappings(
            bills_by_id=bills_by_id,
            line_items_by_bill_id=line_items_by_bill_id,
            bill_bill_repo=bill_bill_repo,
            bill_line_item_bill_line_repo=bill_line_item_bill_line_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            dry_run=dry_run,
        )
        for issue in repair_issues:
            print(issue)
        stats["qbo_repairs"] = repairs_count

    # ── [2/4] QBO -> DB ──────────────────────────────────────────
    print(f"\n  [2/4] QBO -> DB")
    if not realm_id:
        print("  No QBO auth found — skipping.")
    elif not qbo_customer_ref:
        print(f"  Project {project_id} has no QBO customer mapping — skipping.")
    else:
        bill_sync_issues, bill_synced = sync_qbo_to_db_bills(
            qbo_customer_ref=qbo_customer_ref,
            realm_id=realm_id,
            bill_bill_repo=bill_bill_repo,
            qbo_bill_repo=qbo_bill_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            connector=bill_bill_connector,
            dry_run=dry_run,
        )
        for issue in bill_sync_issues:
            print(issue)

        exp_sync_issues, exp_synced = sync_qbo_to_db_expenses(
            qbo_customer_ref=qbo_customer_ref,
            purchase_expense_repo=purchase_expense_repo,
            qbo_purchase_repo=qbo_purchase_repo,
            qbo_purchase_line_repo=qbo_purchase_line_repo,
            connector=purchase_expense_connector,
            dry_run=dry_run,
        )
        for issue in exp_sync_issues:
            print(issue)

        total_synced = bill_synced + exp_synced
        total_issues = len(bill_sync_issues) + len(exp_sync_issues)
        stats["qbo_to_db_synced"] = total_synced
        stats["qbo_to_db_issues"] = total_issues
        if total_synced == 0 and total_issues == 0:
            print(f"  OK — no unmatched QBO records found for this project")

    # ── [3/4] Excel -> DB ────────────────────────────────────────
    # Runs before DB->Excel so col Z backfills happen first — DB->Excel
    # then skips rows that were already backfilled, preventing duplicate rows.
    print(f"\n  [3/4] Excel -> DB {'(DRY RUN)' if dry_run else ''}")
    e2db_issues, backfill_count, orphan_clear_count, backfilled_public_ids = check_excel_to_db(
        data_rows=data_rows,
        all_bill_public_id_map=all_bill_public_id_map,
        all_expense_public_id_map=all_expense_public_id_map,
        vendor_map=vendor_map,
        vendor_norm_map=vendor_norm_map,
        bill_repo=bill_repo,
        expense_repo=expense_repo,
        bill_line_item_repo=bill_line_item_repo,
        expense_line_item_repo=expense_line_item_repo,
        dry_run=dry_run,
        drive_id=drive_id,
        item_id=item_id,
        worksheet_name=worksheet_name,
        project_id=project_id,
    )
    for issue in e2db_issues:
        print(issue)

    # Merge backfilled IDs into the live set so DB->Excel doesn't re-write them
    excel_public_ids |= backfilled_public_ids

    # ── [4/4] DB -> Excel ────────────────────────────────────────
    print(f"\n  [4/4] DB -> Excel")
    if not all_bill_li_by_id and not all_exp_li_by_id:
        print("  No completed DB line items — skipping.")
    else:
        excel_issues = check_db_to_excel(all_bill_li_by_id, all_exp_li_by_id, excel_public_ids)
        stats["db_to_excel_issues"] = len(excel_issues)
        if not excel_issues:
            print(f"  OK — all DB line items have public_ids in Excel col Z")
        else:
            for issue in excel_issues:
                print(issue)

            # Repair: write missing line items to Excel
            write_issues, write_count = repair_db_to_excel(
                bills_by_id=bills_by_id,
                line_items_by_bill_id=line_items_by_bill_id,
                expenses_by_id=expenses_by_id,
                line_items_by_expense_id=line_items_by_expense_id,
                excel_public_ids=excel_public_ids,
                project_id=project_id,
                bill_service=bill_service,
                expense_service=expense_service,
                dry_run=dry_run,
            )
            for issue in write_issues:
                print(issue)
            stats["db_to_excel_written"] = write_count
    stats["excel_to_db_issues"] = len(e2db_issues)
    stats["backfills"] = backfill_count
    stats["orphan_clears"] = orphan_clear_count

    return stats


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Three-way reconciliation: Build One DB <-> QBO <-> SharePoint Excel"
    )
    parser.add_argument("--project-id", type=int, help="Target a single project by DB id")
    parser.add_argument(
        "--write", action="store_true",
        help="Apply all repairs (default: dry run)"
    )
    args = parser.parse_args()

    dry_run = not args.write
    target_project_id = args.project_id

    print("=" * 70)
    if dry_run:
        print("DRY RUN MODE — no changes will be made. Run with --write to apply.")
    else:
        print("WRITE MODE — repairs will be applied.")
    print("=" * 70)

    # ── Init repos and services ──────────────────────────────────
    vendor_repo = VendorRepository()
    bill_repo = BillRepository()
    bill_line_item_repo = BillLineItemRepository()
    expense_repo = ExpenseRepository()
    expense_line_item_repo = ExpenseLineItemRepository()
    bill_bill_repo = BillBillRepository()
    bill_line_item_bill_line_repo = BillLineItemBillLineRepository()
    qbo_bill_repo = QboBillRepository()
    qbo_bill_line_repo = QboBillLineRepository()
    qbo_purchase_repo = QboPurchaseRepository()
    qbo_purchase_line_repo = QboPurchaseLineRepository()
    purchase_expense_repo = PurchaseExpenseRepository()
    customer_project_repo = CustomerProjectRepository()
    qbo_customer_repo = QboCustomerRepository()
    auth_service = QboAuthService()
    excel_mapping_repo = DriveItemProjectExcelRepository()
    driveitem_repo = MsDriveItemRepository()
    drive_repo = MsDriveRepository()

    bill_service = BillService()
    expense_service = ExpenseService()
    bill_bill_connector = BillBillConnector()
    purchase_expense_connector = PurchaseExpenseConnector()

    # ── Load reference data ──────────────────────────────────────
    print("\nLoading reference data...")
    all_vendors = vendor_repo.read_all()
    vendor_map = {v.name.strip().lower(): v for v in all_vendors if v.name}
    vendor_norm_map: Dict[str, Any] = {}
    for v in all_vendors:
        if v.name:
            key = _normalize_vendor(v.name)
            if key not in vendor_norm_map:
                vendor_norm_map[key] = v
    print(f"  Loaded {len(vendor_map)} vendors")

    # ── Determine projects ───────────────────────────────────────
    if target_project_id:
        project_ids = [target_project_id]
    else:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT ProjectId FROM ms.DriveItemProjectExcel")
            project_ids = [row[0] for row in cursor.fetchall()]
            cursor.close()

    print(f"\nProjects to reconcile: {len(project_ids)}")

    # ── Process each project ─────────────────────────────────────
    totals = {
        "db_to_qbo_issues": 0,
        "qbo_repairs": 0,
        "db_pushes": 0,
        "qbo_to_db_synced": 0,
        "qbo_to_db_issues": 0,
        "db_to_excel_issues": 0,
        "db_to_excel_written": 0,
        "excel_to_db_issues": 0,
        "backfills": 0,
        "orphan_clears": 0,
    }

    for project_id in project_ids:
        print(f"\n{'=' * 70}")
        print(f"Project ID: {project_id}")

        stats = process_project(
            project_id=project_id,
            dry_run=dry_run,
            vendor_map=vendor_map,
            vendor_norm_map=vendor_norm_map,
            bill_repo=bill_repo,
            bill_line_item_repo=bill_line_item_repo,
            expense_repo=expense_repo,
            expense_line_item_repo=expense_line_item_repo,
            bill_bill_repo=bill_bill_repo,
            bill_line_item_bill_line_repo=bill_line_item_bill_line_repo,
            qbo_bill_repo=qbo_bill_repo,
            qbo_bill_line_repo=qbo_bill_line_repo,
            qbo_purchase_repo=qbo_purchase_repo,
            qbo_purchase_line_repo=qbo_purchase_line_repo,
            purchase_expense_repo=purchase_expense_repo,
            bill_bill_connector=bill_bill_connector,
            purchase_expense_connector=purchase_expense_connector,
            customer_project_repo=customer_project_repo,
            qbo_customer_repo=qbo_customer_repo,
            auth_service=auth_service,
            bill_service=bill_service,
            expense_service=expense_service,
            driveitem_repo=driveitem_repo,
            drive_repo=drive_repo,
            excel_mapping_repo=excel_mapping_repo,
        )

        for k in totals:
            totals[k] += stats[k]

        action = "would" if dry_run else "did"
        print(f"\n  Project summary:")
        print(f"    DB->QBO issues:          {stats['db_to_qbo_issues']}")
        print(f"    DB->QBO bill pushes:     {stats['db_pushes']} ({action} push)")
        print(f"    QBO line mappings:       {stats['qbo_repairs']} ({action} create)")
        print(f"    QBO->DB synced:          {stats['qbo_to_db_synced']} ({action} sync)")
        print(f"    QBO->DB issues:          {stats['qbo_to_db_issues']}")
        print(f"    DB->Excel issues:        {stats['db_to_excel_issues']}")
        print(f"    DB->Excel written:       {stats['db_to_excel_written']} ({action} write)")
        print(f"    Excel->DB issues:        {stats['excel_to_db_issues']}")
        print(f"    Col Z backfills:         {stats['backfills']} ({action} write)")
        print(f"    Col Z orphan clears:     {stats['orphan_clears']} ({action} clear)")

    print(f"\n{'=' * 70}")
    print(f"TOTAL SUMMARY {'(DRY RUN)' if dry_run else ''}")
    action = "would" if dry_run else "did"
    print(f"  DB->QBO issues:          {totals['db_to_qbo_issues']}")
    print(f"  DB->QBO bill pushes:     {totals['db_pushes']} ({action} push)")
    print(f"  QBO line mappings:       {totals['qbo_repairs']} ({action} create)")
    print(f"  QBO->DB synced:          {totals['qbo_to_db_synced']} ({action} sync)")
    print(f"  QBO->DB issues:          {totals['qbo_to_db_issues']}")
    print(f"  DB->Excel issues:        {totals['db_to_excel_issues']}")
    print(f"  DB->Excel written:       {totals['db_to_excel_written']} ({action} write)")
    print(f"  Excel->DB issues:        {totals['excel_to_db_issues']}")
    print(f"  Col Z backfills:         {totals['backfills']} ({action} write)")
    print(f"  Col Z orphan clears:     {totals['orphan_clears']} ({action} clear)")

    if dry_run:
        any_action = any(
            totals[k] > 0 for k in [
                "db_pushes", "qbo_repairs", "qbo_to_db_synced",
                "db_to_excel_written", "backfills", "orphan_clears",
            ]
        )
        if any_action:
            print(f"\nTo apply all repairs, run with --write:")
            if target_project_id:
                print(f"  python scripts/reconcile_project.py --write --project-id {target_project_id}")
            else:
                print(f"  python scripts/reconcile_project.py --write")


if __name__ == "__main__":
    main()
