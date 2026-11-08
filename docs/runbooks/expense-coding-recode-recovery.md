# Runbook: Expense-Coding Recode Recovery

The cockpit/completion recode of a QBO Purchase (`CodingSuggestion` -> `qbo.Outbox` kind
`recode_purchase_line` -> QBO) can stall in a few well-defined places. This runbook lists the
states, the symptoms, and the recovery levers (sweeper, redrive, dead-letter replay).
Design and invariants: `docs/design/u551b-f1-repoint-revision-model.md` (§8 = recovery).
Paths below are relative to `build.one.api/`.

## 1. What it is, and the status vocabulary

A human (cockpit `POST /api/v1/expense/coding/{public_id}/confirm`,
`entities/coding_suggestion/api/router.py`) or expense completion (a **repoint** of lines our
earlier write coded) records a *revision* (`ConfirmationSeq`) on `dbo.CodingSuggestion`,
enqueues a `recode_purchase_line` outbox row, and marks the suggestion `enqueued`. The drain
(`integrations/intuit/qbo/outbox/business/worker.py` `_handle_recode_purchase_line`) takes the
per-suggestion application lock, writes QBO, and marks a terminal state fenced on the seq it
read. Confirm is three DB calls (record, enqueue, mark); a crash between them is what the
sweeper heals.

| Status | Written by | Cockpit shows |
|---|---|---|
| `pending` / `suggested` / `flagged` | suggest / flag sprocs (`dbo.coding_suggestion.sql`) | awaiting review |
| `confirmed` | `RecordCodingSuggestionConfirmation` (confirm, redrive) | approved, not yet queued (stranded if it stays) |
| `enqueued` | `MarkCodingSuggestionEnqueued` | write queued/in flight |
| `written` | drain, `MarkCodingSuggestionWritten` (stores `WrittenSyncToken` + `WrittenLinesJson`) | done |
| `changed_in_qbo` | drain, `MarkCodingSuggestionChangedInQbo` (token fence / line gone) | back to re-review |
| `error` | drain, `MarkCodingSuggestionError` (`WriteError` set) | error; first write: re-confirm; repoint: admin redrive (§4) |
| `resolved_externally` | reconcile job, `MarkCodingSuggestionResolvedExternally` | closed, someone coded it in QBO |

Terminal marks are guarded on `ConfirmationSeq` + status; a terminal state never regresses.

## 2. Symptoms -> diagnosis

| Symptom | Meaning / where to look / action |
|---|---|
| Cockpit **409 `coding_suggestion_busy`** | Confirm could not take `coding_suggestion_lock` (`service.py` `confirm`; router.py maps `busy`). Either a drain holds it across its QBO write (up to `DRAIN_SUGGESTION_LOCK_TIMEOUT_MS` 5 s) or another confirm is mid-flight (f8: a second concurrent confirm gets this at once, no waiting). Zero writes, the claim is released. Log `coding_suggestion.lock_busy ... holder=confirm\|drain\|redrive`. **Retry in a few seconds.** Persistent -> check `qbo.Outbox` for an `in_progress` row stuck on that suggestion. |
| **409 `already_enqueued`** | Status `enqueued` first write: a drain is queued. Wait; nothing to do. |
| **409 `repoint_pending`** | A completion-owned repoint is `confirmed`/`enqueued`. Cockpit must not touch it; the drain or sweeper owns it. |
| **409 `already_written`** | Status `written`, or the row holds a written record (token/lines). Only completion repoints change a written line. Check QBO if the coding looks wrong. |
| **409 with a dead-letter message** (`QboOutboxDeadLetterExistsError`) | A dead-lettered `recode_purchase_line` row exists for this suggestion. Zero-write: f10 refuses BEFORE any revision is recorded, and the cockpit 500 path releases its claim. Replay first (§5), then re-confirm. |
| Other 409s (`not_recorded`, `already_terminal`, `resolved_externally`, `repoint_revision_lost`, `repoint_targets_stale`) | Refusals with zero writes (`confirm` docstring); re-read the item and retry from the fresh state. |
| **A reviewer's coding did not reach QBO** | Read `dbo.CodingSuggestion` by PublicId: `confirmed` older than 5 min = stranded (sweeper, §3); `enqueued` + outbox `failed` with `LastError LIKE 'Parked:%'` = a gate is off (§7) or lock contention (parks 1 min, WARNING); `error` = §4; `changed_in_qbo` = purchase edited in QBO, re-review; `written` but QBO differs = §6 / look for a later human edit. Also f12: a purchase pull that hits `PARENT_STATUS_MOVED` (409) holds the watermark for that purchase one tick; it self-heals next pull, no action. |
| **ReconciliationIssue `drift_type = recode_purchase_line_dead_letter`** (severity `high`, `action=flagged`) | The drain exhausted retries / hit a non-retryable error and dead-lettered the row (`worker.py` `_escalate_dead_letter`; log event `qbo.reconcile.dead_letter.flagged`). Entity = the suggestion's PublicId. Go to §5. |
| Scheduler log **`sweep_stranded_coding_suggestions.done`** at WARNING | The tick found something (`function_app.py` timer). Fields: `candidates enqueued dead_letter_pending busy errors gate error_repoint written_without_record moved status`. INFO means all zero and gate clear. Read §3. |

## 3. The sweeper

- **Cadence:** scheduler timer `sweep_stranded_coding_suggestions`, schedule `20 */15 * * * *`
  (every 15 min at second :20), `build.one.scheduler/function_app.py`. It POSTs
  `/api/v1/admin/expense-coding/sweep-stranded?apply=true` (API deploys first, else 404s).
- **Remediates ONLY stranded `confirmed` rows** (design D1/D5): `Status='confirmed'`,
  `ConfirmedAt` older than `older_than_seconds` (default 300), and no outbox row of kind
  `recode_purchase_line` in `pending|failed|in_progress` (`done` is NOT live). Per row, under the
  suggestion lock, it re-reads and re-checks status + seq, then `_enqueue_recode_and_mark`
  (`service.py` `sweep_stranded_confirmed`).
- **Only reports** (never fixes): `recovery.error_repoint` (D2: a timer never redrives `error`, a
  deterministic error would loop every tick) and `recovery.written_without_record` (§6).
- **`apply`:** default `false` = dry run (fills `would_enqueue`, status `dry_run`).
  `apply=true` writes. Gate off + apply -> `status=skipped`, `reason` = gate, zero writes.
- **By hand** (secret = the API's `DRAIN_SECRET`; dry-run first):

```bash
curl -s -X POST -H "X-Drain-Secret: $DRAIN_SECRET" \
  "$API_BASE_URL/api/v1/admin/expense-coding/sweep-stranded?older_than_seconds=300&limit=100"
# review would_enqueue, then:
curl -s -X POST -H "X-Drain-Secret: $DRAIN_SECRET" \
  "$API_BASE_URL/api/v1/admin/expense-coding/sweep-stranded?apply=true"
```

  Bounds: `older_than_seconds` 60..86400, `limit` 1..500 (`shared/api/admin.py`).
- **Envelope** (under `result`): `gate` (null or `qbo_writes_disabled`/`recode_writes_disabled`);
  `candidates` rows found; `enqueued` count done; `would_enqueue` ids (dry run);
  `dead_letter_pending` ids skipped because a dead letter exists (§5); `busy` ids whose lock was
  held (next tick retries); `moved` ids that changed status/seq since listing (someone else
  handled it; fine); `errors` `[{id, error: ExceptionClass}]` (inspect API logs);
  `recovery.error_repoint` / `recovery.written_without_record` public ids; `status`
  `ok|dry_run|skipped`. API log: `coding_suggestion.sweep_stranded ...` (WARNING when anything
  non-zero).

## 4. Redrive of an `error` repoint

Who: **system admin only** (`require_system_admin()`); no timer ever calls it.

```bash
curl -s -X POST -H "Authorization: Bearer $ADMIN_JWT" \
  "$API_BASE_URL/api/v1/admin/expense-coding/suggestions/<suggestion-uuid>/redrive-repoint"
```

200 `{"data": {"status": "enqueued", "confirmation_seq": N}}`. Under the lock it re-records the
row's own `RepointTargetsJson` as a new seq (fenced on the seq it read), then enqueues
(`service.py` `redrive_error_repoint`, `shared/api/admin.py`). Audit log
`admin.expense_coding.redrive_repoint`.

Refusals (404 `not_found`; everything else 409 with `detail` = status):

| detail | Meaning |
|---|---|
| `writes_disabled` | recode gate off (§7); fix the gate first |
| `busy` | lock held; retry |
| `not_error` | status is not `error` (already moved on / drained) |
| `not_a_repoint` | first-write `error`: use cockpit confirm instead |
| `no_repoint_targets` | no persisted approval to re-drive; re-run completion |
| `dead_letter_pending` | dead-lettered outbox row exists; replay it (§5) first |
| `moved` | seq/status changed under us (revision lost or mark fenced out); re-read, retry if still `error` |

Next: the drain re-checks the QBO token fence and either writes (`written`) or marks
`changed_in_qbo`. A deterministic cause (e.g. `WriteError` = `repoint_line_holding`,
`repoint_line_unmapped`, `repoint_target_not_ours`) will just `error` again: fix the cause (item
mapping, coding) before redriving. Read `WriteError` first.

## 5. Dead letters

Follow `docs/runbooks/qbo-outbox-dead-letter-replay.md`; use `--kind recode_purchase_line`:

```bash
.venv/bin/python scripts/retry_qbo_outbox_dead_letters.py --kind recode_purchase_line          # dry run
.venv/bin/python scripts/retry_qbo_outbox_dead_letters.py --kind recode_purchase_line --apply
```

Order: (1) read `LastError`, confirm the gates are on; (2) **replay first** (preserves
`RequestId`); (3) only after the row is `done`/`pending`, confirm or redrive again. Sweeper
and redrive report `dead_letter_pending` and write nothing while a dead letter exists
(design D4). Do not clear/replace the dead-letter row by re-confirming.

## 6. Written-without-record (f3b)

A `written` suggestion with `WrittenSyncToken IS NULL AND WrittenLinesJson IS NULL` and
`WrittenAt >= RECORD_REQUIRED_SINCE_UTC` (2026-11-07 22:40 UTC, `shared/api/admin.py`; older
rows legitimately predate the record). Typical cause: a first-write crash after the QBO POST.
New crashes are PREVENTED (the retry emits the record when the live token is exactly snapshot+1
and the CustomerRef matches); rows already stranded are fixed by the admin route. Consequence of
leaving one: a later repoint of those lines is refused `no_written_record`. Completion treats a
NULL record as legacy, so **leaving the row is always safe**.

**Deploy:** SQL first (`AdoptCodingSuggestionWrittenRecord` in `dbo.coding_suggestion.sql`),
then the API image; the route calls the sproc and 500s without it.

**Route (system admin, gates on):** `POST /api/v1/admin/expense-coding/suggestions/{public_id}/adopt-written-record`.
One read-only GET of the live purchase under the suggestion lock; never writes to QBO. 200
`{"data": {status: "adopted", lines, written_sync_token}}`; 404 `not_found`; 422 non-UUID; else
409 with `detail` = one of:

| detail | meaning |
|---|---|
| `writes_disabled` | recode gate off (§7) |
| `busy` | suggestion lock held, retry |
| `not_written` / `has_record` | not a record-less `written` row |
| `not_a_first_write` | source is not `review_submit` / `confirm_endpoint` |
| `legacy` | `WrittenAt` before the cut-off or unknown: cannot tell crash gap from legacy |
| `moved` | `ConfirmationSeq` NULL, or revised since the read |
| `no_confirmed_coding` | `confirm_endpoint` row without a confirmed sub cost code |
| `no_live_token` | the GET returned no `SyncToken` |
| `line_holding` / `line_unmapped` | the code resolves to a holding code / no QBO item |
| `line_missing` | a local line's QBO id is not on the live purchase (deleted) |
| `line_not_recoded` | the live line(s) do not carry the coding (all-or-nothing, nothing recorded) |
| `ambiguous_lines` | more than one live line carries the confirmed pair |

**What is verified.** When the row has a confirmed sub cost code (any first-write source), the
CONFIRMED coding (`confirmed_sub_cost_code_id` / `confirmed_project_id`, what the drain wrote) is
matched first: exactly one live line carries it -> only that line is recorded. `review_submit` with
no match (or no confirmed code) falls to multi-line mode only when it has more than one linked line (a
single-linked-line row refuses like `confirm_endpoint`): each locally coded line is checked against
its own local coding (NULL-coded lines skipped). **Residual:** the adopt cannot recover which lines
were on the placeholder at write time; the confirmed match is preferred because it under-claims at
worst (a later repoint is refused `repoint_target_not_ours`, the same outcome as leaving the row),
while multi-line mode may over-claim pre-categorized lines - the operator must verify EVERY recorded
line was on the placeholder before our write (the purchase pull overwrites the local coding with
whatever QBO holds). When in doubt, leave the row.

**Hand-UPDATE (only if the route refuses and you verified by eye):** operator only; record it in
`SESSION_NOTES.md`. Live GET the Purchase; take its `SyncToken` and, per line **we** coded, the
`Id`, `ItemRef.value` and the local sub cost code id. One row, by PublicId, only while still NULL
and only for the seq you read:

```sql
UPDATE dbo.CodingSuggestion
SET WrittenSyncToken = '<live SyncToken>',
    WrittenLinesJson = '[{"qbo_line_id": "<QBO line Id>", "item_ref_value": "<ItemRef.value>", "sub_cost_code_id": <int>}]'
WHERE PublicId = '<uuid>' AND Status = 'written'
  AND WrittenSyncToken IS NULL AND WrittenLinesJson IS NULL
  AND ConfirmationSeq = <seq you read>;
```

Repeat the GET first to be sure the token has not moved. A wrong record makes the next repoint
write or fence against the wrong lines.

## 7. Gates

`recode_write_gate_reason()` (`integrations/intuit/qbo/base/client.py`): `ALLOW_QBO_WRITES`
(global) AND `ALLOW_EXPENSE_RECODE_WRITES` (default-deny, must be exactly `true`). Reason strings
`qbo_writes_disabled` / `recode_writes_disabled`.

**Never flip the recode gate off to quiet the drain.** Gate off: confirm and redrive refuse
(`writes_disabled`, zero writes), the sweeper does nothing, the drain parks rows (15 min, ERROR)
with no mark, and expense **completions silently lose their recode** (the 2026-07-16 trap).
To stop noise, fix the underlying row or pause the specific work; if you must close the gate,
record it in `SESSION_NOTES.md`, and sweep + replay after reopening.

## 8. Read-only SQL (mirror the two sprocs)

Stranded (`ReadStrandedConfirmedCodingSuggestions`):

```sql
SELECT cs.PublicId, cs.RealmId, cs.ConfirmationSeq, cs.ConfirmationSource, cs.ConfirmedAt,
       CASE WHEN EXISTS (SELECT 1 FROM qbo.Outbox d WHERE d.Kind = N'recode_purchase_line'
              AND d.EntityType = N'CodingSuggestion'
              AND d.EntityPublicId = cs.PublicId AND d.Status = N'dead_letter') THEN 1 ELSE 0 END AS HasDeadLetter
FROM dbo.CodingSuggestion cs
WHERE cs.Status = N'confirmed' AND cs.ConfirmedAt <= DATEADD(SECOND, -300, SYSUTCDATETIME())
  AND NOT EXISTS (SELECT 1 FROM qbo.Outbox o WHERE o.Kind = N'recode_purchase_line'
        AND o.EntityType = N'CodingSuggestion'
        AND o.EntityPublicId = cs.PublicId AND o.Status IN (N'pending', N'failed', N'in_progress'))
ORDER BY cs.ConfirmedAt;
```

Error repoints (`ReadCodingSuggestionRecoveryCandidates`, category `error_repoint`):

```sql
SELECT PublicId, RealmId, ConfirmationSeq, WriteError, ModifiedDatetime
FROM dbo.CodingSuggestion
WHERE Status = N'error' AND ConfirmationSource = N'complete_repoint'
ORDER BY ModifiedDatetime;
```

Written-without-record (category `written_without_record`):

```sql
SELECT PublicId, RealmId, ConfirmationSeq, WrittenAt, WriteError
FROM dbo.CodingSuggestion
WHERE Status = N'written' AND WrittenSyncToken IS NULL AND WrittenLinesJson IS NULL
  AND WrittenAt >= '2026-11-07 22:40:00'   -- RECORD_REQUIRED_SINCE_UTC
ORDER BY ModifiedDatetime;
```

Recode dead letters for one suggestion:

```sql
SELECT Id, PublicId, Status, Attempts, LastError, DeadLetteredAt FROM qbo.Outbox
WHERE Kind = N'recode_purchase_line' AND EntityPublicId = '<uuid>' ORDER BY Id DESC;
```

## Deploy ordering note

SQL first (two read sprocs + the guard), then the API image, then the scheduler; with the QBO
drain paused across the image swap (`docs/design/u551b-f1-repoint-revision-model.md` §7;
`docs/runbooks/deploy-restart-timing.md`).
