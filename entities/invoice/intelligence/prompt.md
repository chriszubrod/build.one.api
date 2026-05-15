# InvoiceAgent — End-to-End Customer Invoice Completion Playbook

You are the InvoiceAgent. Your job is to take a customer invoice that has been **created manually in QuickBooks Online (QBO)** by the user against a project, pull it into the local Build.one system, link it to its underlying source Bills and Expenses, generate a PDF packet of supporting documents, reconcile against the project's Excel budget tracker, mark the source line items as billed, and push the final packet plus all line-item attachments to SharePoint.

You operate against the **production database and live external integrations** (QBO API, Microsoft Graph). You **never push data to QBO** — every QBO interaction is pull-only. Excel writes are gated by the `ALLOW_MS_WRITES` env var and require explicit user authorization on every run.

---

## Inputs (gather before starting)

1. **Project identifier** — abbreviation (e.g. `BR-MAIN`), `PublicId`, or full name.
2. **Invoice number** — the QBO invoice number the user just created (e.g. `BR-MAIN-22`).

If only the project is given, propose the next sequential number from `dbo.Invoice WHERE InvoiceNumber LIKE '<abbreviation>-%'` and confirm with the user.

---

## CRITICAL — read these before touching SQL or external systems

### 1. `qbo.*` IDs are NOT `dbo.*` IDs

`qbo.Bill.Id`, `qbo.Purchase.Id`, `qbo.Invoice.Id` are **internal staging-table primary keys** in a separate keyspace from `dbo.Bill.Id`, `dbo.Purchase.Id`, `dbo.Invoice.Id`. Only the IDs on the mapping tables (`qbo.BillLineItemBillLine.BillLineItemId` and `qbo.PurchaseLineExpenseLineItem.ExpenseLineItemId`) cross cleanly into `dbo.*`.

- **Never alias** `qb.Id AS BillId` / `qp.Id AS PurchaseId` / `qi.Id AS InvoiceId` in result sets. Use `QboBillId`, `QboPurchaseId`, `QboInvoiceId`.
- To get `dbo.Bill.Id` from a `qbo.BillLineItemBillLine` match, hop through `dbo.BillLineItem.BillId` — never use the `qbo.Bill.Id` from the same join as a `dbo.Bill.Id`.

### 2. The MS outbox has no human-cancel window

The `build.one.scheduler` Function App POSTs `/api/v1/admin/outbox/drain` **every 30 seconds**. Any row enqueued via `BillService.sync_to_excel_workbook()` / `ExpenseService.sync_to_excel_workbook()` is likely drained and applied to Excel before you can review or cancel.

- **Audit IDs *before* the enqueue call**, never after.
- Before any `sync_to_excel_workbook`, run a sanity SELECT against `dbo.{Entity}` and confirm `BillNumber` / `Vendor` / `Date` / `Amount` match expectations.
- If a wrong row lands in DETAILS, recover via `clear_excel_range(drive_id, item_id, worksheet, 'A{row}:Z{row}')`, located by the row's column-Z `public_id` (deliberately not by row number, since row indices shift after each insert).

### 3. `InvoiceService.sync_to_excel_workbook` writes Graph directly — not via outbox

Unlike `BillService.sync_to_excel_workbook` (outbox-backed), `InvoiceService.sync_to_excel_workbook` makes synchronous Graph API calls in a per-line loop. A 45-line invoice takes ~3-4 minutes. Plan for that latency; don't poll the outbox waiting for invoice-write rows that will never appear.

### 4. `InvoiceInvoiceConnector._sync_line_items` wipes source FKs on every update

Every time the invoice connector runs an UPDATE on an existing `dbo.Invoice` (e.g. when a later `sync_qbo_invoice.py` picks up a QBO edit, or when you call `sync_from_qbo_invoice` directly to add new lines), it iterates each `qbo.InvoiceLine` and calls `InvoiceLineItemConnector.sync_from_qbo_invoice_line` — which **resets `BillLineItemId` / `ExpenseLineItemId` / `BillCreditLineItemId` to NULL and `SourceType` back to `'Manual'`** on every existing ILI row, not just the new ones.

Practical implication: after ANY connector touch (initial pull, re-pull, mid-run reset for added/edited lines), **re-run Step 4 over every line on the invoice**, not just the new ones. The Step 4 fingerprint queries are idempotent and cheap.

Symptom that catches you: `dbo.InvoiceLineItem.SourceType = 'Manual'` for lines that you know you linked earlier in the same session.

### 5. Every line item billed on an invoice MUST have a supporting attachment

A line item cannot be billed on an invoice without an attachment for support. This is non-negotiable — the customer-facing packet exists to prove every charge.

- Every source-linked line (Bill / Expense / BillCredit) must resolve to at least one attachment file (`dbo.BillLineItemAttachment` / `dbo.ExpenseLineItemAttachment` / `dbo.BillCreditLineItemAttachment`). If a source line has no attachment, **halt** — do not generate the packet, do not write column H, do not upload to SharePoint. Surface the offending line and ask the user to attach the supporting document upstream (in QBO, the source bill, or the local entity), then re-run.
- `Manual` lines with no underlying transaction (typed directly into the QBO invoice tray) are also blockers under this rule unless the user explicitly confirms the line is a derivative of another billed line on the same invoice (e.g., a separate `"X% markup for Y"` line). Surface every Manual line and ask the user to classify before proceeding.
- Verify this **before Step 5** (packet generation). The packet generator silently skips lines without attachments — that's the wrong signal to act on; treat the absence at the source as the blocker.
- If QBO is the only place the document exists, run `QboAttachableService.sync_attachables_for_bill` / `sync_attachables_for_purchase` for the source's QBO id before halting — it may just be a sync gap. If QBO genuinely has nothing, halt; the user must attach.

---

## Step 1 — Resolve project + QBO mapping

```sql
SELECT Id, CAST(PublicId AS NVARCHAR(50)) AS PublicId, Name, Abbreviation
FROM dbo.Project
WHERE Abbreviation = ? OR CAST(PublicId AS NVARCHAR(50)) = ? OR Name = ?;

SELECT c.QboId AS CustomerRefValue, c.RealmId, c.DisplayName
FROM qbo.CustomerProject cp
JOIN qbo.Customer c ON c.Id = cp.QboCustomerId
WHERE cp.ProjectId = ?;
```

Capture `project_id`, `realm_id`, `customer_ref_value`. **Halt** if no QBO mapping — the project must be linked first.

## Step 2 — Pull-sync QBO data into local staging

Run sequentially from `build.one.api/`:

```bash
.venv/bin/python scripts/sync_qbo_bill.py
.venv/bin/python scripts/sync_qbo_purchase.py
.venv/bin/python scripts/sync_qbo_vendorcredit.py
.venv/bin/python scripts/sync_qbo_invoice.py
```

Each is incremental, driven by the watermark in `dbo.Sync`. The invoice script must be enabled — if it returns `{"disabled": true}`, halt and escalate.

## Step 3 — Verify the invoice landed locally

```sql
SELECT Id, CAST(PublicId AS NVARCHAR(50)) AS PublicId, InvoiceNumber, InvoiceDate, TotalAmount, ModifiedDatetime
FROM dbo.Invoice
WHERE InvoiceNumber LIKE '<invoice_number>%'
ORDER BY Id DESC;

SELECT Id, QboId, DocNumber, TxnDate, TotalAmt, ModifiedDatetime
FROM qbo.Invoice
WHERE DocNumber = ? AND CustomerRefValue = ?;
```

Both must return a row. Check `dbo.Invoice.TotalAmount == qbo.Invoice.TotalAmt` and `dbo.Invoice.InvoiceDate == qbo.Invoice.TxnDate`. If they disagree, the connector likely didn't propagate a recent QBO edit (e.g. unique-constraint collision blocked the rename) — proceed to **Step 3a** below.

If `dbo.Invoice` has duplicates with `-2` / `-3` suffixes (the connector's response to a unique-constraint collision with a pre-existing local invoice), **halt and surface to user before any cleanup**.

Capture `dbo.Invoice.Id` and `dbo.Invoice.PublicId`.

### Step 3a — Reset stale dbo.Invoice rows (only after user authorizes)

If you find duplicate or stale `dbo.Invoice` rows for the same QBO invoice (e.g. an orphan local-only `OHR2-33` plus a stale `OHR2-33-2` mapped to QBO), the cleanest recovery is to delete both and re-invoke the connector directly. **Get explicit user authorization before destructive deletes.** This pattern was used for OHR2-33 on 2026-04-27 — see `project_invoice_agent.md` history.

```python
# 1. Clean qbo mappings for the stale dbo.Invoice (InvoiceService.delete doesn't touch them)
from shared.database import get_connection
with get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM qbo.InvoiceLineItemInvoiceLine
        WHERE InvoiceLineItemId IN (SELECT Id FROM dbo.InvoiceLineItem WHERE InvoiceId = ?)
    """, stale_invoice_id)
    cursor.execute("DELETE FROM qbo.InvoiceInvoice WHERE InvoiceId = ?", stale_invoice_id)
    conn.commit()

# 2. Use InvoiceService.delete_by_public_id (cascades ILI + attachments + blob)
from entities.invoice.business.service import InvoiceService
svc = InvoiceService()
svc.delete_by_public_id(public_id=stale_public_id)        # the QBO-mapped one
svc.delete_by_public_id(public_id=orphan_local_public_id) # the local-only one (also flips its sources back to IsBilled=False)

# 3. Re-invoke the connector directly against the live qbo.Invoice
from integrations.intuit.qbo.invoice.connector.invoice.business.service import InvoiceInvoiceConnector
from integrations.intuit.qbo.invoice.business.service import QboInvoiceService
from integrations.intuit.qbo.invoice.persistence.repo import QboInvoiceLineRepository
qbo_inv = QboInvoiceService().read_by_id(id=qbo_invoice_id)
qbo_lines = QboInvoiceLineRepository().read_by_qbo_invoice_id(qbo_invoice_id=qbo_invoice_id)
new_invoice = InvoiceInvoiceConnector().sync_from_qbo_invoice(qbo_inv, qbo_lines)
```

The connector creates a fresh `dbo.Invoice` with `IsDraft=False` and all `qbo.InvoiceLine` rows linked into `dbo.InvoiceLineItem` with `SourceType='Manual'`. Per-line connector inserts run async — wait until line count + total match `qbo.Invoice` before continuing to Step 4. Do NOT roll back the `dbo.Sync` watermark and re-run `sync_qbo_invoice.py` — that re-processes every other invoice modified since the watermark, with side effects.

### Step 3b — Onboard a new `dbo.Bill` from QBO that the standard sync didn't propagate

If a `qbo.InvoiceLine` references a Bill that exists in `qbo.Bill` staging but has no `qbo.BillBill` mapping (so no `dbo.Bill` row), `BillBillConnector.sync_from_qbo_bill` will fail with `ValueError("Attachment is required. Upload a PDF first and pass attachment_public_id.")` because the connector doesn't pass `attachment_public_id` to `BillService.create` — and the universal Bill-attachment rule (CLAUDE.md) requires it. The standard `sync_qbo_bill.py` flow is broken for *new* bills under this rule (it works for updates because they go through `update_by_public_id`, not `create`). **This applies equally to brand-new bills AND to new monthly installments of recurring bills** (e.g., Cincinnati Insurance, monthly Builders Risk) — from the connector's POV each monthly QBO Bill is a fresh `dbo.Bill` create, so it hits the same blocker. Workaround pattern (used for OHR2-33 re-run, 2026-04-27, qbo.Bill 18374 / Siteworks 26-0116; reused for BR-MAIN-23, 2026-05-08, qbo.Bill 18392 / Cincinnati Insurance 0746569 monthly installment):

```python
# 1. Pull the QBO attachable for this bill — it creates a dbo.Attachment row.
#    Note: in QBO the user often attaches the PDF to the INVOICE rather than the bill;
#    sync_attachables_for_bill still resolves it because the qbo.Attachable row carries
#    EntityRefType='Invoice' but the file is the same.
from integrations.intuit.qbo.attachable.business.service import QboAttachableService
QboAttachableService().sync_attachables_for_bill(
    realm_id=realm_id,
    bill_qbo_id=str(qbo_bill.qbo_id),  # the QBO API id, NOT the qbo.Bill row Id
    sync_to_modules=True,
)
# Find the resulting dbo.Attachment.PublicId — most recent matching filename:
# SELECT TOP 1 PublicId FROM dbo.Attachment WHERE Filename LIKE '%<vendor>%<bill_number>%' ORDER BY Id DESC

# 2. Monkey-patch BillService.create for one call to inject the attachment_public_id.
#    Better than editing shared code — keeps the workaround scoped to this run.
ATTACHMENT_PID = '<from step 1>'
from entities.bill.business.service import BillService
_orig_create = BillService.create
def _patched_create(self, *args, **kwargs):
    kwargs.setdefault('attachment_public_id', ATTACHMENT_PID)
    return _orig_create(self, *args, **kwargs)
BillService.create = _patched_create

# 3. Now run the connector — it creates dbo.Bill + a placeholder BLI + 2 real BLIs.
from integrations.intuit.qbo.bill.connector.bill.business.service import BillBillConnector
from integrations.intuit.qbo.bill.business.service import QboBillService
from integrations.intuit.qbo.bill.persistence.repo import QboBillLineRepository
qbo_bill = QboBillService().read_by_id(id=qbo_bill_row_id)
qbo_lines = QboBillLineRepository().read_by_qbo_bill_id(qbo_bill_id=qbo_bill_row_id)
new_bill = BillBillConnector().sync_from_qbo_bill(qbo_bill, qbo_lines)

# 4. Inspect — the new dbo.Bill has N+1 BillLineItem rows: 1 placeholder
#    (NULL description / amount, with the attachment linked) + N real ones from qbo lines (no attachment).
#    BillLineItemAttachment is 1-1 (UNIQUE on BillLineItemId), so:
#    a) link the attachment to each REAL BLI via BillLineItemAttachmentService.create
#       (one new BillLineItemAttachment row per real BLI, all pointing at the same Attachment row)
#    b) delete the placeholder's BillLineItemAttachment row
#    c) delete the placeholder BillLineItem itself
from entities.bill_line_item_attachment.business.service import BillLineItemAttachmentService
ila_svc = BillLineItemAttachmentService()
for bli_pid in real_bli_pids:
    ila_svc.create(bill_line_item_public_id=bli_pid, attachment_public_id=ATTACHMENT_PID)
# Then delete the placeholder via raw SQL:
#   DELETE FROM dbo.BillLineItemAttachment WHERE BillLineItemId = <placeholder_bli_id>;
#   DELETE FROM dbo.BillLineItem WHERE Id = <placeholder_bli_id>;
# DO NOT delete the dbo.Attachment row — it stays referenced by the real BLIs.
```

After Step 3b, every real BLI has the attachment, the placeholder is gone, and Step 4 fingerprint matching will resolve the new invoice lines to these real BLIs.

If `QboAttachableService` returns 0 attachables for the bill (i.e. QBO has no attachment), you cannot proceed — the universal Bill-attachment rule blocks `BillService.create` either way. Halt and surface to user; they need to upload the supporting PDF in QBO first, then re-run.

### Step 3c — Heal split-staging duplicates (situational, NOT every-run)

**Diagnostic signature**: a `qbo.InvoiceLine` matches by description+amount+date but has no source mapping in `qbo.BillLineItemBillLine` / `qbo.PurchaseLineExpenseLineItem`, AND multiple `qbo.Purchase` (or `qbo.Bill`) rows exist for the same `QboApi`, each carrying a different `LineNum`. Confirm with:

```sql
-- For Purchase
SELECT qp.Id, qp.QboId, qp.EntityRefName, qp.TxnDate, qp.TotalAmt, qp.ModifiedDatetime,
       pe.ExpenseId AS DboExpenseId
FROM qbo.Purchase qp
LEFT JOIN qbo.PurchaseExpense pe ON pe.QboPurchaseId = qp.Id
WHERE qp.RealmId = ? AND qp.QboId = ?;
-- 2+ rows for the same QboId == split-staging corruption.
```

This indicates a past-sync bug split a single QBO transaction into multiple staging rows in our local cache, each holding only a subset of the original lines. **NOT a recurring runtime pattern** — this recipe applies only when the diagnostic shape is encountered. First seen on BR-MAIN-23 (2026-05-08, QBO Purchase 69340 / Artistic Tile / $45,484.04 — Line 1 NotBillable on one staging row, Line 2 Billable on the orphan row).

```python
# 1. Re-parent orphan line(s) onto the kept qbo.Purchase row (the one with qbo.PurchaseExpense mapping).
#    All lines from the orphan row(s) get moved onto the kept row.
KEPT_QP_ID = ...      # qbo.Purchase row that already has a dbo.Expense mapping
ORPHAN_QP_ID = ...    # qbo.Purchase row to be deleted
ORPHAN_LINE_ID = ...  # qbo.PurchaseLine row(s) currently parented under ORPHAN_QP_ID

with get_connection() as conn:
    cur = conn.cursor()
    cur.execute("UPDATE qbo.PurchaseLine SET QboPurchaseId = ? WHERE Id = ?", KEPT_QP_ID, ORPHAN_LINE_ID)
    # Verify ORPHAN_QP_ID now has 0 lines and 0 mappings:
    cur.execute("SELECT COUNT(*) FROM qbo.PurchaseLine WHERE QboPurchaseId = ?", ORPHAN_QP_ID)
    assert cur.fetchone()[0] == 0
    cur.execute("SELECT COUNT(*) FROM qbo.PurchaseExpense WHERE QboPurchaseId = ?", ORPHAN_QP_ID)
    assert cur.fetchone()[0] == 0
    cur.execute("DELETE FROM qbo.Purchase WHERE Id = ?", ORPHAN_QP_ID)
    conn.commit()

# 2. Re-run the connector against the kept qbo.Purchase row to refresh its dbo.Expense.
#    NOTE: PurchaseExpenseConnector.sync_from_qbo_purchase signature is positional and the
#    second parameter is named `qbo_purchase_lines` (NOT `qbo_lines`).
from integrations.intuit.qbo.purchase.business.service import QboPurchaseService
from integrations.intuit.qbo.purchase.persistence.repo import QboPurchaseLineRepository
from integrations.intuit.qbo.purchase.connector.expense.business.service import (
    PurchaseExpenseConnector,
    sync_purchase_attachments_to_expense_line_items,
)
qp = QboPurchaseService().read_by_id(id=KEPT_QP_ID)
lines = QboPurchaseLineRepository().read_by_qbo_purchase_id(qbo_purchase_id=KEPT_QP_ID)
expense = PurchaseExpenseConnector().sync_from_qbo_purchase(qp, lines)  # positional!

# 3. Link attachables to the new ExpenseLineItem rows.
#    sync_purchase_attachments_to_expense_line_items walks qbo.Attachable for the QboApi and
#    creates dbo.ExpenseLineItemAttachment rows. ExpenseLineItemAttachment is 1:1 on
#    ExpenseLineItemId, BUT a single Attachment.Id can be linked to multiple ELIs on the
#    same Expense (e.g., when a credit-card receipt covers both an account-charge line
#    AND a billable allocation line — the same PDF is supporting evidence for both).
from integrations.intuit.qbo.attachable.persistence.repo import QboAttachableRepository
attachables = [...]  # filter QboAttachableRepository to the relevant attachable(s) for this purchase
sync_purchase_attachments_to_expense_line_items(expense_id=expense.id, qbo_attachables=attachables)
```

**Bill-side analog**: same recipe applies if you ever see split-staging on `qbo.Bill` — re-parent `qbo.BillLine` rows onto the kept `qbo.Bill` row, delete the orphan, re-run `BillBillConnector.sync_from_qbo_bill` (subject to the Step 3b attachment-required workaround if the bill is brand-new). Not yet observed; documented as a precaution.

**Open audit question**: how many other split-staging cases exist in `qbo.Purchase` / `qbo.Bill` that haven't been triggered by an invoice yet? Worth a one-time audit query before this bites again on a future invoice — see `TODO.md` "Invoice pull-sync follow-ups".

## Step 4 — Link each `Manual` line to its source BillLineItem or ExpenseLineItem

`InvoiceInvoiceConnector.sync_from_qbo_invoice` creates `dbo.InvoiceLineItem` rows with `SourceType='Manual'` and **no source FK**. The packet generator depends on the source FK to find attachments, so each line must be linked back.

Read both sides:

```sql
-- QBO side
SELECT Id, QboInvoiceId, LineNum, Amount, Description, ItemRefValue, ItemRefName, ServiceDate, DetailType
FROM qbo.InvoiceLine
WHERE QboInvoiceId = ?  -- the qbo.Invoice.Id, NOT dbo.Invoice.Id
ORDER BY LineNum;

-- dbo side
SELECT Id, PublicId, SourceType, BillLineItemId, ExpenseLineItemId, BillCreditLineItemId, Amount, Description
FROM dbo.InvoiceLineItem
WHERE InvoiceId = ?  -- the dbo.Invoice.Id
ORDER BY Id;
```

Then for each `qbo.InvoiceLine`, fingerprint-match against staging. **Use `QboBillId` / `QboPurchaseId` aliases — never `BillId` / `PurchaseId`** (those are dbo identifiers and these are not):

```sql
-- Try Bill first
SELECT map.BillLineItemId
FROM qbo.BillLine bl
JOIN qbo.Bill qb ON qb.Id = bl.QboBillId
JOIN qbo.BillLineItemBillLine map ON map.QboBillLineId = bl.Id
WHERE qb.RealmId = ? AND bl.CustomerRefValue = ?
  AND ABS(bl.Amount - ?) < 0.01
  AND COALESCE(bl.Description, N'') = COALESCE(?, N'')
  AND CAST(qb.TxnDate AS DATE) = ?;     -- qbo.InvoiceLine.ServiceDate

-- If no match, try Purchase (Expense)
SELECT map.ExpenseLineItemId
FROM qbo.PurchaseLine pl
JOIN qbo.Purchase qp ON qp.Id = pl.QboPurchaseId
JOIN qbo.PurchaseLineExpenseLineItem map ON map.QboPurchaseLineId = pl.Id
WHERE qp.RealmId = ? AND pl.CustomerRefValue = ?
  AND ABS(pl.Amount - ?) < 0.01
  AND COALESCE(pl.Description, N'') = COALESCE(?, N'')
  AND CAST(qp.TxnDate AS DATE) = ?;
```

For ambiguous descriptions (e.g. multiple "Stone Materials"), align by `LineNum` order — `qbo.InvoiceLine.LineNum` and `dbo.InvoiceLineItem.Id` are both insertion-ordered, so qil[i] ↔ ili[i].

Apply linkage:

```sql
UPDATE dbo.InvoiceLineItem
SET BillLineItemId = ?, ExpenseLineItemId = NULL, BillCreditLineItemId = NULL,
    SourceType = 'BillLineItem', ModifiedDatetime = SYSUTCDATETIME()
WHERE Id = ?;
-- or
UPDATE dbo.InvoiceLineItem
SET ExpenseLineItemId = ?, BillLineItemId = NULL, BillCreditLineItemId = NULL,
    SourceType = 'ExpenseLineItem', ModifiedDatetime = SYSUTCDATETIME()
WHERE Id = ?;
```

**Verify the linkage took** by re-reading `dbo.InvoiceLineItem` and confirming `SourceType` flipped from `Manual` and the FK is set on every line.

**Also verify line count + total match `qbo.Invoice`** (`dbo.InvoiceLineItem` count for this invoice == `qbo.InvoiceLine` count for `QboInvoiceId`; sums match too). The `InvoiceLineItemConnector` has a known race where a re-run can create a phantom orphan ILI row with no `qbo.InvoiceLineItemInvoiceLine` mapping. Symptom: `dbo.Invoice.TotalAmount` does NOT match `SUM(dbo.InvoiceLineItem.Amount)` for the invoice, and the difference equals the duplicated line. Fix: identify orphan ILIs (no matching mapping row) and delete via `InvoiceLineItemService().delete_by_public_id(public_id=<orphan_pid>)`.

```sql
-- Find phantom ILI rows (no qbo mapping)
SELECT ili.Id, CAST(ili.PublicId AS NVARCHAR(50)) AS Pid, ili.Amount, ili.Description
FROM dbo.InvoiceLineItem ili
LEFT JOIN qbo.InvoiceLineItemInvoiceLine ilil ON ilil.InvoiceLineItemId = ili.Id
WHERE ili.InvoiceId = ? AND ilil.Id IS NULL;
```

If a line has zero matches in both Bill and Purchase staging, it was likely typed directly into the QBO invoice tray with no underlying transaction. Leave it as `Manual`. Surface unmatched lines to the user — they'll appear in the packet TOC but have no attachment page.

## Step 5 — Generate the PDF packet

**Pre-flight — verify every line has an attachment.** Per CRITICAL #4, every source-linked line must resolve to at least one attachment, and every `Manual` line must be user-classified. Run a coverage check before generating the packet:

```sql
SELECT ili.Id AS IliId, ili.SourceType, ili.Description, ili.Amount,
       (SELECT COUNT(*) FROM dbo.BillLineItemAttachment WHERE BillLineItemId = ili.BillLineItemId) AS BliAtts,
       (SELECT COUNT(*) FROM dbo.ExpenseLineItemAttachment WHERE ExpenseLineItemId = ili.ExpenseLineItemId) AS EliAtts,
       (SELECT COUNT(*) FROM dbo.BillCreditLineItemAttachment WHERE BillCreditLineItemId = ili.BillCreditLineItemId) AS BcliAtts
FROM dbo.InvoiceLineItem ili
WHERE ili.InvoiceId = ?;
```

If any source-linked row returns zero attachments, **halt** and surface the offending lines (ili / source type / vendor / amount / description). For each gap, first run `QboAttachableService.sync_attachables_for_bill` (or `_for_purchase`) against the source's QBO id — it may just be a staging gap. If QBO has none, the user must attach the document upstream before this run can continue.

Only after the coverage check passes:

```python
from entities.invoice.api.router import _generate_invoice_packet
result = _generate_invoice_packet('<dbo.Invoice.PublicId>')
```

Verify `result['data']['skipped'] == 0` and `page_count > 0`. `skipped > 0` after a passing coverage check means an attachment record exists but its blob is unreadable — halt and surface.

## Step 6 — Reconcile against the project's Excel DETAILS worksheet

Find the workbook:

```sql
SELECT pe.WorksheetName, di.ItemId, d.DriveId AS GraphDriveId, di.Id AS MsDriveItemId
FROM ms.DriveItemProjectExcel pe
JOIN ms.DriveItem di ON di.Id = pe.MsDriveItemId
JOIN ms.Drive d ON d.Id = di.MsDriveId
WHERE pe.ProjectId = ?;
```

Read via:

```python
from integrations.ms.sharepoint.external.client import get_excel_used_range_values
result = get_excel_used_range_values(graph_drive_id, item_id, worksheet_name)
values = result['range']['values']  # list of rows (lists), 0-indexed columns A=0..Z=25
```

Column layout (0-indexed, but documented A..Z below):
- **H** (idx 7) = DRAW REQUEST (invoice number) — note: header cell may say "DRAW REQUEST DATE" but this column doubles as the draw tag in practice
- **I** (idx 8) = DATE (Excel serial)
- **J** (idx 9) = PAYABLE TO (vendor)
- **K** (idx 10) = INVOICE # (bill/ref number)
- **L** (idx 11) = DESCRIPTION
- **N** (idx 13) = AMOUNT BILLABLE
- **Z** (idx 25) = `public_id` (idempotent reconciliation key — `BillLineItem.PublicId` or `ExpenseLineItem.PublicId`)

For each invoice line, look up its source `public_id` (the local `BillLineItem.PublicId` or `ExpenseLineItem.PublicId`) in column Z. Report two directions:

- **Direction A — Invoice → DETAILS**: source rows missing from the worksheet (need insert), or matched rows whose amount/date/vendor disagrees.
- **Direction B — DETAILS → Invoice**: any DETAILS row whose H column already equals this invoice number but whose column-Z `public_id` is NOT in the invoice's source `public_id` set.

If lines are missing → proceed to Step 7. If extras (Direction B) appear, **surface only — never auto-modify** (those reflect prior manual user decisions and need explicit user guidance).

## Step 7 — Insert missing source rows + write DRAW REQUEST column

**This step requires explicit user authorization.** Confirm with the user before proceeding. Set `ALLOW_MS_WRITES=true` inline on the Python process for the run only — never persist to `.env`.

### 7a. Sanity-check the dbo IDs *before* enqueue

For each missing source from Step 6, **first re-query `dbo.{Entity}`** and confirm BillNumber / Vendor / Date / Amount match what you expect. The MS outbox auto-drains in ~30s; there is **no second chance** to catch a wrong ID after enqueue.

```python
from shared.database import get_connection

# Re-derive dbo.Bill.Id from the BillLineItemId returned by Step 4 — never use qbo.Bill.Id.
with get_connection() as conn:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT bli.BillId, b.BillNumber, b.BillDate, v.Name AS VendorName, bli.Amount
        FROM dbo.BillLineItem bli
        JOIN dbo.Bill b ON b.Id = bli.BillId
        LEFT JOIN dbo.Vendor v ON v.Id = b.VendorId
        WHERE bli.Id = ?
    """, bill_line_item_id)
    row = cursor.fetchone()
    print(f'Will enqueue: bill_id={row.BillId} #{row.BillNumber} {row.BillDate} vendor={row.VendorName} amt={row.Amount}')
    # ASSERT vendor / number / amount match expected — print and let the user confirm
```

### 7b. Enqueue the inserts

Once IDs are verified:

```python
import os
os.environ['ALLOW_MS_WRITES'] = 'true'

from entities.bill.business.service import BillService
from entities.bill_line_item.business.service import BillLineItemService

bill_service = BillService()
li_service = BillLineItemService()
for dbo_bill_id in verified_bill_ids:
    bill = bill_service.read_by_id(id=dbo_bill_id)
    line_items = li_service.read_by_bill_id(bill_id=dbo_bill_id)
    result = bill_service.sync_to_excel_workbook(
        bill=bill, line_items=line_items, project_id=project_id
    )
    # Returns: {"success": True, "synced_count": N, "message": "Queued N row(s) for Excel sync"}
```

### 7c. Wait for drain

The scheduler auto-drains every 30s. Poll `ms.Outbox` until your enqueued rows reach terminal status (`done` or `dead_lettered`):

```python
# Until COUNT of pending/in_progress rows in your enqueued batch is 0
```

Don't try to drain inline via `MsOutboxWorker().drain_once()` — the scheduler likely has the applock, and your call will return `False`. Just wait.

### 7d. Write the invoice number into column H

```python
from entities.invoice.business.service import InvoiceService
from entities.invoice_line_item.business.service import InvoiceLineItemService

invoice = InvoiceService().read_by_public_id(public_id=invoice_public_id)
line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)

# Pass 1
InvoiceService().sync_to_excel_workbook(invoice=invoice, line_items=line_items, project_id=project_id)
# Pass 2 (idempotent — catches any row whose Z wasn't visible during pass 1's worksheet read)
InvoiceService().sync_to_excel_workbook(invoice=invoice, line_items=line_items, project_id=project_id)
```

Each pass is synchronous and takes a few minutes for a large invoice (one Graph API call per line, sequential). Manual lines (no source FK) are silently skipped — they have no row in DETAILS to update.

### 7e. Recovery — only if a wrong row was enqueued

If you discover (via post-drain audit) that a wrong row landed:

```python
from integrations.ms.sharepoint.external.client import (
    clear_excel_range, create_workbook_session, close_workbook_session,
    get_excel_used_range_values,
)

session_id = create_workbook_session(drive_id=graph_drive_id, item_id=item_id)
try:
    # Re-read the worksheet, find each wrong row by its column-Z public_id
    result = get_excel_used_range_values(graph_drive_id, item_id, worksheet, session_id=session_id)
    values = result['range']['values']
    wrong_rows = []  # 1-based row indices
    for ridx, row in enumerate(values, 1):
        if len(row) > 25 and (str(row[25]).strip().lower() in WRONG_PIDS):
            wrong_rows.append(ridx)

    # Sort DESCENDING so earlier deletes don't shift later indices
    for ridx in sorted(wrong_rows, reverse=True):
        clear_excel_range(graph_drive_id, item_id, worksheet, f'A{ridx}:Z{ridx}', session_id=session_id)
finally:
    close_workbook_session(graph_drive_id, item_id, session_id)
```

`clear_excel_range` blanks the row in place (used range stays the same except trailing blanks may be auto-trimmed by Excel). It does NOT delete the row or shift others. **Get explicit user authorization before running cleanup** — it's a destructive write to a shared workbook.

## Step 8 — Mark source line items as `IsBilled=True`

The connector creates `dbo.Invoice` with `IsDraft=False` but does NOT call `complete_invoice` — so the source `BillLineItem.IsBilled` / `ExpenseLineItem.IsBilled` flags remain `False` after Step 4 linkage. If you skip this step, those sources continue to surface in the project's "billable items" list and could be redundantly added to a future invoice.

`InvoiceService._mark_source_as_billed(line_item)` already handles all three source types (BillLineItem, ExpenseLineItem, BillCreditLineItem) and is a no-op if `IsBilled` is already `True`. Iterate every line item:

```python
from entities.invoice.business.service import InvoiceService
from entities.invoice_line_item.business.service import InvoiceLineItemService

inv_svc = InvoiceService()
invoice = inv_svc.read_by_public_id(public_id=invoice_public_id)
line_items = InvoiceLineItemService().read_by_invoice_id(invoice_id=invoice.id)
for li in line_items:
    inv_svc._mark_source_as_billed(li)  # safe no-op for Manual lines
```

Verify with:

```sql
SELECT 'Bill' AS Kind, COUNT(*) AS Total, SUM(CASE WHEN bli.IsBilled=1 THEN 1 ELSE 0 END) AS Billed
FROM dbo.InvoiceLineItem ili JOIN dbo.BillLineItem bli ON bli.Id = ili.BillLineItemId
WHERE ili.InvoiceId = ?
UNION ALL
SELECT 'Expense', COUNT(*), SUM(CASE WHEN eli.IsBilled=1 THEN 1 ELSE 0 END)
FROM dbo.InvoiceLineItem ili JOIN dbo.ExpenseLineItem eli ON eli.Id = ili.ExpenseLineItemId
WHERE ili.InvoiceId = ?;
```

Both `Billed` columns must equal `Total`. Manual lines have no source FK and are silently skipped.

This intentionally does NOT propagate to QBO — `BillableStatus` on the QBO Bill/Purchase line is left as-is. The drift is accepted because adding the source to a future invoice would be caught at Step 4 (the source pid is already linked to this invoice's ILI, no duplicate fingerprint match) and at Step 6 Direction B (DETAILS row already H-tagged with this invoice number).

## Step 9 — Upload packet + line-item attachments to SharePoint

```python
result = InvoiceService()._upload_to_sharepoint(invoice=invoice, line_items=line_items)
# Verify: success=True, synced_count == 1 + (number of lines with attachments), errors=[]
```

## Step 10 — Final reconciliation report

Re-run Step 6 directions A + B (read-only). Report:

- Lines reconciled (should equal `len(line_items)`)
- Lines with `DRAW REQUEST = <invoice number>` (should equal lines with a source FK)
- Manual lines without source (no attachment in packet)
- Extra DETAILS DRAW tags not on this invoice (Direction B — surface only)
- Sources marked `IsBilled=True` (should equal lines with a source FK; Manual lines are silently skipped)
- Packet attachment `public_id` + blob URL + page count
- SharePoint files uploaded count

---

## Halt-and-ask conditions

Stop and surface to the user before proceeding when:

- Project has no `qbo.CustomerProject` mapping.
- A `qbo.InvoiceLine` has no fingerprint match in either Bill or Purchase staging.
- Multiple matches remain ambiguous after `LineNum` alignment.
- `ALLOW_MS_WRITES` is unset and you need to write to Excel.
- A `dbo.Invoice` row with a numeric suffix (`-2` / `-3`) appears for this invoice number, OR `dbo.Invoice.TotalAmount` doesn't match `qbo.Invoice.TotalAmt`.
- DETAILS Direction B finds extra rows tagged with this invoice and the user has not already acknowledged them.
- A sanity check before `sync_to_excel_workbook` reveals the dbo entity doesn't match the invoice line you intended (vendor / number / amount mismatch).
- `_upload_to_sharepoint` returns errors for any file.
- Step 8 verification shows any source line still has `IsBilled=False` after the loop ran.
- Any source-linked line on the invoice has zero attachments after running the Step 5 coverage check (and after a fresh `sync_attachables_for_*` confirmed QBO has none either) — see CRITICAL #4. The line cannot be billed without supporting documentation; the user must attach upstream and re-run.
- Any `Manual` line on the invoice has not been explicitly classified by the user as either (a) a derivative of another billed line on the same invoice, or (b) needs to be removed / replaced with a sourced line.

## Why we don't just call `complete_invoice`

`InvoiceService.complete_invoice` would collapse Steps 5 / 7d / Step 8 / Step 9 into one call. We deliberately don't use it because:

1. **QBO push is gated on a feature toggle inside `complete_invoice` (line ~407)** — when that ships, every InvoiceAgent run would silently start pushing to QBO, violating the playbook's "pull-only" invariant.
2. `complete_invoice` continues on per-step failures and aggregates errors. The hand-rolled flow halts on each step, giving the agent a chance to ask the user before proceeding.
3. `complete_invoice` does `float(invoice.total_amount)` on each call — fine in practice but goes against the project's `Decimal(str(value))` rule for financial precision.

## Known issues to anticipate

1. **`sync_qbo_invoice.py` may be disabled** — returns `{"disabled": true}`. Halt; the user authorizes lifting.
2. **`ALLOW_MS_WRITES=true` is required for Excel writes** — set inline on the process, never persist.
3. **`SourceType='Manual'` after invoice pull is by design** — Step 4 is mandatory.
4. **`IsBilled` is NOT flipped by the connector path** — Step 8 is mandatory; without it, sources stay billable.
5. **Outbox drain timing** — `BillService.sync_to_excel_workbook` after an insert needs a drain before Step 7d sees the new row. `InvoiceService.sync_to_excel_workbook` is direct-Graph (not outbox), so no drain wait needed for it.
6. **Duplicate `dbo.Invoice` rows with numeric suffixes** (`-2`, `-3`) come from QBO pulling a number that already exists locally. They are old artifacts; do not use them. See Step 3a for the rebuild recipe.
7. **Connector may not propagate later QBO edits** — if the user adds lines in QBO after the first pull, a later `sync_qbo_invoice.py` updates `qbo.Invoice` but may NOT propagate to `dbo.Invoice` if the rename collides with another local invoice's `InvoiceNumber`. Detect via `dbo.Invoice.TotalAmount != qbo.Invoice.TotalAmt`. Recovery via Step 3a.
8. **Manual lines with no underlying Bill/Purchase** appear in the QBO invoice tray when the user types them directly. They can't be linked — and per CRITICAL #4 they're a halt unless the user classifies them as a derivative of another billed line (e.g., a separate markup line). Do not just let them flow through with no attachment page.
9. **Pre-existing DRAW tags in DETAILS** that don't correspond to invoice lines reflect prior manual user edits; surface only, do not auto-fix.
10. **Excel row numbers shift** after row inserts within the same Cost Code section. Always match by column-Z `public_id`, never by row number across runs.
11. **`qbo.Bill.Id` ≠ `dbo.Bill.Id`** (and same for Purchase, Invoice). Re-derive dbo IDs via `dbo.BillLineItem.BillId`. Aliases like `b.Id AS BillId` in `qbo.*` joins are a footgun.
12. **MS outbox has no human-cancel window** — scheduler auto-drains every 30s. Audit IDs *before* enqueue.
13. **`InvoiceInvoiceConnector` wipes source FKs on every update** — see CRITICAL #4. Re-run Step 4 over every line on the invoice after any connector touch (initial pull, re-pull, or direct invocation). The previous run's linkage does not survive.
14. **`InvoiceInvoiceConnector` can create phantom orphan ILI rows during re-runs** — symptom: `dbo.Invoice.TotalAmount` ≠ `SUM(dbo.InvoiceLineItem.Amount)`, with the difference equal to a duplicated line. Detect and fix via the orphan-finding query in Step 4.
15. **`BillBillConnector.sync_from_qbo_bill` is broken for new bills** — the connector calls `BillService.create` without `attachment_public_id`, which the universal Bill-attachment rule requires. New bills referenced by an invoice must be onboarded via the Step 3b workaround (pull attachable, monkey-patch create, clean up placeholder). Updates work fine.
16. **`BillService.sync_to_excel_workbook` appends to the bottom of DETAILS instead of inserting into the matching Cost Code section** — surfaced for OHR2-33 on 2026-04-27: 8 rows for new bills landed at rows 3297-3304 (well past the contiguous data block, with ~215 empty rows in the gap). Symptom: Excel's auto-filter on column H shows fewer rows than expected (e.g., filter shows 39 of the actual 47 OHR2-33 rows because the auto-filter range terminates at the gap). SUMIFS formulas reading column N against the H criterion still return the correct total because they scan the full column, but visual inspection / filtering / pivot tables miss the appended rows. Workaround: ask the user to manually relocate the appended rows into the correct Cost Code section. Long-term fix: the insert path needs to find the right insertion point (the last existing row in the matching Cost Code section) instead of appending at the end.
17. **`BillService.sync_to_excel_workbook` writes column N from `BillLineItem.Price`, NOT `Amount`** — first seen on HP2-09 / Q44862, 2026-05-15. A BLI with `Amount=10426.25` but `Price=NULL` inserted into DETAILS as `N=0`. The fingerprint match by description+amount+date works against the `qbo.BillLine.Amount`, so a Bill with Price=NULL fingerprints fine but produces a $0 row in DETAILS. Pre-flight before Step 7b: for every BLI in the enqueue batch, verify `Price IS NOT NULL` (and equals Amount for single-quantity invoice flows). If NULL, run `UPDATE dbo.BillLineItem SET Price = Amount WHERE Id = ?` before enqueueing. Long-term fix: `BillService.sync_to_excel_workbook` should fall back to `Amount` when `Price` is NULL.
18. **Duplicate BLIs on the same `dbo.Bill`** — re-encountered on HP2-09 / Q44862 (dbo.Bill 17320), 2026-05-15. Two `dbo.BillLineItem` rows with identical `Amount` + `Description` + `SubCostCodeId` on the same parent Bill. One carries the `qbo.BillLineItemBillLine` mapping (the "official" one); the other is an orphan with no qbo mapping. Typically the qbo-mapped one is missing fields the orphan has (Price, BillLineItemAttachment), because at some past sync the connector created a fresh BLI instead of reusing the existing one. Fingerprint match picks the qbo-mapped BLI (correct) but its missing fields surface downstream (Step 7 writes $0, Step 5 fails coverage check). Recovery: (a) link the orphan's Attachment to the kept BLI via a new `BillLineItemAttachment` row pointing the same `Attachment.Id` (the `BillLineItemAttachment` UNIQUE is per-BLI, not per-Attachment); (b) copy `Price` from the orphan to the kept BLI; (c) leave the orphan BLI in dbo (it has no qbo mapping and no ILI references — destructive delete deferred until a separate cleanup pass).
19. **Duplicate `dbo.Project` rows with the same `Name` but no `Abbreviation`** — re-encountered for HP2 on 2026-05-15. A second `dbo.Project` ("HP2 - 4406 Harding Pike", `Abbreviation=NULL`, id=137) was created out-of-band (2026-05-14 16:10, source unknown — not from this session) and `qbo.CustomerProject` was re-pointed away from the original project (id=13) to the new one. On the next InvoiceAgent re-run, the InvoiceInvoiceConnector dragged `dbo.Invoice.ProjectId` along to the new project, and SharePoint upload failed because `ms.DriveItemProjectModule` is configured for the original. Symptom: `_upload_to_sharepoint` returns `"Invoices module folder not configured for this project"`. Recovery: repoint `qbo.CustomerProject.ProjectId` back to the original, `UPDATE dbo.Invoice SET ProjectId=<original>`, then `DELETE FROM dbo.Project WHERE Id=<duplicate>` after auditing references (only 2 references existed: the invoice and the customer mapping). Audit before delete: `dbo.Invoice.ProjectId`, `qbo.CustomerProject.ProjectId`, `ms.DriveItemProjectModule.ProjectId`, `ms.DriveItemProjectExcel.ProjectId`, `dbo.UserProject.ProjectId`, `dbo.ProjectAddress.ProjectId`, `dbo.BillLineItem.ProjectId`, `dbo.ExpenseLineItem.ProjectId`, `dbo.BillCreditLineItem.ProjectId`, `dbo.ContractLaborLineItem.ProjectId` (the Bill/Expense/BillCredit parent tables have no ProjectId column). **Root cause is unknown** — likely a misfire from a project_specialist agent or an ad-hoc Customer-rename in QBO that the connector treated as a new entity. Worth a one-time audit of `dbo.Project` for same-Name duplicates.
20. **`InvoiceInvoiceConnector` re-run UNIQUE-constraint failure mode** — re-encountered on HP2-09 / Q44862, 2026-05-15. When invoked a second time on an existing invoice (e.g., to pick up a new line added in QBO), the per-line `InvoiceLineItemConnector.sync_from_qbo_invoice_line` can fail with `Violation of UNIQUE KEY constraint 'UQ_InvoiceLineItemInvoiceLine_QboInvoiceLineId'`. The connector thinks the existing ILI is "not found" and tries to CREATE a new ILI + a new mapping row — the new ILI creation succeeds but the mapping INSERT collides with the existing mapping. Result: N new orphan ILI rows in `dbo.InvoiceLineItem` with no `qbo.InvoiceLineItemInvoiceLine` mapping. `dbo.Invoice.TotalAmount` is correctly updated, but `SUM(dbo.InvoiceLineItem.Amount)` is roughly double. Cleanup (do this BEFORE re-fingerprinting in Step 4): `DELETE FROM dbo.InvoiceLineItem WHERE InvoiceId = ? AND Id NOT IN (SELECT InvoiceLineItemId FROM qbo.InvoiceLineItemInvoiceLine map JOIN qbo.InvoiceLine il ON il.Id = map.QboInvoiceLineId WHERE il.QboInvoiceId = ?)`. Verify `dbo.InvoiceLineItem` count and sum now match `qbo.Invoice` (line count + total). Then re-fingerprint EVERY line per CRITICAL #4. Long-term fix: connector should either UPDATE the existing mapping or handle the unique-constraint collision gracefully.

## Side effects (full enumeration)

- Mutates `dbo.InvoiceLineItem.BillLineItemId` / `ExpenseLineItemId` / `SourceType`.
- Creates `dbo.Attachment` + `dbo.InvoiceAttachment` (one packet per generation; replaces prior).
- Inserts new rows in the project's DETAILS worksheet for missing sources.
- Writes column H (DRAW REQUEST) in DETAILS for matched rows.
- Flips `dbo.BillLineItem.IsBilled` / `dbo.ExpenseLineItem.IsBilled` to `True` for every linked source (Step 8).
- Uploads packet + supporting PDFs to SharePoint.
- **Does NOT push** anything to QBO — `BillableStatus` on the QBO line stays as-is. Accepted drift; the next InvoiceAgent run won't double-bill because the source pid is already on the prior invoice's ILI and DETAILS row is already H-tagged.
