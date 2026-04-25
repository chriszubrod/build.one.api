# TODO

Carry-over items from sessions. Check off as done; prune anything stale.

## Intelligence layer

- [x] **Parallel tool dispatch in `runner.py`.** (API `bd327b5`, 2026-04-25) Refactored the dispatch loop into `_dispatch_tools_concurrently` — handlers run via `asyncio.create_task`, events forward live via a shared queue, result_blocks land in original call order. Verified ~2x speedup on the synthetic two-tool case.
- [x] **Add `read_cost_code_by_number` tool.** (API `bd327b5`, 2026-04-25) Endpoint `GET /api/v1/get/cost-code/by-number/{number}` + agent tool added (service + repo + sproc were already in place).
- [ ] **UI lanes for concurrent sub-agent events.** With parallel dispatch live, forwarded sub-agent events from concurrent delegations now interleave on scout's stream. Per-sub-agent chronology is preserved (each event carries its session_public_id), but visually they mix together. Group sub-agent events into their own lane in the tray (collapsible / tabbed / indented) so the user can read each specialist's flow coherently.
- [ ] **Entity expansion: next specialist.** SubCostCode / CostCode / Customer / Project / Vendor all live. Future picks: Bill, Expense, Invoice, ContractLabor, etc. Same template per entity (~20-30 min each).

## Frontend perf

- [x] ~~React Query caching for `/lookups`, `/vendors`, `/projects`~~ — shipped 2026-04-23 via TanStack Query (build.one.web `42e0a90`). staleTime 5min, gcTime 10min, refetchOnWindowFocus off.
- [ ] Consider replacing the "dump all vendors" pattern with a searchable/paginated select when tenant size grows past a threshold.
- [ ] Verify `X-Cache: hit` vs `miss` shows up in dev tools on `bill-folder-summary` after back-to-back loads (we saw 292ms once; want to confirm pattern). If misses are frequent due to per-worker cache with `-w 2`, consider Redis.

## Frontend pre-launch (build.one.web)

- [x] **Graceful token refresh — stop the 30-min lockouts.** (web `36573c6`, 2026-04-24) `client.ts` now calls `POST /api/v1/auth/refresh` on 401 and retries once before wiping localStorage. Covers `request`, `uploadFile`, `fetchViewAttachmentBlob`.
- [x] **Patch `src/agents/sseClient.ts` to use the same refresh machinery.** (web `5b9aaa9`, 2026-04-24) `start`, `continue`, `approve`, and `events` all now route through the shared `fetchWithRefresh` helper exported from `client.ts`. `cancel` stays raw but proactively refreshes first.
- [ ] **Dev-gate the TanStack Query devtools.** `src/main.tsx` currently renders `<ReactQueryDevtools />` unconditionally, so the floating panel ships to prod. Before launch, wrap in `{import.meta.env.DEV && <ReactQueryDevtools ... />}`. Keep the dev-only version for local debugging.

## Jinja purge — migration waves (started 2026-04-23)

Phase 0 audit done. Phase 1 scaffolding (`/auth/me` + SSE `/auth/me/changes` + React `useCurrentUser` + Sidebar gating) shipped.

Phase 2 — delete Jinja per entity in waves, one commit each, verify in UI before next wave:

- [x] **Wave A — leaf entities:** address_type, payment_term, review_status, vendor_type, taxpayer, classification_override, dashboard. (2026-04-24, `f34b63b`; 21 routes retired, 622 → 601)
- [x] **Wave B — reference entities:** cost_code, sub_cost_code, customer, vendor, address, organization, project, company, module. (2026-04-24, `f6cecf2`; 36 routes retired, 601 → 565)
- [x] **Wave C — identity surface:** user, role, role_module, user_module, user_project, user_role, vendor_address, project_address. (2026-04-24, `e0ddc32`; 28 routes retired, 565 → 537. `project_address` controller was dead code — never registered in `app.py`.)
- [x] **Wave D — transactional:** bill, bill_credit, expense, invoice. (2026-04-24, `7758405`; 17 routes retired, 537 → 520. Extracted `_enrich_line_items` → `entities/invoice/business/enrichment.py` as `enrich_line_items` since the invoice packet PDF generator depends on it; dropped legacy `/expense/edit/{id}` redirect.)
- [x] **Post-Wave-E admin rip (2026-04-24):** removed the React admin surface E4 had just built and the dead workflow-approval plumbing. Deleted `entities/admin/` (api + schemas + empty business/persistence stubs), `core/workflow/business/admin.py` (`WorkflowAdmin`), `core/workflow/business/pending_actions.py`, `core/workflow/business/execute_pending_action.py`, `core/workflow/api/pending_action_router.py`, React `pages/admin/AdminDashboard.tsx` + `WorkflowDetail.tsx`, and their App.tsx routes. All action buttons (retry/approve/reject/cancel) gated on workflow states (`awaiting_approval`, `failed`, `needs_review`) that no production code transitions to anymore — the email-intake workflow that used them was decommissioned months ago and agents use their own approval system on `AgentSession`. Kept the workflow **data layer** (`orchestrator`, `instant`, `models`, `WorkflowRepository`, `WorkflowEventRepository`, `execute_synchronous`) — every entity CRUD still records a `Workflow` + `WorkflowEvent` row for audit. Routes: 484 → 472 (−12). If email-intake returns (planned "inbox rebuild"), the admin UI gets rebuilt for the new workflow shape anyway.
- [x] **Wave E — shared-infra deletion (2026-04-24):** deleted all remaining Jinja web controllers (auth, integration, 5 QBO under `integrations/intuit/qbo/*/web/`, sync, plus 4 empty-stub controllers on taxpayer_attachment / invoice_line_item / invoice_attachment / invoice_line_item_attachment), the entire `templates/` tree, the `static/` directory, and all Jinja helper code — `get_current_user_web`, `require_module_web`, `WebAuthenticationRequired`, `RefreshRequired`, the two FastAPI exception handlers in `app.py`, and the `StaticFiles` mount. Dropped `Jinja2==3.1.6` from `requirements.txt` + `requirements-prod.txt`. The QBO OAuth callback now returns a self-contained HTML landing page instead of redirecting — since React runs locally only, there's no public web host to bounce to, so the API renders a "QuickBooks connected, close this tab" page inline via `HTMLResponse`. CSRF helpers kept (React's silent refresh relies on them). `entities/auth/business/service.py` pruned (~100 lines gone, 7 orphan service imports removed). Routes: 508 → 484 (−24). Password reset deferred — ship as its own small wave when needed; it doesn't block anything now.
  - [x] **E4 — admin in React (2026-04-24):** new `AdminDashboard.tsx` (6 stat cards + searchable/filterable workflows table via `/api/v1/admin/stats` + `/admin/recent-workflows` + `/workflows/search`) and `WorkflowDetail.tsx` (meta header, related-entities links, full event timeline, and retry / approve / reject / cancel actions with inline modals). Dropped the Jinja "Inbox Quality" tab entirely — it referenced `/api/v1/admin/inbox-stats` + `/api/v1/classification-overrides*` endpoints that were never implemented (email-intake surface was decommissioned earlier). Also deleted the 4 orphan React `ClassificationOverride*` pages + their App.tsx routes + the `ClassificationOverride` type — the entity was purged from the API months ago. Deleted `entities/admin/web/` + `templates/admin/` (view.html, workflow_detail.html, overrides.html). Routes: 510 → 508 (−2). `static/css/admin.css` is now orphaned and will go with Wave E5.
  - [x] **E3 — contract_labor Import + Bills in React (2026-04-24):** new React pages `ContractLaborImport.tsx` (drag-and-drop Excel upload, results display) and `ContractLaborBills.tsx` (per-vendor card grid, per-vendor + bulk generate, collapsible day summary, auth-aware PDF preview). Added Import/Bills links to `ContractLaborList`. Extracted ~200 lines of vendor-aggregation business logic from the old Jinja `bills_page` into `entities/contract_labor/business/bill_summary.py::build_bills_summary`; exposed at `GET /api/v1/contract-labor/bills-summary?billing_period=...`. Deleted entire `entities/contract_labor/web/` + `templates/contract_labor/` (including the already-redundant `/list`, `/edit`, `/{id}` Jinja routes). Routes: 515 → 516 (+1 summary endpoint) → 510 (−6 web routes) = **510 net**.
  - [x] **E2 — React signup (2026-04-24):** added `SignupPage.tsx` + `signup()` on `AuthContext`, routed at `/signup`, cross-linked from `LoginPage`. Reuses existing `POST /api/v1/signup/auth`; on success redirects to `/user/create` to complete the User entity. Deleted Jinja `GET /auth/signup` route and `templates/auth/signup.html` (routes 516 → 515). Two stale `/auth/signup` links remain in `templates/shared/partials/header.html` + `templates/auth/login.html` — both go with Wave E5 shared-infra deletion, low impact since nobody lands on those Jinja pages.
  - [x] **E1 — dead-code + quick wins (2026-04-24):** deleted `entities/attachment/web/controller.py` (unreachable duplicate of `/api/v1/view/attachment`), deleted entire `entities/legal/` + `templates/legal/` and added React `EulaPage`/`PrivacyPage`, wired `<InlineContacts>` into `UserEdit`/`UserView` (Company already had it). Routes 520 → 516 (−4). (An OAuth `?success=&message=` toast was also added to `IntegrationList` but removed later the same day — React runs locally only, so the callback renders a self-contained HTML landing page on the API host rather than cross-redirecting.)
- [x] **Contact UI re-implementation (2026-04-24).** `<InlineContacts>` React component is wired into all 5 parent edit pages (Vendor, Company, Customer, Project, User) and all 5 parent view pages (readOnly). Old `shared/partials/contacts_*.html` partials are unreachable and will be removed with shared-infra deletion.

Follow-ups / post-purge:

- [ ] **Promote SSE profile events from B-lite to B-full** (cross-worker). Add `[auth].[ProfileChangeEvent]` table + 2s poll in the SSE handler. Needed once we scale past `-w 2` single instance, or once we have a user base that hits cross-worker edges often.
- [ ] **Add `IsNavigable BIT NOT NULL DEFAULT 1` to `dbo.[Module]`.** Today every Module row that exists for RBAC permission gating also shows up in the sidebar. `Attachments`, `Pending Actions`, and `Time Tracking` are legitimately-scoped permission modules but have no top-level UI page; today they render as broken sidebar links. Splitting the flag lets the backend keep RBAC scopes distinct from navigation. `_resolve_me_payload` should then omit non-navigable modules from the `modules[]` array (or the sidebar should filter on the flag).
- [ ] **Password reset flow.** Login + signup + silent refresh all work; reset has no implementation anywhere (no API endpoint, no token schema, no React page). Ship as its own small wave when needed — doesn't block anything.

## Data hygiene

- [ ] **Audit which entities should use soft delete vs hard delete (cross-cutting).** The model is inconsistent today — `Vendor` soft-deletes (`IsDeleted` column + `SoftDeleteVendorByPublicId` sproc), but `CostCode` / `SubCostCode` / `Customer` / `Project` hard-delete. Some hard-delete entities probably want soft: anything FK-referenced from historical records (customers with old projects, sub-cost-codes referenced from old line items, projects on completed bills, etc.). For each entity decide: what FKs point at it, what's the audit/history value of keeping the row, what's the UX cost of orphaned references on hard-delete. Apply uniformly. The agent delete tools' approval gate stays the same either way — only the underlying behavior changes.

## API cleanup (do in a week, after Function architecture is proven stable)

- [ ] Delete `shared/scheduler.py` + remove `start_scheduler()` / `shutdown_scheduler()` calls from `app.py`
- [ ] Remove `apscheduler` from `requirements.txt` and `requirements-prod.txt`
- [ ] Memory savings: probably 50–100 MB per worker after the scheduler imports are gone
- [ ] Keep `ENABLE_SCHEDULER` env var for at least one more deploy cycle so rollback is still available; remove the code+flag together
- [ ] Delete duplicate `read_paginated`/`count` in `entities/bill/persistence/repo.py` (lines 123 & 328, 161 & 369). The first definition of each is dead code — the second wins due to Python attribute order

## QBO hardening follow-ups (2026-04-23)

- [ ] **Clean up stale `ReconciliationIssue` rows.** Before the `upsert_from_external` fix landed, `_reconcile_bill_qbo_missing_locally` always crashed in the connector and recorded the bill as a flagged issue with `LastError LIKE '%vendor_ref_value%'`. Those rows pollute the Ch6 drift count and alert #6. Query and delete (or mark resolved) once the fix has been deployed for one reconcile cycle. Approx query: `DELETE FROM [qbo].[ReconciliationIssue] WHERE DriftType='qbo_missing_locally' AND LastError LIKE '%vendor_ref_value%'` — verify count before running.
- [ ] **Un-defer task #8 (QBO test scaffold).** The `vendor_ref_value` bug in reconciliation + all three `_refresh_*` paths would have been caught by a single sandbox integration test. Revisit when App Insights lands (current blocker for #8 per Phase 2 notes).

## Bill Folder follow-ups (2026-04-24)

- [ ] **`_run_single_file_processing` still uses the in-process dict** and has the same cross-worker `-w 2` bug the bulk Process Folder flow had before today's refactor. Route is `POST /api/v1/process/bill-folder-single` in `entities/bill/api/router.py`. Fix by routing it through the same `BillFolderRunItem` table + tick pattern (one ad-hoc `BillFolderRun` with one item in it) — or by replacing with a direct synchronous call since it's already single-file. Low urgency (users rarely hit it), but it will silently 404 the polling loop when workers disagree.
- [ ] **If we add a third feature that needs the one-file-per-tick pattern**, generalize into `[tasks].[Run]` / `[tasks].[RunItem]` + a `TaskQueueService` so we stop duplicating sprocs. Pattern captured in `project_one_file_per_tick.md` memory.

## Observability / Ops

- [ ] App Insights alert rules on Function App failures: `drain_outbox` > 3 failures in 15 min; any QBO sync > 2 consecutive failures
- [ ] Structured cross-service correlation: Function sends its invocation id in a header; API logs it alongside `admin.job.completed`. Lets App Insights trace "Function ran X" → "API processed Y"
- [ ] Outbox lag metric: query `SELECT COUNT(*) FROM [qbo].[Outbox] WHERE Status='pending' AND ReadyAfter <= GETUTCDATE()` periodically; alert on sustained backlog
- [ ] Write runbook `docs/runbooks/scheduler-function-failure.md` documenting Symptom / Recovery (flip `ENABLE_SCHEDULER=true` + restart) / Verification

## Lower priority / backlog

- [ ] Verify (or add) `Pooling=Yes` in the container's `/etc/odbcinst.ini`. Without driver-manager pooling, each request opens a fresh pyodbc connection (TLS + SQL auth handshake). `pyodbc.pooling = True` alone in Python doesn't help unless the driver config enables it.
- [ ] Rate-limit admin endpoints. Low risk given `sp_getapplock` serializes, but belt-and-suspenders
- [ ] Cross-region latency audit: confirm Azure SQL is in the same region as App Service + Function App. A different region adds 50–200 ms per query
- [ ] Consider going `-w 4` once we confirm `-w 2` is steady. Memory easily accommodates it (current usage ~334 MB on 3.5 GB tier)
- [ ] Shared cache (Redis) for `bill-folder-summary` — eliminates per-worker cache miss when `-w 2` round-robins. Not urgent; Always On mitigates

## Rotate / security hygiene

- [ ] `DRAIN_SECRET` was leaked briefly during this session's debugging, was rotated mid-session. If you see unfamiliar activity in App Insights for `/api/v1/admin/*` endpoints, rotate again.
