# U-551b-f1 — the repoint revision model through the external write

**Status:** design decision for the scoped round 6 (2026-11-06). Written after five review rounds left
five P2s that all describe the same missing thing: a stated model of *which approval revision a QBO
write belongs to, and what may interleave with it*. The unit so far (bite 1 + bite 2 + rounds 2–5 on
`unit/u-551b-f1` @ 6fc280e9) is kept; this round changes the interleaving rules only. **No SQL
changes in this round** — the three columns and three sproc parameters already on the branch are the
whole schema surface; deploy order stays SQL-first.

Citations are to the worktree at 6fc280e9 (`.worktrees/u-551b-f1`), `file:line`.

---

## 1. Definitions

| Term | Meaning | Where it lives |
|---|---|---|
| **Revision** | one confirmation of a suggestion; `ConfirmationSeq` increments on every `RecordCodingSuggestionConfirmation` | `entities/coding_suggestion/sql/dbo.coding_suggestion.sql:673` |
| **Written record** | what OUR last successful QBO write left: `WrittenSyncToken` + `WrittenLinesJson` (line id, item, sub cost code) | `…sql:844`, set only by `MarkCodingSuggestionWritten` |
| **Snapshot** | the written record *as read at revision n* plus the local `ExpenseLineItem` coding read at the same moment | the completion probe, `entities/expense/business/service.py:960` |
| **Approval** | the per-line targets completion computed from a snapshot: `RepointTargetsJson` + `targets_token` (= the snapshot's `WrittenSyncToken`) | `…expense/business/service.py:1047`, persisted by confirm `…coding_suggestion/business/service.py:809` |
| **Drain** | `QboOutboxWorker._handle_recode_purchase_line`: reads the suggestion, resolves targets, calls QBO, marks terminal | `integrations/intuit/qbo/outbox/business/worker.py:1166` |
| **Mark** | a terminal write on the suggestion (`written` / `changed_in_qbo` / `error`), guarded by the seq the drain read | `…worker.py:1191-1201` |

Two revisions are *the same work* when their target sets compare equal (`_repoint_target_key`,
`…coding_suggestion/business/service.py:615`); otherwise a later confirmation is a **new revision**.

## 2. Invariants (the ones the five P2s were missing)

- **I1 — An approval is bound to the snapshot that produced it.** Confirm may record targets only
  against the revision the probe READ. Today confirm guards on the seq it reads *itself*
  (`…service.py:911`), so an older completion that pauses before confirm's read can still replace a
  newer approval (Codex r4b-a P2 #1). *Fix: M2.*
- **I2 — No new revision while a drain holds the suggestion.** The window from the drain's read
  (`…worker.py:1177`) to its mark is an external write in flight; a revision recorded inside it means
  QBO ends up holding a superseded approval and the newer revision fences out as `changed_in_qbo`
  (Codex r4b-b, "new approval arrives after drain reads old revision"). The seq guard on the mark
  detects this *after* the external write; nothing prevents it. *Fix: M1.*
- **I3 — The kill switch means no mark of any kind.** Gate-off must precede *every* terminal mark,
  including the `[]` no-op (`…worker.py:1219`) and the resolve-error marks (`…worker.py:1203-1213`),
  which today land before the gate check at `…worker.py:1229`. *Fix: M3.*
- **I4 — `written` asserts "QBO is as our record says".** The empty-revision path marks `written`
  without touching QBO; a human edit since our write leaves the record asserting ownership that is no
  longer true until the next repoint's fence catches it. *Fix: M4.*
- **I5 — "Could not compute" is never "nothing diverges".** A raised recompute today recovers the
  persisted (possibly superseded) approval (`…expense/business/service.py:1024-1026`), and a local coding
  with no item mapping falls out of the diverged set as if unchanged (`…service.py:1073-1074`). *Fix: M5.*
- **I6 (unchanged, kept)** — the app repoints ONLY lines it wrote, on a purchase untouched since its
  write (up-front fence, `…purchase/connector/expense/business/service.py:909-917`); a terminal state
  never regresses (seq+status predicates on all four writes); every refusal fails closed.

## 3. Interleavings — outcome before and after this round

| # | Sequence | At 6fc280e9 | After round 6 |
|---|---|---|---|
| A | Completion 1 probes at seq n (targets B); completion 2 records C at n+1 against the same token; completion 1 reaches confirm | confirm re-reads n+1, records B at n+2 — **older approval wins** | completion 1 holds the lock from its probe to its confirm, so completion 2 cannot record in between; if 2 ran first, 1's probe (now serialized after it) reads n+1 and the C lines and computes an empty or equal revision; a direct `confirm` handed a stale `expected_confirmation_seq=n` against an item at n+1 → `repoint_revision_lost`, zero writes |
| K | Completion 1 reads lines B; the user recodes to C; completion 2's probe runs; both bind seq n | both record against n; whichever is second is refused, so QBO can hold B while local says C | the probe + confirm are one lock hold, and the lines are read after the revision: completion 2 runs after completion 1's confirm, reads n+1 and the C lines, records C at n+2 (a new revision) |
| B | Drain reads revision n (B), GETs the purchase; confirm records C at n+1; drain POSTs B | B lands in QBO, mark refused; C's drain fences out → `changed_in_qbo`, QBO holds B | confirm blocks on the suggestion lock until the drain's mark lands (record now B @ T1); confirm then reads n with record B @ T1 and C's `targets_token` = T0 → `repoint_targets_stale`, zero writes; the next completion recomputes C from B |
| C | As B, but the drain is slow (> confirm's wait) | — | confirm returns `busy` (on lock timeout, or at once when another thread of the same process is already waiting on that suggestion (U-551b-f1-f8: one waiter per resource per process); fail closed, no writes, logged `coding_suggestion.lock_busy`); completion result carries `skip_reason=busy` |
| D | Confirm holds the lock; drain claims the row | — | drain waits up to its timeout; on timeout raises `CodingSuggestionBusyError` (a `QboWriteRefusedError`) → the row **parks** 1 minute at WARNING (contention is expected; the gate-off parent parks 15 min at ERROR), never dead-letters, suggestion unmarked |
| E | Gate off; drain reads a repoint with `[]` targets, or targets that do not resolve | marks `written` / `error`, outbox row done | gate checked first → `QboRecodeWritesDisabledError`, row parks, **no mark** |
| F | Empty revision (`[]`), human edited the purchase in QBO since our write | marks `written`, record keeps the stale token | connector `verify_purchase_untouched` GET: token moved → `changed_in_qbo` (re-review); token same → `written`, record untouched |
| G | Pending repoint; the recompute raises | bare pending → confirm **recovers the persisted approval** | probe returns `skip_reason=repoint_recompute_failed`; completion reports the skip; no confirm call |
| H | A written line's local sub cost code has no item mapping | line silently treated as not diverged; may yield an empty revision | `repoint_line_unmapped` skip; no confirm call, no first-write fallthrough |
| I | Crash while the drain holds the lock | n/a | lock is `Transaction`-owned on its own connection (`integrations/intuit/qbo/base/locking.py:36-60`); the connection's rollback-on-close releases it — no stranded lock state |
| J | Drain holding the lock also holds the drain-level lock `qbo_outbox_drain` (`…worker.py:170`) | — | confirm never takes the drain lock; the drain takes locks in one order (drain → suggestion); no cycle |

Outcomes that are *accepted and unchanged*: a crash between a REPOINT's QBO POST and the local mark
(retry sees the advanced live token → `changed_in_qbo`; QBO is right, the record is stale — f3/f5
territory); the same crash on a FIRST write (retry finds the target already present →
`already_recoded` → `written` with the previous, possibly empty, record and token — no ownership is
claimed from an observation, so a later repoint of those lines is refused `no_written_record`, f3);
(U-551b-f1-f3b supersedes the empty-record outcome: PREVENT emits the record on a provable retry, ADOPT is the admin fix for rows already stranded — `docs/design/u551b-f1-f3b-adopt-written-record.md`);
a stranded `confirmed` with no outbox row (f5 sweeper); the pull rewriting local coding (f1).

## 4. Mechanisms

### M1 — per-suggestion application lock (closes I2)

- Resource `qbo_coding_suggestion:<suggestion public id>`, built by ONE function
  `coding_suggestion_lock_resource(public_id)` in `entities/coding_suggestion/business/service.py`.
  The id is **canonicalized** the way SQL Server's `UNIQUEIDENTIFIER` conversion reads it (lowercase
  hyphenated; braces, `urn:uuid:` and whitespace stripped; a suffix after the 36/38 characters ignored,
  so `<guid>wrong` locks the row it reads) before interpolation:
  `sp_getapplock` compares resource names case-sensitively, the drain keys on the driver's uppercase
  rendering of a `UNIQUEIDENTIFIER`, and the cockpit route accepts whatever spelling the caller sent
  (round-6 review, Codex + twin: two spellings of one suggestion are two locks),
  acquired through a thin context manager `coding_suggestion_lock(public_id, *, timeout_ms)` that
  wraps `qbo_app_lock` (`integrations/intuit/qbo/base/locking.py:36`). Both holders go through the
  wrapper so the key cannot drift (the U-337 lesson, `…locking.py:16-30`).
- **Holder 1 — the drain**, `_handle_recode_purchase_line`: acquire after the entity-type and gate
  checks, *before* `read_by_public_id`; hold through the terminal mark. Timeout
  `DRAIN_SUGGESTION_LOCK_TIMEOUT_MS = 5_000`; on timeout raise
  `CodingSuggestionBusyError(QboWriteRefusedError)` (new, `integrations/intuit/qbo/base/errors.py`,
  beside `QboRecodeWritesDisabledError`) → `_handle_qbo_error` parks the row. Contention is an
  expected event, not a gate-off: the busy subclass parks for `SUGGESTION_BUSY_PARK_INTERVAL`
  (1 minute) at WARNING, where the gate-off parent parks 15 minutes at ERROR.
- **Holder 2 — confirm** (every mode, not only repoint: the submit path and the cockpit endpoint
  record unguarded revisions too, `…service.py:911`): acquire after the gate check, *before*
  `read_by_public_id`; hold through `mark_enqueued`. **Holder 2b — completion**: the repoint probe
  and its confirm run under ONE hold of the same lock (`confirm(..., lock_held=True)` skips the
  acquire; the caller holds it), so two completions of one expense are serialized against each
  other and against the drain: the second one computes from the lines and the revision the first
  one left behind. `lock_held` is a caller contract (documented, not verifiable); its only caller is
  `_enqueue_repoint_on_complete`. Timeout
  `CONFIRM_SUGGESTION_LOCK_TIMEOUT_MS = 10_000`; on timeout return `{"status": "busy"}` with zero
  writes. The cockpit router maps `busy` → HTTP 409 (`entities/coding_suggestion/api/router.py:250-268`,
  releasing a claim taken by this request exactly as `multi_line_unsupported` does); the submit loop
  counts it as `skipped` with reason `busy`; completion reports `skip_reason=busy`.
- Nothing inside either held region may acquire `qbo_coding_suggestion:*` or `qbo_outbox_drain`
  (self-deadlock). The outbox enqueue and every sproc call open their own short connections; the lock
  connection is a separate pooled session for the duration.
- **Tests**: `qbo_app_lock` is never reached for real (it would open a DB connection with the
  worktree's `.env`). An autouse fixture in `tests/conftest.py` patches
  `entities.coding_suggestion.business.service.qbo_app_lock` to `mock_qbo_app_lock_granted`; contention
  tests patch it to deny inside the test. The wrapper reads `qbo_app_lock` from the service module's
  globals at call time so one patch site covers both holders.

### M2 — revision binding from the probe to confirm (closes I1)

- Every dict the probe returns carries `revision_seq = full.confirmation_seq` (found, pending, empty).
- `_enqueue_repoint_on_complete` passes it as `expected_confirmation_seq`.
- `confirm(..., expected_confirmation_seq: Optional[int] = None)`: **required when `repoint=True`**
  (`None` → `{"status": "invalid", "reason": "revision required"}`); if the item read under the lock
  carries a different seq → `repoint_revision_lost` before any write. The three repoint writes
  (`record_confirmation`, the recovery `mark_enqueued`, the replacing record) all use the caller's seq,
  never confirm's own read. Non-repoint callers pass nothing and keep the NULL (unguarded) behaviour.
- The probe reads the local line items itself (one sproc call) instead of reusing the list completion
  read before its SharePoint/Excel steps (`…expense/business/service.py:1634`), and it reads them
  **after** the suggestion row whose `confirmation_seq` it binds: a snapshot is (revision n, lines read
  after n was read). Reading the lines first lets an older line snapshot be labelled with a newer
  revision (round-6 Codex P2 #1). Both reads happen under the suggestion lock (M1, holder 2b).

### M3 — gate before any mark (closes I3)

`recode_write_gate_reason()` moves to the top of the handler, right after the entity-type check and
before the lock and the read. Gate-off → `QboRecodeWritesDisabledError`, no read, no mark, row parks.
(During gate-off a retry of an already-terminal suggestion also parks instead of completing — that is
the kill switch's meaning: touch nothing.)

**M3b — refuse before recording (U-551b-f1-f10).** A dead-lettered recode outbox row makes `enqueue()`
refuse, so confirm used to record a revision (ConfirmationSeq+1, Status confirmed) it could never enqueue, and
the row stayed `confirmed` with no live outbox row until the dead letter was replayed. `_confirm_under_lock`
now runs the SAME probe `enqueue()` uses — `QboOutboxService.find_coalesce_target_or_raise_dead_letter`
(pending/failed row → coalesce target, no dead-letter read; else dead letter → raise; else None) — BEFORE
`record_confirmation`, under the suggestion lock. Zero writes on refusal; the route answers the same 409 and
releases a claim it took. `enqueue()` re-runs the probe itself, so the pre-check is advisory and the
second read stays the hard guard. The cockpit route's generic 500 path also releases a claim taken by
that request (it was the one refusal branch that did not).

### M4 — the no-op path fences on the live token (closes I4)

New connector method `PurchaseExpenseConnector.verify_purchase_untouched(*, realm_id,
qbo_purchase_qbo_id, expected_sync_token) -> bool`: one `get_purchase_raw`, compare `SyncToken`
(string compare, exactly as the fence at `…connector/expense/business/service.py:912`). The drain's
empty-targets repoint path calls it under the lock: `True` → `mark_written(sync_token=written token,
written_lines_json=None)` (record untouched); `False` → `mark_changed_in_qbo`. A `QboError` from the
GET propagates to the worker's normal error classification (retryable → backoff; non-retryable →
dead-letter) with no suggestion mark either way.

### M5 — failed or unresolvable recompute fails closed (closes I5)

- The probe returns `{"public_id", "skip_reason": "repoint_recompute_failed"}` when
  `_compute_repoint_divergence` raises (pending *or* written candidate; no re-raise into the outer
  handler, no recovery of the persisted approval).
- `_compute_repoint_divergence` raises `RepointUnresolvable(...)` when a written line cannot be judged:
  `repoint_line_unmapped` (its local sub cost code resolves to no item), `repoint_line_holding` (it
  resolves to the holding code — the purchase pull can put one on a completed parent's line after the
  upstream completion check), `repoint_line_missing_local`
  (no local line carries that QBO line id), `repoint_line_uncoded` (the local line has no sub cost
  code). A silent skip of any of these could let a pending approval collapse into an empty revision
  (round-6 Codex B P2).
- `_enqueue_coding_recode_on_complete`: a probe result carrying `skip_reason` returns
  `_recode_skipped_result(skip_reason=…)` immediately — never the first-write fallthrough.

## 5. What this round does NOT do

- No new SQL. `ConfirmationSeq`'s guards already express "this revision only"; M1 is app-level
  because it must span an external call.
- No new suggestion status (a `writing` state was considered: it needs a sweeper for crashes and
  touches every status consumer; the applock is released by the connection on a crash for free).
- No change to the first-write replace-vs-merge rule, the membership/prior-item checks, or the
  connector's (a)/(b)/(c) rule.
- Follow-ups stay booked: f1 pull rewrites local coding during review; f2 dormant confirm endpoint
  has no status guard; f3 error repoints are unrecoverable; f4 no ReconciliationIssue on QBO
  dead-letters; f5 stranded `confirmed` sweeper; f6 runbook.

## 6. Tests the round must add (each named with the mutant it kills)

| Test | Kills |
|---|---|
| older completion handed seq n while the item is at n+1 → `repoint_revision_lost`, zero writes | confirm ignoring the caller's seq |
| repoint with `expected_confirmation_seq=None` → `invalid` | the requirement dropped |
| recovery `mark_enqueued` and replacing `record_confirmation` receive the CALLER's seq | a fallback to the read seq |
| lock denied at confirm → `busy`, zero writes, lock resource = `qbo_coding_suggestion:<id>` | wrong key / write before lock |
| lock acquired AFTER the gate and BEFORE the read, released after `mark_enqueued` (call-order pin) | read outside the lock |
| drain: lock denied → `CodingSuggestionBusyError` is a `QboWriteRefusedError`; no read, no mark | busy dead-letters / marks |
| drain: gate off with `[]` targets, with unresolved targets, with a terminal item → no mark, row parks | gate after a mark |
| drain: `[]` targets, token same → `written` with record untouched; token moved → `changed_in_qbo`; GET raises → propagates | no-op without a fence |
| drain: lock spans read → mark (call-order pin against the mock) | mark outside the lock |
| probe: recompute raises on a pending candidate → `skip_reason`, confirm never called | recovery of a superseded approval |
| probe: unmapped local coding → `repoint_line_unmapped`, no first-write fallthrough | silent "no divergence" |
| probe reads line items fresh; `revision_seq` present on every returned shape | stale snapshot half |
| cockpit router maps `busy` → 409 and releases a claim taken by the request | 500 on busy |
| P3 pins from r4b: empty-revision confirm with NO coding args; holding-skip test with a surviving diverged line; `OR 1=1` pin on `RecordCodingSuggestionConfirmation`'s own test | — |

## 7. Deploy

Unchanged from the parked note: SQL-first (the repo always passes the new sproc parameters), with the
QBO outbox drained or the scheduler's `drain_outbox_qbo` paused across the image swap. The mixed window
is unsafe in BOTH directions: an old-image worker does not know persisted targets or the lock (it would
consume a repoint as a first write and advance `WrittenSyncToken` without lines), and an old-image
confirm does not take the lock. So: apply SQL → pause the QBO drain timer → let in-flight handlers
finish → swap the image → resume. This round adds no SQL.

## 8. Recovery (f5 sweeper, f3 redrive)

Two lost-step failures had no in-app recovery: a `confirmed` row whose enqueue never happened (confirm is
three DB calls: record, enqueue, mark), and a repoint the drain marked `error`.

- **D1** The sweeper remediates ONLY stranded `confirmed` rows: it re-reads the row under
  `coding_suggestion_lock`, re-checks `Status='confirmed'` and an unchanged seq, then runs
  `_enqueue_recode_and_mark(public_id, realm_id, seq)`. Gated by `recode_write_gate_reason()` like confirm
  (gate off + apply = zero writes).
- **D2** No timer ever re-drives an `error` row (a deterministic error such as `repoint_line_holding` would
  loop every tick). `error` rows are only REPORTED (`recovery.error_repoint`).
- **D3** Re-driving an `error` repoint is a human system-admin action (`POST .../redrive-repoint`,
  `require_system_admin()`): under the lock it re-records the row's own `RepointTargetsJson` as a new seq,
  fenced on the seq it read (`RecordCodingSuggestionConfirmation` admits `error` in the seq-given branch
  only), then enqueues. The drain's token fence then writes or marks `changed_in_qbo`.
- **D4** A `dead_letter` recode outbox row blocks both actions (`dead_letter_pending`, zero writes); the
  operator replays it (`scripts/retry_qbo_outbox_dead_letters.py`).
- **D5** Stranded = `confirmed`, `ConfirmedAt` older than `@OlderThanSeconds` (300), and no outbox row of
  kind `recode_purchase_line` (entity type `CodingSuggestion`) in
  `pending|failed|in_progress`; `done` is NOT live.

| State | Action | Fence |
|---|---|---|
| `confirmed`, no live outbox row | sweeper re-enqueues | lock + status + seq re-check; mark fenced on seq |
| `error`, source `complete_repoint` | admin redrive | lock + `ExpectedConfirmationSeq` (admits `error`) |
| `error`, first write | cockpit confirm (existing recovery); redrive refuses `not_a_repoint` | - |
| `written` without a record | report only (`recovery.written_without_record`); adoption = f3b, open | - |
| `dead_letter` outbox row | operator replay; sweeper/redrive report `dead_letter_pending` | - |

`written_without_record` is bounded by `RECORD_REQUIRED_SINCE_UTC` (shared/api/admin.py, the U-551b-f1
deploy instant): older `written` rows legitimately have no record.

Two self-healing paths cover the partial-failure orderings: if the record succeeded but the enqueue raised,
the row is `confirmed` with no outbox row and the sweeper re-enqueues it after 300 s; if the enqueue succeeded
but the mark was fenced out, the drain processes `confirmed` rows and marks on the live seq. Entity type is
`CodingSuggestion` everywhere (sproc anti-join, `HasDeadLetter`, the service and enqueue guards): U-652 retired
`ExpenseCodingItem` and its static test forbids the literal in scoped SQL, so there is no legacy arm to scan.

Deploy: SQL first (two new sprocs + the guard), then the API image, then the scheduler.

