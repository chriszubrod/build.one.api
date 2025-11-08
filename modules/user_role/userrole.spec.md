# Agent Playbook – UserRole Module

Single-file spec for reproducing the existing UserRole module exactly as implemented.

## 1. Module Layout & Flow

- **API (`modules/user_role/api`)**
  - `router.py`: FastAPI `APIRouter(prefix="/api/v1", tags=["api", "user_role"])` with five synchronous routes (`create`, `get` list, `get` by public ID, `update`, `delete`). Every handler depends on `get_current_user_api`, instantiates `UserRoleService()` inline, and returns dictionaries produced from the business model without HTTP error mapping.
  - `schemas.py`: Two Pydantic models. `UserRoleCreate` collects `user_id` and `role_id`. `UserRoleUpdate` extends that payload with a base64 `row_version`. Fields only carry descriptions; there are no validators or custom types.
- **Business (`modules/user_role/business`)**
  - `model.py`: `@dataclass UserRole` with optional string attributes for each database column (`id`, `public_id`, `row_version`, timestamps, `user_id`, `role_id`). Provides `row_version_bytes` (base64 decode), `row_version_hex`, and `to_dict()` that simply calls `asdict`.
  - `service.py`: Thin wrapper around `UserRoleRepository` exposing `create`, `read_all`, `read_by_id`, `read_by_public_id`, `read_by_user_id`, `read_by_role_id`, `update_by_public_id`, and `delete_by_public_id`. Update mutates the in-memory dataclass with payload values before delegating to `repo.update_by_id`; delete looks up by `public_id` and issues a hard delete by internal `id`.
- **Persistence (`modules/user_role/persistence`)**
  - `repo.py`: Uses `shared.database.get_connection`, `.call_procedure`, and `.map_database_error` with `pyodbc`. `_from_db` converts procedure result sets into `UserRole`, base64-encoding the `RowVersion` field. Stored procedure names are literal: `CreateUserRole`, `ReadUserRoles`, `ReadUserRoleById`, `ReadUserRoleByPublicId`, `ReadUserRoleByUserId`, `ReadUserRoleByRoleId`, `UpdateUserRoleById`, `DeleteUserRoleById`.
- **Web (`modules/user_role/web`)**
  - `controller.py`: `APIRouter(prefix="/user_role", tags=["web", "user_role"])` with async route functions that call the synchronous service. Each handler depends on `get_current_user_web`. Templates are loaded via `Jinja2Templates(directory="templates/user_role")`. List/create routes pass dataclasses directly; detail/edit routes convert the record to a dict before rendering.
- **Views (`templates/user_role/`)**
  - `list.html`, `view.html`, `create.html`, `edit.html` use Bootstrap 5.3 + FontAwesome CDNs with inline `<style>` tweaks and vanilla JS `fetch` calls that hit the API endpoints under `/api/v1`. `create.html` and `edit.html` expect `users` and `roles` collections in the template context for `<select>` options even though the current controller does not supply them.
- **Database (`modules/user_role/sql/dbo.userrole.sql`)**
  - Contains the `dbo.UserRole` table definition plus every stored procedure referenced by the repository. Procedures convert timestamps to `VARCHAR(19)` for consumption by `_from_db` and include sample `EXEC` statements after each definition.

Execution path: API/Web handler → `UserRoleService` → `UserRoleRepository` → stored procedure → `_from_db` → service → response/template.

## 2. API Contract

| Method | Path | Request body | Behavior |
| --- | --- | --- | --- |
| `POST` | `/api/v1/create/user_role` | `UserRoleCreate` JSON | Calls `service.create(user_id, role_id)` and returns `user_role.to_dict()`. |
| `GET` | `/api/v1/get/user_roles` | None | Returns `[user_role.to_dict() for user_role in service.read_all()]`. |
| `GET` | `/api/v1/get/user_role/{public_id}` | None | Returns `service.read_by_public_id(public_id).to_dict()`. |
| `PUT` | `/api/v1/update/user_role/{public_id}` | `UserRoleUpdate` JSON | Copies payload data onto the dataclass retrieved by `public_id`, then calls `repo.update_by_id` and returns the updated record as dict. |
| `DELETE` | `/api/v1/delete/user_role/{public_id}` | No body handled | Looks up the record by `public_id`, deletes by its internal `id`, and returns the deleted record dict; any JSON body sent by clients is ignored. |

All handlers assume the repository succeeds and returns a `UserRole`; there is no guard against `None` or explicit HTTP error mapping.

## 3. Business Rules

- Row versions travel as base64 strings. The dataclass helpers decode to `bytes` for persistence (`row_version_bytes`) and expose a hex string (`row_version_hex`) for display or logging.
- Updates require clients to send the current `row_version`. `UserRoleService.update_by_public_id` mutates the fetched dataclass before forwarding it to the repository.
- Deletes do not enforce optimistic concurrency: `delete_by_public_id` ignores `row_version` and calls `repo.delete_by_id` directly.
- `create`/`read_*` methods are direct pass-throughs to the repository; there is no validation or authorization beyond the JWT dependency on the router.
- Additional finder helpers (`read_by_user_id`, `read_by_role_id`) surface repository functionality even though the API layer does not expose dedicated routes.

## 4. Persistence Expectations

- `_from_db` expects result sets containing `Id`, `PublicId`, `RowVersion` (`ROWVERSION/BINARY(8)`), `CreatedDatetime`, `ModifiedDatetime`, `UserId`, `RoleId`. It wraps mapping in `try/except`, logs errors with `logger = logging.getLogger(__name__)`, and re-raises via `map_database_error`.
- Stored procedure parameter bindings:
  - Create: `{"UserId": uuid, "RoleId": uuid}`.
  - Read-all: `{}`; row-by-id/public-id/user-id/role-id pass a single key aligning with the procedure signature.
  - Update: `{"Id": user_role.id, "RowVersion": user_role.row_version_bytes, "UserId": user_role.user_id, "RoleId": user_role.role_id}`.
  - Delete: `{"Id": uuid}`.
- Connections come from `get_connection()` context manager; cursors are obtained per method and re-used for `fetchone()`/`fetchall()` after `call_procedure`.
- Repository methods log contextual error messages before delegating to `map_database_error`.

## 5. Web UI

- Router prefix `/user_role` exposes:
  - `GET /user_role/list` → `list.html` with `user_roles` (list of dataclasses) and `current_user`.
  - `GET /user_role/create` → `create.html` with `current_user`; template still references `users`/`roles` collections.
  - `GET /user_role/{public_id}` → `view.html` with `user_role.to_dict()` and `current_user`.
  - `GET /user_role/{public_id}/edit` → `edit.html` with `user_role.to_dict()` and `current_user`; template again expects `users`/`roles`.
- Templates post JSON to the API: create → `POST /api/v1/create/user_role`; edit → `PUT /api/v1/update/user_role/{public_id}`; delete buttons call `DELETE /api/v1/delete/user_role/{public_id}` and redirect to `/user_role/list` (some links point to `/user_roles/list` as currently written).
- Client-side scripting is plain `fetch` with `response.ok` checks, `alert()` on failure, and hard redirects on success. No CSRF or advanced handling is implemented.

## 6. SQL Artifacts

- Table `dbo.UserRole`:
  - Columns: `Id UNIQUEIDENTIFIER` (PK, `NEWSEQUENTIALID()`), `PublicId UNIQUEIDENTIFIER` (`DEFAULT NEWID()`), `RowVersion ROWVERSION`, `CreatedDatetime DATETIME2(3) NOT NULL`, `ModifiedDatetime DATETIME2(3) NULL`, `UserId UNIQUEIDENTIFIER NOT NULL`, `RoleId UNIQUEIDENTIFIER NOT NULL`.
  - Insert sets both timestamps to `SYSUTCDATETIME()`; there is no tenancy column or foreign-key constraint enforcement.
- Stored procedures:
  - `CreateUserRole` inserts and outputs the created row with timestamps converted to `VARCHAR(19)`.
  - `ReadUserRoles` returns all rows ordered by `UserId`, `RoleId`.
  - `ReadUserRoleById`, `ReadUserRoleByPublicId`, `ReadUserRoleByUserId`, `ReadUserRoleByRoleId` each return a single matching row.
  - `UpdateUserRoleById` updates when both `Id` and `RowVersion` match, returning the refreshed row.
  - `DeleteUserRoleById` performs a hard delete and outputs the removed row (no row-version predicate).
  - Script retains `DROP PROCEDURE IF EXISTS` guards and sample `EXEC` statements for smoke testing.

## 7. Conventions & Helpers

- Preserve the three import comment headers (`# Python Standard Library Imports`, `# Third-party Imports`, `# Local Imports`) even when sections are empty.
- Business models expose `to_dict()` for API/Web serialization; API handlers rely on it.
- Logging follows `logger = logging.getLogger(__name__)` before re-raising via `map_database_error`.
- Templates continue to use Bootstrap/FontAwesome CDNs and inline styles rather than Tailwind, matching the current HTML.

## 8. Environment

- Module depends on shared infrastructure: `shared.database` for database access, logging configuration, and auth helpers `modules.auth.business.service.get_current_user_api/get_current_user_web`.
- No module-specific settings are injected; connection details and JWT handling are assumed to be configured elsewhere in the application.

