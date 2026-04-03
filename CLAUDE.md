At the start of each session, read SESSION_NOTES.md for historical context.

## Working Style

- **Plan before coding.** Propose a step-by-step plan and wait for approval before writing any code. Do not start implementing until the plan is confirmed.

## Architecture Decisions (April 2026)

- **Claude Agent SDK is the only agent framework.** Never use LangGraph, LangChain, or any LangChain ecosystem package. All AI features use the `anthropic` SDK directly or Claude Agent SDK.
- **`core/ai/` no longer exists.** The entire agent layer was removed during the April 2026 strip-and-clean. It is being rebuilt from scratch. Do not reference or attempt to import from `core.ai`.
- **`core/notifications/` no longer exists.** Push notifications (APNs), device tokens, and SLA scheduler were removed. Do not reference `core.notifications`, `device_token`, or push_service.
- **Inbox classification is stubbed.** `_classify_message()` and `_classify_message_heuristic()` in `entities/inbox/business/service.py` return None. The email scheduler was removed. These need to be rebuilt.
- **Extraction pipeline is 2-tier.** `ClaudeExtractionService` (raw anthropic SDK, single Haiku call) → heuristic `BillExtractionMapper` fallback. The LangGraph extraction agent was removed.
- **Azure OpenAI embeddings only.** `shared/ai/embeddings.py` requires `AZURE_OPENAI_ENDPOINT` configured. No local sentence-transformers/torch fallback.
- **Bill/expense folder processing removed.** The bill_agent and expense_agent (folder scanners) were deleted. "Process Folder" buttons on list pages will 404 until rebuilt.

## Project Conventions

- **Entity pattern**: `entities/{name}/` with `api/`, `business/`, `persistence/`, `sql/`, `web/` sub-packages
- **SQL**: All DB access via stored procedures (pyodbc). Migrations run with `python scripts/run_sql.py path/to/file.sql`
- **Concurrency**: SQL Server ROWVERSION columns with base64 encoding for transport
- **Templates**: Jinja2 in `templates/` directory, extend `shared/layout/base.html`. All routes must pass `current_path: request.url.path`
- **Workflow engine**: ProcessEngine for main entity CRUD; lightweight child entities use direct CRUD
- **Lazy imports**: Some services (e.g., BillService) use lazy imports in `__init__` to avoid circular deps — always use `self.*` instance attributes, not bare class names
- **Stored procedure NULL handling**: UPDATE sprocs unconditionally SET all columns. Use CASE WHEN guards for fields that should preserve existing values when NULL is passed
- **Auto-save**: Bill and Expense edit pages debounce saves at 300ms. Any action that depends on persisted state (Complete, Delete) must flush (`await autoSave()`) or guard (`isSaving = true`) against pending auto-saves before sending the request
- **RBAC chain**: User → UserRole → Role → RoleModule → Module. Role entity is the core — UserRole and RoleModule are join tables. Role assignment is managed inline on User create/edit pages. Authorization middleware not yet implemented
- **Join table UI pattern**: Join tables (UserRole, RoleModule) resolve FK UUIDs to names via lookup maps (`user_map`, `role_map`, `module_map`) passed from controllers. Dropdown values use `public_id` (UNIQUEIDENTIFIER), not internal `id` (BIGINT)
- **Contact entity**: Polymorphic child entity with nullable FKs to User, Company, Customer, Project, Vendor. Fields: Email, OfficePhone, MobilePhone, Fax, Notes. Managed inline on parent view/edit pages via reusable partials (`shared/partials/contacts_view.html`, `shared/partials/contacts_edit.html`). Uses instant workflow (ProcessEngine.execute_synchronous)
- **FK cascade on delete**: When deleting entities referenced by FK, nullify or delete child references first. Examples: `BillLineItemService.delete_by_public_id()` nullifies `InvoiceLineItem.BillLineItemId` before deleting; `ExpenseLineItemService.delete_by_public_id()` deletes blob → Attachment → ExpenseLineItemAttachment → ExpenseLineItem. SQL Server FK constraints have no CASCADE DELETE — application code must handle cleanup
- **Expense entity cascade delete**: blob (Azure) → Attachment record → ExpenseLineItemAttachment link → ExpenseLineItem. Each step in its own try-except so cleanup failures don't block the delete
- **Decimal precision**: All financial fields (rate, amount, markup, price, total_amount) must use `Decimal(str(value))` — never `float()`. Float round-trips corrupt values
- **QBO sync mappings**: `sync_to_qbo_bill()` must create `BillLineItemBillLine` mappings after storing QboBillLines, using `line_num` to correlate local BillLineItems with QBO API response lines. Without these mappings, subsequent `sync_from_qbo` creates duplicate BillLineItems
- **Invoice line item enrichment**: `_enrich_line_items()` in `entities/invoice/web/controller.py` batch-fetches parent data per source type in one DB connection. Returns `vendor_name`, `parent_number`, `source_date`, `sub_cost_code_number/name`, `cost_code_number/name` (via `SubCostCode → CostCode` join), and `attachment_public_id`. Used by both web controller and packet generator.
- **Invoice PDF packet**: `POST /api/v1/generate/invoice/{id}/packet` prepends two reportlab-generated TOC pages (basic: type→vendor; expanded: cost_code→type→vendor with subtotals) before merging attachment PDFs. Uses `pypdf` + `reportlab` (both in requirements). TOC "Type" column is derived from `source_type` — no schema field needed.
- **Invoice save before complete**: `saveInvoice()` in `edit.html` returns `true`/`false`. The Complete submit handler must check the return value and bail early on `false` to prevent completing with stale data.
- **Contract Labor status workflow**: `pending_review` → `ready` → `billed`. An entry is `ready` only when it has a `bill_vendor_id` AND at least one complete line item. `pending_review` means line items are missing or incomplete.
- **Contract Labor IsBillable flag**: `ContractLaborLineItem.IsBillable = false` means the item is shown on the PDF with `$0.00` and excluded from `total_amount`. Use `is_billable is not False` (not `if is_billable`) to handle `None` (default billable) correctly.
- **Contract Labor BillLineItemId FK**: `ContractLaborLineItem.BillLineItemId` links back to the generated `BillLineItem`. The UPDATE sproc uses a `CASE WHEN` guard to preserve it when `NULL` is passed. Always read and re-pass the existing value when updating a line item to avoid wiping the FK.
- **Contract Labor bill_service variable shadowing**: In `_generate_combined_pdf()` and `generate_bills()`, inner loop accumulator vars must not be named `total_amount` — use `scc_amount`/`scc_price` to avoid shadowing the outer bill total.
- **Scroll restoration**: Pages that scroll via `<main id="content" class="main-content overflow-y: auto">` must use `document.getElementById('content').scrollTop`, NOT `window.scrollY` / `window.scrollTo()`. Save to `sessionStorage` on navigation; restore on `DOMContentLoaded` with double `requestAnimationFrame`.
- **Contract Labor import tuple unpack**: `_parse_row()` returns `(dict, skip_reason)` — always unpack with `parsed, skip_reason = self._parse_row(...)`. Assigning to a single variable and calling `.get()` on the tuple crashes at runtime.
- **VENDOR_CONFIG**: Single source of truth for vendor rate/markup in `bill_service.py`. Do not maintain parallel hardcoded JS maps in templates — pass `VENDOR_CONFIG` from the controller and derive JS maps via `{{ vendor_config|tojson }}`.
