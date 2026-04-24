# TODO

Carry-over items from sessions. Check off as done; prune anything stale.

## Frontend perf

- [x] ~~React Query caching for `/lookups`, `/vendors`, `/projects`~~ — shipped 2026-04-23 via TanStack Query (build.one.web `42e0a90`). staleTime 5min, gcTime 10min, refetchOnWindowFocus off.
- [ ] Consider replacing the "dump all vendors" pattern with a searchable/paginated select when tenant size grows past a threshold.
- [ ] Verify `X-Cache: hit` vs `miss` shows up in dev tools on `bill-folder-summary` after back-to-back loads (we saw 292ms once; want to confirm pattern). If misses are frequent due to per-worker cache with `-w 2`, consider Redis.

## Frontend pre-launch (build.one.web)

- [ ] **Dev-gate the TanStack Query devtools.** `src/main.tsx` currently renders `<ReactQueryDevtools />` unconditionally, so the floating panel ships to prod. Before launch, wrap in `{import.meta.env.DEV && <ReactQueryDevtools ... />}`. Keep the dev-only version for local debugging.

## Jinja purge — migration waves (started 2026-04-23)

Phase 0 audit done. Phase 1 scaffolding (`/auth/me` + SSE `/auth/me/changes` + React `useCurrentUser` + Sidebar gating) shipped.

Phase 2 — delete Jinja per entity in waves, one commit each, verify in UI before next wave:

- [x] **Wave A — leaf entities:** address_type, payment_term, review_status, vendor_type, taxpayer, classification_override, dashboard. (2026-04-24, `f34b63b`; 21 routes retired, 622 → 601)
- [x] **Wave B — reference entities:** cost_code, sub_cost_code, customer, vendor, address, organization, project, company, module. (2026-04-24, `f6cecf2`; 36 routes retired, 601 → 565)
- [x] **Wave C — identity surface:** user, role, role_module, user_module, user_project, user_role, vendor_address, project_address. (2026-04-24, `e0ddc32`; 28 routes retired, 565 → 537. `project_address` controller was dead code — never registered in `app.py`.)
- [x] **Wave D — transactional:** bill, bill_credit, expense, invoice. (2026-04-24, `7758405`; 17 routes retired, 537 → 520. Extracted `_enrich_line_items` → `entities/invoice/business/enrichment.py` as `enrich_line_items` since the invoice packet PDF generator depends on it; dropped legacy `/expense/edit/{id}` redirect.)
- [ ] **Wave E — remaining gaps + shared infra:** fix React gaps for contract_labor (/import + /bills), admin, auth (signup/reset). Then delete `get_current_user_web`, `require_module_web`, `WebAuthenticationRequired` + its `app.py` handler, CSRF helpers, `entities/auth/web/controller.py`, the 5 QBO `web/controller.py` files, and the `templates/` tree.
  - [x] **E1 — dead-code + quick wins (2026-04-24):** deleted `entities/attachment/web/controller.py` (unreachable duplicate of `/api/v1/view/attachment`), deleted entire `entities/legal/` + `templates/legal/` and added React `EulaPage`/`PrivacyPage`, wired `<InlineContacts>` into `UserEdit`/`UserView` (Company already had it), added OAuth `?success=&message=` toast to `IntegrationList` (dormant — see caveat below). Routes 520 → 516 (−4). QBO OAuth callback endpoint is pure `RedirectResponse` and needs no migration, BUT it redirects relative (`/integration/list?success=...`), which resolves to the **API** host's Jinja `/integration/list` page. That Jinja page is still the one rendering the result banner today. React's toast handler will only fire once the callback is updated to redirect to the web-app host (Wave E5 chore — flip `RedirectResponse("/integration/list?...")` → `RedirectResponse(f"{WEB_APP_URL}/integration/list?...")` in `integrations/intuit/qbo/auth/api/router.py`).
- [x] **Contact UI re-implementation (2026-04-24).** `<InlineContacts>` React component is wired into all 5 parent edit pages (Vendor, Company, Customer, Project, User) and all 5 parent view pages (readOnly). Old `shared/partials/contacts_*.html` partials are unreachable and will be removed with shared-infra deletion.

Follow-ups when the purge is done:

- [ ] **Promote SSE profile events from B-lite to B-full** (cross-worker). Add `[auth].[ProfileChangeEvent]` table + 2s poll in the SSE handler. Needed once we scale past `-w 2` single instance, or once we have a user base that hits cross-worker edges often.
- [ ] **Refresh-token flow for React.** API already issues refresh tokens; React doesn't use them. Add `POST /api/v1/auth/refresh` wrapper in `client.ts` on 401 before wiping localStorage and redirecting.
- [ ] **Add `IsNavigable BIT NOT NULL DEFAULT 1` to `dbo.[Module]`.** Today every Module row that exists for RBAC permission gating also shows up in the sidebar. `Attachments`, `Pending Actions`, and `Time Tracking` are legitimately-scoped permission modules but have no top-level UI page; today they render as broken sidebar links. Splitting the flag lets the backend keep RBAC scopes distinct from navigation. `_resolve_me_payload` should then omit non-navigable modules from the `modules[]` array (or the sidebar should filter on the flag).

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
