# Agent Playbook – Sync Module

Single-file spec for reproducing the existing Sync module exactly as implemented.

## 1. Module Layout & Flow

- **API (`modules/sync/api`)**
  - `router.py`: FastAPI `APIRouter(prefix="/api/v1", tags=["api", "sync"])` with five routes that call `SyncService`. Each handler depends on `get_current_sync_api`, returns dictionaries produced from business models, and never raises HTTP errors.
  - `schemas.py`: Two `BaseModel` classes. `SyncCreate` collects `provider`, `env`, `entity`. `SyncUpdate` includes `row_version` plus the same editable fields.
- **Business (`modules/sync/business`)**
  - `model.py`: `@dataclass Sync` with optional string fields for every column (`id`, `public_id`, `row_version`, timestamps, `provider`, `env`, `entity`). Stores `row_version` as base64 text and exposes `row_version_bytes`/`row_version_hex` helpers.
  - `service.py`: Thin wrapper around `SyncRepository` providing `create`, `read_all`, `read_by_id`, `read_by_public_id`, `read_by_provider`, `update_by_public_id`, and `delete_by_public_id`. Update/delete look up the record by `public_id`, mutate the dataclass, then delegate to repository methods that operate on `id`.
- **Persistence (`modules/sync/persistence`)**
  - `repo.py`: Uses `shared.database.get_connection`, `.call_procedure`, and `.map_database_error` with `pyodbc`. `_from_db` maps `pyodbc.Row` to `Sync`, base64-encoding `RowVersion`. Stored procedure names are literal: `CreateSync`, `ReadSyncs`, `ReadSyncById`, `ReadSyncByPublicId`, `ReadSyncByProvider`, `UpdateSyncById`, `DeleteSyncById`.
  - Update passes `sync.row_version_bytes` to satisfy the stored procedure’s `BINARY(8)` parameter. Delete issues a hard delete without row-version checks.
- **Web (`modules/sync/web`)**
  - `controller.py`: `APIRouter(prefix="/sync", tags=["web", "sync"])` with async handlers that still call the synchronous service. Every handler depends on `get_current_sync_web`. Uses `Jinja2Templates(directory="templates/sync")` and passes either `Sync` dataclasses or `sync.to_dict()` to the templates.
- **Views (`templates/sync/`)**
  - `list.html`, `view.html`, `create.html`, `edit.html` rely on Bootstrap 5.3 + FontAwesome CDNs and a little vanilla JS. All expect `request` (and `current_sync` where available) in context. `create.html` posts JSON to `/api/v1/create/sync`. `edit.html` and `view.html` trigger `PUT`/`DELETE` requests against `/api/v1/update/sync/{public_id}` and `/api/v1/delete/sync/{public_id}`. Inline JS uses `fetch`, surfaces alerts on error, and redirects to `/sync/list` on success.
- **Database (`sql/dbo.sync.sql`)**
  - Owns the `dbo.Sync` table definition plus every stored procedure the repository calls. Procedures return rowsets whose column names exactly match `_from_db` expectations.

Execution path: API/Web handler → `SyncService` → `SyncRepository` → stored procedure → repository `_from_db` → service → response/template.

## 2. API Contract

| Method | Path | Request body | Behavior |
| --- | --- | --- | --- |
| `POST` | `/api/v1/create/sync` | `SyncCreate` JSON | Creates a record via `service.create` and returns `sync.to_dict()`. |
| `GET` | `/api/v1/get/syncs` | None | Returns `[sync.to_dict() for sync in service.read_all()]`. |
| `GET` | `/api/v1/get/sync/{public_id}` | None | Returns `service.read_by_public_id(public_id).to_dict()`. |
| `PUT` | `/api/v1/update/sync/{public_id}` | `SyncUpdate` JSON | Service copies payload values onto the fetched dataclass and calls `repo.update_by_id`. |
| `DELETE` | `/api/v1/delete/sync/{public_id}` | No body handled | Service fetches by public ID then calls `repo.delete_by_id` and returns the deleted record as dict; the handler signature ignores any JSON body clients might send. |

All handlers assume the repository returns a `Sync`; they do not guard against `None` or map errors to HTTP status codes.

## 3. Business Rules

- Row versions are stored as base64 strings in the dataclass. The helper properties decode/encode as needed for persistence and UI display.
- `SyncService.update_by_public_id` mutates the retrieved dataclass in place before calling `repo.update_by_id`. Caller must provide `row_version` (base64) in the update payload.
- `SyncService.delete_by_public_id` looks up the record and deletes by internal `id`; there is no concurrency check on delete despite the UI collecting `row_version`.
- `SyncService.create`/`read_*` methods are thin proxies with no additional validation or error handling. `read_*` includes the provider/env/entity lookups for parity with repository helpers.
- `SyncCreate.provider` and `SyncUpdate.env`  and `SyncUpdate.entity` are optional in the API schema, but the SQL schema requires a value—callers must supply something non-null to avoid database errors.

## 4. Persistence Expectations

- `_from_db` expects the stored procedures to return: `Id`, `PublicId`, `RowVersion` (`ROWVERSION/BINARY(8)`), `CreatedDatetime`, `ModifiedDatetime`, `Provider`, `Env`, `Entity`.
- Every repository method wraps database calls in `try/except`, logs via `logging.getLogger(__name__)`, and raises `map_database_error(error)` on failure.
- Stored procedure parameter bindings:
  - Create: `{"Provider": str, "Env": str, "Entity": str}`.
  - Read (all / by id / by public id / by provider / by env / by entity): `params` dict with the single key expected by the procedure.
  - Update: `{"Id": uuid, "RowVersion": sync.row_version_bytes, "Provider": str, "Env": str, "Entity": str}`.
  - Delete: `{"Id": uuid}`.
- `get_connection()` is used as a context manager; cursors come from `.cursor()` on the connection, and `call_procedure` executes the stored proc then leaves the cursor positioned for `fetchone()` / `fetchall()`.

## 5. Web UI

- Router prefix `/sync` maps to HTML views:
  - `GET /sync/list` → `list.html` with `syncs` (list of dataclasses).
  - `GET /sync/create` → `create.html` (no sync data).
- `GET /sync/{public_id}` → `view.html` with `sync.to_dict()`.
- `GET /sync/{public_id}/edit` → `edit.html` with `sync.to_dict()`.
- Templates assume Bootstrap utility classes, inline `<style>` tweaks, and FontAwesome icons. `create.html` sets both `provider` and `env` and `entity` `maxlength` to 50/255 respectively, while `edit.html` caps `provider` and `env` and `entity` at 50, matching the current markup.
- JavaScript fetch calls point to the API routes listed above, using JSON payloads and redirecting/alerting based on `response.ok`.

## 6. SQL Artifacts

- Table `dbo.Sync`:
  - Columns: `Id UNIQUEIDENTIFIER` (PK, `NEWSEQUENTIALID()`), `PublicId UNIQUEIDENTIFIER DEFAULT NEWID()`, `RowVersion ROWVERSION`, `CreatedDatetime DATETIME2(3) NOT NULL`, `ModifiedDatetime DATETIME2(3) NULL`, `Provider NVARCHAR(50) NOT NULL`, `Env NVARCHAR(255) NOT NULL`, `Entity NVARCHAR(255) NOT NULL`.
  - No tenancy column and no soft-delete flag.
- Stored procedures:
  - `CreateSync` inserts and outputs the created row; both timestamps are set with `SYSUTCDATETIME()` and converted to `VARCHAR(19)` for output.
  - `ReadSyncs` returns all rows ordered by `Provider`.
  - `ReadSyncById`, `ReadSyncByPublicId`, `ReadSyncByProvider` each return a single matching row.
  - `UpdateSyncById` updates fields when both `Id` and `RowVersion` match, returning the updated row with refreshed timestamps.
  - `DeleteSyncById` performs a hard delete and outputs the deleted row (no row version predicate).
  - The script includes `EXEC` statements after each procedure for smoke-testing.

## 7. Conventions & Helpers

- Maintain the three comment headers (`# Python Standard Library Imports`, `# Third-party Imports`, `# Local Imports`) even when empty.
- Logging: use `logger = logging.getLogger(__name__)` and log before rethrowing via `map_database_error`.
- Business models should expose a `to_dict()` helper that is used across API and web layers for JSON serialization.
- All timestamps are handled as strings (already formatted in SQL) when crossing service boundaries—no additional conversion performed in Python.

## 8. Environment

- Module relies on shared infrastructure (`shared.database`, global logging setup, and template directory rooted at `templates/`).
- No runtime configuration is injected directly into this module; the repository depends on ambient environment variables read by `shared.database`.
