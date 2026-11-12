# Web/UI patterns the API constrains

React-side patterns that exist because of an API or persistence decision. The UI itself lives in `build.one.web`.

> Extracted verbatim from `CLAUDE.md` on 2026-11-11 (U-788) to take it off the
> per-session load. **These are standing rules, not history** — they bind the same as
> before. Read this file when touching the area; CLAUDE.md keeps the one-line rule and
> links here.

---

- **UI**: all UI is React (`build.one.web`). No server-rendered templates, no `templates/` directory. API routes return JSON only.
- **Auto-save**: Bill and Expense edit pages debounce saves at 300ms. Any action that depends on persisted state (Complete, Delete, **Submit for Review**) must flush (`await flushAutoSave()`) or guard against pending auto-saves before sending the request — cancelling the timer is NOT enough, an in-flight PUT still races the action's own PUT on the same `row_version` (ExpenseEdit had exactly this, fixed 2026-10-03). ExpenseEdit's `saveAll` now also tracks header/line dirtiness and issues ZERO PUTs on an untouched form (each PUT is a ProcessEngine workflow with audit rows), and ExpenseView offers Submit directly with no pre-save.
- **User Profile page (2026-04-29)**: `/user/:id` is a single React page (`build.one.web/src/pages/users/UserProfile.tsx`) collapsing the old view+edit split. Sections: Profile basics, Credentials (admin), Contacts, Organizations (`UserOrganization`), Companies (`UserCompany` — dropdown filtered to companies linked to the user's Orgs via `OrganizationCompany`), Roles, Modules, Projects. Admin-only edit affordances throughout (`is_admin` from `useCurrentUser()`).
- **User-relationship join entities (2026-04-29)**: `entities/user_organization/`, `entities/user_company/`, `entities/organization_company/` — all gated `Modules.USERS` (or `Modules.ORGANIZATIONS` for the latter), all routed through `ProcessEngine.execute_synchronous` for audit, registered in `app.py` + `SYNCHRONOUS_TASKS` + `SERVICE_REGISTRY`.
- **Join table UI pattern**: Join tables (UserRole, RoleModule, UserOrganization, UserCompany, OrganizationCompany) resolve FK UUIDs to names via lookup maps (`role_map`, `module_map`, `organization_map`, `company_map`) loaded by the consuming React page. Dropdown values use `public_id` (UNIQUEIDENTIFIER) for the entity, but POST bodies pass internal `id` (BIGINT) for FK columns.
- **Scroll restoration**: Pages that scroll via `<main id="content" class="main-content overflow-y: auto">` must use `document.getElementById('content').scrollTop`, NOT `window.scrollY` / `window.scrollTo()`. Save to `sessionStorage` on navigation; restore on `DOMContentLoaded` with double `requestAnimationFrame`.
