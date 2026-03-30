# BUILD.ONE — SESSION HANDOFF DOCUMENT
**Date:** 2026-03-28
**Session Focus:** RBAC Authorization Enforcement + Open Item Fixes
**Status:** Complete

---

## SESSION SUMMARY — WHAT WAS BUILT

### 1. correct_classification Row Version Enforcement (Open Item #3 from prior session)

The `correct_classification` endpoint accepted `row_version` at the API boundary but never enforced it. Now fully wired end-to-end:

- **dbo.UpsertEmailThread sproc** — Added `@RowVersion BINARY(8) = NULL` parameter. MERGE conditional: `WHEN MATCHED AND (@RowVersion IS NULL OR target.RowVersion = @RowVersion)`. Post-MERGE guard: `IF @@ROWCOUNT = 0 AND EXISTS(...)` raises concurrency error (severity 16). Also added `Category` and `ProcessType` to the UPDATE SET clause (were previously missing — correct_classification silently failed to persist reclassification).
- **EmailThreadRepository.upsert()** — Added `row_version: Optional[bytes] = None` parameter, passes decoded bytes to sproc.
- **EmailThreadRepository._from_db()** — Now base64-encodes `RowVersion` bytes to ASCII string (matching Bill/Vendor/all other entity repos). Previously stored raw bytes, breaking JSON round-trip.
- **EmailThreadService.correct_classification()** — Added `row_version: Optional[str]` parameter, decodes base64 → bytes, passes to repo.
- **Email thread API router** — Passes `body.row_version` through to service.

**Concurrency pattern:** Optional row_version (NULL bypasses check) to support both upsert-from-agent (no version) and upsert-from-UI (version required). Differs from other entities (Bill, Vendor) where RowVersion is mandatory on update — appropriate because EmailThread uses MERGE (upsert) while others use pure UPDATE.

### 2. classify_email Caller Audit (Open Item #8 from prior session)

- **classify_email() signature** — `inbox_record_id` changed from `int` to `Optional[int] = None`. Phase 3 (thread write) already guarded with `if inbox_record_id:`. Added `override_service=None` parameter, passed through to `EmailClassifier()`.
- **classify_email_heuristic() signature** — Added `override_service=None` parameter, passed through to `EmailClassifier()`.
- **InboxService._classify_message()** — Removed stale `tenant_id=1` parameter. Added `inbox_record_id` and `internet_message_id` optional parameters (default None). Added `or ""` safety on subject/from_email. Passes `override_service=self._override_svc`.
- **InboxService._classify_message_heuristic()** — Fixed to pass `override_service=self._override_svc`.
- **Callers verified:** Two callers of `_classify_message` (get_message_detail, classify_message) use None defaults safely — Phase 3 skips when inbox_record_id is None. `email_intake.py:122` is a string reference, not a function call.
- **No stale callers remain.**

### 3. RBAC Authorization Enforcement (Open Item #1 from prior session — PRIMARY)

Complete module-level RBAC enforcement across all application endpoints.

#### Architecture

**Dependency factory pattern:**
```python
from shared.rbac import require_module_api, require_module_web
from shared.rbac_constants import Modules

# API routes
@router.get("/bills")
async def list_bills(current_user=Depends(require_module_api(Modules.BILLS))):

@router.post("/bills")
async def create_bill(current_user=Depends(require_module_api(Modules.BILLS, "can_create"))):

# Web routes
@router.get("/bill/list")
async def bill_list(request: Request, current_user=Depends(require_module_web(Modules.BILLS))):
```

**Permission resolution chain:**
```
JWT sub (public_id) → AuthService → user_id
    → UserRoleService → role_id
        → RoleService → admin bypass (role.name == "admin")
        → RoleModuleService + ModuleService → permission check
```

**Seven permission flags** (from RoleModule): `can_read`, `can_create`, `can_update`, `can_delete`, `can_submit`, `can_approve`, `can_complete`

**HTTP method → permission mapping used across all endpoints:**
- GET → `can_read` (default)
- POST → `can_create` (except search/list/filter/lookup endpoints → `can_read`)
- PUT → `can_update`
- DELETE → `can_delete`
- POST complete → `can_complete`
- POST approve → `can_approve`
- POST submit → `can_submit`

#### Files Created

| File | Purpose |
|------|---------|
| `shared/rbac.py` | Dependency factories, cached permission resolution, cache management, startup validation |
| `shared/rbac_constants.py` | `Modules` class with 26 constants matching dbo.[Module].Name values |
| `entities/module/sql/seed.AllModules.sql` | Idempotent INSERT for all 26 module records |

#### Design Decisions

1. **5-minute TTL cache** — `_get_user_permissions(user_sub)` resolves the full chain once, caches as `{module_name: RoleModule}` dict keyed by user_sub. Thread-safe via `threading.Lock`. After first request, RBAC check is a dict lookup — zero DB calls. Matches `ClassificationOverrideService` caching pattern.

2. **Module name constants** — `Modules.BILLS` instead of `"Bills"`. Typos become `AttributeError` at import time, not silent 403s in production.

3. **Admin bypass** — `role.name.strip().lower() == "admin"` grants full access. Cached as `{_ADMIN_SENTINEL: True}` sentinel dict.

4. **Cache invalidation** — `invalidate_all_caches()` called on every UserRole and RoleModule create/update/delete. `invalidate_user_cache(user_sub)` available for targeted invalidation.

5. **Startup validation** — `validate_module_constants()` runs at app startup, logs warnings for any mismatch between `Modules` constants and dbo.[Module] rows. Surfaces missing seed data immediately.

6. **UserModule vs RoleModule** — `get_current_user_web` uses UserModule for UI navigation scoping (what menu items appear). RBAC uses RoleModule for access control (what actions are permitted). These are separate concerns. Keep them in sync when assigning access.

#### Rollout Summary

| Area | Files Modified | Endpoints | Module Constants Used |
|------|---------------|-----------|----------------------|
| Financial (Bill, BillCredit, Expense, Invoice, ContractLabor + line items) | 22 | ~85 | BILLS, BILL_CREDITS, EXPENSES, INVOICES, CONTRACT_LABOR |
| Reference (Vendor, Customer, Project, CostCode + sub-entities) | 18 | ~55 | VENDORS, CUSTOMERS, PROJECTS, COST_CODES |
| Inbox & Email | 3 | ~17 | INBOX, EMAIL_THREADS |
| Admin (User, Role, Module, RoleModule, UserRole, UserProject, Org, Company) | 18 | ~45 | USERS, ROLES, ORGANIZATIONS, COMPANIES, PROJECTS |
| AI & Processing (Anomaly, Categorization, Copilot) | 3 | ~12 | ANOMALY_DETECTION, CATEGORIZATION, COPILOT |
| Attachments (all 6 attachment entities) | 6 | ~42 | ATTACHMENTS |
| Other (Dashboard, Search, QA, ClassOverrides, PendingActions, QBO, Integrations) | 32 | ~50 | DASHBOARD, SEARCH, CLASSIFICATION_OVERRIDES, PENDING_ACTIONS, QBO_SYNC, INTEGRATIONS |
| **Total** | **~102** | **~300+** | **26 modules** |

#### Intentionally Unprotected Routes

- `entities/auth/` — login, signup, refresh, logout (public by design)
- `entities/legal/` — EULA, privacy policy (public by design)
- `app.py` — `/ping`, `/info`, `/` redirect (health checks)

---

## WHAT IS MISSING OR INCOMPLETE

### 1. EmailThread SQL Migration Not Yet Run
The three EmailThread tables, the UpsertEmailThread changes (RowVersion parameter, Category/ProcessType in UPDATE SET), and the Module seed data (seed.AllModules.sql) exist as SQL files but must be run against the production database.

### 2. Module Seed Data Must Be Run Before RBAC Works
`entities/module/sql/seed.AllModules.sql` must be executed to create the 26 Module rows. Without them, `validate_module_constants()` will log warnings at startup and all non-admin users will receive 403 on every endpoint (admin bypass still works).

### 3. RoleModule Assignments Needed
After seeding Modules, an admin must create RoleModule records (via the Roles UI or direct SQL) to grant each role access to appropriate modules with the correct permission flags. Until this is done, only admin users can access the application.

### 4. Email Process Stage Transitions Not Enforced Beyond Initial Stage
(Unchanged from prior session) No scheduler or hook drives auto-advance stages.

### 5. Entity Handoff from Email Thread to Entity Process
(Unchanged from prior session) entity_handoff field returned but not consumed.

### 6. Process Inbox UI
(Unchanged from prior session) API exists, no web template.

### 7. SLA Breach Scheduler
(Unchanged from prior session) Sproc exists, no APScheduler job.

### 8. UpdateEmailThreadMessageClassification Sproc
(Unchanged from prior session) Verify existence in database.

### 9. RBAC Phase 2 — Granular Module Mapping Review
The current module-to-router mapping was done by logical grouping (e.g., all attachment types → Attachments module, sub cost codes → Cost Codes module). Production usage may reveal that some sub-entities need their own modules or different permission levels. Review after initial deployment.

### 10. RBAC Phase 2 — Web Controller Unprotected Routes
Some web controller endpoints for attachment viewing/downloading may need different access patterns (e.g., any user who can read Bills should also be able to view Bill attachments). Currently mapped to Attachments module — may want to inherit from parent entity instead.
