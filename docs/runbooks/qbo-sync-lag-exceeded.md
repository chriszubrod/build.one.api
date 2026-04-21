# Runbook: QBO Sync Lag Exceeded

The local mirror of QBO data is stale. Our view of bills/invoices/etc.
lags QBO's current state by too much.

## Symptom

- App Insights alert: `qbo.sync.lag_seconds > 7200` (2 hours) for any
  transactional entity (warning), or `> 21600` (6 hours) (critical).
- UI shows data that doesn't match what accountants see in QuickBooks.
- Reconciliation or invoice review flags "missing" records that are
  actually in QBO.

## Severity

| Condition | Severity | Response |
|---|---|---|
| Lag > 2h on transactional entity | Warning | Investigate within hours |
| Lag > 6h on transactional entity | Critical | Investigate immediately |
| Lag > 24h on reference entity | Warning | Investigate during business hours |

## Diagnosis

### Step 1 — Confirm lag per entity

```sql
SELECT [Entity], [LastSyncDatetime],
       DATEDIFF(minute, [LastSyncDatetime], SYSUTCDATETIME()) AS minutes_since_last_sync
FROM [dbo].[Sync]
WHERE [Provider] = 'qbo'
ORDER BY minutes_since_last_sync DESC;
```

Baseline:
- Transactional (bill, invoice, purchase, vendorcredit) should be <15 min.
- Reference (vendor, customer, item, account, term) should be <4 hours.
- CompanyInfo should be <24 hours.

### Step 2 — Check scheduler health

```kusto
traces
| where timestamp > ago(1h)
| where customDimensions.event_name == "qbo.sync.pull.started"
| project timestamp, customDimensions.entity_type
| order by timestamp desc
```

Expected: regular heartbeat for each entity at its configured cadence.
Missing entities = their job isn't firing or failing silently.

### Step 3 — Check for recent failures

```kusto
traces
| where timestamp > ago(2h)
| where customDimensions.event_name == "qbo.sync.pull.failed"
| project timestamp, customDimensions.entity_type, customDimensions.error_class, message
| order by timestamp desc
```

If failures are present, the sync job ran but errored. The log message shows
the cause (auth, transport, rate limit, etc.).

### Step 4 — Check QBO API availability

```kusto
requests
| where timestamp > ago(1h)
| where url contains "quickbooks.api.intuit.com"
| summarize success_rate = countif(success == true) * 1.0 / count(),
            median_duration = percentile(duration, 50)
            by bin(timestamp, 5m)
| order by timestamp desc
```

- `success_rate` sustained <95% → QBO itself is degraded or your credentials
  are failing.
- `median_duration` > 5000ms → QBO is slow; lag is a downstream symptom.

## Common causes

1. **Scheduler not running.** `ENABLE_SCHEDULER` unset on App Service, or App Service restarted without completing startup. Check Step 2 heartbeat.
2. **All syncs failing with auth errors.** Refresh token expired or rotating. See `qbo-token-expiration.md`.
3. **A specific entity's sync is looping on an error.** One entity has zero heartbeat for hours while others are fine.
4. **QBO is slow/down.** All syncs take forever or time out. `qbo.http.request.failed` logs with `outcome='timeout'`.
5. **Rate-limited.** 429s from QBO. Logs show `QboRateLimitError`.

## Recovery

### Recovery A — Scheduler isn't running

See `qbo-outbox-backlog-growing.md` → Recovery A. Same fix.

### Recovery B — All syncs failing auth

Token expired or needs refresh. See `qbo-token-expiration.md`.

### Recovery C — One entity looping on error

Identify the entity from Step 3. Run its script manually to see the full
error:

```bash
.venv/bin/python scripts/sync_qbo_<entity>.py
```

Fix the root cause based on the error. Typical patterns:
- Missing mapping (e.g., Item not mapped to SubCostCode) — create the mapping.
- Schema change in QBO that our Pydantic model doesn't tolerate — update the schema.
- Pagination infinite loop — bail early and reload sync state.

### Recovery D — QBO is slow/down

Nothing at the app layer. Wait. Once QBO recovers, the next scheduled fire
catches up.

### Recovery E — Force immediate catch-up

Run all syncs manually to reset lag:

```bash
.venv/bin/python scripts/sync_qbo_bill.py
.venv/bin/python scripts/sync_qbo_invoice.py
.venv/bin/python scripts/sync_qbo_purchase.py
.venv/bin/python scripts/sync_qbo_vendorcredit.py
```

After successful manual runs, the `last_sync_datetime` watermarks advance
and the next scheduled tick resumes normal delta sync.

## Verification

Re-run Step 1. `minutes_since_last_sync` should be small (< the entity's
interval + a few minutes).

## Prevention

- Alert at 2h (warning), not 6h (critical). 2h catches most issues before
  users notice.
- Daily reconciliation (task #16) is the ultimate backstop: even if delta
  sync quietly misses records, the reconciliation sweep catches them.
- Keep the `last_sync_datetime` watermark per entity — never share one
  across entities.
