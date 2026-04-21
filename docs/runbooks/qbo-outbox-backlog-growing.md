# Runbook: QBO Outbox Backlog Growing

The outbox holds rows faster than the worker is draining them. QBO writes are
delayed and may eventually stop completing.

## Symptom

- App Insights alert: `qbo.outbox.backlog.size > 10` sustained over 10+ minutes, OR
- `qbo.outbox.oldest_pending.age_seconds > 600` (oldest pending row older than 10 min), OR
- Users report bills finalized in the app are "stuck" and not appearing in QBO.

## Severity

| Condition | Severity | Response |
|---|---|---|
| Backlog >10 rows, age <10 min | Warning | Investigate within the hour |
| Backlog >10 rows, oldest >10 min | Critical | Investigate immediately |
| Backlog growing unbounded | Critical | Investigate immediately |

## Diagnosis

### Step 1 — Confirm backlog state

```sql
SELECT [Status], COUNT(*) AS [Count],
       MIN([CreatedDatetime]) AS oldest,
       MAX([CreatedDatetime]) AS newest
FROM [qbo].[Outbox]
WHERE [Status] IN ('pending', 'failed', 'in_progress', 'dead_letter')
GROUP BY [Status];
```

- `pending` > 0 and growing → worker isn't draining.
- `in_progress` > 1 → multiple workers? Shouldn't happen; check `sp_getapplock` behavior.
- `in_progress` stuck for hours → a worker crashed mid-process; row is stranded.

### Step 2 — Check the scheduler

```kusto
traces
| where timestamp > ago(30m)
| where message contains "Scheduler started"
    or customDimensions.event_name startswith "qbo.outbox"
| order by timestamp desc
```

Look for:
- `Scheduler started` at the app's boot time — confirms the scheduler is running.
- Periodic `qbo.outbox.row.drained` / `qbo.outbox.row.completed` events — if absent, worker not running.
- `qbo.outbox.drain.tick_failed` — errors in the drain wrapper.

### Step 3 — Inspect the oldest pending row

```sql
SELECT TOP 3 [Id], [Kind], [EntityType], [EntityPublicId], [Status],
             [Attempts], [LastError],
             [CreatedDatetime], [NextRetryAt], [ReadyAfter]
FROM [qbo].[Outbox]
WHERE [Status] IN ('pending', 'failed')
ORDER BY [CreatedDatetime];
```

- Consistently hitting `Attempts = max` for one specific row? That row is poison — move it to dead_letter manually (below) to unblock the queue.
- Old `NextRetryAt` or `ReadyAfter` still in the future? Expected — don't force.

### Step 4 — Check whether `ENABLE_SCHEDULER` is actually set

```bash
# In Azure Portal: App Service → Configuration → Application settings
# Confirm ENABLE_SCHEDULER = true
```

If it was recently removed or changed, the scheduler isn't running — the outbox fills indefinitely.

## Common causes

1. **Scheduler didn't start.** `ENABLE_SCHEDULER` unset/false, or App Service restart crashed. Check Step 4.
2. **A specific row is poisoning the queue.** One row fails repeatedly, but `sp_getapplock` + `READPAST` should let other rows drain. Verify other rows ARE moving.
3. **The QBO API is degraded.** All rows slow to drain because every call times out. Check `qbo.http.request.failed` events.
4. **Worker crashed mid-process.** Row stuck in `in_progress` indefinitely. `sp_getapplock` is session-scoped so it released, but the row status wasn't updated.

## Recovery

### Recovery A — Scheduler never started

1. Confirm `ENABLE_SCHEDULER=true` in App Service Application Settings.
2. Restart App Service. Check logs for `Scheduler started` line.

### Recovery B — Poison row blocking a specific entity

Manually mark it dead_letter so a human can triage later and the rest drains:

```sql
UPDATE [qbo].[Outbox]
SET [Status] = 'dead_letter',
    [DeadLetteredAt] = SYSUTCDATETIME(),
    [LastError] = 'Manual dead-letter: blocking backlog, see runbook'
WHERE [Id] = <the_stuck_id>;
```

### Recovery C — Unstick an `in_progress` row

If a row has been in `in_progress` for >5 minutes it's almost certainly stranded:

```sql
UPDATE [qbo].[Outbox]
SET [Status] = 'failed',
    [NextRetryAt] = SYSUTCDATETIME(),
    [LastError] = 'Manual recovery: row was stranded in_progress'
WHERE [Status] = 'in_progress'
  AND [StartedAt] < DATEADD(minute, -5, SYSUTCDATETIME());
```

Next worker tick picks it up.

### Recovery D — QBO API is slow/down

Not much you can do at the app layer. Verify at https://status.developer.intuit.com.
If confirmed: wait, the worker retries automatically with backoff. Monitor
the backlog; once QBO recovers, the queue drains.

## Verification

After recovery:

```sql
SELECT [Status], COUNT(*) AS [Count]
FROM [qbo].[Outbox]
GROUP BY [Status];
```

Expected: `pending` count decreasing; `done` count increasing.

## Prevention

- Alert on backlog BEFORE it's a problem (warning at >10 rows, not only critical).
- Monitor dead-letter count — growing dead_letter means a persistent problem
  that retries can't solve.
- Build a simple review UI for dead_letter rows so poison messages get
  triaged within the week, not after users complain.
