# Runbook: MS Graph 503 / 5xx Storm

Cascading Graph API failures where a large fraction of outbound calls return
HTTP 5xx, typically during a Microsoft service incident.

## Symptom

Any of:

- App Insights shows a spike in `ms.http.request.failed` events with
  `http_status` in 500-599.
- `ms.Outbox` backlog grows — rows accumulate in `Status='failed'` faster
  than the worker can retry them.
- Many `ms.retry.exhausted` events followed by `ms.outbox.row.dead_lettered`.
- User-facing: SharePoint uploads and Excel writes don't complete within
  the usual ~10-30s window.
- `ms.ReconciliationIssue` rows accumulate with `DriftType` ending in
  `_dead_letter` (dead-letter escalation hook firing).

## Severity

| Condition | Severity | Expected response |
|---|---|---|
| <5% 5xx error rate over 15 minutes | Informational | Let the retry layer absorb it |
| 5-20% sustained for 15+ minutes | Warning | Monitor; alert if persisting |
| >20% sustained for 10+ minutes | Critical | Check Microsoft service health + follow recovery |
| Dead-letter count > 0 for outbox rows | High | Use retry script post-recovery |

## Background

Microsoft Graph is a shared platform and experiences periodic regional or
service-wide incidents. The retry layer in `MsGraphClient` is designed for
individual transient errors (one-off 502s, rate limits); it is NOT designed
to paper over a sustained outage. When many calls fail in sequence:

1. Each outbox row is retried up to 5 times with exponential backoff.
2. After 5 attempts, the row dead-letters.
3. Excel-kind dead-letters create a `critical` `ms.ReconciliationIssue` so
   operators see them (the elevated-dead-letter decision from Phase 3 Round 0).
4. Upload / sendMail dead-letters create a `high` `ms.ReconciliationIssue`.

So a Graph storm longer than ~5-10 minutes will push recent rows to
dead-letter. That's by design — we'd rather have operator-visible failures
than silent infinite retries.

## Immediate action

1. **Check Microsoft 365 service health** — first, confirm this is a Microsoft
   issue, not ours. Azure portal → **Service Health** → filter to your
   Microsoft 365 subscription. Alternatively:

   - https://admin.microsoft.com/adminportal/home#/servicehealth
   - https://status.office.com/ (public-facing)

   Look for active incidents affecting SharePoint, OneDrive, Graph, or Excel
   Online.

2. **Query current outbox health:**

   ```sql
   SELECT
       Kind,
       Status,
       COUNT(*) AS n,
       MIN(CONVERT(VARCHAR(19), CreatedDatetime, 120)) AS oldest,
       MAX(CONVERT(VARCHAR(19), ModifiedDatetime, 120)) AS most_recent
   FROM ms.Outbox
   WHERE Status IN ('pending','failed','in_progress','dead_letter')
   GROUP BY Kind, Status
   ORDER BY Kind, Status;
   ```

   What you want to see: `in_progress` transitioning to `done` quickly
   (few seconds). What you don't want: steady growth of `failed` count
   with rising `Attempts`.

3. If service health confirms an incident: **wait and monitor**. The retry
   layer will absorb short-lived blips. Don't restart the app or flush
   queues unless directed.

## Diagnosis

### Step 1 — How bad is it?

```kusto
traces
| where timestamp > ago(30m)
| where customDimensions.event_name == "ms.http.request.failed"
| summarize count() by bin(timestamp, 1m), tostring(customDimensions.http_status)
| render timechart
```

Shows error count per minute grouped by status code. A sustained climb in
500-series errors confirms a storm.

### Step 2 — Which endpoints?

```kusto
traces
| where timestamp > ago(30m)
| where customDimensions.event_name == "ms.http.request.failed"
| where toint(customDimensions.http_status) between (500 .. 599)
| summarize count() by tostring(customDimensions.request_path)
| top 20 by count_
```

Concentrated on one path (e.g., all `/drives/.../workbook/...`) suggests a
specific service (Excel Online) is hit. Spread across all paths suggests a
Graph-wide incident.

### Step 3 — Retry exhaustion rate

```kusto
traces
| where timestamp > ago(30m)
| where customDimensions.event_name == "ms.outbox.row.retry_exhausted"
| count
```

Each row here is an outbox entry that ran through all 5 attempts before
dead-lettering. Under normal conditions this should be 0 for any 30-minute
window.

## Common causes

1. **Microsoft service incident** — most likely when Step 1 shows
   widespread 5xx and the Microsoft service health page confirms an issue.
2. **Tenant-level throttling** — Graph will return 429 (not 5xx) for rate
   limits, but sustained throttling can cause backend 503s. Check our
   call volume against tenant limits if no public incident is confirmed.
3. **Azure region outage** — partial regional failure may cause intermittent
   503s even without a named incident.
4. **Our code is hitting a pathological endpoint** — rare, but check if
   Step 2 shows a single path the retry layer can't clear. Possible after
   a deploy that changed Graph usage.

## Recovery

### Recovery A — Wait out the incident

If Microsoft confirms the incident:
1. Leave the scheduler running; retries will resume when Graph recovers.
2. New completions continue to enqueue normally — rows sit in `pending`
   until Graph is healthy.
3. Monitor Step 1 query. When error rate returns to <1%, proceed to
   Recovery B to clear any dead-lettered rows.

### Recovery B — Retry dead-lettered rows post-recovery

Once Graph is healthy, dead-lettered rows need explicit reset. Use:

```bash
# See what would be retried
.venv/bin/python scripts/retry_ms_outbox_dead_letters.py

# Actually retry all
.venv/bin/python scripts/retry_ms_outbox_dead_letters.py --apply

# Or narrow to specific kinds (e.g., only Excel after an Excel-specific outage)
.venv/bin/python scripts/retry_ms_outbox_dead_letters.py --kind append_excel_row --kind insert_excel_row --apply
```

The script resets `Status='pending'`, `Attempts=0`, `NextRetryAt=now`,
`LastError=NULL`. Worker picks them up within ~5s.

### Recovery C — Manually trigger drain (optional)

After retrying, you can force immediate drain ticks (don't wait for the
5-second scheduler cadence):

```bash
.venv/bin/python -c "
from integrations.ms.outbox.business.worker import MsOutboxWorker
processed = MsOutboxWorker().drain_all(max_rows=50)
print(f'Drained {processed} row(s)')
"
```

Useful when you want to sanity-check the recovery ran, or when you need
to clear a backlog faster than the natural 5s cadence.

## Verification

After recovery:

1. Outbox health query (from Immediate Action step 2) should show zero
   `failed` and zero `dead_letter` for recent rows.
2. Graph success event rate back to normal:

   ```kusto
   traces
   | where timestamp > ago(10m)
   | where customDimensions.event_name == "ms.http.request.completed"
   | count
   ```

3. A manual bill completion lands cleanly: PDF in SharePoint, row in Excel
   within ~30s.

## Prevention

- **Don't retry the retry.** If `retry_ms_outbox_dead_letters.py` runs and
  the same rows dead-letter again, the underlying issue isn't resolved.
  Investigate before running the script a third time.
- **Watch for alert fatigue.** If Graph has a weekly 503 hiccup, tune the
  5% / 20% thresholds to match reality rather than normalizing high noise.
- **Don't bypass retries.** Tempting to "just run the sync scripts manually"
  during a storm — this floods Graph with additional requests and can
  extend the recovery window. Let the exponential backoff do its job.
