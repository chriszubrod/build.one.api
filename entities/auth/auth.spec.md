# Agent Playbook – Auth Module

Single-file spec for reproducing the existing Auth module exactly as implemented.

## 1. Module Layout & Flow

- **API (`entities/auth/api`)**
  - `router.py`: FastAPI `APIRouter(prefix="/api/v1", tags=["auth"])` with routes for CRUD, login, signup, and token refresh. Handlers return dictionaries produced from business models. Update/delete routes catch `ValueError` and return 404. Login/signup/refresh set HttpOnly cookies and return only the access token in the response body (refresh tokens are never exposed in JSON).
  - `schemas.py`: Pydantic `BaseModel` classes — `AuthCreate`, `AuthUpdate`, `AuthLogin`, `AuthSignup`, `AuthRefreshRequest`.
- **Business (`entities/auth/business`)**
  - `model.py`: `@dataclass Auth` with fields `id` (int), `public_id`, `row_version` (base64), timestamps, `username`, `password_hash`, `user_id`. Stores `row_version` as base64 text and exposes `row_version_bytes`/`row_version_hex` helpers. `to_dict()` strips `password_hash`. Also defines `AuthToken`, `RefreshToken`, and `AuthRefreshTokenRecord` dataclasses.
  - `service.py`: `AuthService` wrapping `AuthRepository` and `AuthRefreshTokenRepository`. Provides `create`, `read_by_public_id`, `read_by_username`, `update_by_public_id`, `update_user_id_by_public_id`, `delete_by_public_id`, `login`, `signup`, `refresh_access_token`, `revoke_refresh_token`, and token generation helpers. Update/delete methods raise `ValueError` when the record is not found. Also defines `get_current_user_api`, `get_current_user_web`, CSRF helpers, `WebAuthenticationRequired`, and `RefreshRequired` exceptions, and shared constants (`CSRF_COOKIE_NAME`, `CSRF_HEADER_NAME`, `UNSAFE_METHODS`).
- **Persistence (`entities/auth/persistence`)**
  - `repo.py`: Uses `shared.database.get_connection`, `.call_procedure`, and `.map_database_error` with `pyodbc`. `_from_db` maps `pyodbc.Row` to `Auth`, base64-encoding `RowVersion`. Stored procedure names: `CreateAuth`, `ReadAuths`, `ReadAuthByPublicId`, `ReadAuthByUsername`, `UpdateAuthById`, `DeleteAuthById`.
  - `token_repo.py`: `AuthRefreshTokenRepository` for refresh token persistence — `CreateAuthRefreshToken`, `ReadAuthRefreshTokenByHash`, `RevokeAuthRefreshTokenByHash`.
  - Update passes `auth.row_version_bytes` to satisfy the stored procedure's `BINARY(8)` parameter. Delete issues a hard delete without row-version checks.
- **Web (`entities/auth/web`)**
  - `controller.py`: `APIRouter(prefix="/auth", tags=["web", "auth"])` with routes for `/login`, `/signup`, `/refresh`, `/logout`, and `/reset`. The `/refresh` route handles silent token rotation via redirect. Logout revokes the refresh token and clears all auth cookies.
- **Views (`templates/auth/`)**
  - `login.html`, `signup.html` — Bootstrap 5.3 forms that POST JSON to the API layer and redirect on success.
- **Static (`static/js/auth.js`, `static/css/auth.css`)**
  - JS intercepts `fetch` to attach CSRF headers, strip `Authorization` headers, and transparently retry on 401 via cookie-based refresh. Clears legacy localStorage tokens on load.
- **Database (`sql/dbo.auth.sql`)**
  - Owns the `dbo.Auth` and `dbo.AuthRefreshToken` tables plus all stored procedures.

Execution path: API/Web handler → `AuthService` → `AuthRepository`/`AuthRefreshTokenRepository` → stored procedure → repository `_from_db` → service → response/template.

## 2. API Contract

| Method | Path | Request body | Behavior |
| --- | --- | --- | --- |
| `POST` | `/api/v1/create/auth` | `AuthCreate` JSON | Requires auth. Creates a record and returns `auth.to_dict()`. |
| `GET` | `/api/v1/get/auth/{public_id}` | None | Requires auth. Returns `auth.to_dict()` or 404. Only own record (sub == public_id). |
| `PUT` | `/api/v1/update/auth/{public_id}` | `AuthUpdate` JSON | Requires auth. Updates record or returns 404. Only own record. |
| `PUT` | `/api/v1/update/auth/{public_id}/user-public-id/{user_public_id}` | None | Requires auth. Links auth to user or returns 404. Only own record. |
| `DELETE` | `/api/v1/delete/auth/{public_id}` | None | Requires auth. Hard deletes record or returns 404. Only own record. |
| `POST` | `/api/v1/auth/login` | `AuthLogin` JSON | Public. Returns `{auth, token}` and sets HttpOnly cookies. |
| `POST` | `/api/v1/signup/auth` | `AuthSignup` JSON | Public. Returns `{auth, token}` and sets HttpOnly cookies. |
| `POST` | `/api/v1/auth/refresh` | `AuthRefreshRequest` JSON (optional) | Public. Returns `{token}` and rotates cookies. Reads refresh from cookie if not in body. |

All CRUD routes enforce `current_user.sub == public_id` (403 Forbidden). Login/signup never return refresh tokens in JSON — only via HttpOnly cookies.

## 3. Business Rules

- Row versions are stored as base64 strings in the dataclass. The helper properties decode/encode as needed for persistence and UI display.
- `AuthService.update_by_public_id` raises `ValueError` if the record is not found. Mutates the retrieved dataclass in place before calling `repo.update_by_id`. Caller must provide `row_version` (base64) in the update payload.
- `AuthService.delete_by_public_id` raises `ValueError` if the record is not found. Deletes by internal `id`; there is no concurrency check on delete.
- `AuthService.create` checks for duplicate usernames and hashes the password with bcrypt before persisting.
- `AuthService.login` verifies credentials using bcrypt, with a legacy SHA-256 fallback that auto-upgrades the hash on success.
- Token rotation: On refresh, the old refresh token is revoked and a new pair (access + refresh) is issued. A 60-second grace period allows concurrent tabs to succeed during rotation.
- Passwords are hashed with bcrypt. Legacy SHA-256 hashes are auto-migrated on successful login.

## 4. Persistence Expectations

- `_from_db` expects the stored procedures to return: `Id`, `PublicId`, `RowVersion` (`ROWVERSION/BINARY(8)`), `CreatedDatetime`, `ModifiedDatetime`, `Username`, `PasswordHash`, `UserId`.
- Every repository method wraps database calls in `try/except`, logs via `logging.getLogger(__name__)`, and raises `map_database_error(error)` on failure.
- Stored procedure parameter bindings:
  - Create: `{"Username": str, "PasswordHash": str}`.
  - Read (by public id / by username): `params` dict with the single key expected by the procedure.
  - Update: `{"Id": int, "RowVersion": auth.row_version_bytes, "Username": str, "PasswordHash": str, "UserId": int}`.
  - Delete: `{"Id": int}` — hard delete, no row version check.
- `get_connection()` is used as a context manager; cursors come from `.cursor()` on the connection, and `call_procedure` executes the stored proc then leaves the cursor positioned for `fetchone()` / `fetchall()`.

## 5. Web UI

- Router prefix `/auth` maps to HTML views:
  - `GET /auth/login` → `login.html`.
  - `GET /auth/signup` → `signup.html`.
  - `GET /auth/refresh` → Silent token rotation via redirect to `next` query param.
  - `GET /auth/logout` → Revokes refresh token, clears cookies, redirects to login.
  - `GET /auth/reset` → Redirects to login.
- Templates assume Bootstrap utility classes, inline `<style>` tweaks, and FontAwesome icons.
- JavaScript fetch interceptor (`auth.js`) handles CSRF headers and transparent 401 retry via cookie-based refresh.

## 6. SQL Artifacts

- Table `dbo.Auth`:
  - Columns: `Id BIGINT IDENTITY(1,1)` (PK), `PublicId UNIQUEIDENTIFIER DEFAULT NEWID()`, `RowVersion ROWVERSION`, `CreatedDatetime DATETIME2(3)`, `ModifiedDatetime DATETIME2(3)`, `Username NVARCHAR(255)` (not null), `PasswordHash NVARCHAR(255)` (not null), `UserId BIGINT` (nullable).
  - No tenancy column and no soft-delete flag.
- Table `dbo.AuthRefreshToken`:
  - Columns: `Id BIGINT IDENTITY(1,1)` (PK), `AuthId BIGINT` (FK → Auth.Id), `TokenHash CHAR(64)` (unique index), `TokenJti UNIQUEIDENTIFIER`, `IssuedDatetime DATETIME2(3)`, `ExpiresDatetime DATETIME2(3)`, `RevokedDatetime DATETIME2(3)` (nullable), `ReplacedByTokenJti UNIQUEIDENTIFIER` (nullable).
  - Indexes: unique on `TokenHash`, non-unique on `AuthId` and `ExpiresDatetime`.
- Stored procedures:
  - `CreateAuth` inserts and outputs the created row; both timestamps are set with `SYSUTCDATETIME()` and converted to `VARCHAR(19)` for output.
  - `ReadAuths` returns all rows ordered by `Username`.
  - `ReadAuthByPublicId`, `ReadAuthByUsername` each return a single matching row.
  - `UpdateAuthById` updates fields when both `Id` and `RowVersion` match, returning the updated row with refreshed timestamps.
  - `DeleteAuthById` performs a hard delete and outputs the deleted row (no row version predicate).
  - `CreateAuthRefreshToken`, `ReadAuthRefreshTokenByHash`, `RevokeAuthRefreshTokenByHash` manage refresh token lifecycle.

## 7. Security

- **Password hashing**: bcrypt with auto-migration from legacy SHA-256 hashes using `hmac.compare_digest` for constant-time comparison.
- **CSRF protection**: Double-submit cookie pattern — `token.csrf` cookie (JS-readable) must match `X-CSRF-Token` header on unsafe methods. Enforced in both API and web auth flows.
- **Token cookies**: Access and refresh tokens stored as HttpOnly, SameSite=Lax cookies. `Secure` flag enabled in non-development environments.
- **Refresh token rotation**: Old token revoked on each refresh. 60-second grace period for concurrent requests. Token hash stored server-side for revocation checks.
- **Authorization**: CRUD routes enforce `current_user.sub == public_id` to prevent cross-account access.
- **Refresh tokens never in JSON**: Login, signup, and refresh endpoints only return access tokens in response bodies. Refresh tokens are transmitted exclusively via HttpOnly cookies.
- **Error messages**: 500-level errors use generic messages to avoid leaking internal details.

## 8. Conventions & Helpers

- Maintain the three comment headers (`# Python Standard Library Imports`, `# Third-party Imports`, `# Local Imports`) even when empty.
- Logging: use `logger = logging.getLogger(__name__)` and log before rethrowing via `map_database_error`.
- Business models should expose a `to_dict()` helper that is used across API and web layers for JSON serialization. `Auth.to_dict()` strips `password_hash`.
- All timestamps are handled as strings (already formatted in SQL) when crossing service boundaries — no additional conversion performed in Python.
- CSRF constants and `_require_csrf` are defined in `service.py` and imported by `router.py` — not duplicated.

## 9. Environment

- Module relies on shared infrastructure (`shared.database`, global logging setup, and template directory rooted at `templates/`).
- No runtime configuration is injected directly into this module; the repository depends on ambient environment variables read by `shared.database`.
- JWT settings (`secret_key`, `algorithm`, `access_token_expire_seconds`, `refresh_token_expire_seconds`) come from `config.Settings`.
