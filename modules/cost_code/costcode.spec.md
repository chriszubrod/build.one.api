# Agent Playbook – Cost Code Module

Single-file spec for reproducing the existing Cost Code module exactly as implemented.

## 1. Module Layout & Flow

- **API (`modules/cost_code/api`)**
  - `router.py`: FastAPI `APIRouter(prefix="/api/v1", tags=["api", "cost-code"])` with five routes that call `CostCodeService`. Handlers return dictionaries produced from business models and never raise HTTP errors.
  - `schemas.py`: Two `BaseModel` classes. `CostCodeCreate` collects `number`, `name`, optional `description`. `CostCodeUpdate` includes `row_version` plus the same editable fields.
- **Business (`modules/cost_code/business`)**
  - `model.py`: `@dataclass CostCode` with optional string fields for every column (`id`, `public_id`, `row_version`, timestamps, `number`, `name`, `description`). Stores `row_version` as base64 text and exposes `row_version_bytes`/`row_version_hex` helpers.
  - `service.py`: Thin wrapper around `CostCodeRepository` providing `create`, `read_all`, `read_by_id`, `read_by_public_id`, `read_by_number`, `update_by_public_id`, and `delete_by_public_id`. Update/delete look up the record by `public_id`, mutate the dataclass, then delegate to repository methods that operate on `id`.
- **Persistence (`modules/cost_code/persistence`)**
  - `repo.py`: Uses `shared.database.get_connection`, `.call_procedure`, and `.map_database_error` with `pyodbc`. `_from_db` maps `pyodbc.Row` to `CostCode`, base64-encoding `RowVersion`. Stored procedure names are literal: `CreateCostCode`, `ReadCostCodes`, `ReadCostCodeById`, `ReadCostCodeByPublicId`, `ReadCostCodeByNumber`, `UpdateCostCodeById`, `DeleteCostCodeById`.
  - Update passes `cost_code.row_version_bytes` to satisfy the stored procedure’s `BINARY(8)` parameter. Delete issues a hard delete without row-version checks.
- **Web (`modules/cost_code/web`)**
  - `controller.py`: `APIRouter(prefix="/cost-code", tags=["web", "cost-code"])` with async handlers that still call the synchronous service. Uses `Jinja2Templates(directory="templates/cost_code")` and passes either `CostCode` dataclasses or `cost_code.to_dict()` to the templates.
- **Views (`templates/cost_code/`)**
  - `list.html`, `view.html`, `create.html`, `edit.html` rely on Bootstrap 5.3 + FontAwesome CDNs and a little vanilla JS. All expect a `request` context var. `create.html` posts JSON to `/api/v1/create/cost-code`. `edit.html` and `view.html` trigger `PUT`/`DELETE` requests against `/api/v1/update/cost-code/{public_id}` and `/api/v1/delete/cost-code/{public_id}`. Inline JS uses `fetch`, surfaces alerts on error, and redirects to `/cost-code/list` on success.
- **Database (`sql/dbo.costcode.sql`)**
  - Owns the `dbo.CostCode` table definition plus every stored procedure the repository calls. Procedures return rowsets whose column names exactly match `_from_db` expectations.

Execution path: API/Web handler → `CostCodeService` → `CostCodeRepository` → stored procedure → repository `_from_db` → service → response/template.

## 2. API Contract

| Method | Path | Request body | Behavior |
| --- | --- | --- | --- |
| `POST` | `/api/v1/create/cost-code` | `CostCodeCreate` JSON | Creates a record via `service.create` and returns `cost_code.to_dict()`. |
| `GET` | `/api/v1/get/cost-codes` | None | Returns `[cost_code.to_dict() for cost_code in service.read_all()]`. |
| `GET` | `/api/v1/get/cost-code/{public_id}` | None | Returns `service.read_by_public_id(public_id).to_dict()`. |
| `PUT` | `/api/v1/update/cost-code/{public_id}` | `CostCodeUpdate` JSON | Service copies payload values onto the fetched dataclass and calls `repo.update_by_id`. |
| `DELETE` | `/api/v1/delete/cost-code/{public_id}` | No body handled | Service fetches by public ID then calls `repo.delete_by_id` and returns the deleted record as dict; the handler signature ignores any JSON body clients might send. |

All handlers assume the repository returns a `CostCode`; they do not guard against `None` or map errors to HTTP status codes.

## 3. Business Rules

- Row versions are stored as base64 strings in the dataclass. The helper properties decode/encode as needed for persistence and UI display.
- `CostCodeService.update_by_public_id` mutates the retrieved dataclass in place before calling `repo.update_by_id`. Caller must provide `row_version` (base64) in the update payload.
- `CostCodeService.delete_by_public_id` looks up the record and deletes by internal `id`; there is no concurrency check on delete despite the UI collecting `row_version`.
- `CostCodeService.create`/`read_*` methods are thin proxies with no additional validation or error handling.

## 4. Persistence Expectations

- `_from_db` expects the stored procedures to return: `Id`, `PublicId`, `RowVersion` (`ROWVERSION/BINARY(8)`), `CreatedDatetime`, `ModifiedDatetime`, `Number`, `Name`, `Description`.
- Every repository method wraps database calls in `try/except`, logs via `logging.getLogger(__name__)`, and raises `map_database_error(error)` on failure.
- Stored procedure parameter bindings:
  - Create: `{"Number": str, "Name": str, "Description": Optional[str]}`.
  - Read (all / by id / by public id / by number): `params` dict with the single key expected by the procedure.
  - Update: `{"Id": uuid, "RowVersion": cost_code.row_version_bytes, "Number": str, "Name": str, "Description": Optional[str]}`.
  - Delete: `{"Id": uuid}`.
- `get_connection()` is used as a context manager; cursors come from `.cursor()` on the connection, and `call_procedure` executes the stored proc then leaves the cursor positioned for `fetchone()` / `fetchall()`.

## 5. Web UI

- Router prefix `/cost-code` maps to HTML views:
  - `GET /cost-code/list` → `list.html` with `cost_codes` (list of dataclasses).
  - `GET /cost-code/create` → `create.html` (no cost code data).
- `GET /cost-code/{public_id}` → `view.html` with `cost_code.to_dict()`.
- `GET /cost-code/{public_id}/edit` → `edit.html` with `cost_code.to_dict()`.
- Templates assume Bootstrap utility classes, inline `<style>` tweaks, and FontAwesome icons. `create.html` sets both `number` and `name` `maxlength` to 50/255 respectively, while `edit.html` caps `name` at 50, matching the current markup.
- JavaScript fetch calls point to the API routes listed above, using JSON payloads and redirecting/alerting based on `response.ok`.

## 6. SQL Artifacts

- Table `dbo.CostCode`:
  - Columns: `Id UNIQUEIDENTIFIER` (PK, `NEWSEQUENTIALID()`), `PublicId UNIQUEIDENTIFIER DEFAULT NEWID()`, `RowVersion ROWVERSION`, `CreatedDatetime DATETIME2(3)`, `ModifiedDatetime DATETIME2(3)`, `Number NVARCHAR(50)` (unique), `Name NVARCHAR(255)`, `Description NVARCHAR(255)` (nullable).
  - No tenancy column and no soft-delete flag.
- Stored procedures:
  - `CreateCostCode` inserts and outputs the created row; both timestamps are set with `SYSUTCDATETIME()` and converted to `VARCHAR(19)` for output.
  - `ReadCostCodes` returns all rows ordered by `Number`.
  - `ReadCostCodeById`, `ReadCostCodeByPublicId`, `ReadCostCodeByNumber` each return a single matching row.
  - `UpdateCostCodeById` updates fields when both `Id` and `RowVersion` match, returning the updated row with refreshed timestamps.
  - `DeleteCostCodeById` performs a hard delete and outputs the deleted row (no row version predicate).

## 7. Conventions & Helpers

- Maintain the three comment headers (`# Python Standard Library Imports`, `# Third-party Imports`, `# Local Imports`) even when empty.
- Logging: use `logger = logging.getLogger(__name__)` and log before rethrowing via `map_database_error`.
- Business models should expose a `to_dict()` helper that is used across API and web layers for JSON serialization.
- All timestamps are handled as strings (already formatted in SQL) when crossing service boundaries—no additional conversion performed in Python.

## 8. Environment

- Module relies on shared infrastructure (`shared.database`, global logging setup, and template directory rooted at `templates/`).
- No runtime configuration is injected directly into this module; the repository depends on ambient environment variables read by `shared.database`.
