At the start of each session, read SESSION_NOTES.md for historical context.

## Project Conventions

- **Entity pattern**: `entities/{name}/` with `api/`, `business/`, `persistence/`, `sql/`, `web/` sub-packages
- **SQL**: All DB access via stored procedures (pyodbc). Migrations run with `python scripts/run_sql.py path/to/file.sql`
- **Concurrency**: SQL Server ROWVERSION columns with base64 encoding for transport
- **Templates**: Jinja2 in `templates/` directory, extend `shared/layout/base.html`. All routes must pass `current_path: request.url.path`
- **Workflow engine**: TriggerRouter for main entity CRUD; lightweight child entities use direct CRUD
- **Lazy imports**: Some services (e.g., BillService) use lazy imports in `__init__` to avoid circular deps — always use `self.*` instance attributes, not bare class names
- **Stored procedure NULL handling**: UPDATE sprocs unconditionally SET all columns. Use CASE WHEN guards for fields that should preserve existing values when NULL is passed
- **Auto-save**: Bill edit page debounces saves at 300ms. Any action that depends on persisted state (Complete, Delete) must flush or guard against pending auto-saves
- **RBAC chain**: User → UserRole → Role → RoleModule → Module. Role entity is the core — UserRole and RoleModule are join tables. Role assignment is managed inline on User create/edit pages. Authorization middleware not yet implemented
- **Join table UI pattern**: Join tables (UserRole, RoleModule) resolve FK UUIDs to names via lookup maps (`user_map`, `role_map`, `module_map`) passed from controllers. Dropdown values use `public_id` (UNIQUEIDENTIFIER), not internal `id` (BIGINT)
- **Contact entity**: Polymorphic child entity with nullable FKs to User, Company, Customer, Project, Vendor. Fields: Email, OfficePhone, MobilePhone, Fax, Notes. Managed inline on parent view/edit pages via reusable partials (`shared/partials/contacts_view.html`, `shared/partials/contacts_edit.html`). Uses instant workflow (TriggerRouter.route_instant)
