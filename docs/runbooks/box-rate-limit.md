# Runbook: Box Rate Limit (429) Storm

Box throttles **per user**: roughly **1,000 API calls per minute** general
traffic and **240 uploads per minute** per user. Our CCG service account is a
single user, so *all* build.one traffic shares one bucket — a bulk backfill
or a busy completion burst can hit the upload limit well before the general
one.

## Symptom

- App Insights: spike in Box request failures with `http_status == 429`
  (`BoxRateLimitError` in logs / `LastError` on outbox rows).
- `box.Outbox` rows cycling `pending → in_progress → failed` with rising
  `Attempts` and 429 text in `LastError`.
- Uploads land, but slowly — backlog age grows during the storm and recovers
  afterwards.

## Severity

| Condition | Severity | Expected response |
|---|---|---|
| Occasional 429s, backlog age < 5 min | Informational | The retry layer absorbs it; do nothing |
| Sustained 429s 15+ min, backlog growing | Warning | Find the volume source |
| Rows dead-lettering with 429 in LastError | High | A storm outlasted 5 attempts — stop the source, then retry dead letters |

## Background

Defense in depth, in order:

1. **Retry-After honored.** The shared `BoxHttpClient` retry layer backs off
   with jitter and honors Box's `Retry-After` header on 429 (and 503).
2. **Outbox absorbs.** If retries exhaust within one drain pass, the row is
   marked `failed` with `NextRetryAt` pushed out by exponential backoff —
   the same backoff math as the MS worker. Nothing is lost.
3. **Drain is naturally throttled.** Each drain call processes at most
   20 rows / 20s; the 5s cadence caps steady-state throughput well below
   Box's general limit. Storms come from *bursts* (bulk scripts, backfills),
   not the steady drain.
4. After `MAX_ATTEMPTS = 5` total attempts a row dead-letters and flags a
   `[box].[ReconciliationIssue]` (critical for upload kinds) so it isn't
   silent.

## Diagnosis

### Step 1 — Confirm it's 429s, not something else

```kusto
traces
| where timestamp > ago(30m)
| where customDimensions.event_name startswith "box."
| where tostring(customDimensions.http_status) == "429"
| summarize count() by bin(timestamp, 1m)
| render timechart
```

### Step 2 — Outbox health

```sql
SELECT Status, COUNT(*) AS n,
       MIN(CONVERT(VARCHAR(19), CreatedDatetime, 120)) AS oldest,
       MAX(Attempts) AS max_attempts
FROM box.Outbox
WHERE Status IN ('pending','failed','in_progress','dead_letter')
GROUP BY Status;
```

### Step 3 — What's generating the volume?

```sql
SELECT TOP 20 Kind, EntityType, COUNT(*) AS n
FROM box.Outbox
WHERE CreatedDatetime > DATEADD(HOUR, -1, SYSUTCDATETIME())
GROUP BY Kind, EntityType
ORDER BY n DESC;
```

A flood of rows from one entity type usually means a bulk script or a
backfill, not organic completion traffic.

## Common causes

1. **Bulk backfill / migration script** pushing hundreds of files — exceeds
   240 uploads/min on the single service-account bucket.
2. **Completion burst** (e.g., contract-labor bill generation creating many
   bills at once) — usually self-resolves within minutes.
3. **A retry loop gone wrong** — same row(s) hammering Box without making
   progress; check for one `Id` with rapidly climbing `Attempts`.

## Recovery

### Recovery A — Let it absorb (default)

Do nothing. Retry-After + outbox backoff drain the burst within minutes.
Watch the Step 2 query trend down.

### Recovery B — Stop the source

If a bulk script caused it: kill the script. Optionally pause draining
entirely so we stop spending budget while the bucket refills:

```
# Azure Portal → App Service → Configuration → Application settings
PAUSE_BOX_DRAIN = true   # + restart; remove + restart to resume
```

Rows accumulate harmlessly as `pending` while paused.

### Recovery C — Dead letters after the storm

```bash
# Inspect first (dry-run)
.venv/bin/python scripts/retry_box_outbox_dead_letters.py

# Reset all (or filter)
.venv/bin/python scripts/retry_box_outbox_dead_letters.py --apply
.venv/bin/python scripts/retry_box_outbox_dead_letters.py --kind upload_box_file --apply
```

## Verification

1. 429 count (Step 1 query) back to zero.
2. Outbox: `pending`/`failed` → 0, `done` increasing, no `dead_letter` rows.
3. Spot-check one recently completed bill's attachment is present in the
   project's Box folder.

## Prevention

- Run bulk pushes through the outbox (never direct client loops) so the
  drain's 20-rows-per-pass throttle applies, and schedule them off-hours.
- Don't shrink `ready_after_seconds` / drain cadence to "speed things up"
  during a backlog — that converts a queue into a 429 storm.
- If sustained organic volume ever approaches the per-user limits, the fix
  is architectural (multiple app users / Box service accounts), not retries.
