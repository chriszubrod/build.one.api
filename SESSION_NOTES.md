# Session Notes

## Session: Role Entity Module (March 11, 2026)

### What Was Built

**Role** — A standalone RBAC entity completing the authorization chain: User → UserRole → **Role** → RoleModule → Module. Both UserRole and RoleModule already existed and referenced `role_id`, but the Role entity itself was missing.

#### Role Entity (Full CRUD)
- `dbo.Role` table with `Name` (NVARCHAR(255)) field + standard fields (Id, PublicId, RowVersion, timestamps)
- 7 stored procedures: Create, ReadAll, ReadById, ReadByPublicId, ReadByName, UpdateById, DeleteById
- Full entity module: model, repository, service, API router (5 endpoints via TriggerRouter), web controller (4 routes)
- Templates: list (card grid), create, view, edit — all following User entity pattern
- CSS: `static/css/role.css`

#### Workflow Registration
- Added `"role"` to `INSTANT_ENTITIES` in `core/workflow/business/definitions/instant.py`
- Added `"role"` to `SERVICE_REGISTRY` in `core/workflow/business/instant.py`
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
- `core/workflow/business/definitions/instant.py` — added "role" to INSTANT_ENTITIES
- `core/workflow/business/instant.py` — added "role" to SERVICE_REGISTRY
- `app.py` — imported and registered role API + web routers

### Remaining Work
- **Wire Role into UserRole/RoleModule**: UserRole and RoleModule templates currently show raw UUIDs for role_id — need to populate role dropdown options from RoleService in their web controllers
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
