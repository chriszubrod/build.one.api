# QBO API budget breaker tripped (monthly CorePlus cap protection)

## Symptom

- Logs show `qbo.budget.threshold_crossed` with `band=blocked` (ERROR) or `band=warn` (WARNING).
- Outbox drain ticks log `qbo.outbox.drain.skipped_budget_blocked` and process nothing; QBO outbox rows accumulate as `pending` (or `failed` with `LastError LIKE 'Parked:%'` and `NextRetryAt` on the 1st of next month).
- QBO calls fail with `QboBudgetExceededError` ("QBO call refused: month-to-date API usage … crossed the block threshold …").
- Distinct from Intuit's own block: Intuit returns `429 ThrottleExceeded` (errorCode 003001) — see the July 2026 incident in `project_qbo_reconcile_firehose_monthly_cap`. The breaker exists to refuse calls locally *before* Intuit hard-blocks the app.

## Severity

Medium. QBO sync is paused by design; no data is lost. Pulls resume on the next tick after the block lifts (watermarks hold); outbox pushes resume when parked/pending rows become claimable. This is the system working — the alternative was July 2026's five-day full block with dead-lettered work.

## How it works (U-211)

- Every HTTP round-trip to the QBO API increments `[qbo].[ApiUsage]` (one row per realm+month, sproc `IncrementQboApiUsage`, `SET LOCK_TIMEOUT 2000` so a stalled hot-row lock errors into fail-open rather than freezing callers).
- Choke points: `QboHttpClient._send_http` (all entity clients) and `QboAttachableClient` (its 4 send sites). OAuth/token/discovery calls and pre-signed attachment downloads are NOT metered (not CorePlus-counted).
- Thresholds: warn at `QBO_BUDGET_WARN_PCT` (default 0.80), block at `QBO_BUDGET_BLOCK_PCT` (default 0.95) of `QBO_MONTHLY_CALL_BUDGET` (default 500,000 = Intuit Builder tier). `QBO_BUDGET_ENFORCE=false` disables blocking (metering + warnings continue).
- The meter FAILS OPEN: if the DB increment/read errors, the call proceeds and `qbo.budget.meter_unavailable` logs. A broken meter never takes down sync.
- Outbox worker: `drain_once` skips claiming while blocked (rows stay `pending`, no attempts burned). If a trip lands mid-row, the row is parked via `mark_failed` with `NextRetryAt` = 1st of the month AFTER the exhausted month (derived from the error's `month_key`) — never dead-lettered.
- Note: while blocked, refused attempts still increment the counter (increment-before-check, deliberate), so the MTD count can read slightly above real Intuit usage during a blocked window.

## Diagnosis

1. Current usage: `SELECT * FROM qbo.ApiUsage WHERE MonthKey = FORMAT(SYSUTCDATETIME(), 'yyyy-MM')` — compare against the Intuit developer dashboard (App: `build.one`, Production).
2. What ate the budget: App Insights — count `qbo.http.request.started` by `operation_name` over the month; a runaway loop shows as one dominant operation (July 2026: per-bill GET reconcile scans = 94%).
3. Parked rows: `SELECT COUNT(*) FROM qbo.Outbox WHERE Status='failed' AND LastError LIKE 'Parked:%'`.

## Recovery

- **Legitimate exhaustion (real usage hit the cap):** wait for the 1st (Intuit resets monthly) or upgrade the Intuit tier. Everything self-resumes; no action needed.
- **Runaway loop ate the budget:** find and stop the loop first (this breaker bounds the damage to ~5% headroom — that margin is why block is 95%, not 100%). The remaining budget covers essential pushes if you temporarily raise `QBO_BUDGET_BLOCK_PCT`.
- **False trip / threshold misconfigured:** fix the env var (`QBO_MONTHLY_CALL_BUDGET` / `QBO_BUDGET_BLOCK_PCT` on the App Service) — new calls unblock immediately (env is read per call, no restart needed). **Already-parked rows do NOT auto-unpark** (booked gap): release them with
  `UPDATE qbo.Outbox SET NextRetryAt = SYSUTCDATETIME() WHERE Status='failed' AND LastError LIKE 'Parked:%'`.
- **Kill switch:** `QBO_BUDGET_ENFORCE=false` disables blocking entirely (use only if the breaker itself misbehaves — you are then unprotected against the Intuit hard cap).

## Verification

- `qbo.outbox.drain.skipped_budget_blocked` stops appearing; outbox `pending` count drains to 0.
- `dbo.Sync WHERE Provider='qbo'` watermarks advance again on the next 15-min ticks.

## Prevention

- App Insights alerts on `qbo.budget.threshold_crossed` (warn band = investigate before block).
- The reconcile query-diff discipline (U-153/U-160) keeps steady-state usage ~8-10% of the cap; any new bulk QBO path should be budgeted against `docs/audit_qbo_integration_2026_08_07.md`'s call-cost analysis before shipping.
