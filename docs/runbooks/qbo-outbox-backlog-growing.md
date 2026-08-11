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

### Recovery C — Unstick an `in_progress` row — **AUTOMATIC since U-215**

**You should not need to do this by hand any more.** The drain worker reclaims stranded
rows itself: every tick, *before* claiming new work and while holding the drain applock,
`QboOutboxWorker._reclaim_stranded_rows()` calls `ReclaimStrandedQboOutbox` and moves any
row that has sat `in_progress` longer than `QBO_OUTBOX_RECLAIM_AFTER_SECONDS` (default
**900s / 15 min**) back to `failed` with `NextRetryAt = now`.

Two properties worth knowing when you are diagnosing:

- **It cannot fire against a live worker.** The reclaim runs inside the same
  `qbo_outbox_drain` applock that a processing worker holds, so a slow-but-healthy row is
  never yanked out from under its handler. The 15-minute age threshold is defence in
  depth on top of that, not the primary guarantee.
- **It counts the attempt.** The reclaim increments `Attempts` and dead-letters at
  `MAX_ATTEMPTS`. That is deliberate: a *poison* row (one whose handler kills the process
  every time) is retried a bounded number of times and then dead-lettered for triage,
  rather than being reclaimed forever in a loop.

Each reclaim emits a `qbo.outbox.row.reclaimed_stranded` warning — one line per row, with
`outbox_public_id`, `entity_type`, `attempts`, `started_at` and the resulting status. If
you are seeing a backlog, **search for that event first**: repeated reclaims of the same
`outbox_public_id` mean the handler is crashing the process, which is a different problem
from a slow QBO.

The reclaim **fails open**: if `ReclaimStrandedQboOutbox` is missing (SQL not yet applied)
or errors, it is logged as `qbo.outbox.reclaim.failed` and the drain continues normally —
behaviour identical to before U-215.

Reach for manual SQL only in the two cases the automatic path deliberately does not cover:
a row whose `StartedAt` is `NULL` (its age is unknowable, so the sproc skips it), or a row
you need unstuck sooner than the threshold.

```sql
UPDATE [qbo].[Outbox]
SET [Status] = 'failed',
    [NextRetryAt] = SYSUTCDATETIME(),
    [LastError] = 'Manual recovery: row was stranded in_progress'
WHERE [Status] = 'in_progress'
  AND ([StartedAt] IS NULL
       OR [StartedAt] < DATEADD(minute, -5, SYSUTCDATETIME()));
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
