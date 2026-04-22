# Session Notes

## Session: MS Integration Hardening — Phases 1-3 + 5 (April 22, 2026)

### Overview
Five-phase hardening of `integrations/ms/` (SharePoint, Mail, Excel, Auth) mirroring the QBO hardening pattern. Phases 1-3 + 5 complete and validated in prod via bill completions 5412 + 5403 (Ideal Millwork & Hardware). Phase 4 (email send) deferred pending a future larger inbox rebuild.

### Phase 1 — Baseline fixes
- **Token encryption at rest**: `MsAuthRepository` encrypts access/refresh tokens via Fernet (`shared/encryption.py`); `decrypt_if_encrypted` self-heals legacy plaintext on next write — no migration sproc needed.
- **Concurrency locks via `sp_getapplock`**: token refresh keyed on `ms_auth_refresh:<tenant_id>` (re-read inside lock to absorb concurrent winners); Excel writes keyed on `ms_excel_write:<drive_item_id>` around `append_excel_rows` and `insert_excel_rows` (the read-then-write race points).
- **Typed error hierarchy** in `integrations/ms/base/errors.py`: `MsGraphError` → transport/timeout/rate-limit/server (→ service-unavailable) / client → auth/not-found/validation/conflict/write-refused/unexpected. Each carries http_status, Graph error code, `is_retryable`, request path/method, correlation_id.
- **Shared `MsGraphClient`** (`integrations/ms/base/client.py`) — auth injection, retry+backoff+jitter with `Retry-After` parsing (both seconds and HTTP-date forms), `x-ms-client-request-id` idempotency header for writes, tiered timeouts (A=5/30 for metadata, B=5/60 for workbook ops, C=5/120 for uploads), 401-refresh-retry-once, write gate via `ALLOW_MS_WRITES=true` env (default-deny).
- **Full rewrite of entity external clients** (Option X — contract-preserved): `sharepoint/external/client.py` (1821 → ~700 lines, 25 functions), `mail/external/client.py` (1801 → ~650 lines, 23 functions), plus 2 Graph-bound helpers in `auth/external/client.py`. All 48 functions route through MsGraphClient; downstream callers unchanged (dict-envelope return shape preserved).

### Phase 2 — Observability
- **`MsContextAdapter`** (`integrations/ms/base/logger.py`) — `logging.LoggerAdapter` that auto-injects `correlation_id` from the MS `ContextVar`. Dropped manual `extra={"correlation_id": ...}` from ~16 log call sites across client/retry/locking.
- **Token expiry gauge**: `_emit_token_expiration_check()` emits `ms.auth.token.expiration.check` on every `ensure_valid_token` call (90-day Azure AD baseline via `MS_REFRESH_TOKEN_LIFETIME_DAYS`). Fast path, post-refresh, and concurrent-refresh-winner paths all emit. Wrapped in try/except so instrumentation never breaks auth.
- **First runbook**: `docs/runbooks/ms-token-expiration.md` — Recovery A (full re-OAuth), B (proactive refresh), C (client-secret rotation in Azure AD).
- **Deferred**: App Insights verification + Azure Portal alert rules (user's schedule).

### Phase 3 — Durable outbox + reconciliation
- **`[ms].[Outbox]`** table + 10 sprocs (`integrations/ms/outbox/sql/ms.outbox.sql`). Mirror of `qbo.Outbox` with adjustments: `TenantId` instead of `RealmId`, nullable `Payload NVARCHAR(MAX)` column for upload-session checkpointing.
- **`[ms].[ReconciliationIssue]`** table + 3 sprocs. MS-specific columns: `DriveItemId`, `WorksheetName`, `OutboxPublicId`. Severity lifecycle for flagged issues.
- **`MsOutboxService`** with per-Kind coalescing policy: `upload_sharepoint_file` coalesces (Policy C debounce, 5s); `append_excel_row` / `insert_excel_row` never coalesce (each bill is a distinct row). Convenience methods `enqueue_excel_append`, `enqueue_excel_insert`, `enqueue_sharepoint_upload`.
- **`MsOutboxWorker`** drain loop: cross-process `ms_app_lock` drain guard, per-Kind dispatch table, retry policy (5 attempts → dead-letter) using the shared `compute_backoff_seconds`, typed-error-to-retry mapping. Per-kind handlers exist for upload/append/insert — send_mail reserved for Phase 4.
- **Resumable large-file upload**: chunked upload-session with per-chunk payload checkpoint. On retry, `uploadUrl` + `completed_bytes` are read from Payload and upload resumes from last offset instead of restarting.
- **Dead-letter escalation hook**: Excel kinds (`append_excel_row`, `insert_excel_row`) create **critical** `ms.ReconciliationIssue` with `DriftType='{kind}_dead_letter'`. Other kinds get high severity. User's explicit requirement: failed Excel outbox rows must be followed up, never silent dead-letter.
- **Completion pipeline rewire** — `BillService.complete_bill` and `ExpenseService.complete_expense` enqueue outbox rows instead of inline Graph calls. Touches `_upload_attachments_to_module_folder`, `sync_to_excel_workbook`, `sync_expenses_batch_to_excel`, `_upload_to_general_receipts_folder`. Return shape preserved; messages now say "Queued X row(s)".
- **Excel missing-row reconciliation**: `ExcelMissingRowDetector` in `integrations/ms/reconciliation/business/excel_detector.py`. Daily run, 30-day lookback, flags `DriftType='excel_row_missing'` severity=high. Narrow Phase 3 scope (missing-only); value drift + duplicate detection deferred.
- **APScheduler**: `ms_outbox_drain` (5s interval) + `ms_reconcile_excel` (daily, 4h startup delay). Shares scheduler instance with QBO jobs. Prod-only via `ENABLE_SCHEDULER=true`.
- **Attachment compression**: confirmed to live upstream in `shared/pdf_utils.compact_pdf` at attachment-upload time (aggressive pypdf compaction). Initially added worker-side duplicate; reverted after confirming upstream coverage.

### Phase 4 — DEFERRED
Original scope (email send wiring, conversation threading, attachment policy, sent_message_id persistence) deferred. Rationale: CLAUDE.md notes "No inbox / email-intake surface" — without an inbox, reply/threading items are moot. User wants a **larger encompassing inbox rebuild** from scratch when the time comes, not a piecemeal add-on.

### Phase 5 — Ops polish
- **`shared/scheduler.py`**: every APScheduler job wrapper converted to `async def` + `await asyncio.to_thread(sync_fn)`. Covers `_drain_qbo_outbox`, `_drain_ms_outbox`, `_reconcile_bills`, `_reconcile_excel`, and the `_isolated(...)` wrapper for all QBO pull entities. Blocking drain/pull/reconcile work runs on the default thread pool; FastAPI event loop stays free for concurrent user requests. Fixes the 504 observed during Phase 3 smoke — a 14s drain tick had blocked a concurrent UI PATCH.
- **`scripts/retry_ms_outbox_dead_letters.py`**: dry-run by default; `--apply` resets dead-letter rows back to `pending` (Attempts=0, NextRetryAt=now, LastError=NULL, DeadLetteredAt=NULL). `--kind` filter for targeted resets after a kind-specific outage; `--limit` cap (default 1000).
- **Three new runbooks** under `docs/runbooks/`: `ms-graph-503-storm.md` (cascading 5xx + dead-letter recovery), `ms-excel-conflict-storm.md` (workbook editor conflicts + stuck locks), `ms-permissions-revoked.md` (Azure AD revocation: app permissions / admin consent / conditional access / service account). All linked from `README.md`.

### Validation
- SQL migrations applied cleanly: `python scripts/run_sql.py integrations/ms/outbox/sql/ms.outbox.sql` + `integrations/ms/reconciliation/sql/ms.reconciliation_issue.sql`.
- **Bill 5412** (Ideal Millwork & Hardware, PublicId 6A542FA0): upload_sharepoint_file drain 8s, insert_excel_row drain 17s, QBO push done. PDF in SharePoint, row in Excel, bill in QBO.
- **Bill 5403** (Ideal Millwork & Hardware, PublicId D6B55EA2): upload 14s, Excel insert 19s, QBO push 30s. All verified in App Service logs showing `ms.outbox.row.{enqueued,drained,completed}` event sequence.
- Phase 5 smoke pending post-deploy (scheduler async conversion needs to fire correctly).

### Gotchas learned
- **Drain lock held during Graph call is NORMAL.** 8-30s holds are just Graph call duration, not a leak. Don't kill sessions mid-drain; it interrupts healthy work. Only suspect a leak if held >60s with no drain activity.
- **Azure App Service restart during deploy can leave orphaned SQL sessions.** ODBC Driver 17's connection pooling keeps sessions alive post-process-death; session-scope app locks persist too. Self-resolves after restart completes.
- **AsyncIOScheduler + sync jobs blocks the event loop.** Any blocking I/O (httpx, pyodbc) in a scheduled job stalls FastAPI request handling. Always wrap with `asyncio.to_thread`.
- **Writes through local dev need explicit opt-in.** `ALLOW_MS_WRITES=true` must be set in App Service Application Settings; default-deny prevents accidental fresh-checkout pushes to prod SharePoint.
- **Option X (contract preservation) dodged a massive cascade.** Rewriting external clients while keeping the dict-envelope return shape meant zero downstream caller changes. Phase 1 shipped without touching service/connector/workflow code.
- **Inline compression at SharePoint upload time is redundant.** `shared/pdf_utils.compact_pdf` already runs at attachment creation with aggressive settings (`level=9`, `compress_identical_objects`). The initial instinct to add worker-side compression was wrong; reverted.

### Commits
- `76c3979` — Phase 2 + Phase 3 (observability, outbox, reconciliation)
- `38820af` — Phase 5 (ops polish)

### Key files (new)
- `integrations/ms/base/` — client.py, errors.py, locking.py, logger.py, correlation.py, idempotency.py, retry.py
- `integrations/ms/outbox/` — business (model, service, worker), persistence (repo), sql (ms.outbox.sql)
- `integrations/ms/reconciliation/` — business (model, service, excel_detector), persistence (repo), sql (ms.reconciliation_issue.sql)
- `docs/runbooks/ms-token-expiration.md`, `ms-graph-503-storm.md`, `ms-excel-conflict-storm.md`, `ms-permissions-revoked.md`
- `scripts/retry_ms_outbox_dead_letters.py`

### Open items
- Post-deploy Phase 5 smoke: confirm scheduler jobs still fire after the async conversion; confirm long drain ticks no longer 504 concurrent requests.
- Performance investigation flagged by user at session end — starting fresh in the next session.

---

## Session: Intelligence Layer Build + Scout Agent + React Integration (April 20–22, 2026)

### Overview
Built an agentic framework from scratch — no LangChain/LangGraph, provider-agnostic, colocated with entities. Five levels stood up end-to-end: canonical messages + transport, tools + loop, persistence, agent identity + orchestration, SSE HTTP surface. First agent (`scout`) is a read-only Q&A assistant for sub-cost-codes, callable from the React app via a right-side drawer with conversation threading.

### Architecture decisions
- **Package name**: `intelligence/` (broader than `agents/` — agents are one discipline inside it; RAG, extraction, etc. will live here too). Renamed `shared/ai/` → `intelligence/` conceptually (old layer was fully deleted earlier; this is a clean rebuild).
- **Agent-as-user**: every agent has its own user row, Auth credentials, and JWT — tools call internal HTTP endpoints with that bearer token. Goes through `require_module_api()` RBAC exactly like a human request. No direct service calls from the tool layer (`call_sync()` exists as an escape hatch but is unused).
- **Tools colocated with entities**: `entities/{name}/intelligence/tools.py`. Same pattern as `api/`, `business/`, `persistence/`, `sql/`. Adding a new entity to scout's scope = drop a `tools.py` + add an import to `intelligence/agents/scout/__init__.py` + append to `scout.tools` tuple.
- **HTTPX directly, no vendor SDKs**: uniform adapter shape per provider (`intelligence/transport/{anthropic,openai,…}.py`). Anthropic-only for now. SDK types don't leak above the Transport boundary.
- **Credentials via `credentials_key`**: each `Agent` declares a key (e.g. `"scout_agent"`); the auth helper reads `{key}_username` / `{key}_password` off `config.Settings`. Scales to many agents without bespoke config code.
- **`ParentSessionId`** (nullable FK) — reserved for future sub-agent composition. Wired into schema now, unused until a specialist agent lands.
- **`PreviousSessionId`** (nullable FK) — active. Conversation threading: each follow-up message = a new `AgentSession` linked to the prior head via this column. `load_chain_history()` walks the chain and synthesizes canonical Messages (user / assistant / tool_result alternation) as prior context for the new session.
- **Aggregates, not raw events**: `AgentTurn` + `AgentToolCall` rows capture turns' aggregate state, not every `LoopEvent`. Replay for reconnect is coarser (text_delta collapses to one chunk) but the row count stays sane. Raw-event table could be added later if per-delta replay becomes critical.

### What was built, by level

**L1 — Canonical messages + Anthropic HTTPX transport** (`intelligence/messages/`, `intelligence/transport/`)
- `Message` / `ContentBlock` (discriminated union: `Text`, `ToolUse`, `ToolResult`, `Image`, `Document`) + `Source` (base64 / url)
- `OutputBlock` union for ToolResult content (text | image | document); `ToolResult.content` is `str | list[OutputBlock]` to support vision-returning tools later
- `Transport` Protocol, one adapter per provider; `AnthropicTransport` does POST `/v1/messages` with `stream: true`, parses SSE (`event:` / `data:` / blank line) into canonical `TransportEvent`s
- `transport/registry.py` for name → factory lookup

**L2 — Tools + async think/act/observe loop** (`intelligence/tools/`, `intelligence/loop/`)
- `Tool` = frozen dataclass `(name, description, input_schema, handler)`; `ToolContext` carries `agent_id`, `auth_token`, `session_id`, `requesting_user_id`; `ToolResult` has `str | list[OutputBlock]` content
- `tools/registry.py` — register / get / resolve / all / clear
- `tools/schema.py` — pydantic model → JSON schema (Anthropic-compatible)
- `tools/builtins.py` — `now` and `add` for wiring tests
- `loop/runner.py` — async `run(...)` drives turns, relays transport events as `LoopEvent`s, dispatches tool handlers between turns, enforces `BudgetPolicy` (max_turns + max_tokens)
- `loop/events.py` — `LoopEvent` union: TurnStart, TextDelta, ToolCallStart, ToolCallEnd, TurnEnd, Done, Error
- `loop/termination.py` — `BudgetPolicy` + `TerminationReason` literal
- `ToolCallStart` carries `input` (fires on transport's `tool_use_complete`, not `tool_use_start`, so input is always populated)

**L3 — Persistence** (`intelligence/persistence/`)
- `AgentSession`, `AgentTurn`, `AgentToolCall` tables + `vw_*` views + per-entity sprocs (view + MERGE pattern from persistence refactor)
- `session_repo.py` — three repo classes (pydantic models, sync pyodbc, wrapped with `asyncio.to_thread` at call sites)
- `session_runner.py` — wraps `run()` with persistence; creates `AgentSession` at start (`Status='running'`), writes turns/tool_calls as events flow, finalizes to `completed` / `failed`. Pure wrapper — `run()` remains testable without a DB
- `history.py` (April 22) — `load_chain_history(session_id)` walks `PreviousSessionId` chain, builds canonical `list[Message]` for continuation
- Parent / previous session id columns added as additive ALTER statements (idempotent)

**L4 — Scout agent, auth, orchestrator** (`intelligence/auth.py`, `intelligence/agents/`, `intelligence/registry/`, `intelligence/run.py`)
- `auth.py` — `login_agent_with_user_id(credentials_key)` POSTs `/api/v1/mobile/auth/login`, returns `(access_token, auth_user_id)`. `AgentAuthError` for clean failure
- `Agent` frozen dataclass + `registry/agents.py` (name → Agent)
- `intelligence/agents/scout/` — `definition.py` builds and registers scout; `prompt.md` holds the system prompt; `__init__.py` imports entity tool modules so they self-register
- `run.py` — `run_agent(name, user_message, requesting_user_id?, previous_session_id?, …)` orchestrates: registry lookup → login → tool resolution → ToolContext → `run_session`

**L5 — SSE HTTP surface** (`intelligence/api/`)
- `POST /api/v1/agents/{name}/runs` — starts background task, returns `session_public_id`
- `GET /api/v1/agents/runs/{public_id}/events` — SSE stream; live from in-memory channel while running, falls back to DB-synthesized replay for completed sessions (via `replay.py`)
- `POST /api/v1/agents/runs/{public_id}/cancel` — 403 if caller isn't the requesting user
- `POST /api/v1/agents/runs/{public_id}/continue` (April 22) — conversation follow-up; creates new session with `PreviousSessionId` set
- `api/channel.py` — `SessionChannel` pub/sub + module-level registry; 60s grace window after completion for late subscribers; disconnect doesn't kill the run
- `api/background.py` — `asyncio.Task` lifecycle + cancellation plumbing

### Scout's tool set (SubCostCode only today)
- `list_sub_cost_codes` — full catalog (expensive; nudged down in prompt + tool description)
- `search_sub_cost_codes` (April 22) — case-insensitive substring search on name + number, default limit 10. ~50× cheaper than list. Scout picks this naturally for name-based queries
- `read_sub_cost_code_by_public_id` — UUID lookup
- `read_sub_cost_code_by_number` — dotted format (`10.01`, etc.); prompt tells scout to normalize `10-01` or spelled-out forms
- `read_sub_cost_code_by_alias` — via SubCostCodeAlias table

Added two new endpoints: `/get/sub-cost-code/by-number/{number}`, `/get/sub-cost-code/by-alias/{alias}`, `/get/sub-cost-code/search?q=...&limit=...`.

### React integration (`build.one.web`)
- `src/agents/types.ts` — LoopEvent types + accumulated `Turn` / `ToolCall` / `ConversationEntry` for the hook
- `src/agents/sseClient.ts` — fetch + ReadableStream + hand-parsed SSE. Uses `VITE_API_BASE_URL` (matches the rest of the app). `startAgentRun` / `continueAgentRun` / `streamAgentEvents` / `cancelAgentRun`
- `src/agents/useAgentRun.ts` — reducer-style accumulation of entries from event stream; routes to `/runs` or `/runs/{head}/continue` based on conversation head; exposes `start(msg)`, `cancel()`, `reset()`
- `src/agents/ScoutTray.tsx` — right-side drawer (420px, flex sibling of `app-main` → push animation, no overlay). User messages as right-aligned bubbles; agent turns grouped below per user message. Collapsible tool-call chips. Auto-resizing textarea (Enter=send, Shift+Enter=newline). Thinking indicator with three pulsing dots between Send and first event. "New" button in the tray header to reset conversation. Esc closes
- `src/layout/AppLayout.tsx` — holds tray open state; renders `ScoutTray` as flex sibling
- `src/layout/Header.tsx` — Scout toggle button (aria-pressed, highlights when open)
- No sidebar nav entry; no `/scout` route

### Config additions
- `anthropic_api_key` (Optional[str])
- `internal_api_base_url` (default `http://localhost:8000`; MUST be set in prod to the app's own prod URL so agent tools don't self-call localhost)
- `scout_agent_username`, `scout_agent_password` (renamed from earlier `agent_one_*`)

### SQL schema additions
- `intelligence/persistence/sql/dbo.agent_session.sql` — `AgentSession` + view + sprocs; includes `ParentSessionId` + `PreviousSessionId` columns, FKs, indexes
- `intelligence/persistence/sql/dbo.agent_turn.sql` — `AgentTurn` + view + sprocs
- `intelligence/persistence/sql/dbo.agent_tool_call.sql` — `AgentToolCall` + view + sprocs
- All files idempotent (IF NOT EXISTS guards), safe to re-run

### Deployment notes
- Prod Azure App Service needs: `ANTHROPIC_API_KEY`, `SCOUT_AGENT_USERNAME`, `SCOUT_AGENT_PASSWORD`, `INTERNAL_API_BASE_URL` set in Application Settings. We hit this live during the session — App Service restarts automatically on setting change (~30–60s)
- Agent user must exist in prod DB with matching credentials. Sign up via web UI or insert via SQL. We repurposed the existing `agent_one` user
- `ENABLE_SCHEDULER` remains prod-only (see `shared/scheduler.py`) — local dev is default-deny, so QBO sync jobs don't conflict between local and prod during development

### Deferred intentionally
- **Sub-agent composition** (e.g. `SubCostCodeAgent` as a delegated tool). `ParentSessionId` column is wired for when this lands. Design preference: extract a specialist when a tool set approaches ~8 tools or when writes with complex validation appear
- **Other entities for scout**: Vendor, Bill, Project, Invoice, etc. Pattern is proven on SubCostCode; expansion is mechanical
- **Other transport providers**: OpenAI, Azure, Bedrock — adapter pattern ready; `transport/registry.py` one-entry today
- **`Last-Event-ID` resumption** on SSE — reconnect currently replays everything
- **Prompt caching** (Anthropic `cache_control`) — would cut input tokens further on multi-turn conversations
- **Context assembler module** (`intelligence/context/`) — loop handles context naturally for scout's scope; defer until truncation/summarization/RAG creates a real need
- **Observability layer** (`intelligence/observability/`) — token counting lives inside `policy/budget.py`; structured tracing defer until needed
- **Conversation list UI** — a "past conversations" sidebar in the tray that loads prior threads from the `PreviousSessionId` chain

---

## Session: Persistence Layer Review & Fix — Full 8-Tier Audit (April 12, 2026)

### Overview
Systematic review of all 45 active repositories and ~90 SQL files across the entire entity persistence layer. Reviewed in 8 tiers (Reference Data → Core/Standalone → Join Tables → Attachments → Financial Parents → Financial Children → Inbox/Email → Specialized). Identified 88 issues and implemented fixes for all priorities except P4-D (tenant_id removal, deferred due to ~50+ file scope).

### Findings Summary

| Priority | Description | Count | Status |
|----------|-------------|-------|--------|
| P1 | Data corruption / runtime failures | 6 | All fixed |
| P2 | Silent data loss / missing guards | 3 | All fixed |
| P3 | Schema integrity (FKs, UNIQUE, indexes) | 3 | All fixed |
| P4 | Consistency & cleanup | 7 | 6 fixed, 1 deferred |

### P1 Fixes — Critical

**P1-A: `float()` → `Decimal(str())` on financial fields (27 locations, 11 files)**
- Replaced all `float()` conversions on Decimal financial fields in `create()` and `update_by_id()` across: Bill, Expense, Invoice, BillCredit, ContractLabor, BillLineItem, ExpenseLineItem, BillCreditLineItem, ContractLaborLineItem repos, plus EmailThread and EmailThreadMessage `classification_confidence`
- InvoiceLineItem was already correct — used as the pattern template

**P1-B: Expense `IsCredit` proc gap (9 procs updated)**
- `dbo.expense.sql` — added `@IsCredit` to CreateExpense, UpdateExpenseById (with CASE WHEN guard), all SELECT procs, DeleteExpenseById OUTPUT, ReadExpensesPaginated, CountExpenses
- Migration file `add_is_credit_column.sql` already had correct procs; main SQL file was stale

**P1-C: Bill `set_completion_result()` missing `@ExpiresAt`**
- `UpsertBillCompletionResult` — made `@ExpiresAt` optional with default `DATEADD(HOUR, 1, SYSUTCDATETIME())`

**P1-D: AddressType proc name mismatch**
- `repo.py` called `ReadAddressTypeName`, SQL defined `ReadAddressTypeByName` — fixed repo

**P1-E: Organization RowVersion ALTER**
- Removed `ALTER TABLE [dbo].[Organization] ALTER COLUMN [RowVersion] BINARY(8) NOT NULL` from `dbo.organization.sql`
- If already executed against live DB, column needs manual restoration

**P1-F: EmailThreadMessage missing RowVersion base64 encoding**
- Added `base64` import and encoding in `message_repo.py` `_from_db()`
- Left StageHistory as-is (append-only, never needs concurrency control)

### P2 Fixes — Data Loss Prevention

**P2-A: CASE WHEN guards on nullable FK UPDATE columns (5 SQL files, 10 columns)**
- Vendor: `VendorTypeId`, `TaxpayerId`
- Project: `CustomerId`
- BillLineItem: `SubCostCodeId`, `ProjectId`
- ExpenseLineItem: `SubCostCodeId`, `ProjectId`
- InvoiceLineItem: `BillLineItemId`, `ExpenseLineItemId`, `BillCreditLineItemId`

**P2-B: Attachment SELECT procs missing extraction/categorization columns**
- Updated 5 procs (ReadAttachments, ReadAttachmentById, ReadAttachmentByPublicId, ReadAttachmentByCategory, ReadAttachmentByHash) to include 10 columns: ExtractionStatus, ExtractedTextBlobUrl, ExtractionError, ExtractedDatetime, AICategory, AICategoryConfidence, AICategoryStatus, AICategoryReasoning, AIExtractedFields, CategorizedDatetime

**P2-C: Stray debug queries removed**
- `dbo.bill_line_item.sql` — removed 2 `SELECT *` with hardcoded IDs
- `dbo.attachment.sql` — removed `SELECT COUNT(Id)`

### P3 Fixes — Schema Integrity

**P3-A: FK constraints added (10 tables, 21 FKs)**
- UserRole → User, Role
- UserModule → User, Module
- UserProject → User, Project
- RoleModule → Role, Module
- VendorAddress → Vendor, Address, AddressType
- BillLineItemAttachment → BillLineItem, Attachment
- InvoiceLineItemAttachment → InvoiceLineItem, Attachment
- BillCreditLineItemAttachment → BillCreditLineItem, Attachment
- TaxpayerAttachment → Taxpayer, Attachment
- SubCostCode → CostCode
- SubCostCodeAlias → SubCostCode

**P3-B: UNIQUE constraints added (6 tables)**
- UserRole (UserId, RoleId), UserModule (UserId, ModuleId), UserProject (UserId, ProjectId), RoleModule (RoleId, ModuleId), BillLineItemAttachment (BillLineItemId), BillCreditLineItemAttachment (BillCreditLineItemId)

**P3-C: PublicId indexes added (12 tables)**
- AddressType, VendorType, PaymentTerm, CostCode, Address, Taxpayer, Organization, Company, Module, Role, User, Customer

### P4 Fixes — Consistency & Cleanup

**P4-A: `read_by_id` type hints `str` → `int` (18 repos, 36 methods)**

**P4-B: TOP 1 added to fetchone-on-non-unique procs (6 procs)**
- UserRole ByUserId/ByRoleId, UserModule ByUserId/ByModuleId, User ByFirstname/ByLastname
- Skipped RoleModule/VendorAddress — procs shared with list-returning methods

**P4-C: Concurrency conflict handling standardized (6 repos)**
- Added raise-on-no-row to: BillCredit, BillLineItem, ExpenseLineItem, BillCreditLineItem, ContractLabor, ContractLaborLineItem `update_by_id()`

**P4-D: `tenant_id` removal — DEFERRED**
- Spans ~50+ files across repo/service/API layers (118 API router references). Needs dedicated session.

**P4-E: Raw SQL → stored procedures (10 methods, 10 new procs)**
- BillLineItemAttachment: `ReadBillLineItemAttachmentsByBillLineItemPublicIds`, `CountBillLineItemAttachmentsByAttachmentId`
- InvoiceLineItemAttachment: `ReadInvoiceLineItemAttachmentsByInvoiceLineItemPublicIds`
- BillCreditLineItemAttachment: `ReadBillCreditLineItemAttachmentsByBillCreditLineItemPublicIds`
- Attachment: `ReadAttachmentsByIds`
- InboxRecord: `ReadInboxRecordsBySender`, `ReadInboxRecordsByConversationId`, `ReadInboxRecordsAwaitingReply`
- ContractLabor: `ReadContractLaborsByBillLineItemId`, `UpdateContractLaborStatusAndLink`
- Schema fix: InboxRecord `RecordPublicId` migrated from NVARCHAR(100) to UNIQUEIDENTIFIER

**P4-F: Debug artifacts removed (3 files)**
- Organization: removed `print()`, unused `UUID` import, fixed `_from_db` type hint `dict` → `pyodbc.Row`
- Auth: removed unused `datetime`/`timezone` import

**P4-G: Datetime format standardized (4 SQL files)**
- InboxRecord, InboxRecord.InternetMessageId, InboxRecordStats, ClassificationOverride procs changed from CONVERT style 126 to 120

### Pending SQL Migrations
All schema changes (FK constraints, UNIQUE constraints, indexes, proc updates) need to be executed against the live DB. Run each entity's SQL file via `python scripts/run_sql.py path/to/file.sql`.

### Files Modified (~60+ files)
- 18 repo `.py` files (type hint fixes)
- 11 repo `.py` files (Decimal precision fixes)
- 6 repo `.py` files (concurrency handling)
- 5 repo `.py` files (raw SQL → stored proc)
- 3 repo `.py` files (debug artifact cleanup)
- 2 repo `.py` files (EmailThread RowVersion/import fixes)
- ~25 SQL files (proc updates, schema additions, constraint additions)

---

## Session: Codebase Restructure — Multi-Repo, API Standardization, React Scaffold (April 10-11, 2026)

### Overview
Major restructure separating the monolithic build.one codebase into three independent repos under a parent directory, standardizing the API response format, and scaffolding a React + Vite + TypeScript frontend.

### Phase 1 — Multi-Repo Structure
- Renamed `build.one/` → `build.one.api/`
- Created parent `build.one/` directory
- Moved `build.one.api/` and `build.one.ios/` under it
- Initialized `build.one.web/` with `git init`
- Created GitHub repos: `chriszubrod/build.one.api`, `chriszubrod/build.one.web`
- Updated git remotes, pushed all repos

**Directory layout:**
```
/Users/chris/Applications/build.one/
├── build.one.api/   → github.com/chriszubrod/build.one.api
├── build.one.web/   → github.com/chriszubrod/build.one.web
└── build.one.ios/   → github.com/chriszubrod/build.one.ios
```

### Phase 2 — API Standardization
**New files:**
- `shared/api/__init__.py`
- `shared/api/responses.py` — `list_response()`, `item_response()`, `accepted_response()`, `raise_workflow_error()`, `raise_not_found()`
- `shared/api/lookups.py` — `GET /api/v1/lookups?include=` endpoint (vendors, projects, sub_cost_codes, cost_codes, payment_terms, customers, vendor_types, address_types, roles, modules)

**Modified files (50 entity API routers):**
All routers updated to use standard response envelope:
- List endpoints: `{"data": [...], "count": N}`
- Single entity: `{"data": {...}}`
- Async (202): `{"status": "accepted", ...}`
- Errors: shared `raise_workflow_error()` and `raise_not_found()`

**Breaking change:** Jinja2 templates (81 files making AJAX calls) expect old raw response format and are broken. Accepted — web UI is being replaced by React.

### Phase 3 — React + Vite + TypeScript Scaffold
**New repo:** `build.one.web/`

**Structure:**
```
src/
├── api/client.ts           — Typed fetch, envelope unwrapping, auth token, 401 redirect
├── auth/AuthContext.tsx     — Auth state provider (login, logout, token storage)
├── auth/LoginPage.tsx       — Login form
├── auth/ProtectedRoute.tsx  — Redirect to /login if no token
├── layout/AppLayout.tsx     — Sidebar + header + content area
├── layout/Sidebar.tsx       — Module nav from /api/v1/lookups
├── layout/Header.tsx        — Username + sign out
├── pages/Dashboard.tsx      — Placeholder
├── pages/vendors/VendorList.tsx — Proof-of-concept list page
├── hooks/useLookups.ts      — Reusable dropdown data hook
├── types/api.ts             — TypeScript types for all API shapes
├── App.tsx                  — React Router wiring
├── main.tsx                 — Entry point
└── index.css                — Full app stylesheet
```

**Key decisions:**
- React + Vite + TypeScript (no Next.js — FastAPI is the server)
- Vite dev server proxies `/api` to `localhost:8000`
- API client uses empty base URL (relative paths through proxy)
- Auth via localStorage token + cookie fallback
- Framework recommendation: React over Vue/Svelte for ecosystem size

### Remaining Work (Phase 4 & 5)
- **Phase 4**: Migrate entity pages incrementally from Jinja2 to React (40+ entities)
- **Phase 5**: Retire Jinja2 templates and web controllers from API repo
- **Skipped (deferred)**: Inbox API routes (Step 1), Dashboard API (Step 3)

### Environment Notes
- Node.js installed via `brew install node` (v25.9.0)
- Python 3.9 system → upgraded to Python 3.11 via `brew install python@3.11`
- venv recreated after directory rename (old paths broke activation)
- GitHub push protection required unblocking secrets in commit history

## Session: Codebase Strip and Clean — LangGraph, ML Stack, Push Notifications Removal (April 2-3, 2026)

### What Was Removed

#### Entire directory trees deleted
- `core/ai/` — all 7 LangGraph agents (email, extraction, copilot, vendor, invoice, contract_labor, bill_validation), 3 traditional processors (bill_agent, expense_agent, expense_categorization), LLM wrappers (claude.py, azure.py, ollama.py), email_classifier.py, base agent framework
- `core/notifications/` — push_service.py, apns_service.py, sla_scheduler.py
- `entities/copilot/` — API router, service, model, tools, persistence, SQL
- `entities/device_token/` — model, repo, SQL schema
- `samples/` — langchain_hello_world.py
- `templates/shared/partials/copilot.html` — sidebar chat panel

#### Dependencies removed from requirements.txt and requirements-prod.txt (38 packages)
- **LangGraph/LangChain stack (13):** langchain, langchain-core, langchain-anthropic, langchain-openai, langchain-ollama, langchain-text-splitters, langgraph, langgraph-checkpoint, langgraph-prebuilt, langgraph-sdk, langsmith, ollama, tiktoken
- **ML stack (7 primary):** torch, sentence-transformers, transformers, scikit-learn, scipy, numpy, pandas
- **ML transitive (13):** joblib, safetensors, tokenizers, threadpoolctl, huggingface-hub, hf-xet, fsspec, filelock, sympy, mpmath, networkx, tqdm, patsy
- **Dead deps (5):** deepagents, google-genai, google-auth, langchain-google-genai, statsmodels

#### Dead files deleted (14)
- app.py.bak, sync_feb.log, 2x .DS_Store, 5x .bak/.bak2 in email_agent, 2x .bak in classification_override, templates/admin/overrides.html.bak

#### Template cleanup
- Removed copilot sidebar include from vendor/view, company/view, project/view
- Removed ~340 lines of vendor agent chat UI from vendor/view.html
- Removed copilot toggle button from header partial
- Removed notification bell icon, dropdown HTML, notifications.css link from header
- Removed notifications.js script tag from base.html
- Removed copilot no-op guards from company/view, project/view, bill/list, expense/list, inbox/message
- Deleted static/js/notifications.js and static/css/notifications.css

#### Config and entity cleanup
- Removed APNs config fields and local_embedding_model from config.py
- Removed device token endpoints from auth router (register + deactivate)
- Removed dead get_summary_generator() from core/__init__.py
- Deleted 4 empty admin entity stubs (model, service, repo, SQL)

### What Was Modified

- **app.py** — removed 4 agent router imports/registrations (vendor_agent, bill_agent, expense_agent, expense_categorization), removed all 3 scheduler start/stop blocks, removed shutdown event entirely. Startup event retained with RBAC validation only.
- **entities/inbox/business/service.py** — removed EmailClassifier, classify_email, classify_email_heuristic, extract_from_ocr imports. Extraction pipeline simplified from 3-tier (agent → Claude → heuristic) to 2-tier (Claude single-call → heuristic). `_classify_message()` and `_classify_message_heuristic()` stubbed to return None. `process_category_queue()` classification stubbed to None (scheduler that called it was also removed).
- **shared/ai/embeddings.py** — rewrote: removed LocalEmbeddingService, get_embedding_service() now returns Azure-only with RuntimeError if not configured. Kept compute_similarity (pure Python).
- **shared/ai/__init__.py** — removed LocalEmbeddingService and EmbeddingService exports

### Current State

- **App starts clean** — `import app` and startup event both succeed with zero errors
- **RBAC warnings** at startup are pre-existing (module constants vs DB rows) and informational
- **Estimated install size reduction:** ~3GB+ (torch alone was ~2GB)
- **requirements.txt:** 138 → 99 packages
- **Kept intact:** transitions (workflow orchestrator), anthropic SDK (raw, used by claude_extraction_service), openai SDK (Azure OpenAI client), all QBO/MS/Azure integrations, all entity CRUD, all templates (except copilot/notification cleanup)

### What Is Broken

1. **Inbox email classification** — `_classify_message()` and `_classify_message_heuristic()` return None. The email scheduler that called `process_category_queue()` was removed. Emails in the inbox list will show no classification type. Extraction still works via ClaudeExtractionService (raw Anthropic SDK).
2. **Bill/Expense folder processing** — "Process Folder" buttons on bill/list and expense/list call `/api/v1/bill-agent/run` and `/api/v1/expense-agent/run` which no longer exist (404). Button handlers will catch the error gracefully.
3. **Expense categorization** — `/api/v1/expense-categorization/suggest-batch` endpoint removed.
4. **Vendor agent** — sidebar chat and batch classification removed. Vendors can still be typed manually.
5. **Embeddings** — require Azure OpenAI configuration (`AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY`). No local fallback.

### Architecture Decisions Made

- **Claude Agent SDK only** — no LangGraph, no LangChain. All future AI features will use the Anthropic SDK directly or Claude Agent SDK.
- **API/web split deferred** — web controllers currently import business services directly (same process). A true split requires rewriting 39 web controllers to call the API via HTTP. Deferred until React frontend rebuild.
- **Azure Static Web Apps for React frontend** — future web layer will be a React SPA deployed via Azure Static Web Apps, calling the FastAPI API.
- **Azure embeddings only** — no local sentence-transformers/torch. Production requires Azure OpenAI configuration.

### Next Session Focus

- Rebuild email classification using Claude Agent SDK (replace stubbed `_classify_message` methods)
- Rebuild document extraction pipeline using Claude Agent SDK (complement existing ClaudeExtractionService)
- Consider rebuilding bill/expense folder processing as simple services (no agent framework)

### Files Deleted (summary)
- `core/ai/` (entire tree, ~86 files)
- `core/notifications/` (3 files)
- `entities/copilot/` (22 files)
- `entities/device_token/` (6 files)
- `samples/` (1 file + directory)
- `templates/shared/partials/copilot.html`
- `static/js/notifications.js`, `static/css/notifications.css`
- 14 dead files (.bak, .log, .DS_Store)
- 4 empty admin stubs

### Files Modified (summary)
- `app.py`, `config.py`, `core/__init__.py`
- `entities/inbox/business/service.py`, `entities/auth/api/router.py`
- `shared/ai/embeddings.py`, `shared/ai/__init__.py`
- `requirements.txt`, `requirements-prod.txt`
- 8 templates (base.html, header.html, vendor/view, company/view, project/view, bill/list, expense/list, inbox/message)

---

## Session: Invoice SharePoint Upload, Manual Attachment UI & Module Folder Picker (March 17, 2026)

### What Was Done

#### 1. Manual InvoiceLineItem Attachment Upload UI (`templates/invoice/edit.html`)
- Added a hidden `<input type="file" id="manual-attachment-input">` outside the table (avoids nesting issues).
- Jinja2: for Manual rows with no attachment, renders a paperclip `📎` as an upload trigger link instead of `—`.
- `buildRowHTML` JS: same logic — Manual rows get the upload trigger link.
- `triggerManualAttachmentUpload(lineItemPublicId, event)`: stores the pending public ID, resets the file input, and calls `.click()`.
- File input `change` listener:
  1. `POST /api/v1/upload/attachment` (FormData, `category=invoice_line_item`) → creates Attachment record + Azure blob.
  2. `POST /api/v1/create/invoice-line-item-attachment` (JSON) → links Attachment to InvoiceLineItem.
  3. Updates row's `data-attachment-id`, swaps `📎` → `📄` link to the attachment.

#### 2. Complete Invoice SharePoint Upload (`entities/invoice/business/service.py`)
- Added SharePoint lazy properties: `_driveitem_service`, `_drive_repo`, `_project_module_connector`.
- Added `_upload_to_sharepoint(invoice, line_items)` method:
  1. Resolves "Invoices" module folder via `DriveItemProjectModuleConnector.get_folder_for_module`.
  2. Batch-fetches attachment metadata for all source types (Bill, Expense, BillCredit, Manual) in one DB connection using raw SQL joins.
  3. Uploads each unique attachment with filename `{InvoiceNumber} - {Vendor} - {ParentNumber} - {Description} - {SccNumber} - ${Price} - {Date}`.
  4. Uploads the PDF packet (if exists, `category="invoice_packet"`) as `{InvoiceNumber} - Packet.pdf`.
- `complete_invoice()` now calls `_upload_to_sharepoint()` after `_mark_source_as_billed`, before QBO sync; result included in response dict.

#### 3. Invoices Module Seed (`entities/module/sql/seed.InvoicesModule.sql`)
- New idempotent seed script: `IF NOT EXISTS ... INSERT INTO dbo.[Module] ... ('Invoices', '/invoice/list')`.
- Executed successfully via `python scripts/run_sql.py entities/module/sql/seed.InvoicesModule.sql`.

#### 4. Project View Folder Picker for Bills, Expenses, and Invoices (`templates/project/view.html`)
- Updated module folder loop condition from `{% if module.name == 'Bills' %}` to `{% if module.name in ['Bills', 'Expenses', 'Invoices'] %}`.
- Fixed `linkModuleFolder` JS to read FastAPI's `{"detail": "..."}` error format: `data.detail || data.message || 'Unknown error'` (FastAPI HTTPException serializes to `detail`, not `message`).

#### 5. Allow Same SharePoint Folder for Multiple Modules
- **Problem**: Needed to link the same SharePoint folder to Bills, Expenses, and Invoices modules simultaneously.
- **Fix 1** (`integrations/ms/sharepoint/driveitem/connector/project_module/business/service.py`): Removed Python-level check that prevented a driveitem from being linked to more than one project+module combination.
- **Fix 2** (`scripts/drop_UQ_DriveItemProjectModule_MsDriveItemId.sql`): New migration script to drop `UQ_DriveItemProjectModule_MsDriveItemId` unique constraint from `ms.DriveItemProjectModule`. Executed successfully.
- **Fix 3** (`integrations/ms/sharepoint/driveitem/connector/project_module/sql/ms.driveitem_project_module.sql`): Removed the `UNIQUE ([MsDriveItemId])` constraint from the table DDL.

### Files Modified
- `templates/invoice/edit.html` — manual attachment upload UI (file input, trigger function, change listener)
- `entities/invoice/business/service.py` — SharePoint lazy properties, `_upload_to_sharepoint()`, `complete_invoice()` integration
- `entities/module/sql/seed.InvoicesModule.sql` — new seed script (Invoices module)
- `templates/project/view.html` — module name filter for Bills/Expenses/Invoices, JS error reads `data.detail`
- `integrations/ms/sharepoint/driveitem/connector/project_module/business/service.py` — removed duplicate driveitem check
- `integrations/ms/sharepoint/driveitem/connector/project_module/sql/ms.driveitem_project_module.sql` — removed `UQ_DriveItemProjectModule_MsDriveItemId`
- `scripts/drop_UQ_DriveItemProjectModule_MsDriveItemId.sql` — new migration script (executed)

---

## Session: Budget Tracker Reconciliation — First Principles (March 18–19, 2026)

### Project Reconciliation Health Checks (per project)

These checks are run manually or via script for a given project to verify DB integrity and QBO sync state.

#### Step 1 — Orphaned BillLineItems
**Question**: Does every BillLineItem have a parent Bill?
**Query**: `SELECT bli.* FROM dbo.BillLineItem bli LEFT JOIN dbo.Bill b ON b.Id = bli.BillId WHERE b.Id IS NULL`
**MR2-MAIN (project 93) result**: ✅ 0 orphaned BillLineItems

#### Step 2 — QBO Mapping Coverage (DB → QBO)
**Question**: Does every non-draft BillLineItem have a mapping to a QBO BillLine (`qbo.BillLineItemBillLine`)?
**Query**: Join `dbo.BillLineItem` → `qbo.BillLineItemBillLine` on `BillLineItemId`, filter `IsDraft = 0` and `ProjectId = {id}`, find rows with no mapping.
**MR2-MAIN (project 93) result**: ✅ 0 unmapped non-draft BillLineItems

#### Step 3 — Orphaned QBO BillLines
**Question**: Does every QBO BillLine have a parent QBO Bill?
**Query**: `SELECT bl.* FROM qbo.BillLine bl LEFT JOIN qbo.Bill b ON b.Id = bl.QboBillId WHERE b.Id IS NULL` — filtered to lines mapped to project BillLineItems.
**MR2-MAIN (project 93) result**: ✅ 0 orphaned QBO BillLines

#### Step 4 — QBO Mapping Coverage (QBO → DB)
**Question**: Does every QBO BillLine for this project have a mapping to a DB BillLineItem?
**Query**: Join `qbo.BillLine` → `qbo.BillLineItemBillLine` on `QboBillLineId`, filter by `CustomerRefValue` matching the project's QBO customer, find rows with no mapping.
**MR2-MAIN (project 93) result**: ✅ 0 unmapped QBO BillLines

### Reconciliation Scope Rules
- **Date**: Only items dated 2026-01-01 or later
- **Billed status**: Only items not yet billed — Excel col H ("DRAW REQUEST") must be null; DB `IsBilled = False`
- **Draft status**: DB records must be non-draft (`IsDraft = False`)
- **Direction**: Both — DB is authoritative for what exists, Excel is authoritative for what should exist
- **New records going forward**: DB → Excel push happens automatically when a Bill is marked Complete (no change to current process)

#### Step 5 — Sync DB ↔ QBO if variances found
**Action**: If step 2 or step 4 has variances, run the appropriate sync:
- DB missing QBO mapping → `sync_to_qbo_bill()` to push DB record to QBO, or create `BillLineItemBillLine` mapping manually.
- QBO missing DB mapping → `sync_from_qbo_bill()` to pull QBO record into DB, or create mapping manually.
**MR2-MAIN (project 93) result**: ✅ No action required — steps 2 and 4 were clean.

### Excel Column Map (range always fetched as A1:Z{lastRow}; index 0 = col A)
| 0-based index | Excel col | Field |
|---|---|---|
| 7 | H | DRAW REQUEST (null = not yet billed) |
| 8 | I | Date |
| 9 | J | Vendor Name |
| 10 | K | Bill / Ref Number |
| 11 | L | Description |
| 13 | N | Price (amount) |
| 25 | Z | public_id anchor (col Z) |

Note: `get_excel_used_range_values` now calls `usedRange` only to find the last row, then fetches `A1:Z{lastRow}` explicitly. Append function pads all rows to 26 columns.

#### Step 6 — Build scoped DB set
**Scope**: Non-draft, unbilled BillLineItems for this project dated >= 2026-01-01 (`IsDraft = False`, `IsBilled = False`, `BillDate >= 2026-01-01`).

#### Step 7 — Build scoped Excel set
**Scope**: Excel rows dated >= 2026-01-01 where col H ("DRAW REQUEST") is null.

#### Step 8 — Match Excel → DB
For each scoped Excel row:
- **Col Z present**: verify public_id exists in scoped DB set. If not → orphaned row (flag for manual cleanup).
- **Col Z absent**: attempt match on all five fields: date + vendor (fuzzy) + bill number + description + amount. All five must agree.
  - Unambiguous match → backfill col Z (write mode only).
  - Any field off, or ambiguous (multiple candidates) → flag for manual review. Do not auto-link.

#### Step 9 — Match DB → Excel
For each scoped DB record:
- Public_id found in col Z of a scoped Excel row → verified, no action.
- Public_id not found in any col Z → missing from Excel. Flag it (Bill completion push may have failed or not yet run).

#### Step 10 — Resolve variances
Manual review of all flagged items from steps 8 and 9. No automatic record creation.

---

## Session: Contract Labor Entity Module — Deep Dive, Bug Fixes & Bill Generation (March 16, 2026)

### What Was Done

#### Full Module Review & Two Deep-Dive Bug Fix Passes

Performed a comprehensive review of the Contract Labor entity module: `entities/contract_labor/`, `templates/contract_labor/`, and `entities/contract_labor/business/bill_service.py`. Fixed 13 bugs across two passes.

**Bug 1 — Vendor sort A-Z not working** (`entities/contract_labor/sql/dbo.contract_labor.sql`)
- `ReadContractLaborsPaginated` ordered by `v.[Name] ASC` but all entries had NULL VendorId (assigned during review step), so sort did nothing.
- Fixed: `ISNULL(v.[Name], cl.[EmployeeName]) ASC`.

**Bug 2 — BillLineItemId wiped on every line item save** (sql + repo + router)
- SQL UPDATE sproc didn't have a `@BillLineItemId` parameter — field was silently reset to NULL on each save.
- Repo had the param commented out; router didn't pass the existing value.
- Fixed: added `@BillLineItemId` with CASE WHEN guard to sproc; repo passes it; router reads `existing_item.bill_line_item_id` and passes it through.

**Bug 3 — "Too many arguments" on Save & Mark Ready** (`entities/contract_labor/persistence/line_item_repo.py`)
- Repo was passing `BillLineItemId` before the sproc had the parameter (from an earlier partial fix).
- Fixed: kept in sync — both sproc and repo now include `BillLineItemId`.

**Bug 4 — Dead billing endpoints** (`entities/contract_labor/api/router.py`)
- `GET /billing/summary` and `POST /billing/create-bills` called non-existent service methods.
- Fixed: removed both dead endpoints.

**Bug 5 — Import preview crash on tuple unpack** (`entities/contract_labor/business/import_service.py`)
- `get_import_preview()` assigned `self._parse_row(row, row_num)` to a single variable and called `.get()` on the returned tuple — immediate AttributeError.
- Fixed: `parsed, skip_reason = self._parse_row(...)` throughout.

**Bug 6 — Import preview used hardcoded filename** (`entities/contract_labor/business/import_service.py`)
- `get_import_preview()` called `load_workbook(io.BytesIO(file_content))` ignoring the actual filename, breaking `.csv` detection.
- Fixed: added `filename` parameter; delegates to `_load_excel_rows()`.

**Bug 7 — Variable shadowing corrupts bill total** (`entities/contract_labor/business/bill_service.py`)
- Inner loop declared `total_amount = Decimal("0")` which shadowed the outer bill total. PDF packet received only the last SCC group's subtotal, not the full bill amount.
- Fixed: renamed inner accumulator vars to `scc_amount` / `scc_price`.

**Bug 8 — Non-billable items included in total_amount** (`entities/contract_labor/business/bill_service.py`)
- `total_amount` summed all line items regardless of `IsBillable`.
- Fixed: `sum(... for item in items if item["line_item"].is_billable is not False)`.

**Bug 9 — Non-billable items shown with real amount on PDF** (`entities/contract_labor/business/bill_service.py`)
- PDF used the item's actual `price` for non-billable items instead of $0.00.
- Fixed: `amount = "$0.00" if li.is_billable is False else f"${float(li.price or 0):,.2f}"`.

**Bug 10 — Non-billable SCC groups included in PDF** (`entities/contract_labor/business/bill_service.py`)
- SCC groups where all items are non-billable still generated PDF sections with $0.00 subtotals.
- Fixed: track `any_billable` flag; skip groups where no billable items exist.

**Bug 11 — Zero markup corrupted to NULL on save (JS)** (`templates/contract_labor/edit.html`)
- `markupPercent / 100 || null` evaluates to `null` when `markupPercent = 0`.
- Fixed: `markup: markupPercent / 100` (never use `|| null` for numeric fields).

**Bug 12 — Zero markup not displayed on edit page (Jinja2)** (`templates/contract_labor/edit.html`)
- `value="{{ item.markup * 100 if item.markup else '' }}"` — Jinja2 treats `Decimal('0')` as falsy, showing blank.
- Fixed: `value="{{ (item.markup * 100) if item.markup is not none else '' }}"`.

**Bug 13 — Entries with no project-assigned line items silently skipped** (`entities/contract_labor/business/bill_service.py`)
- If no line items had a `project` assigned, the entry was silently excluded from the bill with no feedback.
- Fixed: added warning to `result["errors"]` for each skipped entry.

#### Features Added

**1. Scroll position restoration** (`templates/contract_labor/list.html`)
- Edit link saves `document.getElementById('content').scrollTop` to `sessionStorage`.
- On `DOMContentLoaded`, restores via double `requestAnimationFrame` (waits for list render).

**2. Auto-populate one line item on edit page** (`templates/contract_labor/edit.html`)
- If no `.cl-line-item` elements exist at page load, calls `addLineItem()` automatically.

#### Operational Work

**Reversed incorrect billing run (2026-01-31 and 2026-02-28)**
- Generate Bills for 2026-02-15 incorrectly marked ALL billing periods as billed.
- Used a Python script to reset 148 entries to their correct status (pending_review or ready), delete 41 draft bills, and clear 85 BillLineItem FK references from ContractLaborLineItems.
- Left 2026-01-15 entries intact (correctly billed).

**Deleted 16 incorrect draft bills (2026-02-15 run)**
- Reset 2026-02-15 entries back to `ready` status after deletion.

**8h/day compliance review (all 6 main subcontractors)**
- Verified Brayan, Emilson, Elmer, Wilmer all total exactly 8.00h per day.
- Denis had 2 days at 7h — corrected by user.
- Selvin has intentional sub-8h days — left as-is by user's decision.

**Marked Selvin's "DO NOT BILL" line item IsBillable=false**
- ContractLaborLineItem ID=206 ("Met with Tanner. DO NOT BILL.") confirmed and verified as `IsBillable=false`.

### Files Modified
- `entities/contract_labor/sql/dbo.contract_labor.sql` — ORDER BY fix, BillLineItemId in all SELECT/UPDATE sprocs
- `entities/contract_labor/persistence/line_item_repo.py` — BillLineItemId in update params
- `entities/contract_labor/api/router.py` — removed dead billing endpoints, pass existing `bill_line_item_id` on update
- `entities/contract_labor/business/bill_service.py` — variable shadowing fix, non-billable total/PDF fixes, all-non-billable group skip, missing-project warning
- `entities/contract_labor/business/import_service.py` — tuple unpack fix, filename parameter for `get_import_preview`
- `templates/contract_labor/list.html` — scroll position save/restore
- `templates/contract_labor/edit.html` — auto-add line item, zero markup Jinja2 + JS fixes

---

## Session: Invoice Entity Module — Deep Dive, Bug Fixes & PDF Packet TOC (March 16, 2026)

### What Was Done

#### Deep Dive & Bug Fix Pass on Invoice Entity Module

Performed a comprehensive review of `/entities/invoice`, `/entities/invoice_line_item`, `/entities/invoice_attachment`, `/entities/invoice_line_item_attachment`, and related templates. Identified and fixed 5 bugs.

**Bug 1 — InvoiceLineItem delete: wrong cascade order** (`entities/invoice_line_item/business/service.py`)
- `delete_by_public_id()` tried to delete the `Attachment` record before the `InvoiceLineItemAttachment` join record, causing FK violation. After the silent catch, the join record delete was skipped, leaving the InvoiceLineItem delete to fail on its own FK.
- Fixed: correct order — read attachment info → delete join record → delete blob (best-effort) → delete Attachment record. Each step in its own try/except.

**Bug 2 — complete_invoice project_id type mismatch** (`entities/invoice/business/service.py`)
- `project_service.read_by_id(id=str(invoice.project_id))` passed a `str` but `ProjectService.read_by_id` expects `int`.
- Fixed: removed `str()` cast.

**Bug 3 — 404 crash on invalid invoice public_id** (`entities/invoice/web/controller.py`)
- Both `view_invoice` and `edit_invoice` called `.to_dict()` on a potentially-None invoice, raising AttributeError instead of 404.
- Fixed: added `if not invoice: raise HTTPException(status_code=404)` before any attribute access.

**Bug 4 — saveInvoice() returned void, Complete ignored save failure** (`templates/invoice/edit.html`)
- The Complete Invoice submit handler had no signal from `saveInvoice()` about whether the save succeeded. If the save failed, Complete would proceed with stale DB state.
- Fixed: `saveInvoice()` now returns `true`/`false`; submit handler checks the return value and bails early on `false`.

**Bug 5 — Falsy 0 display bug for zero-value amounts** (`templates/invoice/edit.html`, `templates/invoice/create.html`)
- `buildRowHTML` and `reAddLineItem` used `||` short-circuit which treated `0` as falsy, showing `null` instead of `$0.00` for zero-value amount/markup/price fields.
- Fixed: replaced with explicit `!== null && !== ''` guards in both templates.

#### Features Added

**1. Line items sort: Type → Vendor ascending**
- Server-side sort in `edit_invoice` after `_enrich_line_items`: `(type_order, vendor_name.lower())` — Bill (0) → BillCredit (1) → Expense (2), then vendor A→Z.
- Client-side `sortLineItemsTable()` uses the same compound key so newly loaded items (via "Load Billable Items") stay in sync with server order.

**2. PDF Packet pre-flight missing attachment warning**
- Added `getIncludedRowsMissingPDF()` in `edit.html` that scans included rows for items with a source record (`data-parent-public-id`) but no attachment (`data-attachment-id` empty).
- If any found, `generatePacket()` shows a `confirm()` dialog listing each item (type, ref number, vendor) before proceeding. Manual line items are excluded from the warning.

**3. PDF Packet TOC pages** (`entities/invoice/api/router.py`)
- Two Table of Contents pages are now prepended to every generated PDF packet, before the attachment images.
- **Basic TOC**: Ordered Bill → Credit → Expense, then vendor A→Z. Columns: Date, Vendor, Invoice, Description, Type, Amount. Grand total row.
- **Expanded TOC**: Ordered by CostCode number (numeric ascending), then type, then vendor. Columns: Cost Code, Date, Vendor, Invoice, Description, Type, Amount. Subtotal row per CostCode group + grand total.
- Styled with `reportlab` (Helvetica font, dark navy blue `#1F3864` headers) to match provided sample PDFs.
- "Type" column shows "Bill", "Credit", or "Expense" derived from `source_type` — no new schema field needed.
- TOC includes ALL invoice line items (including those without attachments); the merged pages that follow only include items with PDFs.

**4. CostCode enrichment in `_enrich_line_items()`** (`entities/invoice/web/controller.py`)
- All three source queries (bill, expense, credit) now join `dbo.CostCode` via `SubCostCode.CostCodeId`.
- Returns `cost_code_number` and `cost_code_name` (parent CostCode) alongside existing `sub_cost_code_number/name`.
- Used by the expanded TOC to group by CostCode rather than SubCostCode.

### Files Modified
- `entities/invoice/web/controller.py` — HTTPException import, 404 guards in view/edit, type+vendor sort in edit_invoice, CostCode join in all three enrichment queries, `cost_code_number/name` in result maps and defaults
- `entities/invoice/business/service.py` — removed `str()` cast on `project_id` in `complete_invoice`
- `entities/invoice/api/router.py` — `_toc_source_label()`, `_build_toc_basic_pdf()`, `_build_toc_expanded_pdf()` helper functions; TOC generation + prepend in `generate_invoice_packet_router`; expanded sort key uses `cost_code_number`
- `entities/invoice_line_item/business/service.py` — delete cascade order fix (join record → blob → Attachment), each step in own try/except
- `templates/invoice/edit.html` — `saveInvoice()` bool return, Complete guard on save failure, falsy 0 fixes in `buildRowHTML`/`reAddLineItem`, `getIncludedRowsMissingPDF()` pre-flight check in `generatePacket()`, `sortLineItemsTable()` compound sort key
- `templates/invoice/create.html` — falsy 0 fixes in `buildRowHTML`/`reAddLineItem`

---

## Session: Expense Entity Module — Bug Fixes & Scheduler Cleanup (March 13, 2026)

### What Was Done

#### Deep Dive & 9-Bug Fix Pass on Expense Entity Module

Performed a comprehensive review of `/entities/expense`, `/entities/expense_line_item`, `/entities/expense_line_item_attachment`, and `/templates/expense`. Identified and fixed 9 bugs.

**Bug 1 — Auto-save race on Complete Expense** (`templates/expense/edit.html`)
- `handleCompleteExpense()` was canceling the debounced auto-save timer instead of flushing it
- Fixed: await `autoSaveExpense()` before sending the complete request (mirrors Bill fix)

**Bug 2 — Delete without auto-save guard** (`templates/expense/edit.html`)
- `deleteExpense()` did not set `isSaving = true` before canceling the timer, allowing a pending auto-save to fire after delete
- Fixed: set `isSaving = true` at the top of `deleteExpense()`

**Bug 3 — Float precision loss on Decimal fields** (`entities/expense/api/router.py`, `entities/expense_line_item/api/router.py`)
- `float(body.total_amount)` and similar conversions introduced floating-point rounding errors on financial values
- Fixed: replaced all `float(...)` with `Decimal(str(...)) if value is not None else None`

**Bug 4 — Float precision in complete_expense()** (`entities/expense/business/service.py`)
- `complete_expense()` passed `float(expense.total_amount)` to internal services
- Fixed: same `Decimal(str(...))` pattern applied throughout

**Bug 5 — Wrong module fallback in _upload_attachments_to_module_folder** (`entities/expense/business/service.py`)
- Fell back to "Bills" module if "Expenses"/"Expense" not found, uploading expense files into the Bills SharePoint folder
- Also had a last-resort `read_all()[0]` fallback which could silently upload to any random module
- Fixed: return `{"success": False, "message": "Expense module not found..."}` if neither "Expenses" nor "Expense" found

**Bug 6 — Success flag ignored synced_count** (`entities/expense/business/service.py`)
- `_upload_attachments_to_module_folder` and `_sync_to_excel_workbook` returned `"success": synced_count > 0 or not errors` — zero files with no errors returned success=False
- Fixed: changed to `"success": not errors`

**Bug 7 — Expense 404 crash in web controller** (`entities/expense/web/controller.py`)
- `view_expense` called `expense.to_dict()` without null-checking, crashing with AttributeError for missing expenses
- Fixed: added `if not expense: raise HTTPException(status_code=404)`

**Bug 8 — Missing cascade delete on ExpenseLineItem** (`entities/expense_line_item/business/service.py`)
- `delete_by_public_id()` deleted the ExpenseLineItem directly, leaving orphaned ExpenseLineItemAttachment, Attachment records, and Azure blobs
- Fixed: cascade delete order — blob → Attachment record → ExpenseLineItemAttachment link → ExpenseLineItem

**Bug 9 — Raw SQL in ExpenseLineItemAttachment repo** (`entities/expense_line_item_attachment/persistence/repo.py`, `sql/dbo.expense_line_item_attachment.sql`)
- `read_by_expense_line_item_public_ids()` built a raw SQL query with an IN clause instead of using a stored procedure
- Fixed: replaced with `call_procedure("ReadExpenseLineItemAttachmentsByExpenseLineItemPublicIds", ...)` using STRING_SPLIT
- Also added FK constraints, UNIQUE constraint, and indexes to the SQL table definition (with idempotent migration blocks)

#### Removed Expense Processing from BillAgent Scheduler

- Identified that `core/ai/agents/bill_agent/scheduler.py` was running both `run_bill_folder_processing` and `run_expense_folder_processing` every 30 minutes
- Removed the `# --- Expense processing ---` block (lines 37–56) at user's request
- Updated docstring and logger message to no longer reference ExpenseAgent

### Files Modified
- `entities/expense/business/service.py` — Decimal precision fix, module fallback fix, success flag fix
- `entities/expense/api/router.py` — Decimal precision fix in update payload
- `entities/expense/web/controller.py` — 404 guard in view_expense
- `entities/expense_line_item/business/service.py` — cascade delete (blob → attachment → link → line item)
- `entities/expense_line_item/api/router.py` — Decimal precision fix in create/update payloads
- `entities/expense_line_item_attachment/persistence/repo.py` — replaced raw SQL with stored procedure call
- `entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql` — FK constraints, UNIQUE constraint, indexes, new `ReadExpenseLineItemAttachmentsByExpenseLineItemPublicIds` sproc
- `templates/expense/edit.html` — auto-save flush on complete, isSaving guard on delete
- `core/ai/agents/bill_agent/scheduler.py` — removed expense processing block

### Pending
- Run SQL migration: `python scripts/run_sql.py entities/expense_line_item_attachment/sql/dbo.expense_line_item_attachment.sql`

---

## Session: BillLineItem Delete & QBO Sync Deduplication Fix (March 11, 2026)

### What Was Fixed

#### 1. BillLineItem Delete FK Violation
- **Bug**: Deleting a BillLineItem failed with `FK_InvoiceLineItem_BillLineItem` constraint violation when an InvoiceLineItem referenced it
- **Root cause**: `BillLineItemService.delete_by_public_id()` didn't handle the InvoiceLineItem FK dependency, and the FK has no CASCADE DELETE
- **Fix**: Added `NullifyInvoiceLineItemsByBillLineItemId` stored procedure that sets `BillLineItemId = NULL` on referencing InvoiceLineItem rows. Called from `BillLineItemService.delete_by_public_id()` before deleting the BillLineItem. This preserves the InvoiceLineItem records (description, amount, etc.) while breaking the FK link.

#### 2. QBO Sync Line Item Deduplication
- **Bug**: Bill with `public_id=4AE71E1F-A92F-4DF8-A5F2-C6CD24D9DAC8` had two BillLineItems — one with an attachment (original), one linked to QBO (duplicate created by sync)
- **Root cause**: `sync_to_qbo_bill()` stored QboBillLine records locally but never created `BillLineItemBillLine` mappings. When a subsequent `sync_from_qbo` ran, QBO lines appeared unmapped, so `BillLineItemConnector.sync_from_qbo_bill_line()` created duplicate BillLineItems.
- **Fix**: After storing QboBillLines in `sync_to_qbo_bill()`, now creates `BillLineItemBillLine` mappings by matching `line_num` between the request lines and QBO API response lines. Also changed `_store_qbo_bill_line()` to return the created record (was void) so its ID can be used for the mapping.

### Files Modified
- `entities/bill_line_item/business/service.py` — nullify InvoiceLineItem FKs before delete
- `entities/invoice_line_item/sql/dbo.invoice_line_item.sql` — new `NullifyInvoiceLineItemsByBillLineItemId` stored procedure
- `entities/invoice_line_item/persistence/repo.py` — new `nullify_bill_line_item_id()` method
- `integrations/intuit/qbo/bill/connector/bill/business/service.py` — `line_num_to_line_item_id` tracking, line item mapping creation in `sync_to_qbo_bill()`, `_store_qbo_bill_line()` returns created record

---

## Session: Contact Entity Module (March 11, 2026)

### What Was Built

**Contact** — A polymorphic child entity for storing contact details (email, phone, fax, etc.) linked to User, Company, Customer, Project, and Vendor entities via nullable FK columns. Each parent can have multiple contacts. Managed inline on parent pages using reusable Jinja2 partials.

#### Contact Entity (Full CRUD)
- `dbo.Contact` table with nullable FKs: UserId, CompanyId, CustomerId, ProjectId, VendorId
- Fields: Email (NVARCHAR 255), OfficePhone (NVARCHAR 50), MobilePhone (NVARCHAR 50), Fax (NVARCHAR 50), Notes (NVARCHAR MAX)
- 11 stored procedures: Create, ReadAll, ReadById, ReadByPublicId, ReadByUserId/CompanyId/CustomerId/ProjectId/VendorId, UpdateById, DeleteById
- Full entity module: model, repository, service, API schemas, API router (ProcessEngine instant)

#### Inline UI on Parent Pages
- **Reusable partials**: `shared/partials/contacts_view.html` (read-only table) and `shared/partials/contacts_edit.html` (inline CRUD with JS)
- **Edit partial**: Add Contact form, per-row inline editing (onchange updates via API), delete per row with confirmation
- **View partial**: Read-only table showing all contacts
- Wired into all 5 parent entities (User, Company, Customer, Project, Vendor) — both view and edit pages
- CSS: `static/css/contact.css`

#### Workflow Registration
- Added `"contact"` to `SYNCHRONOUS_TASKS` in `core/workflow/business/definitions/instant.py`
- Added `"contact"` to `PROCESS_REGISTRY` in `core/workflow/business/instant.py`
- Registered API router in `app.py`

### Files Created
- `entities/contact/sql/dbo.contact.sql`
- `entities/contact/business/model.py`
- `entities/contact/persistence/repo.py`
- `entities/contact/business/service.py`
- `entities/contact/api/schemas.py`
- `entities/contact/api/router.py`
- `entities/contact/__init__.py`, `api/__init__.py`, `business/__init__.py`, `persistence/__init__.py`
- `templates/shared/partials/contacts_view.html`
- `templates/shared/partials/contacts_edit.html`
- `static/css/contact.css`

### Files Modified
- `app.py` — imported and registered contact API router
- `core/workflow/business/definitions/instant.py` — added "contact" to SYNCHRONOUS_TASKS
- `core/workflow/business/instant.py` — added ContactService to PROCESS_REGISTRY
- `entities/user/web/controller.py` — ContactService import, fetch contacts in view/edit
- `entities/company/web/controller.py` — ContactService import, fetch contacts in view/edit
- `entities/customer/web/controller.py` — ContactService import, fetch contacts in view/edit
- `entities/project/web/controller.py` — ContactService import, fetch contacts in view/edit
- `entities/vendor/web/controller.py` — ContactService import, fetch contacts in view/edit
- `templates/user/view.html`, `edit.html` — contact.css + partial includes
- `templates/company/view.html`, `edit.html` — contact.css + partial includes
- `templates/customer/view.html`, `edit.html` — contact.css + partial includes
- `templates/project/view.html`, `edit.html` — contact.css + partial includes
- `templates/vendor/view.html`, `edit.html` — contact.css + partial includes

### Design Decisions
- **Nullable FK columns** (not join table or generic FK) — simplest approach, consistent with codebase patterns
- **No firstname/lastname/title** — Contact stores only communication details, not identity info
- **Inline UI via reusable partials** — same pattern as UserRole on User pages, but using `{% include %}` partials for DRY across 5 parent entities
- **Instant workflow** — uses ProcessEngine.execute_synchronous for audit trail, same as UserRole

---

## Session: RBAC Wiring — Role into User, UserRole, RoleModule (March 11, 2026)

### What Was Built

Wired the Role entity into the UserRole and RoleModule join table UIs, and added inline role assignment to the User entity pages.

#### UserRole & RoleModule — Dropdown + Name Resolution
- **Controllers** (`entities/user_role/web/controller.py`, `entities/role_module/web/controller.py`):
  - Import and load related services (UserService, RoleService, ModuleService)
  - Create/edit routes pass entity lists for dropdown population
  - List/view routes pass lookup maps (`user_map`, `role_map`, `module_map`) for UUID-to-name resolution
  - Added missing `current_path` to all template contexts
  - Fixed template directory from `templates/user_role` to `templates` with prefixed paths
- **Templates** (8 files across `templates/user_role/` and `templates/role_module/`):
  - Dropdowns now use `public_id` for values (was `id` — BIGINT vs UNIQUEIDENTIFIER mismatch)
  - List/view pages show human-readable names instead of raw UUIDs
  - Fixed broken navigation links (`/user_roles/list` → `/user_role/list`, `/role_modules/list` → `/role_module/list`)

#### User Entity — Inline Role Assignment
- **Controller** (`entities/user/web/controller.py`):
  - Imports RoleService and UserRoleService
  - `create_user` passes `roles` list for dropdown
  - `view_user` resolves current role name via UserRoleService → RoleService
  - `edit_user` passes `roles` list + current `user_role` (if any)
- **Templates**:
  - `templates/user/create.html` — Role dropdown (optional). After user creation, creates UserRole via API if role selected
  - `templates/user/edit.html` — Role dropdown pre-selected with current role. Handles three cases on save: create (new assignment), update (role changed), delete (role cleared)
  - `templates/user/view.html` — Displays resolved role name (or "No role assigned")

### Files Modified
- `entities/user/web/controller.py` — RoleService/UserRoleService imports, role data in create/view/edit contexts
- `entities/user_role/web/controller.py` — UserService/RoleService imports, lookup maps, template fixes
- `entities/role_module/web/controller.py` — RoleService/ModuleService imports, lookup maps, template fixes
- `templates/user/create.html` — role dropdown + JS role assignment after create
- `templates/user/edit.html` — role dropdown + JS create/update/delete role assignment
- `templates/user/view.html` — role name display
- `templates/user_role/list.html` — name resolution via maps
- `templates/user_role/view.html` — name resolution, fixed links
- `templates/user_role/create.html` — public_id for dropdown values
- `templates/user_role/edit.html` — public_id for dropdown values + selected comparison
- `templates/role_module/list.html` — name resolution via maps
- `templates/role_module/view.html` — name resolution, fixed links
- `templates/role_module/create.html` — public_id for dropdown values
- `templates/role_module/edit.html` — public_id for dropdown values + selected comparison

### Bug Fixes
- **Dropdown value mismatch**: Templates used `id` (BIGINT) for dropdown values but join tables store `public_id` (UNIQUEIDENTIFIER) — selected state and submitted values never matched
- **Missing `current_path`**: All UserRole and RoleModule template contexts were missing `current_path: request.url.path` (required by sidebar)
- **Broken nav links**: View templates had plural routes (`/user_roles/list`, `/role_modules/list`) that don't exist

### Remaining Work
- **Authorization middleware**: Build middleware/dependency that checks current user's role(s) via UserRole → Role → RoleModule chain to gate access to modules
- **Sidebar integration**: Register Role in the Modules table for sidebar navigation
- **Default role seeding**: Create initial roles (e.g., Admin, Project Manager, Viewer)

---

## Session: Role Entity Module (March 11, 2026)

### What Was Built

**Role** — A standalone RBAC entity completing the authorization chain: User → UserRole → **Role** → RoleModule → Module. Both UserRole and RoleModule already existed and referenced `role_id`, but the Role entity itself was missing.

#### Role Entity (Full CRUD)
- `dbo.Role` table with `Name` (NVARCHAR(255)) field + standard fields (Id, PublicId, RowVersion, timestamps)
- 7 stored procedures: Create, ReadAll, ReadById, ReadByPublicId, ReadByName, UpdateById, DeleteById
- Full entity module: model, repository, service, API router (5 endpoints via ProcessEngine), web controller (4 routes)
- Templates: list (card grid), create, view, edit — all following User entity pattern
- CSS: `static/css/role.css`

#### Workflow Registration
- Added `"role"` to `SYNCHRONOUS_TASKS` in `core/workflow/business/definitions/instant.py`
- Added `"role"` to `PROCESS_REGISTRY` in `core/workflow/business/instant.py`
- Registered routers in `app.py`

### Files Created
- `entities/role/sql/dbo.role.sql`
- `entities/role/business/model.py`
- `entities/role/persistence/repo.py`
- `entities/role/business/service.py`
- `entities/role/api/schemas.py`
- `entities/role/api/router.py`
- `entities/role/web/controller.py`
- `templates/role/list.html`, `create.html`, `view.html`, `edit.html`
- `static/css/role.css`

### Files Modified
- `core/workflow/business/definitions/instant.py` — added "role" to SYNCHRONOUS_TASKS
- `core/workflow/business/instant.py` — added "role" to PROCESS_REGISTRY
- `app.py` — imported and registered role API + web routers

### Remaining Work
- ~~**Wire Role into UserRole/RoleModule**~~ — DONE (March 11, 2026 session above)
- **Authorization middleware**: Build middleware/dependency that checks current user's role(s) via UserRole → Role → RoleModule chain to gate access to modules
- **Sidebar integration**: Register Role in the Modules table for sidebar navigation
- **Role seeding**: Create default roles (e.g., Admin, Project Manager, Viewer)

---

## Session: Bill Entity — Email Display, Delete Fix, QBO Sync Fix (March 11, 2026)

### What Was Built

#### 1. Inline Source Email Display on Bill Edit/View Pages
- Added AJAX endpoint `GET /inbox/message/{message_id}/detail` on inbox controller — returns full email details as JSON
- Bill edit and view templates now show a "Show Source Email" toggle button that loads the linked email inline (lazy-loaded on first click)
- "Open in Outlook" link populated from `email.web_link` after AJAX fetch
- Source email lookup added to `view_bill` controller (was already in `edit_bill`)

#### 2. Bill Delete Cascade Fix
- **Bug**: Deleting a draft bill from `/bill/edit` failed with "BillLineItemService is not defined"
- **Root cause 1**: `delete_by_public_id()` used bare class names (`BillLineItemService()`) instead of `self.bill_line_item_service` — the classes are lazy-imported in `__init__`, not at module level
- **Root cause 2**: Attachment cleanup exceptions could skip line item deletion due to shared try-except block
- **Fix**: Changed to `self.*` instance references; separated attachment cleanup and line item delete into independent try-except blocks
- Added `isSaving = true` guard in `deleteBill()` JS to prevent auto-save racing during delete

#### 3. QBO Sync — Missing SubCostCode Fix
- **Bug**: "QBO sync skipped: Bill has 1 line item(s) but none have QBO Item mappings" after completing a bill where SubCostCode was visibly selected
- **Root cause 1**: Copilot agent's `create_bill_from_extraction()` was not passing `sub_cost_code_id` when creating line items
- **Root cause 2**: `handleCompleteBill()` was canceling pending auto-saves instead of flushing them — if user selected SubCostCode and immediately clicked Complete, the 300ms debounced save was lost
- **Fix**: Added `sub_cost_code_id` to copilot tool's line item creation; changed Complete Bill to `await` pending auto-saves before sending the complete request

#### 4. Complete Bill Validation
- Added client-side validation in `validateBillForm()` that all saved line items have a Sub Cost Code selected before allowing Complete Bill

### Files Modified
- `entities/bill/web/controller.py` — source_email lookup in view_bill
- `entities/bill/business/service.py` — delete cascade fix (self.* references, separated try-except)
- `entities/inbox/web/controller.py` — new `/message/{message_id}/detail` JSON endpoint
- `templates/bill/edit.html` — inline email section, delete guard, auto-save flush on complete, SubCostCode validation
- `templates/bill/view.html` — inline email section, toggle button, Outlook link
- `core/ai/agents/copilot_agent/graph/tools.py` — added sub_cost_code_id to create_bill_from_extraction

---

## Session: SubCostCode Entity Module (March 11, 2026)

### What Was Built

**SubCostCodeAlias** — A child entity for SubCostCode that supports agentic fuzzy matching in BillAgent and ExpenseAgent.

#### Alias Entity (Separate Table — Option A)
- `dbo.SubCostCodeAlias` table with stored procedures
- Full business layer: model (`alias_model.py`), repository (`alias_repo.py`), service (`alias_service.py`)
- API endpoints: POST create, GET by sub_cost_code_id, DELETE by public_id (direct CRUD, no workflow engine)
- Pydantic schemas: `SubCostCodeAliasCreate`, `SubCostCodeAliasUpdate`

#### Agent Integration
- BillAgent and ExpenseAgent `_resolve_sub_cost_code()` now falls back to alias matching
- Checks both normalized format and raw input value against alias table
- Loads `sub_cost_code_aliases` as reference data during processing

#### UI Enhancements
- **Edit page** (`templates/sub_cost_code/edit.html`): Aliases card with inline AJAX add/remove
- **View page** (`templates/sub_cost_code/view.html`): Read-only aliases section + Intuit QBO Item section
- QBO Item display uses two-step lookup: mapping via `ItemSubCostCodeConnector`, then item via `QboItemService`

#### Bug Fixes (Pre-existing)
- Fixed `TemplateNotFound` — changed `Jinja2Templates(directory="templates")` and prefixed template names
- Fixed `current_path is undefined` — added to all four route template contexts

### Files Created
- `entities/sub_cost_code/sql/dbo.subcostcodealias.sql`
- `entities/sub_cost_code/business/alias_model.py`
- `entities/sub_cost_code/persistence/alias_repo.py`
- `entities/sub_cost_code/business/alias_service.py`

### Files Modified
- `entities/sub_cost_code/api/schemas.py` — alias Pydantic models
- `entities/sub_cost_code/api/router.py` — alias API endpoints
- `entities/sub_cost_code/web/controller.py` — template fixes, alias + QBO loading
- `templates/sub_cost_code/edit.html` — alias management UI
- `templates/sub_cost_code/view.html` — aliases + QBO item display
- `core/ai/agents/bill_agent/business/processor.py` — alias fallback matching
- `core/ai/agents/expense_agent/business/processor.py` — alias fallback matching

### Deferred Work Update
- **SubCostCode alias table** — NOW IMPLEMENTED (was deferred from BillAgent session)

---

# Session: BillAgent (March 2026)

## What Was Built

**BillAgent** — An automated system that processes PDF invoices from a SharePoint folder, extracts bill data, and creates bill drafts in the application.

### Architecture (7 Phases)

1. **Database — Bill Folder Connector** (`integrations/ms/sharepoint/driveitem/connector/bill_folder/`)
   - `ms.DriveItemBillFolder` table linking SharePoint folders to companies with `FolderType` discriminator (`source` / `processed`)
   - Model, repository, connector service, and API router

2. **SharePoint Client — `move_item()` and `delete_item()`** (`integrations/ms/sharepoint/external/client.py`)
   - `move_item()` — PATCH `/drives/{drive_id}/items/{item_id}` to move files between folders
   - `delete_item()` — DELETE `/drives/{drive_id}/items/{item_id}` for cleanup before moves
   - Service wrappers in `integrations/ms/sharepoint/driveitem/business/service.py`

3. **Bill Folder Processing** (`core/ai/agents/bill_agent/`)
   - **Processor** (`business/processor.py`) — Deterministic processing loop:
     - Lists PDFs in source SharePoint folder
     - Parses 7-segment filenames: `{Project} - {Vendor} - {BillNumber} - {Description} - {SubCostCode} - {Rate} - {BillDate}`
     - Runs Azure Document Intelligence OCR + Claude extraction for supplemental data
     - Merges results (filename fields take priority over OCR)
     - Creates bill draft with line items and attachment
     - Moves processed file to processed folder (delete-then-move pattern for conflicts)
   - **Models** (`business/models.py`) — `BillAgentRun`, `ProcessingResult`, `FilenameParsedData`
   - **Runner** (`business/runner.py`) — Entry point wrapping processor with run tracking
   - **Service** (`business/service.py`) — Run lifecycle management
   - **Repository** (`persistence/repo.py`) — `BillAgentRun` persistence

4. **BillAgent API** (`core/ai/agents/bill_agent/api/`)
   - `POST /api/v1/bill-agent/run` — Trigger processing (background task, returns 202)
   - `GET /api/v1/bill-agent/run/{public_id}` — Check run status
   - `GET /api/v1/bill-agent/runs` — List recent runs
   - `GET /api/v1/bill-agent/folder-status` — Source folder file count for UI

5. **Scheduler** (`core/ai/agents/bill_agent/scheduler.py`)
   - Async background scheduler running at configurable interval (default 30 min)
   - Registered in `app.py` startup/shutdown events

6. **Bill List UI** (`templates/bill/list.html`, `static/css/bill.css`)
   - Folder summary section showing file count and "Process Folder" button
   - JavaScript for triggering processing and polling for completion

7. **Company Settings UI** (`templates/company/view.html`)
   - Bill Processing Folders section with SharePoint folder picker for source and processed folders

### Key Implementation Details

- **PaymentTerms**: All bill drafts set to "Due on receipt" — looked up once during reference data loading, passed through to `bill_service.create()`
- **Bill line items**: Created with `markup=Decimal("0")` and `price=rate`
- **File move conflicts**: Uses delete-then-move pattern — lists processed folder children, finds existing file by name, deletes it, then retries the move
- **SubCostCode matching**: Normalizes decimal format (e.g., `18.1` → `18.01`) before matching against `sub_cost_code.number`
- **Entity resolution**: Fuzzy matching for Project (prefix match), Vendor (Jaccard/containment), SubCostCode (normalized number match)
- **Error handling**: Failed files are skipped and left in source folder; processing continues with remaining files

## Results

- Working well in production. Most files process correctly.
- Occasional vendor mismatches and some sub cost codes not resolved — handled by draft review workflow.

## Deferred Work

- **SubCostCode alias table** — For commonly missed codes where the filename abbreviation doesn't match the database value. Explicitly deferred ("That is for another time").
- **LLM fallback for entity resolution** — Discussed and decided against. Draft workflow handles edge cases well enough. Would add cost, latency, and risk of wrong matches.

## File Inventory

### New Files Created
- `integrations/ms/sharepoint/driveitem/connector/bill_folder/` — full package (sql, model, repo, service, API)
- `core/ai/agents/bill_agent/` — full package (models, processor, runner, service, repo, API, scheduler, sql)
- Various `__init__.py` files for new packages

### Modified Files
- `integrations/ms/sharepoint/external/client.py` — added `move_item()`, `delete_item()`
- `integrations/ms/sharepoint/driveitem/business/service.py` — added `move_item()` wrapper
- `app.py` — registered new routers + scheduler startup/shutdown
- `entities/bill/web/controller.py` — bill folder summary for list page
- `templates/bill/list.html` — folder summary UI section
- `static/css/bill.css` — folder summary styles
- `entities/company/web/controller.py` — bill folder data in template context
- `templates/company/view.html` — Bill Processing Folders section with picker
