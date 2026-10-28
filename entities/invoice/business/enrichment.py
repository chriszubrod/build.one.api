import logging
from decimal import Decimal

from shared.database import get_connection

logger = logging.getLogger(__name__)

_KNOWN_SOURCE_TYPES = {"BillLineItem", "ExpenseLineItem", "BillCreditLineItem", "EmployeeLaborLineItem", "Manual"}


def _is_manual(li) -> bool:
    """A line with no source parent. ``None`` counts: legacy rows predate the
    column being populated, and the resolution loop below already treats the two
    identically. One definition — collection and resolution must not drift, or a
    documented line is either looked up and never applied, or never collected and
    silently dropped from the packet again."""
    return getattr(li, "source_type", None) in (None, "Manual") and getattr(li, "id", None) is not None


def enrich_line_items(line_items) -> list[dict]:
    """
    Batch-enrich invoice line items with parent number, vendor, cost code,
    and attachment indicator in a single DB round-trip per source type.
    """
    bill_ids = []
    expense_ids = []
    credit_ids = []
    employee_labor_ids = []
    # Manual lines have no source parent to inherit a document from, so their
    # evidence (when they have any) hangs off the invoice line itself. Collect
    # them for the one extra lookup below.
    manual_ids = []
    manual_invoice_ids = set()
    for li in line_items:
        if _is_manual(li):
            manual_ids.append(li.id)
            if getattr(li, "invoice_id", None) is not None:
                manual_invoice_ids.add(li.invoice_id)
        if li.source_type and li.source_type not in _KNOWN_SOURCE_TYPES:
            logger.warning(
                "enrich_line_items: unknown source_type '%s' on InvoiceLineItem id=%s — line will be dropped from PDF packet",
                li.source_type,
                getattr(li, "id", "?"),
            )
        if li.source_type == "BillLineItem" and li.bill_line_item_id:
            bill_ids.append(li.bill_line_item_id)
        elif li.source_type == "ExpenseLineItem" and li.expense_line_item_id:
            expense_ids.append(li.expense_line_item_id)
        elif li.source_type == "BillCreditLineItem" and li.bill_credit_line_item_id:
            credit_ids.append(li.bill_credit_line_item_id)
        elif (
            li.source_type == "EmployeeLaborLineItem"
            and getattr(li, "employee_labor_line_item_id", None)
        ):
            employee_labor_ids.append(li.employee_labor_line_item_id)

    bill_map = {}
    manual_map = {}
    expense_map = {}
    credit_map = {}
    employee_labor_map = {}

    with get_connection() as conn:
        cursor = conn.cursor()

        if bill_ids:
            placeholders = ",".join("?" * len(bill_ids))
            cursor.execute(f"""
                SELECT bli.Id,
                       bli.PublicId AS SourceLinePublicId,
                       b.BillNumber AS ParentNumber,
                       b.PublicId AS ParentPublicId,
                       b.BillDate AS SourceDate,
                       v.Name AS VendorName,
                       scc.Number AS SccNumber, scc.Name AS SccName,
                       cc.Number AS CcNumber, cc.Name AS CcName,
                       bli.Price AS BilledPrice,
                       att_first.PublicId AS AttachmentPublicId
                FROM dbo.BillLineItem bli
                JOIN dbo.Bill b ON b.Id = bli.BillId
                LEFT JOIN dbo.Vendor v ON v.Id = b.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = bli.SubCostCodeId
                LEFT JOIN dbo.CostCode cc ON cc.Id = scc.CostCodeId
                OUTER APPLY (
                    SELECT TOP 1 a.PublicId
                    FROM dbo.BillLineItemAttachment blia
                    JOIN dbo.Attachment a ON a.Id = blia.AttachmentId
                    WHERE blia.BillLineItemId = bli.Id
                    ORDER BY a.Id
                ) att_first
                WHERE bli.Id IN ({placeholders})
            """, bill_ids)
            for row in cursor.fetchall():
                bill_map[row.Id] = {
                    "source_line_public_id": str(row.SourceLinePublicId) if row.SourceLinePublicId else "",
                    "parent_number": row.ParentNumber or "",
                    "parent_public_id": str(row.ParentPublicId) if row.ParentPublicId else "",
                    "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                    "vendor_name": row.VendorName or "",
                    "sub_cost_code_number": row.SccNumber or "",
                    "sub_cost_code_name": row.SccName or "",
                    "cost_code_number": row.CcNumber or "",
                    "cost_code_name": row.CcName or "",
                    # Client-billed (marked-up) price of the SOURCE line. The
                    # TOC prefers this over the InvoiceLineItem's own Price/Amount
                    # because markup is split into separate Manual lines on the
                    # QBO invoice (excluded from the TOC) — so the ILI carries
                    # only the un-marked-up base. bli.Price = Amount + Markup,
                    # matching what the Bill's own detail page shows.
                    "billed_price": float(row.BilledPrice) if row.BilledPrice is not None else None,
                    "attachment_public_id": str(row.AttachmentPublicId) if row.AttachmentPublicId else "",
                }

        if expense_ids:
            placeholders = ",".join("?" * len(expense_ids))
            cursor.execute(f"""
                SELECT eli.Id,
                       eli.PublicId AS SourceLinePublicId,
                       e.ReferenceNumber AS ParentNumber,
                       e.PublicId AS ParentPublicId,
                       e.ExpenseDate AS SourceDate,
                       e.IsCredit AS IsCredit,
                       v.Name AS VendorName,
                       scc.Number AS SccNumber, scc.Name AS SccName,
                       cc.Number AS CcNumber, cc.Name AS CcName,
                       eli.Price AS BilledPrice,
                       att_first.PublicId AS AttachmentPublicId
                FROM dbo.ExpenseLineItem eli
                JOIN dbo.Expense e ON e.Id = eli.ExpenseId
                LEFT JOIN dbo.Vendor v ON v.Id = e.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = eli.SubCostCodeId
                LEFT JOIN dbo.CostCode cc ON cc.Id = scc.CostCodeId
                OUTER APPLY (
                    SELECT TOP 1 a.PublicId
                    FROM dbo.ExpenseLineItemAttachment elia
                    JOIN dbo.Attachment a ON a.Id = elia.AttachmentId
                    WHERE elia.ExpenseLineItemId = eli.Id
                    ORDER BY a.Id
                ) att_first
                WHERE eli.Id IN ({placeholders})
            """, expense_ids)
            for row in cursor.fetchall():
                expense_map[row.Id] = {
                    "source_line_public_id": str(row.SourceLinePublicId) if row.SourceLinePublicId else "",
                    "parent_number": row.ParentNumber or "",
                    "parent_public_id": str(row.ParentPublicId) if row.ParentPublicId else "",
                    "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                    "vendor_name": row.VendorName or "",
                    "sub_cost_code_number": row.SccNumber or "",
                    "sub_cost_code_name": row.SccName or "",
                    "cost_code_number": row.CcNumber or "",
                    "cost_code_name": row.CcName or "",
                    # See BillLineItem note above — eli.Price = Amount + Markup,
                    # the client-billed price the TOC should display.
                    "billed_price": float(row.BilledPrice) if row.BilledPrice is not None else None,
                    "attachment_public_id": str(row.AttachmentPublicId) if row.AttachmentPublicId else "",
                    "is_credit": bool(row.IsCredit),
                }

        if credit_ids:
            placeholders = ",".join("?" * len(credit_ids))
            cursor.execute(f"""
                SELECT bcli.Id,
                       bcli.PublicId AS SourceLinePublicId,
                       bc.CreditNumber AS ParentNumber,
                       bc.PublicId AS ParentPublicId,
                       bc.CreditDate AS SourceDate,
                       v.Name AS VendorName,
                       scc.Number AS SccNumber, scc.Name AS SccName,
                       cc.Number AS CcNumber, cc.Name AS CcName,
                       att_first.PublicId AS AttachmentPublicId
                FROM dbo.BillCreditLineItem bcli
                JOIN dbo.BillCredit bc ON bc.Id = bcli.BillCreditId
                LEFT JOIN dbo.Vendor v ON v.Id = bc.VendorId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = bcli.SubCostCodeId
                LEFT JOIN dbo.CostCode cc ON cc.Id = scc.CostCodeId
                OUTER APPLY (
                    SELECT TOP 1 a.PublicId
                    FROM dbo.BillCreditLineItemAttachment bclia
                    JOIN dbo.Attachment a ON a.Id = bclia.AttachmentId
                    WHERE bclia.BillCreditLineItemId = bcli.Id
                    ORDER BY a.Id
                ) att_first
                WHERE bcli.Id IN ({placeholders})
            """, credit_ids)
            for row in cursor.fetchall():
                credit_map[row.Id] = {
                    "source_line_public_id": str(row.SourceLinePublicId) if row.SourceLinePublicId else "",
                    "parent_number": row.ParentNumber or "",
                    "parent_public_id": str(row.ParentPublicId) if row.ParentPublicId else "",
                    "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                    "vendor_name": row.VendorName or "",
                    "sub_cost_code_number": row.SccNumber or "",
                    "sub_cost_code_name": row.SccName or "",
                    "cost_code_number": row.CcNumber or "",
                    "cost_code_name": row.CcName or "",
                    "attachment_public_id": str(row.AttachmentPublicId) if row.AttachmentPublicId else "",
                }

        if employee_labor_ids:
            # EmployeeLabor source — no Vendor (Employee is the "party providing the
            # labor"), no Attachment (EmployeeLabor has no source PDF — invoice
            # packet renders it via the TOC only, no detail page).
            placeholders = ",".join("?" * len(employee_labor_ids))
            cursor.execute(f"""
                SELECT elli.Id,
                       elli.PublicId AS SourceLinePublicId,
                       el.PublicId AS ParentPublicId,
                       el.WorkDate AS SourceDate,
                       e.Firstname + ' ' + e.Lastname AS VendorName,
                       scc.Number AS SccNumber, scc.Name AS SccName,
                       cc.Number AS CcNumber, cc.Name AS CcName
                FROM dbo.EmployeeLaborLineItem elli
                JOIN dbo.EmployeeLabor el ON el.Id = elli.EmployeeLaborId
                LEFT JOIN dbo.Employee e ON e.Id = el.EmployeeId
                LEFT JOIN dbo.SubCostCode scc ON scc.Id = elli.SubCostCodeId
                LEFT JOIN dbo.CostCode cc ON cc.Id = scc.CostCodeId
                WHERE elli.Id IN ({placeholders})
            """, employee_labor_ids)
            for row in cursor.fetchall():
                employee_labor_map[row.Id] = {
                    # No parent_number — EmployeeLabor doesn't have a human-facing
                    # ID like Bill.BillNumber. Falls back to "" so the TOC renders
                    # a blank cell rather than failing.
                    "parent_number": "",
                    "source_line_public_id": str(row.SourceLinePublicId) if row.SourceLinePublicId else "",
                    "parent_public_id": str(row.ParentPublicId) if row.ParentPublicId else "",
                    "source_date": row.SourceDate.strftime("%m-%d-%Y") if row.SourceDate else "",
                    "vendor_name": row.VendorName or "",
                    "sub_cost_code_number": row.SccNumber or "",
                    "sub_cost_code_name": row.SccName or "",
                    "cost_code_number": row.CcNumber or "",
                    "cost_code_name": row.CcName or "",
                    "attachment_public_id": "",
                }

        if manual_ids and manual_invoice_ids:
            placeholders = ",".join("?" * len(manual_ids))
            # Belt: re-assert the invoice, the house pattern for this table
            # (`InvoiceService._collect_line_attachment_rows`). Redundant while the
            # ids come from one invoice's lines, load-bearing the day they don't.
            inv_placeholders = ",".join("?" * len(manual_invoice_ids))
            cursor.execute(f"""
                SELECT ila.InvoiceLineItemId AS Id, a.PublicId AS AttachmentPublicId
                FROM dbo.InvoiceLineItemAttachment ila
                JOIN dbo.Attachment a ON a.Id = ila.AttachmentId
                JOIN dbo.InvoiceLineItem ili ON ili.Id = ila.InvoiceLineItemId
                WHERE ila.InvoiceLineItemId IN ({placeholders})
                  AND ili.InvoiceId IN ({inv_placeholders})
                ORDER BY a.Id
            """, manual_ids + sorted(manual_invoice_ids))
            for row in cursor.fetchall():
                # setdefault + ORDER BY a.Id = first-by-Id wins, the same tie-break
                # the sibling branches pin with `TOP 1 ... ORDER BY a.Id`. The link
                # table has no per-line uniqueness constraint, so without this the
                # merged page for a twice-linked line varies between runs.
                manual_map.setdefault(row.Id, {
                    "attachment_public_id": str(row.AttachmentPublicId) if row.AttachmentPublicId else "",
                })

        cursor.close()

    results = []
    for li in line_items:
        li_dict = li.to_dict()
        for key, value in li_dict.items():
            if isinstance(value, Decimal):
                li_dict[key] = float(value)

        enrichment = {}
        if li.source_type == "BillLineItem" and li.bill_line_item_id:
            enrichment = bill_map.get(li.bill_line_item_id, {})
        elif li.source_type == "ExpenseLineItem" and li.expense_line_item_id:
            enrichment = expense_map.get(li.expense_line_item_id, {})
        elif li.source_type == "BillCreditLineItem" and li.bill_credit_line_item_id:
            enrichment = credit_map.get(li.bill_credit_line_item_id, {})
        elif li.source_type == "EmployeeLaborLineItem" and getattr(li, "employee_labor_line_item_id", None):
            enrichment = employee_labor_map.get(li.employee_labor_line_item_id, {})
        elif _is_manual(li):
            # Only the document — a Manual line has no parent to borrow a
            # vendor, cost code or parent number from; those stay blank.
            enrichment = manual_map.get(li.id, {})

        if li.source_type != "Manual" and li.source_type is not None:
            has_fk = (
                li.bill_line_item_id
                or li.expense_line_item_id
                or li.bill_credit_line_item_id
                or getattr(li, "employee_labor_line_item_id", None)
            )
            if not has_fk or not enrichment:
                continue

        li_dict.setdefault("parent_number", "")
        li_dict.setdefault("parent_public_id", "")
        li_dict.setdefault("source_date", "")
        li_dict.setdefault("vendor_name", "")
        li_dict.setdefault("sub_cost_code_number", "")
        li_dict.setdefault("sub_cost_code_name", "")
        li_dict.setdefault("cost_code_number", "")
        li_dict.setdefault("cost_code_name", "")
        # Sources without a marked-up Price column (BillCredit, EmployeeLabor)
        # leave this None → the TOC falls back to the ILI's own Price/Amount.
        li_dict.setdefault("billed_price", None)
        li_dict.setdefault("attachment_public_id", "")
        li_dict.update(enrichment)

        results.append(li_dict)

    return results
