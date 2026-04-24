At the start of each session, read SESSION_NOTES.md for historical context.

## Working Style

- **Plan before coding.** Propose a step-by-step plan and wait for approval before writing any code. Do not start implementing until the plan is confirmed.

## Architecture Decisions (April 2026)

- **Multi-repo structure.** The project is split into three repos under `/Users/chris/Applications/build.one/`: `build.one.api` (this repo), `build.one.web` (React frontend), `build.one.ios` (iOS app). Each is an independent GitHub repo.
- **API response envelope.** All API endpoints return standardized envelopes: `{"data": [...], "count": N}` for lists, `{"data": {...}}` for single entities, `{"status": "accepted", ...}` for 202. Use helpers from `shared/api/responses.py`.
- **Lookup endpoint.** `GET /api/v1/lookups?include=vendors,projects,...` returns slim dropdown data. Used by React frontend. See `shared/api/lookups.py`.
- **Jinja2 templates are broken.** The response envelope change broke 81 templates that make AJAX calls. The templates are being replaced by the React app and should not be fixed.
- **React frontend.** `build.one.web` uses React + Vite + TypeScript. Dev server proxies `/api` to `localhost:8000`. Entity pages are being migrated incrementally from Jinja2.
- **Old AI layer was removed; new `intelligence/` layer replaces it.** The legacy AI surface is gone (`shared/ai/`, `integrations/azure/ai/`, `entities/qa`, `entities/search`, `entities/anomaly`, `entities/categorization`, `ClaudeExtractionService`, the project-resolution agent, and the attachment `ExtractionService`). Azure OpenAI, Document Intelligence, AI Search, and embeddings are all gone. A new `intelligence/` package has been built from scratch (see section below). **Do not reintroduce LangChain/LangGraph** — the new layer uses HTTPX directly against provider APIs.
- **No inbox / email-intake surface.** `entities/inbox`, `entities/email_thread`, `entities/classification_override`, `entities/review_entry`, the `email_intake` workflow definition + handlers in `ProcessEngine`, the `core/workflow/business/process_registry/` package, and the `templates/inbox/` + `templates/agent/` template trees are all gone. Bills and expenses are created manually via the web UI / API. `integrations/ms/mail` is intentionally retained for a future rebuild but currently has no callers in the app.
- **`core/notifications/` no longer exists.** Push notifications (APNs), device tokens, and SLA scheduler were removed. Do not reference `core.notifications`, `device_token`, or push_service.
- **Bill/expense folder processing removed.** The bill_agent and expense_agent (folder scanners) were deleted. "Process Folder" buttons on list pages will 404 until rebuilt.
- **`ProcessEngine` is CRUD-only.** Only the instant-workflow path (`execute_synchronous` / `_handle_instant_workflow`) is wired up. The `executor` property still raises `RuntimeError`; the email-intake / expense-intake / approval / timeout handlers are gone. `route()` will return `"Unknown workflow type"` for anything that isn't an instant workflow.

## Intelligence Layer (April 2026)

- **`intelligence/` package** houses all AI disciplines — agents today, room for RAG, extraction, classification, etc. Built from scratch to replace the removed AI layer. No LangChain/LangGraph; HTTPX directly against provider APIs.
- **Scout agent** (`intelligence/agents/scout/`) — first and only agent today. Read-only Q&A assistant. Scope: SubCostCode entity only. Expands one entity at a time by adding `entities/{name}/intelligence/tools.py` + updating `intelligence/agents/scout/__init__.py` imports + appending to `scout.tools` tuple in `definition.py`.
- **Agent-as-user auth.** Each agent has its own `User` + `Auth` row. At run start, the orchestrator logs the agent in via `POST /api/v1/mobile/auth/login` and obtains a JWT. Every tool call is a real HTTP request with that bearer token, routed through `shared.rbac.require_module_api()` the same way a human request would be. Tools never call services directly — `ToolContext.call_api()` is the one and only path. Prod `AgentSession` rows therefore record `AgentUserId` + `RequestingUserId` for full audit.
- **Tools are colocated with entities.** An entity's agent-facing tools live at `entities/{name}/intelligence/tools.py` alongside its `api/`, `business/`, `persistence/`, `sql/`. Self-register on import. Wraps GET endpoints via `ctx.call_api()`.
- **Agent credentials pattern.** Each `Agent` declares a `credentials_key` (e.g. `"scout_agent"`); the auth helper reads `{key}_username` / `{key}_password` off `config.Settings`. Prod Azure App Service Application Settings must carry these — also `ANTHROPIC_API_KEY` and `INTERNAL_API_BASE_URL` (set to the app's own prod URL; the default `http://localhost:8000` is wrong in prod).
- **Conversation threading.** `AgentSession.PreviousSessionId` links continuation sessions. `POST /api/v1/agents/runs/{public_id}/continue` with a follow-up `user_message` creates a new session pointing at the prior head; `intelligence/persistence/history.py::load_chain_history()` walks the chain and synthesizes canonical Messages as prior context.
- **Sub-agent composition (deferred).** `AgentSession.ParentSessionId` is reserved for specialist agents invoked as delegated tools (e.g. future `SubCostCodeAgent`). Schema is wired; no code path uses it yet. Extract a specialist when a tool set approaches ~8 tools or when writes with non-trivial validation appear.
- **HTTP surface** (`intelligence/api/router.py`): `POST /api/v1/agents/{name}/runs`, `GET /api/v1/agents/runs/{public_id}/events` (SSE; live from in-memory channel or DB replay for completed sessions), `POST /api/v1/agents/runs/{public_id}/cancel`, `POST /api/v1/agents/runs/{public_id}/continue`. All gated on `Modules.DASHBOARD`. Cancel / continue require caller to match the session's `RequestingUserId`.
- **React integration.** `build.one.web/src/agents/` holds the hook + SSE client; `ScoutTray` renders as a right-side push drawer toggled by a header button. Conversation threading (user bubbles + agent blocks + "New" reset) is wired. No `/scout` route and no sidebar entry — intentional.
- **Schema files.** `intelligence/persistence/sql/dbo.agent_session.sql`, `dbo.agent_turn.sql`, `dbo.agent_tool_call.sql`. All idempotent (`IF NOT EXISTS` guards + `ALTER TABLE ADD` for additive changes). View + MERGE pattern.
- **Test scripts.** `scripts/smoke_intelligence_level1.py` (transport-only), `scripts/intelligence_loop_dry_run.py` (L2 tool loop; `--no-persist` skips DB), `scripts/scout_dry_run.py` (full scout end-to-end; needs API server running), `scripts/scout_sse_dry_run.py` (exercises the SSE surface via a real HTTP client), `scripts/inspect_agent_session.py` (reads a persisted session by public_id).

## Project Conventions

- **Entity pattern**: `entities/{name}/` with `api/`, `business/`, `persistence/`, `sql/`, `web/` sub-packages
- **SQL**: All DB access via stored procedures (pyodbc). Migrations run with `python scripts/run_sql.py path/to/file.sql`
- **Concurrency**: SQL Server ROWVERSION columns with base64 encoding for transport
- **Templates**: Jinja2 in `templates/` directory, extend `shared/layout/base.html`. All routes must pass `current_path: request.url.path`
- **Workflow engine**: ProcessEngine for main entity CRUD; lightweight child entities use direct CRUD
- **Lazy imports**: Some services (e.g., BillService) use lazy imports in `__init__` to avoid circular deps — always use `self.*` instance attributes, not bare class names
- **Stored procedure NULL handling**: UPDATE sprocs unconditionally SET all columns. Use CASE WHEN guards for fields that should preserve existing values when NULL is passed
- **Auto-save**: Bill and Expense edit pages debounce saves at 300ms. Any action that depends on persisted state (Complete, Delete) must flush (`await autoSave()`) or guard (`isSaving = true`) against pending auto-saves before sending the request
- **RBAC chain**: User → UserRole → Role → RoleModule → Module. Role entity is the core — UserRole and RoleModule are join tables. Role assignment is managed inline on User create/edit pages. Authorization middleware is implemented: `shared/rbac.require_module_api(Modules.X, "can_read"|"can_create"|…)` is a FastAPI dependency used on every entity router; permission map is cached per-user for 5 minutes
- **Join table UI pattern**: Join tables (UserRole, RoleModule) resolve FK UUIDs to names via lookup maps (`user_map`, `role_map`, `module_map`) passed from controllers. Dropdown values use `public_id` (UNIQUEIDENTIFIER), not internal `id` (BIGINT)
- **Contact entity**: Polymorphic child entity with nullable FKs to User, Company, Customer, Project, Vendor. Fields: Email, OfficePhone, MobilePhone, Fax, Notes. Managed inline on parent view/edit pages via reusable partials (`shared/partials/contacts_view.html`, `shared/partials/contacts_edit.html`). Uses instant workflow (ProcessEngine.execute_synchronous)
- **FK cascade on delete**: When deleting entities referenced by FK, nullify or delete child references first. Examples: `BillLineItemService.delete_by_public_id()` nullifies `InvoiceLineItem.BillLineItemId` before deleting; `ExpenseLineItemService.delete_by_public_id()` deletes blob → Attachment → ExpenseLineItemAttachment → ExpenseLineItem. SQL Server FK constraints have no CASCADE DELETE — application code must handle cleanup
- **Expense entity cascade delete**: blob (Azure) → Attachment record → ExpenseLineItemAttachment link → ExpenseLineItem. Each step in its own try-except so cleanup failures don't block the delete
- **Decimal precision**: All financial fields (rate, amount, markup, price, total_amount) must use `Decimal(str(value))` — never `float()`. Float round-trips corrupt values
- **QBO sync mappings**: `sync_to_qbo_bill()` must create `BillLineItemBillLine` mappings after storing QboBillLines, using `line_num` to correlate local BillLineItems with QBO API response lines. Without these mappings, subsequent `sync_from_qbo` creates duplicate BillLineItems
- **Invoice line item enrichment**: `enrich_line_items()` in `entities/invoice/business/enrichment.py` batch-fetches parent data per source type in one DB connection. Returns `vendor_name`, `parent_number`, `source_date`, `sub_cost_code_number/name`, `cost_code_number/name` (via `SubCostCode → CostCode` join), and `attachment_public_id`. Called by the packet generator in `entities/invoice/api/router.py`.
- **Invoice PDF packet**: `POST /api/v1/generate/invoice/{id}/packet` prepends two reportlab-generated TOC pages (basic: type→vendor; expanded: cost_code→type→vendor with subtotals) before merging attachment PDFs. Uses `pypdf` + `reportlab` (both in requirements). TOC "Type" column is derived from `source_type` — no schema field needed.
- **Contract Labor status workflow**: `pending_review` → `ready` → `billed`. An entry is `ready` only when it has a `bill_vendor_id` AND at least one complete line item. `pending_review` means line items are missing or incomplete.
- **Contract Labor IsBillable flag**: `ContractLaborLineItem.IsBillable = false` means the item is shown on the PDF with `$0.00` and excluded from `total_amount`. Use `is_billable is not False` (not `if is_billable`) to handle `None` (default billable) correctly.
- **Contract Labor BillLineItemId FK**: `ContractLaborLineItem.BillLineItemId` links back to the generated `BillLineItem`. The UPDATE sproc uses a `CASE WHEN` guard to preserve it when `NULL` is passed. Always read and re-pass the existing value when updating a line item to avoid wiping the FK.
- **Contract Labor bill_service variable shadowing**: In `_generate_combined_pdf()` and `generate_bills()`, inner loop accumulator vars must not be named `total_amount` — use `scc_amount`/`scc_price` to avoid shadowing the outer bill total.
- **Scroll restoration**: Pages that scroll via `<main id="content" class="main-content overflow-y: auto">` must use `document.getElementById('content').scrollTop`, NOT `window.scrollY` / `window.scrollTo()`. Save to `sessionStorage` on navigation; restore on `DOMContentLoaded` with double `requestAnimationFrame`.
- **Contract Labor import tuple unpack**: `_parse_row()` returns `(dict, skip_reason)` — always unpack with `parsed, skip_reason = self._parse_row(...)`. Assigning to a single variable and calling `.get()` on the tuple crashes at runtime.
- **VENDOR_CONFIG**: Single source of truth for vendor rate/markup in `bill_service.py`. Do not maintain parallel hardcoded JS maps in templates — pass `VENDOR_CONFIG` from the controller and derive JS maps via `{{ vendor_config|tojson }}`.

## External Integrations (QBO + MS) — Hardening Patterns (April 2026)

Both `integrations/intuit/qbo/` and `integrations/ms/` follow the same hardened shape after the April 2026 refactors. Future external integrations must follow this pattern.

- **`base/` package per integration**: `client.py` (shared HTTP wrapper), `errors.py` (typed exception hierarchy with `is_retryable`), `locking.py` (`sp_getapplock` context manager), `logger.py` (`LoggerAdapter` that auto-injects `correlation_id` from a ContextVar), `retry.py` (RetryPolicy + `execute_with_retry` with backoff + jitter + Retry-After), `correlation.py` (ContextVar-backed correlation + idempotency key), `idempotency.py` (UUID generation / validation).
- **Durable outbox pattern**: every external write enqueues a row in `[qbo].[Outbox]` or `[ms].[Outbox]` instead of calling the external API inline. Drain is triggered by the `build.one.scheduler` Azure Function App (see "Scheduler Architecture" below), which POSTs `/api/v1/admin/outbox/drain` every 30s. The handler claims rows via `ClaimNextPending*Outbox` (UPDLOCK+READPAST) and dispatches through per-Kind handlers. 5 failed attempts → dead-letter. Policy C debounce coalesces rapid edits via `ReadyAfter` column. (In-process APScheduler fallback exists in `shared/scheduler.py` gated on `ENABLE_SCHEDULER=true`; currently disabled in prod.)
- **Write gates**: `ALLOW_QBO_WRITES=true` and `ALLOW_MS_WRITES=true` must be explicitly set (case-insensitive `"true"`) in prod App Service Application Settings. Default-deny — a fresh checkout cannot accidentally push to prod. The gate applies at both the shared HTTP client level AND at `*OutboxService.enqueue()`.
- **Scheduler jobs must be async-wrapped.** Only relevant if `ENABLE_SCHEDULER=true` (fallback mode). `shared/scheduler.py` registers every job as `async def wrapper()` that awaits `asyncio.to_thread(sync_fn)`. Blocking I/O (httpx, pyodbc) runs on the default thread pool; the FastAPI event loop stays free. Synchronous jobs registered on `AsyncIOScheduler` would block ALL user requests during the tick — do not regress this.
- **Token storage encrypted at rest**: `QboAuthRepository` and `MsAuthRepository` both wrap access/refresh tokens with `shared.encryption.encrypt_sensitive_data()` on write and `decrypt_if_encrypted()` on read. Requires `ENCRYPTION_KEY` env var in prod; self-heals plaintext rows on next write (no migration required).
- **Dead-letter recovery**: `scripts/retry_ms_outbox_dead_letters.py` (and future QBO equivalent) resets rows back to `pending`. Dry-run by default; `--apply` to mutate; `--kind` to filter.
- **Reconciliation**: `[qbo].[ReconciliationIssue]` / `[ms].[ReconciliationIssue]` capture drift + dead-letter escalations. Excel-kind MS dead-letters escalate to **critical** severity so they aren't silent. Daily reconciliation jobs detect "locally complete but not in external system" drift.
- **Completion pipelines enqueue, never call external APIs inline.** `BillService.complete_bill` / `ExpenseService.complete_expense` finalize the entity locally, then enqueue outbox rows for SharePoint upload, Excel sync, QBO push. The API returns 202 immediately; side effects drain asynchronously within ~5-30s.
- **Runbook discipline**: every external failure mode documented in `docs/runbooks/*.md` with Symptom / Severity / Diagnosis / Recovery / Verification / Prevention sections. Follow the existing runbook format when adding new ones.

## Scheduler Architecture (April 2026)

Recurring jobs (outbox drain, QBO pulls, daily reconciles) live in the sibling `build.one.scheduler` repo — an Azure Function App (Consumption, Python 3.11) that POSTs to admin endpoints on the API.

- **Why separate**: in-process APScheduler on `-w 1/2` stole threadpool slots from user requests. Moving it out eliminates contention and lets the API run at `-w 2` comfortably.
- **Admin endpoints** (see `shared/api/admin.py`): all require `X-Drain-Secret` header.
  - `POST /api/v1/admin/outbox/drain` — drains QBO + MS outboxes (30s cadence)
  - `POST /api/v1/admin/sync/qbo/{entity}` — one of bill/invoice/purchase/vendorcredit/vendor/customer/item/account/term/company_info
  - `POST /api/v1/admin/reconcile/qbo` — daily
  - `POST /api/v1/admin/reconcile/ms` — daily
- **Secret**: `DRAIN_SECRET` env var on both App Service + Function App. Never commit. Rotate if leaked.
- **Rollback to in-process scheduler**: set `ENABLE_SCHEDULER=true` on App Service + restart. Safe — `sp_getapplock` prevents Function + in-process from double-draining.
- **Fail-closed auth**: admin endpoints return 503 when `DRAIN_SECRET` is unset on the server, 401 on mismatch.

## App Service Config — Required Production Settings

- **Always On = true**: critical. Without it, the container gets unloaded after ~20 min of inactivity and cold starts take 10–15 seconds. Enable via `az webapp config set --name <app> --resource-group <rg> --always-on true`.
- **`-w 2`**: configured in `startup.sh`. Two gunicorn/uvicorn workers on B2. Fits in memory now that APScheduler is out of the web process. `-w 4` is feasible but unnecessary at current load.
- **Env vars** that must exist on App Service: `ENABLE_SCHEDULER=false` (in-process scheduler disabled), `DRAIN_SECRET` (matches Function App), `ALLOW_QBO_WRITES=true`, `ALLOW_MS_WRITES=true`, `ENCRYPTION_KEY`, plus DB creds.
