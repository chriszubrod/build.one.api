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

### Step 5 — Rule out a DELIBERATE hold (U-217)

**Check this before treating a stalled watermark as a failure.** Since U-217 the
watermark is *held on purpose* when any record in the run failed to persist, so
`LastSyncDatetime` standing still can be the system working correctly — it is
refusing to skip past a record it could not save.

```kusto
traces
| where timestamp > ago(6h)
| where message has "Holding sync watermark"
| project timestamp, message
| order by timestamp desc
```

The message names which tier failed and the QBO ids:

- **`staging failed: <ids>`** — the record never reached the `qbo.*` staging
  tables at all.
- **`projection failed: <ids>`** — it reached staging but never became a
  `dbo.*` Bill / Expense / BillCredit / Vendor / …

Every pull script's result envelope also carries a `watermark` key with
`fetched` / `synced` / `failed_count` / `staging_failed_ids` /
`projection_failed_ids` / `skipped_count` / `skipped_ids`.

**Held vs. broken:**

| Observation | Meaning |
|---|---|
| Hold warning present, ids change each tick | Working as designed — transient failures, retrying |
| Hold warning present, **same ids every tick** | **Wedged.** Go to Recovery F |
| No hold warning, watermark still stalled | A real failure — continue with Steps 2–4 |

`skipped_ids` are *permanent* data issues (e.g. an unmapped vendor). They are
reported but deliberately do **not** hold the watermark, because they would
never self-resolve and would wedge the entity forever.

## Common causes

0. **A deliberate hold that has wedged** (U-217). One record fails the same way
   every tick, so the watermark never advances and lag grows without bound. This
   is the trade the hold makes: never silently skip a record, at the cost of a
   stall that needs a human. Identified by the same ids repeating in Step 5.
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

### Recovery F — A held watermark has wedged (U-217)

Step 5 shows the **same** ids held every tick. The entity will not advance until
that record persists or is excluded. Do **not** "fix" this by hand-advancing
`dbo.Sync` — that is exactly the silent skip the hold exists to prevent, and the
record would be lost until someone edits it in QBO again.

1. Run the entity's script manually to see the full error for the held id:
   ```bash
   .venv/bin/python scripts/sync_qbo_<entity>.py
   ```
2. Fix the root cause (missing mapping, schema drift, a payload that violates a
   column constraint such as an over-long `PrivateNote`).
3. Re-run. The watermark advances on the first clean run and the backlog in the
   held window is re-pulled automatically — upserts are idempotent.

If the record is genuinely unprocessable and must be abandoned, make the failure
*permanent* rather than transient so it is classified as a skip (which reports
but does not hold) — do not bump the watermark past it.

**Tuning the overlap.** Each run re-pulls a small window before its start time so
a QBO edit landing mid-run is not missed. `QBO_SYNC_WATERMARK_OVERLAP_SECONDS`
(default `60`) controls it; invalid or negative values fall back to the default.
Raise it if edits are being missed, lower it if the re-pull is costing too much
QBO API budget (see `qbo-api-budget-breaker.md`).

## Verification

Re-run Step 1. `minutes_since_last_sync` should be small (< the entity's
interval + a few minutes).

If a hold was the cause, also confirm the hold warning from Step 5 has stopped
and the envelope's `failed_count` is back to `0`.

## Prevention

- Alert at 2h (warning), not 6h (critical). 2h catches most issues before
  users notice.
- Daily reconciliation (task #16) is the ultimate backstop: even if delta
  sync quietly misses records, the reconciliation sweep catches them.
- Keep the `last_sync_datetime` watermark per entity — never share one
  across entities.
