# U-652d4 — Gate before claim, sweeper visibility, service-level access

Approved by Chris at Gate 1, 2026-11-10 (TODO items U-652d 10, 12-gap and 14). P0 surface: the QBO write path.

## Item 10: check the write gate before the claim

**Problem.** The confirm router claims the suggestion (`ClaimCodingSuggestion`, a durable `ClaimedByUserId`/`ClaimedAt` write) before checking the recode write gate inside `confirm()`.

- With a gate off, every exit releases the claim.
- A release that fails, or a `BaseException`, leaves the claim set for up to 900 s. Other users get 409 "claimed by another user" in that window.

**Fix.** In `entities/coding_suggestion/api/router.py`, check `recode_write_gate_reason` before `service.claim()` and return the same 422 `detail` (`qbo_writes_disabled` / `recode_writes_disabled`) that `confirm()` returns. The in-service check stays as a backstop.

No `claim_and_confirm` sproc. The claim is not on the money path: no status, no outbox.

## Item 12 gap: make stranded `enqueued` rows visible, report-only

**Problem.** The stranded-confirmed sweep (arm A) selects only `Status='confirmed'`. No arm selects:

- an `enqueued` suggestion whose outbox row is **dead-lettered**;
- an `enqueued` suggestion with **no live outbox row** (the terminal mark was fenced out, or the row is gone).

Those rows are stranded and unreported. Separately, a dead letter set by `ReclaimStrandedQboOutbox` (a SQL-side flip) raises no `ReconciliationIssue` at all.

**Fix: report, never auto-replay.**

Re-enqueuing an `enqueued` row could re-send a write QBO already applied, for example once old outbox rows are pruned. So:

- `ReadCodingSuggestionRecoveryCandidates` gains a category `enqueued_stranded`. It covers `Status='enqueued'`, older than the cut-off by `ModifiedDatetime`, with no live outbox row (`pending`/`failed`/`in_progress`) for the suggestion.
- The sweeper lists these under `result.recovery.enqueued_stranded`. It never enqueues them.
- A row with a dead letter stays visible as before (`dead_letter_pending`) and is listed in the new category too.
- Live-row and dead-letter checks match `'CodingSuggestion'` only. The legacy `'ExpenseCodingItem'` is NOT widened: prod (read-only, 2026-11-10) has 57 legacy outbox rows, all `done`, none live or dead-lettered, so widening buys nothing and breaks the standing U-652 rule (EM decision).
- Escalate a reclaim-path dead letter (the worker's reclaim at worker.py ~247) to a `[qbo].[ReconciliationIssue]`, the same shape the worker's own dead-letter escalation uses. The realm comes from the existing `read_by_id` (`RealmId` is NOT NULL on prod; the reclaim proc does not return it, and no SQL change is made). A read failure is isolated and records nothing.

Carried over from U-652d3:

- A transient ELI read failure on a **multi-line** drain still becomes a sticky `error`.
- `error` rows remain report-only under `recovery`.

Nothing changes there in this unit.

## Item 14: access check in the service

**Problem.** Row-level access for coding suggestions is checked only in the router (`_assert_can_access_coding_suggestion`, router.py ~42-51). Non-router callers do no row check:

- **Expense submit** (`ExpenseService.enqueue_coding_recode_on_submit`, reached from `ReviewService`): an HTTP user.
- **`suggest_pending`** (`POST /expense/coding/suggest`): walks the whole realm queue and writes suggestion rows for purchases the user may not be able to see.

**Fix.**

- Move the check into `CodingSuggestionService` as `assert_accessible(item)`, reusing `shared/access.assert_can_access_expense` / `assert_can_access_project`. The router calls the service method.
- Call it in `_confirm_under_lock` right after the read.
- Call it in `record_flag` and `record_suggestion`/`persist_result`.
- `suggest_pending` skips queue rows the actor cannot access. Filter with `resolve_accessible_project_ids()` / `can_access_expense`. It does not raise, so one inaccessible row doesn't fail the batch.
- System callers (`system_authz()`: completion job, QBO pull, outbox worker, CLI) bypass as today. Expense attribution is UNCHANGED (the `_SYSTEM_ACTOR_USER_ID = 17` fallback stays). `system_actor_user_id()` resolves the claude_agent user (Id 33) by DB lookup and raises if it is not provisioned, so it is not a drop-in for the fallback (EM decision 2026-11-10).

## Not changed

- Confirm's money path (record, then enqueue, then mark) and the worker's gating.
- The 900 s reclaim window.

## Gate 2

- SQL: `dbo.coding_suggestion.sql` (the recovery and stranded-read procs). Drift-check it first.
- The scheduler endpoint's response gains a `recovery.enqueued_stranded` key, an additive JSON field.
- **Order matters: the SQL must be live BEFORE the image.** The repo now passes `@EnqueuedOlderThanSeconds` to `ReadCodingSuggestionRecoveryCandidates`; an image deployed ahead of the proc fails every 15-minute sweep (the parameter does not exist yet). Apply `dbo.coding_suggestion.sql` first, then deploy the image.

## Accepted behaviour (EM decision 2026-11-10)

- `suggest_pending` fails closed for a non-admin: a queue row whose expense does not exist yet is skipped, not suggested. Accepted.
