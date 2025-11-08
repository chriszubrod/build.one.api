# Agent Playbook – RoleModule Module

Single-file spec for reproducing the existing RoleModule module exactly as implemented.

## 1. Module Layout & Flow

- **API (`modules/role_module/api`)**
  - `router.py`: FastAPI `APIRouter(prefix="/api/v1", tags=["api", "role_module"])` with five synchronous routes (`create`, `get` list, `get` by public ID, `update`, `delete`). Every handler depends on `get_current_user_api`, instantiates `RoleModuleService()` inline, and returns dictionaries produced from the business model without HTTP error mapping.
  - `schemas.py`: Two Pydantic models. `RoleModuleCreate` collects `role_id` and `module_id`. `RoleModuleUpdate` extends that payload with a base64 `row_version`. Fields only carry descriptions; there are no validators or custom types.
- **Business (`modules/role_module/business`)**
  - `model.py`: `@dataclass RoleModule` with optional string attributes for each database column (`id`, `public_id`, `row_version`, timestamps, `role_id`, `module_id`). Provides `row_version_bytes` (base64 decode), `row_version_hex`, and `to_dict()` that simply calls `asdict`.
  - `service.py`: Thin wrapper around `RoleModuleRepository` exposing `create`, `read_all`, `read_by_id`, `read_by_public_id`, `read_by_role_id`, `read_by_module_id`, `update_by_public_id`, and `delete_by_public_id`. Update mutates the in-memory dataclass with payload values before delegating to `repo.update_by_id`; delete looks up by `public_id` and issues a hard delete by internal `id`.
- **Persistence (`modules/role_module/persistence`)**
  - `repo.py`: Uses `shared.database.get_connection`, `.call_procedure`, and `.map_database_error` with `pyodbc`. `_from_db` converts procedure result sets into `RoleModule`, base64-encoding the `RowVersion` field. Stored procedure names are literal: `CreateRoleModule`, `ReadRoleModules`, `ReadRoleModuleById`, `ReadRoleModuleByPublicId`, `ReadRoleModuleByRoleId`, `ReadRoleModuleByModuleId`, `UpdateRoleModuleById`, `DeleteRoleModuleById`.
- **Web (`modules/role_module/web`)**
  - `controller.py`: `APIRouter(prefix="/role_module", tags=["web", "role_module"])` with async route functions that call the synchronous service. Each handler depends on `get_current_user_web`. Templates are loaded via `Jinja2Templates(directory="templates/role_module")`. List/create routes pass dataclasses directly; detail/edit routes convert the record to a dict before rendering.
- **Views (`templates/role_module/`)**
  - `list.html`, `view.html`, `create.html`, `edit.html` use Bootstrap 5.3 + FontAwesome CDNs with inline `<style>` tweaks and vanilla JS `fetch` calls that hit the API endpoints under `/api/v1`. `create.html` and `edit.html` expect `users` and `roles` collections in the template context for `<select>` options even though the current controller does not supply them.
- **Database (`modules/role_module/sql/dbo.userrole.sql`)**
  - Contains the `dbo.RoleModule` table definition plus every stored procedure referenced by the repository. Procedures convert timestamps to `VARCHAR(19)` for consumption by `_from_db` and include sample `EXEC` statements after each definition.

Execution path: API/Web handler → `RoleModuleService` → `RoleModuleRepository` → stored procedure → `_from_db` → service → response/template.

## 2. API Contract

| Method | Path | Request body | Behavior |
| --- | --- | --- | --- |
| `POST` | `/api/v1/create/role_module` | `RoleModuleCreate` JSON | Calls `service.create(role_id, module_id)` and returns `role_module.to_dict()`. |
| `GET` | `/api/v1/get/role_modules` | None | Returns `[role_module.to_dict() for role_module in service.read_all()]`. |
| `GET` | `/api/v1/get/role_module/{public_id}` | None | Returns `service.read_by_public_id(public_id).to_dict()`. |
| `PUT` | `/api/v1/update/role_module/{public_id}` | `RoleModuleUpdate` JSON | Copies payload data onto the dataclass retrieved by `public_id`, then calls `repo.update_by_id` and returns the updated record as dict. |
| `DELETE` | `/api/v1/delete/role_module/{public_id}` | No body handled | Looks up the record by `public_id`, deletes by its internal `id`, and returns the deleted record dict; any JSON body sent by clients is ignored. |

All handlers assume the repository succeeds and returns a `RoleModule`; there is no guard against `None` or explicit HTTP error mapping.

## 3. Business Rules

- Row versions travel as base64 strings. The dataclass helpers decode to `bytes` for persistence (`row_version_bytes`) and expose a hex string (`row_version_hex`) for display or logging.
- Updates require clients to send the current `row_version`. `RoleModuleService.update_by_public_id` mutates the fetched dataclass before forwarding it to the repository.
- Deletes do not enforce optimistic concurrency: `delete_by_public_id` ignores `row_version` and calls `repo.delete_by_id` directly.
- `create`/`read_*` methods are direct pass-throughs to the repository; there is no validation or authorization beyond the JWT dependency on the router.
- Additional finder helpers (`read_by_role_id`, `read_by_module_id`) surface repository functionality even though the API layer does not expose dedicated routes.

## 4. Persistence Expectations

- `_from_db` expects result sets containing `Id`, `PublicId`, `RowVersion` (`ROWVERSION/BINARY(8)`), `CreatedDatetime`, `ModifiedDatetime`, `RoleId`, `ModuleId`. It wraps mapping in `try/except`, logs errors with `logger = logging.getLogger(__name__)`, and re-raises via `map_database_error`.
- Stored procedure parameter bindings:
  - Create: `{"RoleId": uuid, "ModuleId": uuid}`.
  - Read-all: `{}`; row-by-id/public-id/user-id/role-id pass a single key aligning with the procedure signature.
  - Update: `{"Id": role_module.id, "RowVersion": role_module.row_version_bytes, "RoleId": role_module.role_id, "ModuleId": role_module.module_id}`.
  - Delete: `{"Id": uuid}`.
- Connections come from `get_connection()` context manager; cursors are obtained per method and re-used for `fetchone()`/`fetchall()` after `call_procedure`.
- Repository methods log contextual error messages before delegating to `map_database_error`.

## 5. Web UI

- Router prefix `/role_module` exposes:
  - `GET /role_module/list` → `list.html` with `role_modules` (list of dataclasses) and `current_user`.
  - `GET /role_module/create` → `create.html` with `current_user`; template still references `users`/`roles` collections.
  - `GET /role_module/{public_id}` → `view.html` with `role_module.to_dict()` and `current_user`.
  - `GET /role_module/{public_id}/edit` → `edit.html` with `role_module.to_dict()` and `current_user`; template again expects `users`/`roles`.
- Templates post JSON to the API: create → `POST /api/v1/create/role_module`; edit → `PUT /api/v1/update/role_module/{public_id}`; delete buttons call `DELETE /api/v1/delete/role_module/{public_id}` and redirect to `/role_module/list` (some links point to `/role_modules/list` as currently written).
- Client-side scripting is plain `fetch` with `response.ok` checks, `alert()` on failure, and hard redirects on success. No CSRF or advanced handling is implemented.

## 6. SQL Artifacts

- Table `dbo.RoleModule`:
  - Columns: `Id UNIQUEIDENTIFIER` (PK, `NEWSEQUENTIALID()`), `PublicId UNIQUEIDENTIFIER` (`DEFAULT NEWID()`), `RowVersion ROWVERSION`, `CreatedDatetime DATETIME2(3) NOT NULL`, `ModifiedDatetime DATETIME2(3) NULL`, `RoleId UNIQUEIDENTIFIER NOT NULL`, `ModuleId UNIQUEIDENTIFIER NOT NULL`.
  - Insert sets both timestamps to `SYSUTCDATETIME()`; there is no tenancy column or foreign-key constraint enforcement.
- Stored procedures:
  - `CreateRoleModule` inserts and outputs the created row with timestamps converted to `VARCHAR(19)`.
  - `ReadRoleModules` returns all rows ordered by `RoleId`, `ModuleId`.
  - `ReadRoleModuleById`, `ReadRoleModuleByPublicId`, `ReadRoleModuleByRoleId`, `ReadRoleModuleByModuleId` each return a single matching row.
  - `UpdateRoleModuleById` updates when both `Id` and `RowVersion` match, returning the refreshed row.
  - `DeleteRoleModuleById` performs a hard delete and outputs the removed row (no row-version predicate).
  - Script retains `DROP PROCEDURE IF EXISTS` guards and sample `EXEC` statements for smoke testing.

## 7. Conventions & Helpers

- Preserve the three import comment headers (`# Python Standard Library Imports`, `# Third-party Imports`, `# Local Imports`) even when sections are empty.
- Business models expose `to_dict()` for API/Web serialization; API handlers rely on it.
- Logging follows `logger = logging.getLogger(__name__)` before re-raising via `map_database_error`.
- Templates continue to use Bootstrap/FontAwesome CDNs and inline styles rather than Tailwind, matching the current HTML.

## 8. Environment

- Module depends on shared infrastructure: `shared.database` for database access, logging configuration, and auth helpers `modules.auth.business.service.get_current_user_api/get_current_user_web`.
- No module-specific settings are injected; connection details and JWT handling are assumed to be configured elsewhere in the application.

