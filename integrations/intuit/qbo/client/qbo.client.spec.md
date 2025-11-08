# Agent Playbook – QboClient Module

Single-file spec for building the Qbo Client credentials module following the same layout and conventions as the existing Qbo Vendor module.

## 1. Module Layout & Flow

- **API (`integrations/intuit/qbo/client/api`)**
  - `router.py`: FastAPI `APIRouter(prefix="/api/v1", tags=["api", "qbo-client"])` with five routes that call `QboClientService`. Each handler depends on `get_current_qbo_client_api`, returns dictionaries produced from business models, and never raises HTTP errors.
  - `schemas.py`: Two `BaseModel` classes. `QboClientCreate` collects `client_id`, `client_secret`. `QboClientUpdate` includes `row_version` plus the same editable fields; `client_secret` remains required so callers always send the full value.
- **Business (`integrations/intuit/qbo/client/business`)**
  - `model.py`: `@dataclass QboClient` with optional string fields for every column (`id`, `public_id`, `tenant_id`, `row_version`, timestamps, `client_id`, `client_secret`). Stores `row_version` as base64 text and exposes `row_version_bytes`/`row_version_hex` helpers. Provides `to_dict(mask_secret: bool = True)` that redacts `client_secret` unless instructed otherwise.
  - `service.py`: Thin wrapper around `QboClientRepository` providing `create`, `read_all`, `read_by_id`, `read_by_public_id`, `read_by_client_id`, `update_by_public_id`, and `delete_by_public_id`. Update/delete look up the record by `public_id`, mutate the dataclass, then delegate to repository methods that operate on `id`. `create` and `update` always return models with the secret masked via `to_dict()`.
- **Persistence (`integrations/intuit/qbo/client/persistence`)**
  - `repo.py`: Uses `shared.database.get_connection`, `.call_procedure`, and `.map_database_error` with `pyodbc`. `_from_db` maps `pyodbc.Row` to `QboClient`, base64-encoding `RowVersion`. Stored procedure names are literal: `CreateQboClient`, `ReadQboClients`, `ReadQboClientById`, `ReadQboClientByPublicId`, `ReadQboClientByClientId`, `UpdateQboClientById`, `DeleteQboClientById`. All calls include `TenantId`.
  - Update passes `qbo_client.row_version_bytes` to satisfy the stored procedure’s `BINARY(8)` parameter. Delete issues a hard delete without soft-delete flags; the stored procedure enforces `TenantId`.
- **Web (`integrations/intuit/qbo/client/web`)**
  - `controller.py`: `APIRouter(prefix="/qbo-client", tags=["web", "qbo-client"])` with async handlers that still call the synchronous service. Every handler depends on `get_current_qbo_client_web`. Uses `Jinja2Templates(directory="templates/qbo-client")` and passes either `QboClient` dataclasses or `qbo_client.to_dict()` to the templates (with masked secret).
- **Views (`templates/qbo-client/`)**
  - `list.html`, `view.html`, `create.html`, `edit.html` rely on Bootstrap 5.3 + FontAwesome CDNs and a little vanilla JS. All expect `request` (and `current_qbo_client` where available) in context. `create.html` posts JSON to `/api/v1/create/qbo-client`. `edit.html` and `view.html` trigger `PUT`/`DELETE` requests against `/api/v1/update/qbo-client/{public_id}` and `/api/v1/delete/qbo-client/{public_id}`. Inline JS uses `fetch`, surfaces alerts on error, and redirects to `/qbo-client/list` on success. Inputs never echo the stored `client_secret`; edit forms show a placeholder instead.
- **Database (`integrations/intuit/qbo/client/sql`)**
  - Owns the `dbo.QboClient` table definition plus every stored procedure the repository calls. Procedures return rowsets whose column names exactly match `_from_db` expectations.

Execution path: API/Web handler → `QboClientService` → `QboClientRepository` → stored procedure → repository `_from_db` → service → response/template.

## 2. API Contract

| Method | Path | Request body | Behavior |
| --- | --- | --- | --- |
| `POST` | `/api/v1/create/qbo-client` | `QboClientCreate` JSON | Creates a record via `service.create` and returns `qbo_client.to_dict(mask_secret=True)`. |
| `GET` | `/api/v1/get/qbo-clients` | None | Returns `[qbo_client.to_dict(mask_secret=True) for qbo_client in service.read_all()]` scoped to the caller’s tenant. |
| `GET` | `/api/v1/get/qbo-client/{public_id}` | None | Returns `service.read_by_public_id(public_id).to_dict(mask_secret=True)`. |
| `PUT` | `/api/v1/update/qbo-client/{public_id}` | `QboClientUpdate` JSON | Service copies payload values onto the fetched dataclass and calls `repo.update_by_id`. |
| `DELETE` | `/api/v1/delete/qbo-client/{public_id}` | No body handled | Service fetches by public ID then calls `repo.delete_by_id` and returns the deleted record as dict; the handler signature ignores any JSON body clients might send. |

All handlers assume the repository returns a `QboClient`; they do not guard against `None` or map errors to HTTP status codes.

## 3. Business Rules

- Row versions are stored as base64 strings in the dataclass. The helper properties decode/encode as needed for persistence and UI display.
- `QboClientService.update_by_public_id` mutates the retrieved dataclass in place before calling `repo.update_by_id`. Caller must provide `row_version` (base64) in the update payload.
- `QboClientService.delete_by_public_id` looks up the record and deletes by internal `id`; there is no concurrency check on delete beyond the stored procedure validating `RowVersion`.
- `QboClientService.create`/`read_*` methods are thin proxies with no additional validation or error handling. `read_by_client_id` mirrors repository helpers.
- `QboClientCreate.client_secret` is required even though the view hides the stored value; callers must supply a non-empty secret to avoid database errors.
- `to_dict()` masks the secret for API/web responses by default, exposing only `"********"` unless `mask_secret=False` is requested internally.

## 4. Persistence Expectations

- `_from_db` expects the stored procedures to return: `Id`, `PublicId`, `TenantId`, `RowVersion` (`ROWVERSION/BINARY(8)`), `CreatedDatetime`, `ModifiedDatetime`, `ClientId`, `ClientSecret`.
- Every repository method wraps database calls in `try/except`, logs via `logging.getLogger(__name__)`, and raises `map_database_error(error)` on failure. Log records include the tenant identifier.
- Stored procedure parameter bindings:
  - Create: `{"TenantId": uuid, "ClientId": str, "ClientSecret": str}`.
  - Read (all / by id / by public id / by client id): params dict with the tenant plus the lookup key.
  - Update: `{"Id": uuid, "TenantId": uuid, "RowVersion": qbo_client.row_version_bytes, "ClientId": str, "ClientSecret": str}`.
  - Delete: `{"Id": uuid, "TenantId": uuid, "RowVersion": qbo_client.row_version_bytes}`.
- `get_connection()` is used as a context manager; cursors come from `.cursor()` on the connection, and `call_procedure` executes the stored proc then leaves the cursor positioned for `fetchone()` / `fetchall()`.

## 5. Web UI

- Router prefix `/qbo-client` maps to HTML views:
  - `GET /qbo-client/list` → `list.html` with `qbo_clients` (list of dataclasses).
  - `GET /qbo-client/create` → `create.html` (no qbo-client data).
- `GET /qbo-client/{public_id}` → `view.html` with `qbo_client.to_dict(mask_secret=True)`.
- `GET /qbo-client/{public_id}/edit` → `edit.html` with `qbo_client.to_dict(mask_secret=True)`. Edit template never pre-fills the secret input; it shows placeholder text and requires a new secret on submit.
- Templates mirror the vendor module’s Bootstrap structure, inline `<style>` tweaks, and FontAwesome icons. Form inputs have `maxlength` 512 for `client_id` and `client_secret`.
- JavaScript fetch calls point to the API routes listed above, using JSON payloads and redirecting/alerting based on `response.ok`.

## 6. SQL Artifacts

- Table `dbo.QboClient`:
  - Columns: `Id UNIQUEIDENTIFIER` (PK, `NEWSEQUENTIALID()`), `PublicId UNIQUEIDENTIFIER DEFAULT NEWID()`, `TenantId UNIQUEIDENTIFIER NOT NULL`, `RowVersion ROWVERSION`, `CreatedDatetime DATETIME2(3) NOT NULL`, `ModifiedDatetime DATETIME2(3) NULL`, `ClientId NVARCHAR(MAX) NOT NULL`, `ClientSecret NVARCHAR(MAX) NOT NULL`.
  - Nonclustered unique index on (`TenantId`, `ClientId`) to prevent duplicates per tenant.
- Stored procedures:
  - `CreateQboClient` inserts and outputs the created row; both timestamps are set with `SYSUTCDATETIME()` and converted to `VARCHAR(19)` for output.
  - `ReadQboClients` returns all rows for a tenant ordered by `CreatedDatetime` DESC.
  - `ReadQboClientById`, `ReadQboClientByPublicId`, `ReadQboClientByClientId` each return a single matching row scoped to `TenantId`.
  - `UpdateQboClientById` updates fields when both `Id`, `TenantId`, and `RowVersion` match, returning the updated row with refreshed timestamps.
  - `DeleteQboClientById` performs a hard delete and outputs the deleted row (no soft delete flag).
  - Each script includes `EXEC` statements after procedure definitions for smoke-testing.

## 7. Conventions & Helpers

- Maintain the three comment headers (`# Python Standard Library Imports`, `# Third-party Imports`, `# Local Imports`) even when empty.
- Logging: use `logger = logging.getLogger(__name__)` and log before rethrowing via `map_database_error`, including `tenant_id` and `corr_id` in the log context.
- Business models should expose a `to_dict()` helper that is used across API and web layers for JSON serialization and masking.
- All timestamps are handled as strings (already formatted in SQL) when crossing service boundaries—no additional conversion performed in Python.
- Secrets are never logged; redact values before writing to logs or telemetry.

## 8. Environment

- Module relies on shared infrastructure (`shared.database`, global logging setup, and template directory rooted at `templates/`).
- JWT middleware supplies the tenant ID claim used for scoping; failure to resolve the tenant results in access denied at the dependency layer.
- No runtime configuration is injected directly into this module; the repository depends on ambient environment variables read by `shared.database`.
