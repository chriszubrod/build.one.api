# Agent Playbook – Sub Cost Code Module

## 1. Module Layout & Flow

- **API layer (`modules/sub_cost_code/api`)**
  - `router.py`: FastAPI `APIRouter` with prefix `/api/v1`; delegates to `SubCostCodeService`.
  - `schemas.py`: Pydantic request payload models (`SubCostCodeCreate`, `SubCostCodeUpdate`).
- **Business layer (`modules/sub_cost_code/business`)**
  - `model.py`: `@dataclass SubCostCode` storing plain strings; row version kept as base64 with helper properties for bytes/hex.
  - `service.py`: Thin façade over `SubCostCodeRepository`; handles translation between API schemas and repo models.
- **Persistence layer (`modules/sub_cost_code/persistence`)**
  - `repo.py`: Database access using stored procedures defined in `sql/dbo.sub_cost_code.sql`.
  - Relies on `shared.database` helpers (`get_connection`, `call_procedure`, `map_database_error`).
- **Web layer (`modules/sub_cost_code/web`)**
  - `controller.py`: FastAPI router under `/sub-cost-code`; renders templates from `templates/sub_cost_code`.
- **Views (`templates/sub_cost_code`)**
  - Bootstrap+FontAwesome templates (`list.html`, `view.html`, `create.html`, `edit.html`). They expect contexts matching the business models.
- **Database (`sql/dbo.sub_cost_code.sql`)**
  - SQL script defines `dbo.SubCostCode` table and CRUD stored procedures used by the repository.

Execution flow: API/Web → Business Service → Repository → Stored Procedures → Repository → Business → API/Web response/template.

## 2. Domain Rules & Constraints

- Each sub cost code belongs to exactly one parent cost code (linked by parent cost code `PublicId`).
- Fields captured per record:
  - `CostCodePublicId` (GUID) – parent identifier required on create/update.
  - `Number` (NVARCHAR 50) – required and unique per parent cost code.
  - `Name` (NVARCHAR 255) – required.
  - `Description` (NVARCHAR 255) – optional free-form notes.
- Common columns (`Id`, `PublicId`, `RowVersion`, timestamps) follow BuildOne standards.
- Deletions are hard deletes for now (no soft delete requirement supplied).
- Optimistic concurrency enforced with `RowVersion` for updates.
- All timestamps recorded in UTC via `SYSUTCDATETIME()` and returned as ISO-8601 strings.

## 3. API Contract

- `POST   /api/v1/create/sub-cost-code`
  - Body: `SubCostCodeCreate` (parent `cost_code_public_id`, `number`, `name`, optional `description`).
  - Returns: Created sub cost code payload.
- `GET    /api/v1/get/sub-cost-codes`
  - Query params: optional `cost_code_public_id` filter.
  - Returns: array of sub cost code payloads.
- `GET    /api/v1/get/sub-cost-code/{public_id}`
  - Returns single sub cost code payload.
- `PUT    /api/v1/update/sub-cost-code/{public_id}`
  - Body: `SubCostCodeUpdate` (includes `row_version`).
  - Returns updated payload. Concurrency violations → HTTP 409.
- `DELETE /api/v1/delete/sub-cost-code/{public_id}`
  - Deletes and returns deleted payload.

## 4. Acceptance Criteria

- Stored procedures support tenant scoping via parent cost code lookups and enforce uniqueness on (`CostCodeId`, `Number`).
- Repository converts `pyodbc.Row` objects into dataclasses with base64 row versions.
- Services surface `DatabaseConcurrencyError` as 409 and validation issues as 422.
- Web templates follow Bootstrap styling consistent with the cost/organization modules and consume service outputs.
- README documents any assumptions made due to missing upstream specifications.
