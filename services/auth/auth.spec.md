# Agent Playbook – Auth Module

Single-file spec for reproducing the existing Auth module exactly as implemented.

## 1. Module Layout & Flow

- **API (`modules/auth/api`)**
  - `router.py`: FastAPI `APIRouter(prefix="/api/v1", tags=["api", "auth"])` with five routes that call `AuthService`. Handlers return dictionaries produced from business models and never raise HTTP errors.
  - `schemas.py`: Two `BaseModel` classes. `AuthCreate` collects `username`, `password`. `AuthUpdate` includes `row_version` plus the same editable fields.
- **Business (`modules/auth/business`)**
  - `model.py`: `@dataclass Auth` with optional string fields for every column (`id`, `public_id`, `row_version`, timestamps, `username`, `password`). Stores `row_version` as base64 text and exposes `row_version_bytes`/`row_version_hex` helpers.
  - `service.py`: Thin wrapper around `AuthRepository` providing `create`, `read_all`, `read_by_id`, `read_by_public_id`, `read_by_username`, `update_by_public_id`, and `delete_by_public_id`. Update/delete look up the record by `public_id`, mutate the dataclass, then delegate to repository methods that operate on `id`.
- **Persistence (`modules/auth/persistence`)**
  - `repo.py`: Uses `shared.database.get_connection`, `.call_procedure`, and `.map_database_error` with `pyodbc`. `_from_db` maps `pyodbc.Row` to `Auth`, base64-encoding `RowVersion`. Stored procedure names are literal: `CreateAuth`, `ReadAuth`, `ReadAuthById`, `ReadAuthByPublicId`, `ReadAuthByUsername`, `UpdateAuthById`, `DeleteAuthById`.
  - Update passes `auth.row_version_bytes` to satisfy the stored procedure’s `BINARY(8)` parameter. Delete issues a hard delete without row-version checks.
- **Web (`modules/auth/web`)**
  - `controller.py`: `APIRouter(prefix="/auth", tags=["web", "auth"])` with async handlers that still call the synchronous service. Uses `Jinja2Templates(directory="templates/auth")` and passes either `Auth` dataclasses or `auth.to_dict()` to the templates.
- **Views (`templates/auth/`)**
  - `list.html`, `view.html`, `create.html`, `edit.html` rely on Bootstrap 5.3 + FontAwesome CDNs and a little vanilla JS. All expect a `request` context var. `create.html` posts JSON to `/api/v1/create/auth`. `edit.html` and `view.html` trigger `PUT`/`DELETE` requests against `/api/v1/update/auth/{public_id}` and `/api/v1/delete/auth/{public_id}`. Inline JS uses `fetch`, surfaces alerts on error, and redirects to `/auth/list` on success.
- **Database (`sql/dbo.auth.sql`)**
  - Owns the `dbo.Auth` table definition plus every stored procedure the repository calls. Procedures return rowsets whose column names exactly match `_from_db` expectations.

Execution path: API/Web handler → `AuthService` → `AuthRepository` → stored procedure → repository `_from_db` → service → response/template.

## 2. API Contract

| Method | Path | Request body | Behavior |
| --- | --- | --- | --- |
| `POST` | `/api/v1/create/auth` | `AuthCreate` JSON | Creates a record via `service.create` and returns `auth.to_dict()`. |
| `GET` | `/api/v1/get/auth` | None | Returns `[auth.to_dict() for auth in service.read_all()]`. |
| `GET` | `/api/v1/get/auth/{public_id}` | None | Returns `service.read_by_public_id(public_id).to_dict()`. |
| `PUT` | `/api/v1/update/auth/{public_id}` | `AuthUpdate` JSON | Service copies payload values onto the fetched dataclass and calls `repo.update_by_id`. |
| `DELETE` | `/api/v1/delete/auth/{public_id}` | No body read | Service fetches by public ID then calls `repo.delete_by_id` and returns the deleted record as dict. |

All handlers assume the repository returns a `Auth`; they do not guard against `None` or map errors to HTTP status codes.

## 3. Business Rules

- Row versions are stored as base64 strings in the dataclass. The helper properties decode/encode as needed for persistence and UI display.
- `AuthService.update_by_public_id` mutates the retrieved dataclass in place before calling `repo.update_by_id`. Caller must provide `row_version` (base64) in the update payload.
- `AuthService.delete_by_public_id` looks up the record and deletes by internal `id`; there is no concurrency check on delete despite the UI collecting `row_version`.
- `AuthService.create`/`read_*` methods are thin proxies with no additional validation or error handling.

## 4. Persistence Expectations

- `_from_db` expects the stored procedures to return: `Id`, `PublicId`, `RowVersion` (`ROWVERSION/BINARY(8)`), `CreatedDatetime`, `ModifiedDatetime`, `Username`, `Password`.
- Every repository method wraps database calls in `try/except`, logs via `logging.getLogger(__name__)`, and raises `map_database_error(error)` on failure.
- Stored procedure parameter bindings:
  - Create: `{"Username": str, "Password: str}`.
  - Read (all / by id / by public id / by number): `params` dict with the single key expected by the procedure.
  - Update: `{"Id": uuid, "RowVersion": auth.row_version_bytes, "Username": str, "Password": str}`.
  - Delete: `{"Id": uuid}`.
- `get_connection()` is used as a context manager; cursors come from `.cursor()` on the connection, and `call_procedure` executes the stored proc then leaves the cursor positioned for `fetchone()` / `fetchall()`.

## 5. Web UI

- Router prefix `/auth` maps to HTML views:
  - `GET /auth/list` → `list.html` with `auth` (list of dataclasses).
  - `GET /auth/create` → `create.html` (no Auth data).
  - `GET /auth/{public_id}` → `view.html` with `auth.to_dict()`.
  - `GET /auth/{public_id}/edit` → `edit.html` with `auth.to_dict()`.
- Templates assume Bootstrap utility classes, inline `<style>` tweaks, and FontAwesome icons.
- JavaScript fetch calls point to the API routes listed above, using JSON payloads and redirecting/alerting based on `response.ok`.

## 6. SQL Artifacts

- Table `dbo.Auth`:
  - Columns: `Id UNIQUEIDENTIFIER` (PK, `NEWSEQUENTIALID()`), `PublicId UNIQUEIDENTIFIER DEFAULT NEWID()`, `RowVersion ROWVERSION`, `CreatedDatetime DATETIME2(3)`, `ModifiedDatetime DATETIME2(3)`, `Username NVARCHAR(50)` (unique), `Password NVARCHAR(255)` (nullable).
  - No tenancy column and no soft-delete flag.
- Stored procedures:
  - `CreateAuth` inserts and outputs the created row; both timestamps are set with `SYSUTCDATETIME()` and converted to `VARCHAR(19)` for output.
  - `ReadAuth` returns all rows ordered by `Number`.
  - `ReadAuthById`, `ReadAuthByPublicId`, `ReadAuthByUsername` each return a single matching row.
  - `UpdateAuthById` updates fields when both `Id` and `RowVersion` match, returning the updated row with refreshed timestamps.
  - `DeleteAuthById` performs a hard delete and outputs the deleted row (no row version predicate).

## 7. Conventions & Helpers

- Maintain the three comment headers (`# Python Standard Library Imports`, `# Third-party Imports`, `# Local Imports`) even when empty.
- Logging: use `logger = logging.getLogger(__name__)` and log before rethrowing via `map_database_error`.
- Business models should expose a `to_dict()` helper that is used across API and web layers for JSON serialization.
- All timestamps are handled as strings (already formatted in SQL) when crossing service boundaries—no additional conversion performed in Python.

## 8. Environment

- Module relies on shared infrastructure (`shared.database`, global logging setup, and template directory rooted at `templates/`).
- No runtime configuration is injected directly into this module; the repository depends on ambient environment variables read by `shared.database`.
