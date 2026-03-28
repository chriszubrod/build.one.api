# Session Notes

## Session: Invoice SharePoint Upload, Manual Attachment UI & Module Folder Picker (March 17, 2026)

### What Was Done

#### 1. Manual InvoiceLineItem Attachment Upload UI (`templates/invoice/edit.html`)
- Added a hidden `<input type="file" id="manual-attachment-input">` outside the table (avoids nesting issues).
- Jinja2: for Manual rows with no attachment, renders a paperclip `📎` as an upload trigger link instead of `—`.
- `buildRowHTML` JS: same logic — Manual rows get the upload trigger link.
- `triggerManualAttachmentUpload(lineItemPublicId, event)`: stores the pending public ID, resets the file input, and calls `.click()`.
- File input `change` listener:
  1. `POST /api/v1/upload/attachment` (FormData, `category=invoice_line_item`) → creates Attachment record + Azure blob.
  2. `POST /api/v1/create/invoice-line-item-attachment` (JSON) → links Attachment to InvoiceLineItem.
  3. Updates row's `data-attachment-id`, swaps `📎` → `📄` link to the attachment.

#### 2. Complete Invoice SharePoint Upload (`entities/invoice/business/service.py`)
- Added SharePoint lazy properties: `_driveitem_service`, `_drive_repo`, `_project_module_connector`.
- Added `_upload_to_sharepoint(invoice, line_items)` method:
  1. Resolves "Invoices" module folder via `DriveItemProjectModuleConnector.get_folder_for_module`.
  2. Batch-fetches attachment metadata for all source types (Bill, Expense, BillCredit, Manual) in one DB connection using raw SQL joins.
  3. Uploads each unique attachment with filename `{InvoiceNumber} - {Vendor} - {ParentNumber} - {Description} - {SccNumber} - ${Price} - {Date}`.
  4. Uploads the PDF packet (if exists, `category="invoice_packet"`) as `{InvoiceNumber} - Packet.pdf`.
- `complete_invoice()` now calls `_upload_to_sharepoint()` after `_mark_source_as_billed`, before QBO sync; result included in response dict.

#### 3. Invoices Module Seed (`entities/module/sql/seed.InvoicesModule.sql`)
- New idempotent seed script: `IF NOT EXISTS ... INSERT INTO dbo.[Module] ... ('Invoices', '/invoice/list')`.
- Executed successfully via `python scripts/run_sql.py entities/module/sql/seed.InvoicesModule.sql`.

#### 4. Project View Folder Picker for Bills, Expenses, and Invoices (`templates/project/view.html`)
- Updated module folder loop condition from `{% if module.name == 'Bills' %}` to `{% if module.name in ['Bills', 'Expenses', 'Invoices'] %}`.
- Fixed `linkModuleFolder` JS to read FastAPI's `{"detail": "..."}` error format: `data.detail || data.message || 'Unknown error'` (FastAPI HTTPException serializes to `detail`, not `message`).

#### 5. Allow Same SharePoint Folder for Multiple Modules
- **Problem**: Needed to link the same SharePoint folder to Bills, Expenses, and Invoices modules simultaneously.
- **Fix 1** (`integrations/ms/sharepoint/driveitem/connector/project_module/business/service.py`): Removed Python-level check that prevented a driveitem from being linked to more than one project+module combination.
- **Fix 2** (`scripts/drop_UQ_DriveItemProjectModule_MsDriveItemId.sql`): New migration script to drop `UQ_DriveItemProjectModule_MsDriveItemId` unique constraint from `ms.DriveItemProjectModule`. Executed successfully.
- **Fix 3** (`integrations/ms/sharepoint/driveitem/connector/project_module/sql/ms.driveitem_project_module.sql`): Removed the `UNIQUE ([MsDriveItemId])` constraint from the table DDL.

### Files Modified
- `templates/invoice/edit.html` — manual attachment upload UI (file input, trigger function, change listener)
- `entities/invoice/business/service.py` — SharePoint lazy properties, `_upload_to_sharepoint()`, `complete_invoice()` integration
- `entities/module/sql/seed.InvoicesModule.sql` — new seed script (Invoices module)
- `templates/project/view.html` — module name filter for Bills/Expenses/Invoices, JS error reads `data.detail`
- `integrations/ms/sharepoint/driveitem/connector/project_module/business/service.py` — removed duplicate driveitem check
- `integrations/ms/sharepoint/driveitem/connector/project_module/sql/ms.driveitem_project_module.sql` — removed `UQ_DriveItemProjectModule_MsDriveItemId`
- `scripts/drop_UQ_DriveItemProjectModule_MsDriveItemId.sql` — new migration script (executed)

---

## Session: Budget Tracker Reconciliation — First Principles (March 18–19, 2026)

### Project Reconciliation Health Checks (per project)

These checks are run manually or via script for a given project to verify DB integrity and QBO sync state.

#### Step 1 — Orphaned BillLineItems
**Question**: Does every BillLineItem have a parent Bill?
**Query**: `SELECT bli.* FROM dbo.BillLineItem bli LEFT JOIN dbo.Bill b ON b.Id = bli.BillId WHERE b.Id IS NULL`
**MR2-MAIN (project 93) result**: ✅ 0 orphaned BillLineItems

#### Step 2 — QBO Mapping Coverage (DB → QBO)
**Question**: Does every non-draft BillLineItem have a mapping to a QBO BillLine (`qbo.BillLineItemBillLine`)?
**Query**: Join `dbo.BillLineItem` → `qbo.BillLineItemBillLine` on `BillLineItemId`, filter `IsDraft = 0` and `ProjectId = {id}`, find rows with no mapping.
**MR2-MAIN (project 93) result**: ✅ 0 unmapped non-draft BillLineItems

#### Step 3 — Orphaned QBO BillLines
**Question**: Does every QBO BillLine have a parent QBO Bill?
**Query**: `SELECT bl.* FROM qbo.BillLine bl LEFT JOIN qbo.Bill b ON b.Id = bl.QboBillId WHERE b.Id IS NULL` — filtered to lines mapped to project BillLineItems.
**MR2-MAIN (project 93) result**: ✅ 0 orphaned QBO BillLines

#### Step 4 — QBO Mapping Coverage (QBO → DB)
**Question**: Does every QBO BillLine for this project have a mapping to a DB BillLineItem?
**Query**: Join `qbo.BillLine` → `qbo.BillLineItemBillLine` on `QboBillLineId`, filter by `CustomerRefValue` matching the project's QBO customer, find rows with no mapping.
**MR2-MAIN (project 93) result**: ✅ 0 unmapped QBO BillLines

### Reconciliation Scope Rules
- **Date**: Only items dated 2026-01-01 or later
- **Billed status**: Only items not yet billed — Excel col H ("DRAW REQUEST") must be null; DB `IsBilled = False`
- **Draft status**: DB records must be non-draft (`IsDraft = False`)
- **Direction**: Both — DB is authoritative for what exists, Excel is authoritative for what should exist
- **New records going forward**: DB → Excel push happens automatically when a Bill is marked Complete (no change to current process)

#### Step 5 — Sync DB ↔ QBO if variances found
**Action**: If step 2 or step 4 has variances, run the appropriate sync:
- DB missing QBO mapping → `sync_to_qbo_bill()` to push DB record to QBO, or create `BillLineItemBillLine` mapping manually.
- QBO missing DB mapping → `sync_from_qbo_bill()` to pull QBO record into DB, or create mapping manually.
**MR2-MAIN (project 93) result**: ✅ No action required — steps 2 and 4 were clean.

### Excel Column Map (range always fetched as A1:Z{lastRow}; index 0 = col A)
| 0-based index | Excel col | Field |
|---|---|---|
| 7 | H | DRAW REQUEST (null = not yet billed) |
| 8 | I | Date |
| 9 | J | Vendor Name |
| 10 | K | Bill / Ref Number |
| 11 | L | Description |
| 13 | N | Price (amount) |
| 25 | Z | public_id anchor (col Z) |

Note: `get_excel_used_range_values` now calls `usedRange` only to find the last row, then fetches `A1:Z{lastRow}` explicitly. Append function pads all rows to 26 columns.

#### Step 6 — Build scoped DB set
**Scope**: Non-draft, unbilled BillLineItems for this project dated >= 2026-01-01 (`IsDraft = False`, `IsBilled = False`, `BillDate >= 2026-01-01`).

#### Step 7 — Build scoped Excel set
**Scope**: Excel rows dated >= 2026-01-01 where col H ("DRAW REQUEST") is null.

#### Step 8 — Match Excel → DB
For each scoped Excel row:
- **Col Z present**: verify public_id exists in scoped DB set. If not → orphaned row (flag for manual cleanup).
- **Col Z absent**: attempt match on all five fields: date + vendor (fuzzy) + bill number + description + amount. All five must agree.
  - Unambiguous match → backfill col Z (write mode only).
  - Any field off, or ambiguous (multiple candidates) → flag for manual review. Do not auto-link.

#### Step 9 — Match DB → Excel
For each scoped DB record:
- Public_id found in col Z of a scoped Excel row → verified, no action.
- Public_id not found in any col Z → missing from Excel. Flag it (Bill completion push may have failed or not yet run).

#### Step 10 — Resolve variances
Manual review of all flagged items from steps 8 and 9. No automatic record creation.

---

## Session: Contract Labor Entity Module — Deep Dive, Bug Fixes & Bill Generation (March 16, 2026)

### What Was Done

#### Full Module Review & Two Deep-Dive Bug Fix Passes

Performed a comprehensive review of the Contract Labor entity module: `entities/contract_labor/`, `templates/contract_labor/`, and `entities/contract_labor/business/bill_service.py`. Fixed 13 bugs across two passes.

**Bug 1 — Vendor sort A-Z not working** (`entities/contract_labor/sql/dbo.contract_labor.sql`)
- `ReadContractLaborsPaginated` ordered by `v.[Name] ASC` but all entries had NULL VendorId (assigned during review step), so sort did nothing.
- Fixed: `ISNULL(v.[Name], cl.[EmployeeName]) ASC`.

**Bug 2 — BillLineItemId wiped on every line item save** (sql + repo + router)
- SQL UPDATE sproc didn't have a `@BillLineItemId` parameter — field was silently reset to NULL on each save.
- Repo had the param commented out; router didn't pass the existing value.
- Fixed: added `@BillLineItemId` with CASE WHEN guard to sproc; repo passes it; router reads `existing_item.bill_line_item_id` and passes it through.

**Bug 3 — "Too many arguments" on Save & Mark Ready** (`entities/contract_labor/persistence/line_item_repo.py`)
- Repo was passing `BillLineItemId` before the sproc had the parameter (from an earlier partial fix).
- Fixed: kept in sync — both sproc and repo now include `BillLineItemId`.

**Bug 4 — Dead billing endpoints** (`entities/contract_labor/api/router.py`)
- `GET /billing/summary` and `POST /billing/create-bills` called non-existent service methods.
- Fixed: removed both dead endpoints.

**Bug 5 — Import preview crash on tuple unpack** (`entities/contract_labor/business/import_service.py`)
- `get_import_preview()` assigned `self._parse_row(row, row_num)` to a single variable and called `.get()` on the returned tuple — immediate AttributeError.
- Fixed: `parsed, skip_reason = self._parse_row(...)` throughout.

**Bug 6 — Import preview used hardcoded filename** (`entities/contract_labor/business/import_service.py`)
- `get_import_preview()` called `load_workbook(io.BytesIO(file_content))` ignoring the actual filename, breaking `.csv` detection.
- Fixed: added `filename` parameter; delegates to `_load_excel_rows()`.

**Bug 7 — Variable shadowing corrupts bill total** (`entities/contract_labor/business/bill_service.py`)
- Inner loop declared `total_amount = Decimal("0")` which shadowed the outer bill total. PDF packet received only the last SCC group's subtotal, not the full bill amount.
- Fixed: renamed inner accumulator vars to `scc_amount` / `scc_price`.

**Bug 8 — Non-billable items included in total_amount** (`entities/contract_labor/business/bill_service.py`)
- `total_amount` summed all line items regardless of `IsBillable`.
- Fixed: `sum(... for item in items if item["line_item"].is_billable is not False)`.

**Bug 9 — Non-billable items shown with real amount on PDF** (`entities/contract_labor/business/bill_service.py`)
- PDF used the item's actual `price` for non-billable items instead of $0.00.
- Fixed: `amount = "$0.00" if li.is_billable is False else f"${float(li.price or 0):,.2f}"`.

**Bug 10 — Non-billable SCC groups included in PDF** (`entities/contract_labor/business/bill_service.py`)
- SCC groups where all items are non-billable still generated PDF sections with $0.00 subtotals.
- Fixed: track `any_billable` flag; skip groups where no billable items exist.

**Bug 11 — Zero markup corrupted to NULL on save (JS)** (`templates/contract_labor/edit.html`)
- `markupPercent / 100 || null` evaluates to `null` when `markupPercent = 0`.
- Fixed: `markup: markupPercent / 100` (never use `|| null` for numeric fields).

**Bug 12 — Zero markup not displayed on edit page (Jinja2)** (`templates/contract_labor/edit.html`)
- `value="{{ item.markup * 100 if item.markup else '' }}"` — Jinja2 treats `Decimal('0')` as falsy, showing blank.
- Fixed: `value="{{ (item.markup * 100) if item.markup is not none else '' }}"`.

**Bug 13 — Entries with no project-assigned line items silently skipped** (`entities/contract_labor/business/bill_service.py`)
- If no line items had a `project` assigned, the entry was silently excluded from the bill with no feedback.
- Fixed: added warning to `result["errors"]` for each skipped entry.

#### Features Added

**1. Scroll position restoration** (`templates/contract_labor/list.html`)
- Edit link saves `document.getElementById('content').scrollTop` to `sessionStorage`.
- On `DOMContentLoaded`, restores via double `requestAnimationFrame` (waits for list render).

**2. Auto-populate one line item on edit page** (`templates/contract_labor/edit.html`)
- If no `.cl-line-item` elements exist at page load, calls `addLineItem()` automatically.

#### Operational Work

**Reversed incorrect billing run (2026-01-31 and 2026-02-28)**
- Generate Bills for 2026-02-15 incorrectly marked ALL billing periods as billed.
- Used a Python script to reset 148 entries to their correct status (pending_review or ready), delete 41 draft bills, and clear 85 BillLineItem FK references from ContractLaborLineItems.
- Left 2026-01-15 entries intact (correctly billed).

**Deleted 16 incorrect draft bills (2026-02-15 run)**
- Reset 2026-02-15 entries back to `ready` status after deletion.

**8h/day compliance review (all 6 main subcontractors)**
- Verified Brayan, Emilson, Elmer, Wilmer all total exactly 8.00h per day.
- Denis had 2 days at 7h — corrected by user.
- Selvin has intentional sub-8h days — left as-is by user's decision.

**Marked Selvin's "DO NOT BILL" line item IsBillable=false**
- ContractLaborLineItem ID=206 ("Met with Tanner. DO NOT BILL.") confirmed and verified as `IsBillable=false`.

### Files Modified
- `entities/contract_labor/sql/dbo.contract_labor.sql` — ORDER BY fix, BillLineItemId in all SELECT/UPDATE sprocs
- `entities/contract_labor/persistence/line_item_repo.py` — BillLineItemId in update params
- `entities/contract_labor/api/router.py` — removed dead billing endpoints, pass existing `bill_line_item_id` on update
- `entities/contract_labor/business/bill_service.py` — variable shadowing fix, non-billable total/PDF fixes, all-non-billable group skip, missing-project warning
- `entities/contract_labor/business/import_service.py` — tuple unpack fix, filename parameter for `get_import_preview`
- `templates/contract_labor/list.html` — scroll position save/restore
- `templates/contract_labor/edit.html` — auto-add line item, zero markup Jinja2 + JS fixes

---

## Session: Invoice Entity Module — Deep Dive, Bug Fixes & PDF Packet TOC (March 16, 2026)

### What Was Done

#### Deep Dive & Bug Fix Pass on Invoice Entity Module

Performed a comprehensive review of `/entities/invoice`, `/entities/invoice_line_item`, `/entities/invoice_attachment`, `/entities/invoice_line_item_attachment`, and related templates. Identified and fixed 5 bugs.

**Bug 1 — InvoiceLineItem delete: wrong cascade order** (`entities/invoice_line_item/business/service.py`)
- `delete_by_public_id()` tried to delete the `Attachment` record before the `InvoiceLineItemAttachment` join record, causing FK violation. After the silent catch, the join record delete was skipped, leaving the InvoiceLineItem delete to fail on its own FK.
- Fixed: correct order — read attachment info → delete join record → delete blob (best-effort) → delete Attachment record. Each step in its own try/except.

**Bug 2 — complete_invoice project_id type mismatch** (`entities/invoice/business/service.py`)
- `project_service.read_by_id(id=str(invoice.project_id))` passed a `str` but `ProjectService.read_by_id` expects `int`.
- Fixed: removed `str()` cast.

**Bug 3 — 404 crash on invalid invoice public_id** (`entities/invoice/web/controller.py`)
- Both `view_invoice` and `edit_invoice` called `.to_dict()` on a potentially-None invoice, raising AttributeError instead of 404.
- Fixed: added `if not invoice: raise HTTPException(status_code=404)` before any attribute access.

**Bug 4 — saveInvoice() returned void, Complete ignored save failure** (`templates/invoice/edit.html`)
- The Complete Invoice submit handler had no signal from `saveInvoice()` about whether the save succeeded. If the save failed, Complete would proceed with stale DB state.
- Fixed: `saveInvoice()` now returns `true`/`false`; submit handler checks the return value and bails early on `false`.

**Bug 5 — Falsy 0 display bug for zero-value amounts** (`templates/invoice/edit.html`, `templates/invoice/create.html`)
- `buildRowHTML` and `reAddLineItem` used `||` short-circuit which treated `0` as falsy, showing `null` instead of `$0.00` for zero-value amount/markup/price fields.
- Fixed: replaced with explicit `!== null && !== ''` guards in both templates.

#### Features Added

**1. Line items sort: Type → Vendor ascending**
- Server-side sort in `edit_invoice` after `_enrich_line_items`: `(type_order, vendor_name.lower())` — Bill (0) → BillCredit (1) → Expense (2), then vendor A→Z.
- Client-side `sortLineItemsTable()` uses the same compound key so newly loaded items (via "Load Billable Items") stay in sync with server order.

**2. PDF Packet pre-flight missing attachment warning**
- Added `getIncludedRowsMissingPDF()` in `edit.html` that scans included rows for items with a source record (`data-parent-public-id`) but no attachment (`data-attachment-id` empty).
- If any found, `generatePacket()` shows a `confirm()` dialog listing each item (type, ref number, vendor) before proceeding. Manual line items are excluded from the warning.

**3. PDF Packet TOC pages** (`entities/invoice/api/router.py`)
- Two Table of Contents pages are now prepended to every generated PDF packet, before the attachment images.
- **Basic TOC**: Ordered Bill → Credit → Expense, then vendor A→Z. Columns: Date, Vendor, Invoice, Description, Type, Amount. Grand total row.
- **Expanded TOC**: Ordered by CostCode number (numeric ascending), then type, then vendor. Columns: Cost Code, Date, Vendor, Invoice, Description, Type, Amount. Subtotal row per CostCode group + grand total.
- Styled with `reportlab` (Helvetica font, dark navy blue `#1F3864` headers) to match provided sample PDFs.
- "Type" column shows "Bill", "Credit", or "Expense" derived from `source_type` — no new schema field needed.
- TOC includes ALL invoice line items (including those without attachments); the merged pages that follow only include items with PDFs.

**4. CostCode enrichment in `_enrich_line_items()`** (`entities/invoice/web/controller.py`)
- All three source queries (bill, expense, credit) now join `dbo.CostCode` via `SubCostCode.CostCodeId`.
- Returns `cost_code_number` and `cost_code_name` (parent CostCode) alongside existing `sub_cost_code_number/name`.
- Used by the expanded TOC to group by CostCode rather than SubCostCode.

### Files Modified
- `entities/invoice/web/controller.py` — HTTPException import, 404 guards in view/edit, type+vendor sort in edit_invoice, CostCode join in all three enrichment queries, `cost_code_number/name` in result maps and defaults
- `entities/invoice/business/service.py` — removed `str()` cast on `project_id` in `complete_invoice`
- `entities/invoice/api/router.py` — `_toc_source_label()`, `_build_toc_basic_pdf()`, `_build_toc_expanded_pdf()` helper functions; TOC generation + prepend in `generate_invoice_packet_router`; expanded sort key uses `cost_code_number`
- `entities/invoice_line_item/business/service.py` — delete cascade order fix (join record → blob → Attachment), each step in own try/except
- `templates/invoice/edit.html` — `saveInvoice()` bool return, Complete guard on save failure, falsy 0 fixes in `buildRowHTML`/`reAddLineItem`, `getIncludedRowsMissingPDF()` pre-flight check in `generatePacket()`, `sortLineItemsTable()` compound sort key
- `templates/invoice/create.html` — falsy 0 fixes in `buildRowHTML`/`reAddLineItem`

---

## Session: Expense Entity Module — Bug Fixes & Scheduler Cleanup (March 13, 2026)

### What Was Done

#### Deep Dive & 9-Bug Fix Pass on Expense Entity Module

Performed a comprehensive review of `/entities/expense`, `/entities/expense_line_item`, `/entities/expense_line_item_attachment`, and `/templates/expense`. Identified and fixed 9 bugs.

**Bug 1 — Auto-save race on Complete Expense** (`templates/expense/edit.html`)
- `handleCompleteExpense()` was canceling the debounced auto-save timer instead of flushing it
- Fixed: await `autoSaveExpense()` before sending the complete request (mirrors Bill fix)

**Bug 2 — Delete without auto-save guard** (`templates/expense/edit.html`)
- `deleteExpense()` did not set `isSaving = true` before canceling the timer, allowing a pending auto-save to fire after delete
- Fixed: set `isSaving = true` at the top of `deleteExpense()`

**Bug 3 — Float precision loss on Decimal fields** (`entities/expense/api/router.py`, `entities/expense_line_item/api/router.py`)
- `float(body.total_amount)` and similar conversions introduced floating-point rounding errors on financial values
- Fixed: replaced all `float(...)` with `Decimal(str(...)) if value is not None else None`

**Bug 4 — Float precision in complete_expense()** (`entities/expense/business/service.py`)
- `complete_expense()` passed `float(expense.total_amount)` to internal services
- Fixed: same `Decimal(str(...))` pattern applied throughout

**Bug 5 — Wrong module fallback in _upload_attachments_to_module_folder** (`entities/expense/business/service.py`)
- Fell back to "Bills" module if "Expenses"/"Expense" not found, uploading expense files into the Bills SharePoint folder
- Also had a last-resort `read_all()[0]` fallback which could silently upload to any random module
- Fixed: return `{"success": False, "message": "Expense module not found..."}` if neither "Expenses" nor "Expense" found

**Bug 6 — Success flag ignored synced_count** (`entities/expense/business/service.py`)
- `_upload_attachments_to_module_folder` and `_sync_to_excel_workbook` returned `"success": synced_count > 0 or not errors` — zero files with no errors returned success=False
- Fixed: changed to `"success": not errors`

**Bug 7 — Expense 404 crash in web controller** (`entities/expense/web/controller.py`)
- `view_expense` called `expense.to_dict()` without null-checking, crashing with AttributeError for missing expenses
- Fixed: added `if not expense: raise HTTPException(status_code=404)`

**Bug 8 — Missing cascade delete on ExpenseLineItem** (`entities/expense_line_item/business/service.py`)
- `delete_by_public_id()` deleted the ExpenseLineItem directly, leaving orphaned ExpenseLineItemAttachment, Attachment records, and Azure blobs
- Fixed: cascade delete order — blob → Attachment record → ExpenseLineItemAttachment link → ExpenseLineItem

**Bug 9 — Raw SQL in ExpenseLineItemAttachment repo** (`entities/expense_line_item_attachment/persistence/repo.py`, `sql/dbo.expense_line_item_attachment.sql`)
- `read_by_expense_line_item_public_ids()` built a raw SQL query with an IN clause instead of using a stored procedure
- Fixed: replaced with `call_procedure("ReadExpenseLineItemAttachmentsByExpenseLineItemPublicIds", ...)` using STRING_SPLIT
- Also added FK constraints, UNIQUE constraint, and indexes to the SQL table definition (with idempotent migration blocks)

#### Removed Expense Processing from BillAgent Scheduler

- Identified that `core/ai/agents/bill_agent/scheduler.py` was running both `run_bill_folder_processing` and `run_expense_folder_processing` every 30 minutes
- Removed the `# --- Expense processing ---` block (lines 37–56) at user's request
- Updated docstring and logger message to no longer reference ExpenseAgent

### Files Modified
- `entities/expense/business/service.py` — Decimal precision fix, module fallback fix, success flag fix
- `entities/expense/api/router.py` — Decimal precision fix in update payload
- `entities/expense/web/controller.py` — 404 guard in view_expense
- `entities/expense_line_item/business/service.py` — cascade delete (blob → attachment → link → line item)
- `entities/expense_line_item/api/router.py` — Decimal precision fix in create/update payloads
- `entities/expense_line_item_attachment/persistence/repo.py` — replaced raw SQL with stored procedure call
- `entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql` — FK constraints, UNIQUE constraint, indexes, new `ReadExpenseLineItemAttachmentsByExpenseLineItemPublicIds` sproc
- `templates/expense/edit.html` — auto-save flush on complete, isSaving guard on delete
- `core/ai/agents/bill_agent/scheduler.py` — removed expense processing block

### Pending
- Run SQL migration: `python scripts/run_sql.py entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql`

---

## Session: BillLineItem Delete & QBO Sync Deduplication Fix (March 11, 2026)

### What Was Fixed

#### 1. BillLineItem Delete FK Violation
- **Bug**: Deleting a BillLineItem failed with `FK_InvoiceLineItem_BillLineItem` constraint violation when an InvoiceLineItem referenced it
- **Root cause**: `BillLineItemService.delete_by_public_id()` didn't handle the InvoiceLineItem FK dependency, and the FK has no CASCADE DELETE
- **Fix**: Added `NullifyInvoiceLineItemsByBillLineItemId` stored procedure that sets `BillLineItemId = NULL` on referencing InvoiceLineItem rows. Called from `BillLineItemService.delete_by_public_id()` before deleting the BillLineItem. This preserves the InvoiceLineItem records (description, amount, etc.) while breaking the FK link.

#### 2. QBO Sync Line Item Deduplication
- **Bug**: Bill with `public_id=4AE71E1F-A92F-4DF8-A5F2-C6CD24D9DAC8` had two BillLineItems — one with an attachment (original), one linked to QBO (duplicate created by sync)
- **Root cause**: `sync_to_qbo_bill()` stored QboBillLine records locally but never created `BillLineItemBillLine` mappings. When a subsequent `sync_from_qbo` ran, QBO lines appeared unmapped, so `BillLineItemConnector.sync_from_qbo_bill_line()` created duplicate BillLineItems.
- **Fix**: After storing QboBillLines in `sync_to_qbo_bill()`, now creates `BillLineItemBillLine` mappings by matching `line_num` between the request lines and QBO API response lines. Also changed `_store_qbo_bill_line()` to return the created record (was void) so its ID can be used for the mapping.

### Files Modified
- `entities/bill_line_item/business/service.py` — nullify InvoiceLineItem FKs before delete
- `entities/invoice_line_item/sql/dbo.invoice_line_item.sql` — new `NullifyInvoiceLineItemsByBillLineItemId` stored procedure
- `entities/invoice_line_item/persistence/repo.py` — new `nullify_bill_line_item_id()` method
- `integrations/intuit/qbo/bill/connector/bill/business/service.py` — `line_num_to_line_item_id` tracking, line item mapping creation in `sync_to_qbo_bill()`, `_store_qbo_bill_line()` returns created record

---

## Session: Contact Entity Module (March 11, 2026)

### What Was Built

**Contact** — A polymorphic child entity for storing contact details (email, phone, fax, etc.) linked to User, Company, Customer, Project, and Vendor entities via nullable FK columns. Each parent can have multiple contacts. Managed inline on parent pages using reusable Jinja2 partials.

#### Contact Entity (Full CRUD)
- `dbo.Contact` table with nullable FKs: UserId, CompanyId, CustomerId, ProjectId, VendorId
- Fields: Email (NVARCHAR 255), OfficePhone (NVARCHAR 50), MobilePhone (NVARCHAR 50), Fax (NVARCHAR 50), Notes (NVARCHAR MAX)
- 11 stored procedures: Create, ReadAll, ReadById, ReadByPublicId, ReadByUserId/CompanyId/CustomerId/ProjectId/VendorId, UpdateById, DeleteById
- Full entity module: model, repository, service, API schemas, API router (ProcessEngine instant)

#### Inline UI on Parent Pages
- **Reusable partials**: `shared/partials/contacts_view.html` (read-only table) and `shared/partials/contacts_edit.html` (inline CRUD with JS)
- **Edit partial**: Add Contact form, per-row inline editing (onchange updates via API), delete per row with confirmation
- **View partial**: Read-only table showing all contacts
- Wired into all 5 parent entities (User, Company, Customer, Project, Vendor) — both view and edit pages
- CSS: `static/css/contact.css`

#### Workflow Registration
- Added `"contact"` to `SYNCHRONOUS_TASKS` in `core/workflow/business/definitions/instant.py`
- Added `"contact"` to `PROCESS_REGISTRY` in `core/workflow/business/instant.py`
- Registered API router in `app.py`

### Files Created
- `entities/contact/sql/dbo.contact.sql`
- `entities/contact/business/model.py`
- `entities/contact/persistence/repo.py`
- `entities/contact/business/service.py`
- `entities/contact/api/schemas.py`
- `entities/contact/api/router.py`
- `entities/contact/__init__.py`, `api/__init__.py`, `business/__init__.py`, `persistence/__init__.py`
- `templates/shared/partials/contacts_view.html`
- `templates/shared/partials/contacts_edit.html`
- `static/css/contact.css`

### Files Modified
- `app.py` — imported and registered contact API router
- `core/workflow/business/definitions/instant.py` — added "contact" to SYNCHRONOUS_TASKS
- `core/workflow/business/instant.py` — added ContactService to PROCESS_REGISTRY
- `entities/user/web/controller.py` — ContactService import, fetch contacts in view/edit
- `entities/company/web/controller.py` — ContactService import, fetch contacts in view/edit
- `entities/customer/web/controller.py` — ContactService import, fetch contacts in view/edit
- `entities/project/web/controller.py` — ContactService import, fetch contacts in view/edit
- `entities/vendor/web/controller.py` — ContactService import, fetch contacts in view/edit
- `templates/user/view.html`, `edit.html` — contact.css + partial includes
- `templates/company/view.html`, `edit.html` — contact.css + partial includes
- `templates/customer/view.html`, `edit.html` — contact.css + partial includes
- `templates/project/view.html`, `edit.html` — contact.css + partial includes
- `templates/vendor/view.html`, `edit.html` — contact.css + partial includes

### Design Decisions
- **Nullable FK columns** (not join table or generic FK) — simplest approach, consistent with codebase patterns
- **No firstname/lastname/title** — Contact stores only communication details, not identity info
- **Inline UI via reusable partials** — same pattern as UserRole on User pages, but using `{% include %}` partials for DRY across 5 parent entities
- **Instant workflow** — uses ProcessEngine.execute_synchronous for audit trail, same as UserRole

---

## Session: RBAC Wiring — Role into User, UserRole, RoleModule (March 11, 2026)

### What Was Built

Wired the Role entity into the UserRole and RoleModule join table UIs, and added inline role assignment to the User entity pages.

#### UserRole & RoleModule — Dropdown + Name Resolution
- **Controllers** (`entities/user_role/web/controller.py`, `entities/role_module/web/controller.py`):
  - Import and load related services (UserService, RoleService, ModuleService)
  - Create/edit routes pass entity lists for dropdown population
  - List/view routes pass lookup maps (`user_map`, `role_map`, `module_map`) for UUID-to-name resolution
  - Added missing `current_path` to all template contexts
  - Fixed template directory from `templates/user_role` to `templates` with prefixed paths
- **Templates** (8 files across `templates/user_role/` and `templates/role_module/`):
  - Dropdowns now use `public_id` for values (was `id` — BIGINT vs UNIQUEIDENTIFIER mismatch)
  - List/view pages show human-readable names instead of raw UUIDs
  - Fixed broken navigation links (`/user_roles/list` → `/user_role/list`, `/role_modules/list` → `/role_module/list`)

#### User Entity — Inline Role Assignment
- **Controller** (`entities/user/web/controller.py`):
  - Imports RoleService and UserRoleService
  - `create_user` passes `roles` list for dropdown
  - `view_user` resolves current role name via UserRoleService → RoleService
  - `edit_user` passes `roles` list + current `user_role` (if any)
- **Templates**:
  - `templates/user/create.html` — Role dropdown (optional). After user creation, creates UserRole via API if role selected
  - `templates/user/edit.html` — Role dropdown pre-selected with current role. Handles three cases on save: create (new assignment), update (role changed), delete (role cleared)
  - `templates/user/view.html` — Displays resolved role name (or "No role assigned")

### Files Modified
- `entities/user/web/controller.py` — RoleService/UserRoleService imports, role data in create/view/edit contexts
- `entities/user_role/web/controller.py` — UserService/RoleService imports, lookup maps, template fixes
- `entities/role_module/web/controller.py` — RoleService/ModuleService imports, lookup maps, template fixes
- `templates/user/create.html` — role dropdown + JS role assignment after create
- `templates/user/edit.html` — role dropdown + JS create/update/delete role assignment
- `templates/user/view.html` — role name display
- `templates/user_role/list.html` — name resolution via maps
- `templates/user_role/view.html` — name resolution, fixed links
- `templates/user_role/create.html` — public_id for dropdown values
- `templates/user_role/edit.html` — public_id for dropdown values + selected comparison
- `templates/role_module/list.html` — name resolution via maps
- `templates/role_module/view.html` — name resolution, fixed links
- `templates/role_module/create.html` — public_id for dropdown values
- `templates/role_module/edit.html` — public_id for dropdown values + selected comparison

### Bug Fixes
- **Dropdown value mismatch**: Templates used `id` (BIGINT) for dropdown values but join tables store `public_id` (UNIQUEIDENTIFIER) — selected state and submitted values never matched
- **Missing `current_path`**: All UserRole and RoleModule template contexts were missing `current_path: request.url.path` (required by sidebar)
- **Broken nav links**: View templates had plural routes (`/user_roles/list`, `/role_modules/list`) that don't exist

### Remaining Work
- **Authorization middleware**: Build middleware/dependency that checks current user's role(s) via UserRole → Role → RoleModule chain to gate access to modules
- **Sidebar integration**: Register Role in the Modules table for sidebar navigation
- **Default role seeding**: Create initial roles (e.g., Admin, Project Manager, Viewer)

---

## Session: Role Entity Module (March 11, 2026)

### What Was Built

**Role** — A standalone RBAC entity completing the authorization chain: User → UserRole → **Role** → RoleModule → Module. Both UserRole and RoleModule already existed and referenced `role_id`, but the Role entity itself was missing.

#### Role Entity (Full CRUD)
- `dbo.Role` table with `Name` (NVARCHAR(255)) field + standard fields (Id, PublicId, RowVersion, timestamps)
- 7 stored procedures: Create, ReadAll, ReadById, ReadByPublicId, ReadByName, UpdateById, DeleteById
- Full entity module: model, repository, service, API router (5 endpoints via ProcessEngine), web controller (4 routes)
- Templates: list (card grid), create, view, edit — all following User entity pattern
- CSS: `static/css/role.css`

#### Workflow Registration
- Added `"role"` to `SYNCHRONOUS_TASKS` in `core/workflow/business/definitions/instant.py`
- Added `"role"` to `PROCESS_REGISTRY` in `core/workflow/business/instant.py`
- Registered routers in `app.py`

### Files Created
- `entities/role/sql/dbo.role.sql`
- `entities/role/business/model.py`
- `entities/role/persistence/repo.py`
- `entities/role/business/service.py`
- `entities/role/api/schemas.py`
- `entities/role/api/router.py`
- `entities/role/web/controller.py`
- `templates/role/list.html`, `create.html`, `view.html`, `edit.html`
- `static/css/role.css`

### Files Modified
- `core/workflow/business/definitions/instant.py` — added "role" to SYNCHRONOUS_TASKS
- `core/workflow/business/instant.py` — added "role" to PROCESS_REGISTRY
- `app.py` — imported and registered role API + web routers

### Remaining Work
- ~~**Wire Role into UserRole/RoleModule**~~ — DONE (March 11, 2026 session above)
- **Authorization middleware**: Build middleware/dependency that checks current user's role(s) via UserRole → Role → RoleModule chain to gate access to modules
- **Sidebar integration**: Register Role in the Modules table for sidebar navigation
- **Role seeding**: Create default roles (e.g., Admin, Project Manager, Viewer)

---

## Session: Bill Entity — Email Display, Delete Fix, QBO Sync Fix (March 11, 2026)

### What Was Built

#### 1. Inline Source Email Display on Bill Edit/View Pages
- Added AJAX endpoint `GET /inbox/message/{message_id}/detail` on inbox controller — returns full email details as JSON
- Bill edit and view templates now show a "Show Source Email" toggle button that loads the linked email inline (lazy-loaded on first click)
- "Open in Outlook" link populated from `email.web_link` after AJAX fetch
- Source email lookup added to `view_bill` controller (was already in `edit_bill`)

#### 2. Bill Delete Cascade Fix
- **Bug**: Deleting a draft bill from `/bill/edit` failed with "BillLineItemService is not defined"
- **Root cause 1**: `delete_by_public_id()` used bare class names (`BillLineItemService()`) instead of `self.bill_line_item_service` — the classes are lazy-imported in `__init__`, not at module level
- **Root cause 2**: Attachment cleanup exceptions could skip line item deletion due to shared try-except block
- **Fix**: Changed to `self.*` instance references; separated attachment cleanup and line item delete into independent try-except blocks
- Added `isSaving = true` guard in `deleteBill()` JS to prevent auto-save racing during delete

#### 3. QBO Sync — Missing SubCostCode Fix
- **Bug**: "QBO sync skipped: Bill has 1 line item(s) but none have QBO Item mappings" after completing a bill where SubCostCode was visibly selected
- **Root cause 1**: Copilot agent's `create_bill_from_extraction()` was not passing `sub_cost_code_id` when creating line items
- **Root cause 2**: `handleCompleteBill()` was canceling pending auto-saves instead of flushing them — if user selected SubCostCode and immediately clicked Complete, the 300ms debounced save was lost
- **Fix**: Added `sub_cost_code_id` to copilot tool's line item creation; changed Complete Bill to `await` pending auto-saves before sending the complete request

#### 4. Complete Bill Validation
- Added client-side validation in `validateBillForm()` that all saved line items have a Sub Cost Code selected before allowing Complete Bill

### Files Modified
- `entities/bill/web/controller.py` — source_email lookup in view_bill
- `entities/bill/business/service.py` — delete cascade fix (self.* references, separated try-except)
- `entities/inbox/web/controller.py` — new `/message/{message_id}/detail` JSON endpoint
- `templates/bill/edit.html` — inline email section, delete guard, auto-save flush on complete, SubCostCode validation
- `templates/bill/view.html` — inline email section, toggle button, Outlook link
- `core/ai/agents/copilot_agent/graph/tools.py` — added sub_cost_code_id to create_bill_from_extraction

---

## Session: SubCostCode Entity Module (March 11, 2026)

### What Was Built

**SubCostCodeAlias** — A child entity for SubCostCode that supports agentic fuzzy matching in BillAgent and ExpenseAgent.

#### Alias Entity (Separate Table — Option A)
- `dbo.SubCostCodeAlias` table with stored procedures
- Full business layer: model (`alias_model.py`), repository (`alias_repo.py`), service (`alias_service.py`)
- API endpoints: POST create, GET by sub_cost_code_id, DELETE by public_id (direct CRUD, no workflow engine)
- Pydantic schemas: `SubCostCodeAliasCreate`, `SubCostCodeAliasUpdate`

#### Agent Integration
- BillAgent and ExpenseAgent `_resolve_sub_cost_code()` now falls back to alias matching
- Checks both normalized format and raw input value against alias table
- Loads `sub_cost_code_aliases` as reference data during processing

#### UI Enhancements
- **Edit page** (`templates/sub_cost_code/edit.html`): Aliases card with inline AJAX add/remove
- **View page** (`templates/sub_cost_code/view.html`): Read-only aliases section + Intuit QBO Item section
- QBO Item display uses two-step lookup: mapping via `ItemSubCostCodeConnector`, then item via `QboItemService`

#### Bug Fixes (Pre-existing)
- Fixed `TemplateNotFound` — changed `Jinja2Templates(directory="templates")` and prefixed template names
- Fixed `current_path is undefined` — added to all four route template contexts

### Files Created
- `entities/sub_cost_code/sql/dbo.subcostcodealias.sql`
- `entities/sub_cost_code/business/alias_model.py`
- `entities/sub_cost_code/persistence/alias_repo.py`
- `entities/sub_cost_code/business/alias_service.py`

### Files Modified
- `entities/sub_cost_code/api/schemas.py` — alias Pydantic models
- `entities/sub_cost_code/api/router.py` — alias API endpoints
- `entities/sub_cost_code/web/controller.py` — template fixes, alias + QBO loading
- `templates/sub_cost_code/edit.html` — alias management UI
- `templates/sub_cost_code/view.html` — aliases + QBO item display
- `core/ai/agents/bill_agent/business/processor.py` — alias fallback matching
- `core/ai/agents/expense_agent/business/processor.py` — alias fallback matching

### Deferred Work Update
- **SubCostCode alias table** — NOW IMPLEMENTED (was deferred from BillAgent session)

---

# Session: BillAgent (March 2026)

## What Was Built

**BillAgent** — An automated system that processes PDF invoices from a SharePoint folder, extracts bill data, and creates bill drafts in the application.

### Architecture (7 Phases)

1. **Database — Bill Folder Connector** (`integrations/ms/sharepoint/driveitem/connector/bill_folder/`)
   - `ms.DriveItemBillFolder` table linking SharePoint folders to companies with `FolderType` discriminator (`source` / `processed`)
   - Model, repository, connector service, and API router

2. **SharePoint Client — `move_item()` and `delete_item()`** (`integrations/ms/sharepoint/external/client.py`)
   - `move_item()` — PATCH `/drives/{drive_id}/items/{item_id}` to move files between folders
   - `delete_item()` — DELETE `/drives/{drive_id}/items/{item_id}` for cleanup before moves
   - Service wrappers in `integrations/ms/sharepoint/driveitem/business/service.py`

3. **Bill Folder Processing** (`core/ai/agents/bill_agent/`)
   - **Processor** (`business/processor.py`) — Deterministic processing loop:
     - Lists PDFs in source SharePoint folder
     - Parses 7-segment filenames: `{Project} - {Vendor} - {BillNumber} - {Description} - {SubCostCode} - {Rate} - {BillDate}`
     - Runs Azure Document Intelligence OCR + Claude extraction for supplemental data
     - Merges results (filename fields take priority over OCR)
     - Creates bill draft with line items and attachment
     - Moves processed file to processed folder (delete-then-move pattern for conflicts)
   - **Models** (`business/models.py`) — `BillAgentRun`, `ProcessingResult`, `FilenameParsedData`
   - **Runner** (`business/runner.py`) — Entry point wrapping processor with run tracking
   - **Service** (`business/service.py`) — Run lifecycle management
   - **Repository** (`persistence/repo.py`) — `BillAgentRun` persistence

4. **BillAgent API** (`core/ai/agents/bill_agent/api/`)
   - `POST /api/v1/bill-agent/run` — Trigger processing (background task, returns 202)
   - `GET /api/v1/bill-agent/run/{public_id}` — Check run status
   - `GET /api/v1/bill-agent/runs` — List recent runs
   - `GET /api/v1/bill-agent/folder-status` — Source folder file count for UI

5. **Scheduler** (`core/ai/agents/bill_agent/scheduler.py`)
   - Async background scheduler running at configurable interval (default 30 min)
   - Registered in `app.py` startup/shutdown events

6. **Bill List UI** (`templates/bill/list.html`, `static/css/bill.css`)
   - Folder summary section showing file count and "Process Folder" button
   - JavaScript for triggering processing and polling for completion

7. **Company Settings UI** (`templates/company/view.html`)
   - Bill Processing Folders section with SharePoint folder picker for source and processed folders

### Key Implementation Details

- **PaymentTerms**: All bill drafts set to "Due on receipt" — looked up once during reference data loading, passed through to `bill_service.create()`
- **Bill line items**: Created with `markup=Decimal("0")` and `price=rate`
- **File move conflicts**: Uses delete-then-move pattern — lists processed folder children, finds existing file by name, deletes it, then retries the move
- **SubCostCode matching**: Normalizes decimal format (e.g., `18.1` → `18.01`) before matching against `sub_cost_code.number`
- **Entity resolution**: Fuzzy matching for Project (prefix match), Vendor (Jaccard/containment), SubCostCode (normalized number match)
- **Error handling**: Failed files are skipped and left in source folder; processing continues with remaining files

## Results

- Working well in production. Most files process correctly.
- Occasional vendor mismatches and some sub cost codes not resolved — handled by draft review workflow.

## Deferred Work

- **SubCostCode alias table** — For commonly missed codes where the filename abbreviation doesn't match the database value. Explicitly deferred ("That is for another time").
- **LLM fallback for entity resolution** — Discussed and decided against. Draft workflow handles edge cases well enough. Would add cost, latency, and risk of wrong matches.

## File Inventory

### New Files Created
- `integrations/ms/sharepoint/driveitem/connector/bill_folder/` — full package (sql, model, repo, service, API)
- `core/ai/agents/bill_agent/` — full package (models, processor, runner, service, repo, API, scheduler, sql)
- Various `__init__.py` files for new packages

### Modified Files
- `integrations/ms/sharepoint/external/client.py` — added `move_item()`, `delete_item()`
- `integrations/ms/sharepoint/driveitem/business/service.py` — added `move_item()` wrapper
- `app.py` — registered new routers + scheduler startup/shutdown
- `entities/bill/web/controller.py` — bill folder summary for list page
- `templates/bill/list.html` — folder summary UI section
- `static/css/bill.css` — folder summary styles
- `entities/company/web/controller.py` — bill folder data in template context
- `templates/company/view.html` — Bill Processing Folders section with picker
