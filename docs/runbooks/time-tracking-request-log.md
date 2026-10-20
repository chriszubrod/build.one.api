# Time-tracking write request log (U-663)

## Symptom

Need to answer who changed time entries/logs, whether edits failed because a day was approved, or whether time-tracking API traffic dropped.

## Severity

**Low** — diagnostic / audit; no automatic alert unless combined with user reports of lost edits.

## Where the data lives

App Insights resource **`buildone`** (same as U-650):

| Signal | What it carries |
|--------|-----------------|
| **`requests`** | Every HTTP request since **2026-10-20** — URL, status, duration, operation name (route template when ASP.NET/OTel names it). |
| **`traces`** | One INFO line per time-tracking **write** (`POST`/`PUT`/`PATCH`/`DELETE` on `/api/v1/time-entries` or `/api/v1/time-logs`) with `customDimensions.event_name == "api.time_write"`. Fields: `method`, `route` (the matched route template, or `<unmatched>` when routing found none — never the raw path), `status`, `duration_ms`, `user_id`, `error_code` (`validation_error` / `not_found` / `entry_locked` / `entry_approved` / … , null on success). An unhandled exception still leaves a line with `status = 500`. |
| **Request spans** | `customDimensions["enduser.id"]`, `buildone.company_id`, and on errors `buildone.error_code` (when OpenTelemetry is enabled in prod). |

**Retention:** 90 days (default App Insights workspace).

**Known gap:** No reliable request telemetry between **2026-10-11** and **2026-10-20 10:47Z** (U-650a wiring). Do not infer zero traffic in that window.

## KQL queries

### (a) One worker's time-tracking writes for a calendar day

```kusto
traces
| where timestamp between (datetime(2026-10-20) .. datetime(2026-10-21))
| where customDimensions.event_name == "api.time_write"
| where customDimensions.user_id == "17"
| project timestamp, method=tostring(customDimensions.method), route=tostring(customDimensions.route),
          status=toint(customDimensions.status), duration_ms=toint(customDimensions.duration_ms),
          error_code=tostring(customDimensions.error_code)
| order by timestamp asc
```

Replace the date range and `user_id` (internal `User.Id` as logged on the request).

### (b) Write failures in the last 24 hours by `error_code`

```kusto
traces
| where timestamp > ago(24h)
| where customDimensions.event_name == "api.time_write"
| where toint(customDimensions.status) >= 400
| summarize count() by error_code=tostring(customDimensions.error_code), route=tostring(customDimensions.route)
| order by count_ desc
```

`entry_approved` / `entry_locked` indicate edits against a non-draft day (U-663).

### (c) Requests-per-hour continuity (traffic drop check)

```kusto
requests
| where timestamp > ago(7d)
| where url has "/api/v1/time-entries" or url has "/api/v1/time-logs"
| summarize requestCount=count() by bin(timestamp, 1h)
| order by timestamp asc
```

Compare against the known 2026-10-11 → 2026-10-20 gap before declaring an outage.

## Recovery / verification

- After deploy, trigger one draft time-log `PUT` in prod; within ~2 min expect a `api.time_write` trace and a `requests` row for the same operation.
- Logger **`shared.api.request_log`** must appear in `EXPORTED_INFO_LOGGERS` (`shared/observability.py`) or traces will not export at INFO.

## Prevention

- Do not log bodies, query strings, headers, tokens, notes, or GPS — only route template, status, duration, user id, and machine `error_code`.
- Typed lock errors (`entry_approved`, `entry_locked`) must stay **422**, not 409, so mobile clients discard or retain queued edits correctly.
