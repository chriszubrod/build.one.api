# Python Standard Library Imports
import logging
from decimal import Decimal
from typing import Any, List

# Local Imports
# All entity/service imports are lazy (inside build_details_rows) so importing
# this module — and the outbox worker that dispatches to it — does not pull in
# the full entity stack at import time.

logger = logging.getLogger(__name__)


# Type label per entity, mirroring each entity's existing
# sync_to_excel_workbook column-M value.
#   Bill        -> "Bill"
#   BillCredit  -> "Credit"
#   Expense     -> "Expense" / "Expense Credit" (is_credit) — resolved per-row
ENTITY_TYPES = ("bill", "expense", "bill_credit")

DETAILS_ROW_WIDTH = 26


def _empty_row() -> List[Any]:
    """A 26-element DETAILS row pre-filled with empty cells (None)."""
    return [None] * DETAILS_ROW_WIDTH


def _cost_code_from_subcostcode(sub_cost_code_number: str) -> str:
    """CostCode (col B) = SubCostCode number's first segment split on '.'."""
    if not sub_cost_code_number:
        return ""
    return sub_cost_code_number.split(".")[0] if "." in sub_cost_code_number else sub_cost_code_number


def _decimal_or_zero(value: Any) -> Decimal:
    """Decimal-safe coercion — never float(). None → Decimal('0')."""
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def build_details_rows(entity_type: str, entity_public_id: str) -> List[List[Any]]:
    """
    Re-fetch the entity + its line items by public_id at drain time and build
    one 26-col DETAILS row per line item (Z = line_item.public_id), mirroring
    each entity's existing sync_to_excel_workbook row build.

    The drain handler calls this fresh (not from the enqueue-time snapshot) so
    the rows always reflect the current DB state — the same discipline as the
    MS Graph Excel path, which re-reads the entity inside sync_to_excel_workbook.

    Row shape (26 cols, A..Z): see workbook_editor.py. Per entity:
      - Bill:       I=bill_date, J=vendor, K=bill_number, L=desc,
                    M="Bill", N=line.price
      - Expense:    I=expense_date, J=vendor, K=reference_number, L=desc,
                    M="Expense"/"Expense Credit", N=line.price
      - BillCredit: I=credit_date, J=vendor, K=credit_number, L=desc,
                    M="Credit", N=line.amount  (no `price` field on credits)

    CostCode (col B) = SubCostCode number split on '.' first segment. Price/
    amount via Decimal(str(...)) — never float. Line items with no
    sub_cost_code_id are still included with empty B/C (matches the MS path,
    which groups under a None SubCostCode and appends at end).

    Returns a list of 26-element rows (may be empty if the entity has no line
    items / was deleted).
    """
    et = (entity_type or "").strip().lower()
    if et not in ENTITY_TYPES:
        raise ValueError(
            f"build_details_rows: unsupported entity_type {entity_type!r} "
            f"(expected one of {ENTITY_TYPES})"
        )

    # Lazy imports — keep module + worker import light.
    from entities.vendor.business.service import VendorService
    from entities.sub_cost_code.business.service import SubCostCodeService

    vendor_service = VendorService()
    sub_cost_code_service = SubCostCodeService()

    # Per-SubCostCode number cache so we don't re-read the same SCC for every
    # line item in a multi-line entity.
    scc_number_cache: dict = {}

    def _scc_number(sub_cost_code_id) -> str:
        if not sub_cost_code_id:
            return ""
        if sub_cost_code_id in scc_number_cache:
            return scc_number_cache[sub_cost_code_id]
        scc = sub_cost_code_service.read_by_id(id=str(sub_cost_code_id))
        number = (scc.number or "") if scc else ""
        scc_number_cache[sub_cost_code_id] = number
        return number

    if et == "bill":
        from entities.bill.business.service import BillService
        from entities.bill_line_item.business.service import BillLineItemService

        bill = BillService().read_by_public_id(public_id=entity_public_id)
        if not bill or not bill.id:
            logger.info(
                "box.excel.row_builder.entity_missing",
                extra={"event_name": "box.excel.row_builder.entity_missing",
                       "entity_type": et, "entity_public_id": entity_public_id},
            )
            return []
        vendor = vendor_service.read_by_id(id=bill.vendor_id) if bill.vendor_id else None
        vendor_name = (vendor.name or "") if vendor else ""
        line_items = BillLineItemService().read_by_bill_id(bill_id=bill.id)

        rows: List[List[Any]] = []
        for li in line_items:
            scc_number = _scc_number(li.sub_cost_code_id)
            row = _empty_row()
            row[1] = _cost_code_from_subcostcode(scc_number)            # B
            row[2] = scc_number                                          # C
            row[8] = bill.bill_date[:10] if bill.bill_date else ""       # I
            row[9] = vendor_name                                         # J
            row[10] = bill.bill_number or ""                            # K
            row[11] = li.description or ""                              # L
            row[12] = "Bill"                                            # M
            row[13] = _decimal_or_zero(li.price)                       # N
            row[25] = str(li.public_id) if li.public_id else ""        # Z
            rows.append(row)
        return rows

    if et == "expense":
        from entities.expense.business.service import ExpenseService
        from entities.expense_line_item.business.service import ExpenseLineItemService

        expense = ExpenseService().read_by_public_id(public_id=entity_public_id)
        if not expense or not expense.id:
            logger.info(
                "box.excel.row_builder.entity_missing",
                extra={"event_name": "box.excel.row_builder.entity_missing",
                       "entity_type": et, "entity_public_id": entity_public_id},
            )
            return []
        vendor = vendor_service.read_by_id(id=expense.vendor_id) if expense.vendor_id else None
        vendor_name = (vendor.name or "") if vendor else ""
        type_label = "Expense Credit" if getattr(expense, "is_credit", False) else "Expense"
        line_items = ExpenseLineItemService().read_by_expense_id(expense_id=expense.id)

        rows = []
        for li in line_items:
            scc_number = _scc_number(li.sub_cost_code_id)
            row = _empty_row()
            row[1] = _cost_code_from_subcostcode(scc_number)            # B
            row[2] = scc_number                                          # C
            row[8] = expense.expense_date[:10] if expense.expense_date else ""  # I
            row[9] = vendor_name                                         # J
            row[10] = expense.reference_number or ""                    # K
            row[11] = li.description or ""                              # L
            row[12] = type_label                                        # M
            row[13] = _decimal_or_zero(li.price)                       # N
            row[25] = str(li.public_id) if li.public_id else ""        # Z
            rows.append(row)
        return rows

    # et == "bill_credit"
    from entities.bill_credit.business.service import BillCreditService
    from entities.bill_credit_line_item.business.service import BillCreditLineItemService

    bill_credit = BillCreditService().read_by_public_id(public_id=entity_public_id)
    if not bill_credit or not bill_credit.id:
        logger.info(
            "box.excel.row_builder.entity_missing",
            extra={"event_name": "box.excel.row_builder.entity_missing",
                   "entity_type": et, "entity_public_id": entity_public_id},
        )
        return []
    vendor = vendor_service.read_by_id(id=bill_credit.vendor_id) if bill_credit.vendor_id else None
    vendor_name = (vendor.name or "") if vendor else ""
    line_items = BillCreditLineItemService().read_by_bill_credit_id(bill_credit_id=bill_credit.id)

    rows = []
    for li in line_items:
        scc_number = _scc_number(li.sub_cost_code_id)
        row = _empty_row()
        row[1] = _cost_code_from_subcostcode(scc_number)               # B
        row[2] = scc_number                                             # C
        row[8] = bill_credit.credit_date[:10] if bill_credit.credit_date else ""  # I
        row[9] = vendor_name                                            # J
        row[10] = bill_credit.credit_number or ""                      # K
        row[11] = li.description or ""                                 # L
        row[12] = "Credit"                                            # M
        # BillCreditLineItem has no `price` field — col N carries `amount`
        # (verbatim from BillCreditCompleteService.sync_to_excel_workbook).
        row[13] = _decimal_or_zero(li.amount)                         # N
        row[25] = str(li.public_id) if li.public_id else ""           # Z
        rows.append(row)
    return rows
