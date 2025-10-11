# Agent Playbook – Cost Code Module

## 1. Module Layout & Flow

- **API layer (`modules/cost-code/api`)**
  - `router.py`: FastAPI `APIRouter` with prefix `/api/v1`; resolves to `CostCodeService`.
  - `schemas.py`: Pydantic models for request payloads (`CostCodeCreate`, `CostCodeUpdate`).
- **Business layer (`modules/cost-code/business`)**
  - `model.py`: `@dataclass CostCode` storing strings; row version kept as base64 text with helper properties for bytes/hex.
  - `service.py`: Thin façade over `CostCodeRepository`; performs data massaging before persistence calls.
- **Persistence layer (`modules/cost-code/persistence`)**
  - `repo.py`: Handles all database interaction through stored procedures defined in `sql/dbo.costcode.sql`.
  - Uses helpers from `shared.database` (`get_connection`, `call_procedure`, `map_database_error`).
- **Web layer (`modules/cost-code/web`)**
  - `controller.py`: FastAPI router for HTML pages under `/cost-code` prefix; relies on `CostCodeService` and `templates/cost-code`.
- **Views (`templates/cost-code`)**
  - Bootstrap+FontAwesome templates (`list.html`, `view.html`, `create.html`, `edit.html`), each expecting `request` plus the data payload described below.
- **Database (`sql/dbo.costcode.sql`)**
  - SQL script defines the `dbo.CostCode` table and all CRUD stored procedures used by the repository.

Execution flow: API/Web → Service (business) → Repository → SQL stored procedures → Repository → Service → API/Web response/template.

## 2. Coding Conventions

- **Imports**: Keep the three-block comment headers (`# Python Standard Library Imports`, `# Third-party Imports`, `# Local Imports`) even when a section is empty.
- **Routers**: Instantiate `CostCodeService()` at module scope; endpoints return `.to_dict()` or list comprehension over service results.
- **Schemas**: Use `pydantic.BaseModel` with field constraints (`Field(min_length=1, max_length=255, description=...)`).
- **Models**: Use dataclasses with optional string fields. Store row version as base64 string and expose helper properties (`row_version_bytes`, `row_version_hex`) as in `model.py`.
- **Services**: No heavy business logic yet—primarily repository delegation with minimal data prep. Accept dataclass instances (or `CostCodeUpdate` models converted before repository calls).
- **Repositories**:
  - Wrap database calls in `try/except`, log via `logging.getLogger(__name__)`, and rethrow using `map_database_error`.
  - Use `_from_db` helper to convert `pyodbc.Row` into the dataclass.
  - For rowversion parameters, send bytes via `cost_code.row_version_bytes`.
  - Keep stored-procedure names literal and centralize param dictionaries in `call_procedure`.
- **Templates**: Expect Jinja context keys:
  - `list.html`: `cost_codes` list of `CostCode` (dataclass) instances.
  - `view.html`, `edit.html`: `cost_code` dictionary (`to_dict()` output).
  - Include Bootstrap CDN, guard optional fields with Jinja conditionals, and keep inline CSS for small tweaks.

## 3. SQL Expectations

- `sql/dbo.costcode.sql` must:
  - Drop dependent constraints/columns before recreating tables when altering schema.
  - Define `Create/Read/Update/Delete` stored procedures returning the same shape used in `_from_db`.
  - Ensure rowversion outputs appear in the `OUTPUT` clause so repository updates have concurrency data.

## 4. Adding Features

When adding to the module:

1. **Start with SQL**: Create or adjust stored procedures first; maintain consistent column aliases.
2. **Update Repository**: Add methods that map one-to-one with the new procedures; ensure `_from_db` still handles returned shape.
3. **Extend Service**: Provide a simple wrapper that the API/Web layers can call.
4. **Adjust Schemas**: Add request/response models matching new fields.
5. **Expose in API/Web**: Register new routes under existing prefixes, return dictionaries or render templates similarly to existing handlers.
6. **Templates/JS**: For web updates, replicate Bootstrap styling and guard optional data; fetch data from `/api/v1/...` endpoints using `fetch` with JSON payloads when needed.
7. **Testing/Verification**: Use existing service and repo methods as examples; verify rowversion behavior to avoid concurrency mismatches.

## 5. Environment & Config

- Runtime configuration comes from `config.Settings` (Pydantic). In production, values are delivered via environment variables or App Service settings (no `.env` file committed).
- The service `CostCodeService()` and repository assume database connectivity through `shared.database`; ensure DSN and credentials are available before running.
