# Runbook: Box Excel Conflict Storm

Box-hosted DETAILS-tab workbook updates (`update_box_excel` outbox rows)
repeatedly defer or fail because the workbook is being co-edited by a human in
Excel-for-web (a WOPI lock), the file version keeps changing underneath us
(412 storms), or our own Box lock / app-lock is stuck.

Unlike the MS Graph Excel path (which edits cells via a live Graph workbook
session), the Box path **downloads the whole `.xlsx`, edits the DETAILS sheet
with openpyxl, and uploads a NEW VERSION**. All read / idempotency / insertion
/ write happens in the drain handler, serialized by both `box_app_lock` and a
real Box file lock.

## Symptom

Any of:

- Repeated `box.outbox.excel.deferred_locked` log events for one `box_file_id` —
  our handler keeps backing off because a human holds a WOPI (Office-for-web)
  co-edit lock.
- Multiple `box.Outbox` retries for `Kind='update_box_excel'` on the same
  `box_file_id`.
- `http_status=412` (precondition failed) on `PUT files/{id}` (lock) or
  `upload_file_version` — the file version (etag) keeps changing faster than we
  can apply our edit (a "412 storm").
- `http_status=403` / `BoxLockedError` on the file when a WOPI session is live.
- User reports: completed a bill / expense / credit, QBO and the Box SharePoint
  upload landed, but the new rows haven't appeared on the project workbook's
  DETAILS tab after several minutes.
- A `box` `ReconciliationIssue` row with a `update_box_excel` dead-letter
  `DriftType` and `Severity='critical'`.
- `box_file_write:<box_file_id>` application lock held without clearing.
- `box.outbox.excel.stamp_lost_no_match` warnings: an Invoice completed and
  tried to stamp the DRAW REQUEST column, but the workbook didn't have any
  matching col-Z rows — usually because the source Bill/Expense Box outbox
  rows hadn't drained yet. Run `scripts/backfill_box_workbook.py --project <pid>`
  to seed the missing source rows, then re-complete the invoice to re-fire
  the stamp.

## Severity

| Condition | Severity | Expected response |
|---|---|---|
| Single workbook, human co-editing, <5 entities queued | Warning | Ask them to close; rows drain on next retry after the WOPI lock clears |
| Single workbook, 5-20 entities queued, deferring all day | High | Same-day intervention — the human is leaving the sheet open past policy |
| 412 storm — rapid version churn on one file | High | Find what else is writing the file (another app, a sync client) |
| Multiple workbooks affected | Critical | Likely broader (Box outbox stuck, worker sick, write-gate flip) — investigate the drain, not the file |
| `update_box_excel` dead-letter `ReconciliationIssue` created | Critical | A completed entity did NOT land on DETAILS and the user likely doesn't know — follow recovery |

## Background

Three layers of serialization apply to a Box DETAILS write:

1. **`box_app_lock` keyed on `box_file_write:<box_file_id>`** — the handler
   holds this across the read-download-edit-upload sequence. Prevents two
   workers (or two drain ticks) from racing the same file version. If the lock
   is busy the row requeues (the handler raises a retryable "lock busy"; it does
   NOT mark done).
2. **A real Box file lock** — the handler takes a Box lock
   (`PUT files/{id}` with `lock.access=lock`, `expires_at = now+5min`,
   `If-Match: etag0`) before downloading and uploads the new version under the
   etag the lock response returned (`etag1`, NOT `etag0` — taking a lock bumps
   the etag). Released best-effort in `finally` (`lock: null`).
3. **Box WOPI co-edit lock** — when a human opens the file in Excel-for-web,
   Box reports a `lock` with `app_type` starting `office_wopi`. We **don't fight
   it**: if a live (non-expired) WOPI lock is present, the handler raises
   `BoxLockedError` (retryable contention) and logs
   `box.outbox.excel.deferred_locked`. The row requeues; we wait for the human
   to close.

Layers 1 and 2 handle worker-vs-worker and our-own-version-churn contention.
Layer 3 is the **human co-edit** case — the most common storm.

### Idempotency — column Z

Every DETAILS data row carries the line item's `public_id` in **column Z**
(index 25). Before inserting, the handler reads existing column-Z values and
skips any line item already present. If **all** rows for the entity are already
present, `apply_rows_to_details` returns `bytes=None` and the handler skips the
upload entirely — a **no-op**. This is what makes retries safe:

- A retry after a successful-but-uncommitted upload re-downloads the now-updated
  file, finds the keys present, and no-ops (closes the
  crash-after-upload-before-mark_done window).
- A retry after a deferred WOPI lock just re-applies; already-present keys are
  skipped, so a partially-applied edit can't double-write.

A no-op upload is the **expected steady state** for any row that already
succeeded — `applied=0, skipped=N` in the handler log is healthy, not an error.

### v1 tradeoff — one version per entity

v1 enqueues one `update_box_excel` row per completed entity per workbook (no
Policy-C coalesce). Each successful drain produces one new Box file version.
A busy project completing many entities will churn many versions. This is a
documented, accepted tradeoff; col-Z idempotency keeps re-runs safe.
Batch-apply-per-workbook is a future optimization — if version churn becomes a
problem, that's the fix, not disabling the feature.

## Immediate action

1. **Identify which workbook is affected:**

   ```sql
   SELECT TOP 20
       Id, Kind, Status, Attempts, NextRetryAt, LastError,
       JSON_VALUE(Payload, '$.box_file_id')      AS box_file_id,
       JSON_VALUE(Payload, '$.worksheet_name')   AS worksheet,
       JSON_VALUE(Payload, '$.entity_type')      AS entity_type,
       JSON_VALUE(Payload, '$.entity_public_id') AS entity_public_id,
       JSON_VALUE(Payload, '$.operation')        AS op
   FROM box.Outbox
   WHERE Kind = 'update_box_excel'
     AND Status IN ('pending','failed','dead_letter')
   ORDER BY Id DESC;
   ```

   `op` is the operation discriminator inside the payload — `insert` (default
   when missing) seeds DETAILS rows for a completed Bill/Expense/BillCredit;
   `stamp_draw_request` writes the DRAW REQUEST column (H) on existing rows
   when an Invoice completes. Either path can show up in this query.

   Group by `box_file_id`. One `box_file_id` with many deferring/failing rows =
   one workbook is the problem.

2. **Map `box_file_id` back to project + workbook so you know who to ask:**

   ```sql
   SELECT p.Name AS project_name, pw.WorksheetName, pw.BoxFileId, pw.PublicId
   FROM box.ProjectWorkbook pw
   JOIN dbo.Project p ON p.Id = pw.ProjectId
   WHERE pw.BoxFileId = '<box_file_id_from_step_1>';
   ```

3. **Ask the human to close the workbook.** A live `office_wopi` lock means
   someone has the sheet open in Excel-for-web (or a browser tab left open
   counts). Closing the tab releases the WOPI lock and our next retry applies.
   Per the project's **daily-close policy**, workbooks should be closed at end
   of day — a sheet open for days is a policy violation that will eventually
   retry-exhaust to dead-letter.

## Diagnosis

### Step 1 — Is a human co-editing right now? (most common)

```kusto
traces
| where timestamp > ago(30m)
| where customDimensions.event_name == "box.outbox.excel.deferred_locked"
| project timestamp, customDimensions.box_file_id, message
| order by timestamp desc
```

A steady stream of `deferred_locked` on one `box_file_id` = a live WOPI
co-edit session. This is **contention, not a bug** — we deliberately back off.

Confirm directly against Box:

```bash
.venv/bin/python -c "
from integrations.box.base.client import BoxHttpClient
c = BoxHttpClient()
m = c.get('files/<box_file_id>', params={'fields':'etag,lock,name'})
print('etag', m.get('etag'))
print('lock', m.get('lock'))
"
```

If `lock.app_type` starts with `office_wopi` and `expires_at` is null or in the
future, a human is in the sheet. Nothing to fix on our side — wait for them to
close.

### Step 2 — Is the version churning (412 storm)?

```kusto
traces
| where timestamp > ago(30m)
| where customDimensions.http_status == "412"
| where tostring(customDimensions.request_path) contains "files/<box_file_id>"
| project timestamp, customDimensions.request_path, message
| order by timestamp desc
```

Sustained 412s mean the file version changes between our `get` and our
`upload_file_version` (or between lock and upload). Each 412 is retryable —
the handler refetches and reapplies (cheap, the edit is DB-derived). A *storm*
of them means something ELSE is writing the file rapidly — another Box app, a
Box Drive sync client, or a human force-saving in a tight loop. Find and stop
the other writer.

### Step 3 — Are our own drain ticks / app-lock stuck?

```sql
SELECT resource_description, request_session_id, request_status,
       DATEDIFF(second, request_request_time, GETUTCDATE()) AS held_seconds
FROM sys.dm_tran_locks
WHERE resource_type = 'APPLICATION'
  AND resource_description LIKE '%box_file_write%';
```

Held >60s on a single file suggests a drain tick stuck mid-edit (e.g. a hung
download). See Recovery C.

### Step 4 — Did the entity actually have rows to write?

Pull the handler log for the row. `applied=0, skipped=N` is a **healthy no-op**
(col-Z keys already present) — not a failure. `applied>0` with no new Box
version means the upload step failed; check the next log line for the upload
error.

## Common causes

1. **Human editing the workbook in Excel-for-web** — most common. A live WOPI
   lock; we defer (`box.outbox.excel.deferred_locked`). Close the session and
   the next retry lands.
2. **Sheet left open past the daily-close policy** — the deferral budget is
   workday-scale, but a sheet open for *days* will retry-exhaust and
   dead-letter. The dead-letter is intentional and visible (a
   `ReconciliationIssue`, not a silent drop).
3. **412 storm from another writer** — a second Box app, Box Drive sync, or a
   rapid human save loop changing the file version faster than we can apply.
4. **Stuck `box_app_lock`** — rare; a drain tick hung mid-download holding
   `box_file_write:<id>`. New rows requeue (lock busy) until it clears.
5. **Write gate flipped off** — if `ALLOW_BOX_WRITES` is no longer `true`,
   `enqueue_box_excel` refuses (returns None + warns) and nothing is ever
   queued. Check the App Service setting before chasing a "missing rows" report.

## Recovery

### Recovery A — Unblock the human, then let retries drain

Once the editing human has closed the workbook (WOPI lock released):

- Deferred rows are still `pending`/`failed` with a future `NextRetryAt`. They
  drain on their own once the lock is gone — no action needed beyond confirming.
- If you want them to drain immediately rather than waiting out the backoff,
  reset `NextRetryAt`:

  ```sql
  UPDATE box.Outbox
  SET NextRetryAt = SYSUTCDATETIME()
  WHERE Kind = 'update_box_excel'
    AND Status IN ('pending','failed')
    AND JSON_VALUE(Payload, '$.box_file_id') = '<box_file_id>';
  ```

  The Box drain timer (`POST /api/v1/admin/box/drain`, ~30s) picks them up.
  Monitor `box.Outbox` for transitions to `done`.

### Recovery B — Retry dead-letters after the cause is cleared

If rows already dead-lettered (workday-scale horizon exhausted):

```bash
# Inspect what's dead-lettered (dry run by default)
.venv/bin/python scripts/retry_box_outbox_dead_letters.py --kind update_box_excel

# Reset them back to pending
.venv/bin/python scripts/retry_box_outbox_dead_letters.py --kind update_box_excel --apply
```

The drain picks them up within ~30s. Because of col-Z idempotency, replaying a
dead-letter that *had* actually uploaded before failing on bookkeeping is a safe
no-op (keys already present → `bytes=None` → upload skipped).

### Recovery C — Release a stuck `box_app_lock`

If Step 3 shows `box_file_write:<id>` held with `held_seconds > 60`:

```sql
-- Identify the session
SELECT request_session_id
FROM sys.dm_tran_locks
WHERE resource_type = 'APPLICATION'
  AND resource_description LIKE '%box_file_write%';

-- Kill it (rolls back the in-progress drain; the row returns to pending and
-- the worker retries on the next tick). Only do this if stuck >2 min.
KILL <session_id>;
```

### Recovery D — Force-release a stuck Box file lock

If our own Box lock is stuck (we took it, then the process died before the
`finally` unlock), the file shows our service-account lock and new versions
can't upload. Clear it:

```bash
.venv/bin/python -c "
from integrations.box.base.client import BoxHttpClient
c = BoxHttpClient()
c.put('files/<box_file_id>', json_body={'lock': None})
print('unlocked')
"
```

This is the same best-effort unlock the handler does in `finally`; running it by
hand clears a lock left behind by a crashed drain. (A human's WOPI lock is NOT
clearable this way — only the human closing the sheet releases that.)

## Verification

1. Query `box.Outbox` for the affected `box_file_id` — rows should reach
   `done` within a minute once the WOPI lock / other writer is gone.
2. Open the workbook's DETAILS tab in Box → verify the expected rows landed at
   the correct SubCostCode insertion points, and the **summary/formula tabs
   still recalculate** (the editor sets `fullCalcOnLoad` so Excel recomputes on
   next open — confirm the summary totals look right, not stale).
3. Spot-check that column Z on the new rows holds the line-item public_ids
   (the reconciliation key) and there are **no duplicate** rows for the same
   public_id (idempotency held).
4. Clear any resolved `box` `ReconciliationIssue` rows:

   ```sql
   UPDATE box.ReconciliationIssue
   SET Status = 'resolved', ResolvedAt = SYSUTCDATETIME(), ModifiedDatetime = SYSUTCDATETIME()
   WHERE Status = 'open'
     AND DriftType = 'update_box_excel_dead_letter'
     AND BoxFileId = '<box_file_id that was affected>';
   ```

## Prevention

- **Daily-close policy.** The single biggest lever: project workbooks must be
  closed in Excel-for-web at end of day. A closed sheet has no WOPI lock, so
  `update_box_excel` rows apply on the first try. The deferral budget is sized
  for a workday, not a multi-day open session.
- **Don't co-edit during a completion batch.** If your team often completes
  bills/expenses while editing, agree on a window where nobody has the project
  workbook open (e.g. the afternoon billing run).
- **One writer.** Don't point a second Box app or a Box Drive sync client at the
  project workbooks — that's the usual source of a 412 storm.
- **WOPI co-edit is deferred, not abandoned.** v1 backs off and retries on
  standard timing while logging `box.outbox.excel.deferred_locked`; it only
  escalates to dead-letter + `ReconciliationIssue` after the workday-scale
  horizon. True WOPI co-edit awareness (e.g. a longer dedicated backoff so a
  human keeping the sheet open never burns the 5-attempt budget) is a planned
  refinement — see TODO. Until then, the daily-close policy is the contract.
- **Monitor dead-letter alerts.** A `update_box_excel` dead-letter
  `ReconciliationIssue` at `Severity='critical'` should page someone — it means
  a completed entity did NOT land on the DETAILS tab and the user probably
  doesn't know.
- **Trust the no-op.** `applied=0, skipped=N` handler logs are the idempotency
  layer working as designed — don't "fix" them by clearing col-Z, which would
  cause duplicate rows on the next drain.
