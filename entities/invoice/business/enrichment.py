from decimal import Decimal

from shared.database import get_connection


def enrich_line_items(line_items) -> list[dict]:
    """
    Batch-enrich invoice line items with parent number, vendor, cost code,
    and attachment indicator in a single DB round-trip per source type.
    """
    bill_ids = []
    expense_ids = []
    credit_ids = []
    for li in line_items:
        if li.source_type == "BillLineItem" and li.bill_line_item_id:
            bill_ids.append(li.bill_line_item_id)
        elif li.source_type == "ExpenseLineItem" and li.expense_line_item_id:
            expense_ids.append(li.expense_line_item_id)
        elif li.source_type == "BillCreditLineItem" and li.bill_credit_line_item_id:
            credit_ids.append(li.bill_credit_line_item_id)

    bill_map = {}
    expense_map = {}
    credit_map = {}

    with get_connection() as conn:
        cursor = conn.cursor()

        if bill_ids:
            placeholders = ",".join("?" * len(bill_ids))
            cursor.execute(f"""
                SELECT bli.Id,
                       b.BillNumber AS ParentNumber,
                       b.PublicId AS ParentPublicId,
                       b.BillDate AS SourceDate,
                       v.Name AS VendorName,
                       scc.Number AS SccNumber, scc.Name AS SccName,
                       cc.Number AS CcNumber, cc.Name AS CcName,
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

        if expense_ids:
            placeholders = ",".join("?" * len(expense_ids))
            cursor.execute(f"""
                SELECT eli.Id,
                       e.ReferenceNumber AS ParentNumber,
                       e.PublicId AS ParentPublicId,
                       e.ExpenseDate AS SourceDate,
                       v.Name AS VendorName,
                       scc.Number AS SccNumber, scc.Name AS SccName,
                       cc.Number AS CcNumber, cc.Name AS CcName,
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

        if credit_ids:
            placeholders = ",".join("?" * len(credit_ids))
            cursor.execute(f"""
                SELECT bcli.Id,
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

        if li.source_type != "Manual" and li.source_type is not None:
            has_fk = li.bill_line_item_id or li.expense_line_item_id or li.bill_credit_line_item_id
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
        li_dict.setdefault("attachment_public_id", "")
        li_dict.update(enrichment)

        results.append(li_dict)

    return results
