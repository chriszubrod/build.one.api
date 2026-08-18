# Agent Playbook – QboClient Module

> **Corrected (2026-08-18):** End-to-end fact pass against live Python + checked-in SQL (`integrations/intuit/qbo/client/`). Section 6 column list/types reflect `sql/qbo.client.sql` only — not independently re-verified against a live SQL Server instance.

Single-file spec for building the Qbo Client credentials module following the same layout and conventions as the existing Qbo Vendor module.

## 1. Module Layout & Flow

- **API (`integrations/intuit/qbo/client/api`)**
  - `router.py`: FastAPI `APIRouter(prefix="/api/v1", tags=["api", "qbo-client"])` with five routes that call `QboClientService` — three keyed on path `{app}` (GET/PUT/DELETE by app), one POST with `app` from the request body, and one unscoped list read (`GET /get/qbo-clients`). Each handler depends on `require_module_api(Modules.QBO_SYNC, …)`, wraps results in `item_response` / `list_response`, and returns dictionaries produced from business models via `to_dict()`.
  - `schemas.py`: Two `BaseModel` classes. `QboClientCreate` and `QboClientUpdate` each require three strings (`app`, `client_id`, `client_secret`; `max_length=512`). Neither schema carries a `row_version` field.
- **Business (`integrations/intuit/qbo/client/business`)**
  - `model.py`: `@dataclass QboClient` with three optional string fields: `app`, `client_id`, `client_secret`. Provides `to_dict()` that always omits `client_secret` and exposes `client_secret_set: bool`; internal callers needing the secret read the `client_secret` attribute directly.
  - `service.py`: Thin wrapper around `QboClientRepository` providing `create(*, app, client_id, client_secret)`, `read_all()`, `read_by_app(app)`, `update_by_app(app, client_id, client_secret)`, and `delete_by_app(app)`. Update/delete look up the record by `app`, mutate the dataclass in place, then delegate to the matching repository method. Service methods return the raw `QboClient` dataclass (or `None` when update/delete lookup misses); `to_dict()` serialization happens only in the router layer.
- **Persistence (`integrations/intuit/qbo/client/persistence`)**
  - `repo.py`: Uses `shared.database.get_connection`, `.call_procedure`, and `.map_database_error` with `pyodbc`. `_from_db` maps `pyodbc.Row` to `QboClient` (`App`, `ClientId`, `ClientSecret`). Stored procedure names are literal: `CreateQboClient`, `ReadQboClients`, `ReadQboClientByApp`, `UpdateQboClientByApp`, `DeleteQboClientByApp`. `ClientSecret` is encrypted at rest on write (`encrypt_sensitive_data`) and decrypted on read (`decrypt_if_encrypted`).
  - Delete issues a hard delete with no soft-delete flags and no concurrency token.
- **Database (`integrations/intuit/qbo/client/sql`)**
  - Owns the `qbo.Client` table definition plus every stored procedure the repository calls. Procedures return rowsets whose column names exactly match `_from_db` expectations (`App`, `ClientId`, `ClientSecret`).

Execution path: API handler → `QboClientService` → `QboClientRepository` → stored procedure → repository `_from_db` → service → API response envelope.

## 2. API Contract

All routes are gated by `require_module_api(Modules.QBO_SYNC, …)` (`can_create` on POST, `can_update` on PUT, `can_delete` on DELETE; read routes use the default read permission).

| Method | Path | Request body | Behavior |
| --- | --- | --- | --- |
| `POST` | `/api/v1/create/qbo-client` | `QboClientCreate` JSON (`app`, `client_id`, `client_secret`) | Creates a record via `service.create` and returns `item_response(qbo_client.to_dict())`. |
| `GET` | `/api/v1/get/qbo-clients` | None | Returns `list_response([qbo_client.to_dict() for qbo_client in service.read_all()])`. |
| `GET` | `/api/v1/get/qbo-client/{app}` | None | Returns `item_response(service.read_by_app(app).to_dict())`. |
| `PUT` | `/api/v1/update/qbo-client/{app}` | `QboClientUpdate` JSON (`app`, `client_id`, `client_secret`) | Service copies payload values onto the fetched dataclass and calls `repo.update_by_app`; returns `item_response(qbo_client.to_dict())`. Path `{app}` is the lookup key; body fields supply the new values. `body.app` is required by `QboClientUpdate` but the router passes only path `{app}` to the service — a different `app` in the body has no effect. |
| `DELETE` | `/api/v1/delete/qbo-client/{app}` | None | Service fetches by `app` then calls `repo.delete_by_app` and returns `item_response` with the deleted record as dict. |

Response bodies expose `app`, `client_id`, and `client_secret_set` (never the raw secret). Handlers assume the service/repository return a `QboClient`; they do not guard against `None` or map errors to HTTP status codes.

## 3. Business Rules

- `QboClientService.create`, `read_all`, `read_by_app`, `update_by_app`, and `delete_by_app` are thin proxies with no additional validation or error handling beyond the repository lookup on update/delete.
- `QboClientService.update_by_app` mutates the retrieved dataclass in place before calling `repo.update_by_app`.
- `QboClientService.delete_by_app` looks up the record by `app` and deletes via `repo.delete_by_app`; there is no concurrency token.
- `QboClientCreate.client_secret` and `QboClientUpdate.client_secret` are required; callers must supply a non-empty secret to avoid database errors.
- `to_dict()` always omits `client_secret` for API responses and exposes `client_secret_set: bool`; there is no `mask_secret` parameter. Internal callers needing the secret read the `QboClient.client_secret` attribute.

## 4. Persistence Expectations

- `_from_db` expects the stored procedures to return: `App`, `ClientId`, `ClientSecret`.
- Every repository method wraps database calls in `try/except`, logs via `logging.getLogger(__name__)`, and raises `map_database_error(error)` on failure.
- Stored procedure parameter bindings (as invoked by `QboClientRepository`):
  - Create: `{"App": str, "ClientId": str, "ClientSecret": encrypt_sensitive_data(str)}`.
  - Read all: `{}`.
  - Read by app: `{"App": str}`.
  - Update by app: `{"App": str, "ClientId": str, "ClientSecret": encrypt_sensitive_data(str)}`.
  - Delete by app: `{"App": str}`.
- The checked-in SQL also defines `ReadQboClientByClientId` and `UpdateQboClientByClientId` (keyed on `@ClientId`); the Python repository does not call them today.
- `get_connection()` is used as a context manager; cursors come from `.cursor()` on the connection, and `call_procedure` executes the stored proc then leaves the cursor positioned for `fetchone()` / `fetchall()`.

## 6. SQL Artifacts

- Table `qbo.Client`:
  - Columns: `App NVARCHAR(MAX) NOT NULL`, `ClientId NVARCHAR(MAX) NOT NULL`, `ClientSecret NVARCHAR(MAX) NOT NULL`.
  - No `RowVersion`, timestamp, or surrogate-key columns in the checked-in definition.
  - No primary key, unique constraint, or index on any column (including `App`). Duplicate `App` rows are not prevented; `UpdateQboClientByApp` and `DeleteQboClientByApp` key on `@App` with no `TOP 1` or surrogate key, so duplicates would be multi-row affected silently.
- Stored procedures:
  - `CreateQboClient` inserts and outputs the created row (`App`, `ClientId`, `ClientSecret`).
  - `ReadQboClients` returns all rows.
  - `ReadQboClientByApp` returns the row matching `@App`.
  - `ReadQboClientByClientId` returns the row matching `@ClientId` (defined in SQL; not wired in the Python repository).
  - `UpdateQboClientByApp` updates fields keyed on `@App`, using `CASE WHEN` guards so `NULL` parameters preserve existing values; outputs the updated row.
  - `UpdateQboClientByClientId` same update shape keyed on `@ClientId` (defined in SQL; not wired in the Python repository).
  - `DeleteQboClientByApp` performs a hard delete and outputs the deleted row.

## 7. Conventions & Helpers

- Maintain the three comment headers (`# Python Standard Library Imports`, `# Third-party Imports`, `# Local Imports`) even when empty.
- Logging: use `logger = logging.getLogger(__name__)` and log before rethrowing via `map_database_error`.
- Business models should expose a `to_dict()` helper used by API handlers for JSON serialization; `client_secret` is omitted and `client_secret_set` indicates presence.
- Secrets are never logged; redact values before writing to logs or telemetry.

## 8. Environment

- Module relies on shared infrastructure (`shared.database`, `shared.encryption`, global logging setup).
- API routes are gated by `require_module_api(Modules.QBO_SYNC, …)`; callers without the module permission are denied at the dependency layer.
- `ClientSecret` encryption at rest requires `ENCRYPTION_KEY` (see `shared.encryption`). No other runtime configuration is injected directly into this module; the repository depends on ambient environment variables read by `shared.database`.
