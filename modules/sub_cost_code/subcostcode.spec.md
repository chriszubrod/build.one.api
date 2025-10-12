# Agent Playbook – Sub Cost Code Module

## 1. Module Layout & Flow

- **API layer (`modules/sub_cost_code/api`)**
  - `router.py`: FastAPI `APIRouter` registered at `/api/v1` with tags `["api", "sub-cost-code"]`. Routes call into `SubCostCodeService`, convert returned business objects via `.to_dict()`, and funnel all `DatabaseError` instances through `_handle_database_error` (409 on concurrency/duplicate text, 422 when the message mentions a missing parent cost code, otherwise 500).
  - `schemas.py`: Pydantic models `SubCostCodeCreate` and `SubCostCodeUpdate`. Payloads use the raw `cost_code_id` (database GUID string) and send `row_version` as a base64 string on updates.
- **Business layer (`modules/sub_cost_code/business`)**
  - `model.py`: `@dataclass SubCostCode` with fields `id`, `public_id`, `row_version`, `created_datetime`, `modified_datetime`, `number`, `name`, `description`, `cost_code_id`. Provides `row_version_bytes`/`row_version_hex` helpers plus `to_dict()` for template/API serialization.
  - `service.py`: Wraps `SubCostCodeRepository`. Exposes `create`, `read_all`, `read_by_id`, `read_by_public_id`, `read_by_number`, `read_by_cost_code_id`, `update_by_public_id`, and `delete_by_public_id`. `update_by_public_id` loads the current record, overlays values from a `SubCostCodeUpdate` payload, then calls `repo.update_by_id`.
- **Persistence layer (`modules/sub_cost_code/persistence`)**
  - `repo.py`: Synchronous `pyodbc` repository that uses `shared.database.get_connection`, `call_procedure`, and `map_database_error`. Serializes `RowVersion` to base64, maps `pyodbc.Row` objects into dataclasses, and delegates to sprocs: `CreateSubCostCode`, `ReadSubCostCodes`, `ReadSubCostCodeById`, `ReadSubCostCodeByPublicId`, `ReadSubCostCodeByNumber`, `ReadSubCostCodeByCostCodeId`, `UpdateSubCostCodeById`, `DeleteSubCostCodeById`.
- **Web layer (`modules/sub_cost_code/web`)**
  - `controller.py`: FastAPI router mounted at `/sub-cost-code` using `Jinja2Templates(directory="templates/sub_cost_code")`. Reads data via `SubCostCodeService` and fetches parent cost codes through `CostCodeService` for dropdowns and detail views.
- **Templates (`templates/sub_cost_code`)**
  - `list.html`, `view.html`, `create.html`, `edit.html`: Bootstrap 5 + FontAwesome pages that drive CRUD flows with vanilla JS `fetch` calls to the API. Forms submit JSON bodies containing `cost_code_id` (and `row_version` for edits/deletes).
- **Database (`sql/dbo.subcostccode.sql`)**
  - Defines the `dbo.SubCostCode` table and all sprocs referenced above. Sproc outputs convert datetimes to `VARCHAR(19)` in `YYYY-MM-DD HH:MM:SS` format.

Execution path: API/web controller → `SubCostCodeService` → `SubCostCodeRepository` → stored procedures → repository dataclass → service → route/template.

## 2. Domain Model & Data Rules

- Sub cost codes link to a parent cost code via `CostCodeId` (a GUID stored in the sub cost code table). Routes expose and accept only the raw `CostCodeId`; public IDs are used solely for lookup endpoints.
- Table schema includes `Id`, `PublicId`, `RowVersion`, `CreatedDatetime`, `ModifiedDatetime`, `Number`, `Name`, `Description`, `CostCodeId`. Datetimes are recorded with `SYSUTCDATETIME()` and surfaced as SQL-formatted strings.
- Unique constraint `UQ_SubCostCode_Number` enforces global uniqueness on `Number` (not scoped within a cost code).
- Stored procedures do not enforce tenant scoping or validate that `CostCodeId` exists in `dbo.CostCode`.
- Optimistic concurrency hinges on `RowVersion` (binary `ROWVERSION` mapped to base64 in the API). The update sproc accepts `@CostCodeId` but does not modify that column, so parent reassignment is effectively ignored.
- Deletes are hard deletes; there is no soft delete flag. The delete sproc returns the removed row for confirmation.

## 3. API Contract

- `POST /api/v1/create/sub-cost-code`
  - Body: `SubCostCodeCreate` (`number`, `name`, optional `description`, `cost_code_id`).
  - Response: dictionary representation containing identifiers, base64 `row_version`, timestamps, and the persisted `cost_code_id`.
  - Errors: duplicate/unique/conflict text → HTTP 409; `"parent cost code not found"` → HTTP 422; unhandled `DatabaseError` → HTTP 500.
- `GET /api/v1/get/sub-cost-codes`
  - Returns a list of all sub cost codes ordered by number (no filters or paging).
- `GET /api/v1/get/sub-cost-code/{public_id}`
  - Looks up by `PublicId`. Missing records raise HTTP 404.
- `PUT /api/v1/update/sub-cost-code/{public_id}`
  - Body: `SubCostCodeUpdate` (`row_version`, `number`, `name`, optional `description`, `cost_code_id`).
  - Service copies the payload into the existing dataclass before calling `repo.update_by_id`. Row version mismatches raise HTTP 409.
- `DELETE /api/v1/delete/sub-cost-code/{public_id}`
  - Executes a hard delete via `repo.delete_by_id` and returns the deleted record. Missing rows emit HTTP 404. The route ignores request bodies even though the web client currently sends `{ "row_version": ... }`.

All routes share `_handle_database_error`, which inspects exception text to determine status codes.

## 4. Business, Persistence, and SQL Expectations

- `SubCostCodeService` instantiates a repository when one is not supplied, providing simple orchestration over repository calls.
- Repository methods wrap each stored procedure invocation in try/except, forwarding errors through `map_database_error` so the API layer always receives `DatabaseError` subclasses.
- `_from_db` base64-encodes `RowVersion` bytes and returns populated `SubCostCode` dataclasses; timestamp strings are passed through unchanged.
- Stored procedures:
  - `CreateSubCostCode` inserts a row, sets both timestamps to `SYSUTCDATETIME()`, and outputs the inserted record.
  - `Read` sprocs fetch rows ordered by `Number` with filters for `Id`, `PublicId`, `Number`, and `CostCodeId`.
  - `UpdateSubCostCodeById` matches on `Id` and `RowVersion`, updating `Number`, `Name`, `Description`, and `ModifiedDatetime` (parent cost code is not changed).
  - `DeleteSubCostCodeById` performs a hard delete and outputs the deleted row.
- No sprocs accept tenant identifiers or verify cost code ownership.

## 5. Web Experience

- `/sub-cost-code/list`: renders all sub cost codes as Bootstrap cards with links to view and edit routes.
- `/sub-cost-code/create`: loads cost codes via `CostCodeService.read_all()`, renders a form, and posts JSON to `POST /api/v1/create/sub-cost-code`.
- `/sub-cost-code/{public_id}`: fetches the sub cost code plus its parent cost code (via `CostCodeService.read_by_id`) for display, with client-side delete support.
- `/sub-cost-code/{public_id}/edit`: pre-populates the form with the record and cost code options; submit issues a PUT request, and the Delete button hits the DELETE endpoint.
- Client-side JavaScript uses vanilla `fetch`, expects JSON error payloads with a `detail` property, and redirects to `/sub-cost-code/list` on success.

## 6. Acceptance Criteria

- API responses expose `RowVersion` as base64 strings, and `_handle_database_error` maps repository errors to the documented HTTP codes.
- Updates reuse the existing record’s `Id` and `RowVersion`; mismatches propagate to HTTP 409 via `DatabaseConcurrencyError`.
- Repository methods return populated dataclasses; `None` denotes missing rows and drives the 404 handling in the API.
- SQL artifacts in `sql/dbo.subcostccode.sql` remain the single source for schema and sprocs referenced by the repository.
- Bootstrap/FontAwesome assets are expected by the templates, and context dictionaries must provide the keys used in the markup.
- Cost code dropdowns assume `CostCodeService` results expose `.id` and `.name` attributes.
