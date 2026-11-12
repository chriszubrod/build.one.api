At the start of each session, read SESSION_NOTES.md for historical context.

## The work board — `build.one.team`

Unit tracking lives **outside this repo**, in the sibling `build.one.team` repo (the sixth alongside the five
product repos; `github.com/chriszubrod/build.one.team`). Read `../build.one.team/BOARD.md` at the start of a
substantive session — it is the single cross-repo view of what is in flight, and it is where work gets booked.

- Every unit carries a `U-###` id, echoed in its commit's `Unit: U-###` trailer, so any sha traces back to its
  board row and spec.
- `/em` (engineering) and `/pm` (product) are conducted from there; their role briefs live under
  `../build.one.team/engineering/` and `../build.one.team/product/`.
- ⚠️ `BOARD.md` is edited by parallel sessions. Re-read before writing, commit it by explicit pathspec, and
  never sweep another session's in-progress rows into your commit.
- It has no `CLAUDE.md`/`SESSION_NOTES.md`/`TODO.md` of its own — `BOARD.md` is its state. It is committed and
  pushed like any repo, but it is **never deployable**.

## Parallel sub-agents & worktrees

⚠️ Sessions fan out to sub-agents, and **several sessions may be working this repo at once**. Agents share one
filesystem and one git index. Full rules in the umbrella `../CLAUDE.md`; the ones that bite here:

- **Disjoint `SCOPE` per writing agent.** Read-only agents may overlap freely; two agents writing one file is
  the collision. Verify the diff against SCOPE mechanically (`--porcelain -uall` + md5), never by eye.
- **Sub-agents never commit, push, deploy, or write prod data** — they hand back a diff; the session applies it.
- **Any agent that writes code gets its own worktree**, and mutation testing ALWAYS does — in-place mutation
  injects broken code into other sessions' test runs. Worktrees live in the **umbrella's** `.worktrees/` — use
  the absolute path; a relative one lands *inside* this repo (`.worktrees/` is gitignored as a guard, U-466d):
  `git worktree add -b unit/u-<n> /Users/chris/Applications/build.one/.worktrees/u-<n> origin/master` for a
  code unit (`--detach` instead of `-b unit/u-<n>` for a throwaway mutation run), then symlink `.venv` and copy `.env`.
- ⛔ **Worktrees on durable disk, never `/tmp`** (it gets wiped mid-unit). ⛔ **The stash stack is SHARED** —
  never bare `git stash`/`pop`; push with a unique `-m` tag, `apply` by tag, drop by tag.
- **Land by fast-forward only** (U-466d): rebase onto the current `origin/master`, re-verify, then
  `git -C <worktree> push origin unit/u-<n>:master`; rejected ⇒ rebase and re-verify again, never merge. Then delete the branch.
- **Remove worktrees when done.** A stale one is a live writer, and has blocked a prod DROP before. Stale =
  branch merged **and** nothing uncommitted — HEAD on trunk is not evidence (every worktree starts there).
- **Commit by explicit pathspec, never `-A`, and verify `git show <sha> --stat` before pushing** — another
  session's uncommitted files are one careless command from your commit. ⚠️ Hunk-staged
  (`git apply --cached`) ⇒ commit with **NO** pathspec, or you re-sweep the working tree.

## Working Style

- **Plan before coding.** Propose a step-by-step plan and wait for approval before writing any code. Do not start implementing until the plan is confirmed.

## Testing

- **Run the suite under the project venv, NOT system `python3`:** `./.venv/bin/python -m pytest` (Python 3.11 with `requirements.txt` installed). A bare `python3` may resolve to a system interpreter missing deps (`fastapi`, `transitions`, …) — app-importing tests then error at collection and pure-logic tests give a misleading green. `tests/conftest.py::pytest_configure` fails fast with this guidance if the wrong interpreter is used. Config: `[tool.pytest.ini_options]` in `pyproject.toml` (`testpaths=['tests']`).
- **v1 harness (2026-07-11) is pure-logic / no-live-DB** — money math (`Decimal`-not-float), contract-labor bill math, naming, response envelopes, and the instant-workflow `tenant_id` dispatch contract. DB-integration tests (sprocs/pyodbc) are a separate future unit.


## Where the detail lives

This file is the **rule layer** — one line per standing rule. The reasoning, incident
history and call-site inventories moved to `docs/conventions/` on 2026-11-11 (U-788),
which took this file from 59 KB to ~15 KB of per-session load. Nothing was deleted and
**nothing was downgraded**: a rule below binds exactly as it did when it was a paragraph.
Read the matching file before working an area.

| file | when to read it |
|---|---|
| [`docs/conventions/sql-and-sprocs.md`](docs/conventions/sql-and-sprocs.md) | any SQL, sproc or migration |
| [`docs/conventions/entities-and-lifecycle.md`](docs/conventions/entities-and-lifecycle.md) | adding/changing an entity, any delete path |
| [`docs/conventions/rbac-and-access.md`](docs/conventions/rbac-and-access.md) | auth, scoping, `UserCanAccess*`, tenancy |
| [`docs/conventions/money.md`](docs/conventions/money.md) | anything touching an amount |
| [`docs/conventions/qbo-and-invoice.md`](docs/conventions/qbo-and-invoice.md) | QBO pull/push, invoices, packets |
| [`docs/conventions/expense-coding.md`](docs/conventions/expense-coding.md) | the coding cockpit / any app→QBO expense write |
| [`docs/conventions/contract-labor.md`](docs/conventions/contract-labor.md) | contract labor, generated bills |
| [`docs/conventions/notifications.md`](docs/conventions/notifications.md) | review-submit email |
| [`docs/conventions/integrations-hardening.md`](docs/conventions/integrations-hardening.md) | **before adding any external integration** |
| [`docs/conventions/scheduler-and-admin-endpoints.md`](docs/conventions/scheduler-and-admin-endpoints.md) | timers, drains, the admin endpoint surface |
| [`docs/conventions/app-service-and-env.md`](docs/conventions/app-service-and-env.md) | env vars, write gates, prod settings |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | the orchestrator, the agent fleet, the DI pipeline, or when April-2026 rationale matters |
| [`docs/README.md`](docs/README.md) | index of all 186 docs — what is live vs historical |

## Project Conventions — the rules

**Layout & contract**
- `entities/{name}/` with `api/` `business/` `persistence/` `sql/`. No `web/` — React owns the UI, API returns JSON only.
- **UI rules live in `build.one.web`** (moved there 2026-11-11, U-788). Two are shared contracts: an FK in a POST body is the internal `id` (BIGINT), not the `public_id` (UUID) dropdowns carry — the API does not coerce between them; and a debounced edit-page PUT can race an action's own PUT on the same `row_version`.
- **An entity's contract is in the code, not a spec file (U-631):** the `service.py` module docstring (Owns / Invariants / External writes / Sprocs) + the `-- Contract:` header on `sql/dbo.*.sql`. Read both FIRST; update them in the same change. `tests/test_entity_docstrings.py` enforces it.
- ProcessEngine for main entity CRUD; direct CRUD for lightweight children. ROWVERSION + base64 for concurrency.
- Lazy-imported services (e.g. `BillService`) must be used via `self.*`, never a bare class name.

**SQL** → [detail](docs/conventions/sql-and-sprocs.md)
- All DB access via stored procedures. Migrations: `./.venv/bin/python scripts/run_sql.py <file>` **from the repo root** — not `python`, not `python3`, and not from the umbrella (CWD is load-bearing; `.env` resolves against it).
- ⛔ **The entity base file is the canonical home for a sproc.** A migration must never carry a duplicate body of a sproc its base file defines. Only `time_entry` + `role_module` are provably converted (guarded by `tests/test_sproc_single_source.py`); for the other 35, **diff the LIVE sproc's `sys.parameters` against the base file before redefining it** — a stale base silently drops prior work (U-037 dropped RBAC actor params → cross-user payroll exposure).
- **EXCEPTION:** the `dbo.UserCanAccess*` family lives in `shared/sql/dbo.access_udfs.sql`, never entity-homed — `SCHEMABINDING` makes an entity-local home a build *cycle*. A 6th defined elsewhere fails the suite.
- **Result-set discipline (pyodbc):** mutation sprocs need `SET NOCOUNT ON` (else pyodbc raises "No results"), and ⛔ **never `ROLLBACK` inside a sproc** (pyodbc is autocommit-off → error 266). Validate-first, COMMIT unconditionally, signal conflict with an empty result set.
- **NULL-handling rule:** UPDATE sprocs SET all columns, so use `CASE WHEN` guards for fields that must survive a NULL being passed.
- ⛔ **Every sproc batch must be `GO`-terminated.** A `CREATE PROCEDURE` body runs to the end of the *batch*, not to its matching `END`, so a trailing `IF NOT EXISTS … ALTER TABLE` block with no `GO` is silently swallowed into the last sproc — mutating it on re-apply, and the constraint never runs. (`tests/test_qbo_reconciliation_bulk_ops_u249.py` cites this rule.)
- New sproc params take `= NULL` defaults so older callers stay safe.

**Money** → [detail](docs/conventions/money.md)
- ⛔ **The exact-decimal rule:** `Decimal(str(value))` for every financial field — **never `float()`**. Float round-trips corrupt values. (`shared/api/money.py` and `tests/test_completion_money_zero_coercion.py` cite it by this name.)
- Labor is **two-shot** cent rounding: `cost = round(hours × rate, 2)` then `price = round(cost × (1 + markup), 2)`, half away from zero. Canonical: `shared/api/money.py` (`round_money`, `labor_price_two_shot`). Single-shot lands a cent off.
- Guard zero explicitly (`is not None`, never truthiness) — a falsy `Decimal("0")` has been dropped to NULL before.

**RBAC & access** → [detail](docs/conventions/rbac-and-access.md)
- Chain: User → UserRole → Role → RoleModule → Module. Gate every entity router with `shared/rbac.require_module_api(...)`.
- JWT carries `uid`/`cid`/`isa`; ContextVars per request. `User.IsSystemAdmin = 1` is the bypass primitive. Permissions are the OR-union over UserRole rows, cached 5 min per `(user_sub, company_id)`.
- Tokens missing `uid`/`cid` **fail closed** (`JWT_CID_GRACE_DAYS=0`) — no DB fallback.
- `UserModule` merges on top of `RoleModule` as **read-only** and never downgrades a role grant.
- Non-admins see only rows on projects they hold `UserProject` for. `EntityNotAccessibleError` → **404, not 403**, so the URL cannot confirm existence.
- Mutations inherit scoping by fetching through `read_by_public_id` first — keep it that way.

**Deletes** → [detail](docs/conventions/entities-and-lifecycle.md)
- SQL Server FKs here have **no CASCADE DELETE** — application code nullifies or deletes children first.
- Expense line item order is verified and load-bearing: link row → `Attachment` row → **Azure blob LAST**, each step in its own try/except. The guarded row delete decides; the blob must survive a refusal because another completed Bill may cite the same attachment.
- `completed` is terminal (`shared/lifecycle/terminal_lock.py`); every mutation sproc takes `@AllowTerminalParent`.

**QBO** → [detail](docs/conventions/qbo-and-invoice.md)
- ⛔ **`qbo.*` IDs are NOT `dbo.*` IDs** — separate keyspace. Never alias `qb.Id AS BillId`; hop `dbo.BillLineItem.BillId` to reach the parent.
- ⛔ **Never clobber a human-edited document number** on an UPDATE path — route it through `preserve_human_edited_ref(...)`. A re-pull otherwise reverts a correction within one tick. CREATE paths pass the QBO value as-is.
- ⛔ **Never `delete mapping` → fall through to CREATE when a mapped entity reads empty** ("heal-don't-delete"). A *transient* empty read mints a duplicate. Re-resolve first; repoint in place, or heal only if a natural fingerprint re-binds the **same** id; otherwise record a `ReconciliationIssue` and **raise, mutating nothing**.
- `sync_to_qbo_bill()` must write `BillLineItemBillLine` mappings keyed on `line_num`, or the next pull duplicates line items.
- Invoice is **pull-only**; `complete_invoice` deliberately does not push. The push path is dry-run only (`QBO_INVOICE_PUSH` off by default).
- Expense→QBO writes go **only** through the coding cockpit's surgical `recode_purchase_line`. ⛔ Do **not** reuse `sync_to_qbo_purchase` — it rebuilds all lines and drops siblings (verified P0). Double-gated by `ALLOW_QBO_WRITES` **and** `ALLOW_EXPENSE_RECODE_WRITES`, both checked at the top of `confirm()`: either off ⇒ 422, **zero DB writes**.

**External integrations** → [detail](docs/conventions/integrations-hardening.md)
- QBO, MS and Box share one hardened shape (`base/` package, typed retryable errors, applock, correlation ids). **A new integration must follow it.**
- ⛔ **Every external write enqueues an outbox row — never an inline API call.** Completion returns 202; side effects drain. Each integration drains on its own 60s timer so one slow drain cannot head-of-line-block another.
- Outbox workers declare system intent via `system_authz()` from `shared/authz/context.py` — never a hand-rolled save/set/restore (that pattern shipped a bug). Without it every drain dead-letters as `EntityNotAccessibleError`.
- Write gates default-deny: `ALLOW_QBO_WRITES` / `ALLOW_MS_WRITES` / `ALLOW_BOX_WRITES`. A fresh checkout cannot push to prod.
- Tokens are encrypted at rest (`ENCRYPTION_KEY`). 5 failed attempts → dead-letter → `ReconciliationIssue`.
- ⚠️ **MS outbox has no human-cancel window** (60s drain) — audit ids *before* enqueuing; recovery is row-by-row.
- Every external failure mode gets a `docs/runbooks/*.md` with Symptom / Severity / Diagnosis / Recovery / Verification / Prevention.

**Scheduler & admin endpoints** → [detail](docs/conventions/scheduler-and-admin-endpoints.md)
- Recurring work lives in the sibling `build.one.scheduler` Function App and POSTs admin endpoints; all require `X-Drain-Secret` (503 unset, 401 mismatch). A few take a system-admin JWT instead.
- ⛔ **Never set `AzureWebJobs.<fn>.Disabled` on Flex** — it de-registers the function and `false` does not bring it back; recovery is a republish.
- Flex `alwaysReady` is required or timers silently miss ticks.

**Contract labor** → [detail](docs/conventions/contract-labor.md)
- `pending_review` → `ready` → `billed`; `ready` needs a `bill_vendor_id` **and** a complete line item.
- `IsBillable = false` means *not billable to the client* — it is **still on the vendor A/P bill** (we pay for every logged hour). Billable and non-billable consolidate into **separate** BLIs. Use `is_billable is not False`, never `if is_billable`.
- Re-pass the existing `BillLineItemId` on update or the `CASE WHEN` guard cannot save it.

**Prod settings** → [detail](docs/conventions/app-service-and-env.md)
- Always On = true (else 10–15 s cold starts); `-w 2` in `startup.sh`; `ENABLE_SCHEDULER=false`.
- `DRAIN_SECRET`, `ENCRYPTION_KEY`, the three write gates and DB creds must all exist on App Service.
