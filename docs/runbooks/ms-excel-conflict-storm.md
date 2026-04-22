# Runbook: MS Excel Conflict Storm

Excel workbook writes repeatedly fail because another session (human user or
another API path) is holding the workbook in a way that blocks the worker.

## Symptom

Any of:

- Multiple `ms.outbox.row.retry_scheduled` events for `append_excel_row` or
  `insert_excel_row` kinds.
- `http_status=409` (conflict) or `http_status=423` (locked) on Graph calls
  to `/workbook/...` endpoints.
- User reports: completed a bill, QBO and SharePoint landed, but no row in
  the Excel workbook yet after several minutes.
- `ms.ReconciliationIssue` row with `DriftType='append_excel_row_dead_letter'`
  or `insert_excel_row_dead_letter` and `Severity='critical'`.
- `ms_excel_write:<drive_item_id>` application lock held for >60s without
  clearing.

## Severity

| Condition | Severity | Expected response |
|---|---|---|
| Single workbook affected, <5 rows queued | Warning | Identify the editor; rows drain after they close |
| Single workbook affected, 5-20 rows queued | High | Same-day intervention needed |
| Multiple workbooks affected | Critical | Likely a broader issue (outbox stuck, worker sick) — investigate |
| Excel dead-letter `ReconciliationIssue` created | Critical | Follow recovery to retry after unblocking |

## Background

Two layers of serialization apply to Excel writes:

1. **`sp_getapplock` keyed on `ms_excel_write:<drive_item_id>`** — the
   worker holds this across the read-then-write sequence in
   `append_excel_rows` and `insert_excel_rows`. Prevents two workers (or
   two drain ticks) from targeting the same "next row" simultaneously.
2. **Graph Workbook session** — Microsoft's server-side serialization.
   A workbook session is created on first access and persists for ~30
   minutes. Multiple sessions on the same workbook serialize operations.

These handle worker-vs-worker contention. They do NOT handle the case where
a **human user has the workbook open in Excel Online or Excel Desktop with
pending edits**. A user's edit session can hold a lock that rejects our
Graph `insert` call with a 409 or 423.

Our retry layer absorbs a short-lived 409 (user saves and closes, retry
succeeds). Sustained editing (10+ minutes) can push the row to dead-letter.

## Immediate action

1. **Identify which workbook is affected:**

   ```sql
   SELECT TOP 20
       Id, Kind, EntityType, Attempts, LastError,
       JSON_VALUE(Payload, '$.drive_id')        AS drive_id,
       JSON_VALUE(Payload, '$.item_id')         AS item_id,
       JSON_VALUE(Payload, '$.worksheet_name')  AS worksheet
   FROM ms.Outbox
   WHERE Kind IN ('append_excel_row','insert_excel_row')
     AND Status IN ('failed','dead_letter')
   ORDER BY Id DESC;
   ```

   Group failing rows by `item_id`. One `item_id` with many failures = one
   workbook is the problem.

2. **Map `item_id` back to project + workbook filename** so you know which
   human to ask:

   ```sql
   SELECT p.Name AS project_name, di.Name AS workbook_name, di.WebUrl
   FROM dbo.MsDriveItem di
   LEFT JOIN dbo.ProjectExcel pe ON pe.MsDriveItemId = di.Id
   LEFT JOIN dbo.Project p       ON p.Id = pe.ProjectId
   WHERE di.ItemId = '<item_id_from_step_1>';
   ```

3. **Ask the human to close the workbook.** If someone is actively editing
   the project's Excel sheet in Excel Online or Excel Desktop with pending
   unsaved changes, that's almost certainly the cause. Browser tabs left
   open also count — closing the tab releases the session.

## Diagnosis

### Step 1 — Is the workbook locked right now?

```kusto
traces
| where timestamp > ago(15m)
| where customDimensions.event_name == "ms.http.request.failed"
| where toint(customDimensions.http_status) in (409, 423)
| where tostring(customDimensions.request_path) contains "workbook"
| project timestamp, customDimensions.request_path, customDimensions.http_status, message
| order by timestamp desc
```

A steady stream of 409/423 on the same `request_path` suggests active
edit contention.

### Step 2 — Are our own drain ticks colliding?

This shouldn't happen (APScheduler `max_instances=1`), but verify:

```sql
SELECT resource_description, request_session_id, request_status,
       DATEDIFF(second, request_request_time, GETUTCDATE()) AS held_seconds
FROM sys.dm_tran_locks
WHERE resource_type = 'APPLICATION'
  AND resource_description LIKE '%ms_excel_write%';
```

Held >60s on a single row suggests a drain tick got stuck mid-write. See
Recovery C.

### Step 3 — Is a specific user session the holder?

Graph doesn't expose "who has this workbook open" directly. Practical
substitute: check SharePoint's "Recently modified" and the workbook's
sharing pane for "currently editing" indicators. Or ask the project team.

## Common causes

1. **Human editing the workbook in Excel Online / Desktop** — most common.
   Close the session, workbook unlocks, retries succeed.
2. **Workbook has pending AutoSave that failed** — Excel Online occasionally
   gets stuck trying to save. Closing and reopening the workbook in Excel
   clears the state.
3. **Workbook is locked by a stale Graph session from a prior drain tick**
   — rare. Our `close_workbook_session` is best-effort and if it fails
   silently the session sits for ~30 minutes until Graph auto-expires it.
4. **Concurrent writes from two processes** — should be impossible given
   `sp_getapplock` + APScheduler `max_instances=1`, but if a manual script
   is run against the same workbook mid-drain, they could collide.

## Recovery

### Recovery A — Unblock, then retry dead-letters

Once the editing human has closed the workbook:

```bash
# See what's queued
.venv/bin/python scripts/retry_ms_outbox_dead_letters.py --kind append_excel_row --kind insert_excel_row

# Reset them
.venv/bin/python scripts/retry_ms_outbox_dead_letters.py --kind append_excel_row --kind insert_excel_row --apply
```

Worker picks them up within ~5s. Monitor `ms.Outbox` for transitions from
`pending` → `done`.

### Recovery B — Force a stale Graph session to close

If Step 2 diagnosis confirms no human editor but writes still fail, a
stale Graph session may be holding the workbook. Force-close it:

```bash
.venv/bin/python -c "
from integrations.ms.sharepoint.external.client import close_workbook_session
# Fabricate a session_id that matches the hung one — Graph's session
# endpoint requires it. Easier: wait ~30 minutes for Graph to expire it.
# The close call below is best-effort; if it fails, waiting is the fallback.
"
```

In practice: waiting 30 minutes for Graph's server-side session TTL is the
only reliable unstick. Don't over-engineer — Excel Online sessions are
ephemeral.

### Recovery C — Release a stuck `sp_getapplock`

If Step 2 shows a held `ms_excel_write:<id>` with `held_seconds > 60`:

```sql
-- Identify the session
SELECT request_session_id
FROM sys.dm_tran_locks
WHERE resource_type = 'APPLICATION'
  AND resource_description LIKE '%ms_excel_write%';

-- Kill it (this will rollback any in-progress drain; the row returns
-- to pending and the worker tries again on the next tick)
-- Only do this if the session has been stuck for >2 minutes without progress.
KILL <session_id>;
```

## Verification

1. Query `ms.Outbox` for the affected workbook — rows should drain to
   `done` within a minute of the last editor closing.
2. Open the workbook in Excel Online → verify expected rows are present at
   the correct SubCostCode insertion points.
3. Clear any resolved `ms.ReconciliationIssue` rows manually (lifecycle
   UI is future work; for now run an update):

   ```sql
   UPDATE ms.ReconciliationIssue
   SET Status = 'resolved', ResolvedAt = SYSUTCDATETIME(), ModifiedDatetime = SYSUTCDATETIME()
   WHERE Status = 'open'
     AND DriftType IN ('append_excel_row_dead_letter','insert_excel_row_dead_letter')
     AND DriveItemId = '<the item_id that was affected>';
   ```

## Prevention

- **Editing protocol.** The project workbook should be closed by human
  editors before bill completions run. If your team often completes bills
  while editing, consider a lightweight convention (e.g., nobody edits
  workbooks during the 4 PM billing batch).
- **Monitor dead-letter alerts.** An Excel dead-letter `ReconciliationIssue`
  at `Severity='critical'` should page someone — it represents a bill that
  DIDN'T land in the workbook and the user probably doesn't know yet.
- **Do not run `scripts/sync_*.py` targeting the same workbook while the
  outbox is draining.** Wait for the outbox to quiesce (all rows `done`)
  before manual scripts hit the same item.
