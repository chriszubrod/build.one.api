# Runbook: Cascade Rung Outcomes (App Insights)

Reference for measuring the model cascade in production: per-task accept rate,
per-rung win share, latency, and cost. The cascade emits structured events; the
queries below read them from `traces` in Application Insights.

## Events

| Event | Emitted by | One record per | Key custom dimensions |
|---|---|---|---|
| `cascade.ladder.completed` | `intelligence/cascade/core.py` `run_ladder` | finished ladder (single-shot tasks and agent cascades) | `task`, `accepted`, `needs_human`, `accepted_via`, `winning_model`, `winning_index`, `attempt_count`, `latency_ms_total`, `input_tokens`, `output_tokens`, `cost_usd`, `attempts_json` |
| `cascade.transport.rung` | `intelligence/transport/cascade.py` `CascadeTransport.stream` | rung transition in the agent-loop transport | `outcome` (`fallback` / `accepted` / `exhausted`), `rung_index`, `provider`, `model`, `error_code`, `fallbacks`, `rung_count` |

Custom dimensions arrive as strings. Cast them in the query (`toint`, `todouble`,
`tobool`).

A null field is omitted from the event, so it reads as empty in KQL. `cost_usd` is
absent when any attempt's cost is unknown: its model is missing from
`intelligence/observability/pricing.py`, it has no usage, or the pricing lookup
raised. Add the price there, then recent events carry a cost.

If enrichment of a decision fails, the event is minimal (`event_name`, `task`,
`accepted`, `needs_human`) with `telemetry_error` set to the exception type.
`where isnotempty(customDimensions.telemetry_error)` finds those.

## Scope

The two cascade loggers (`intelligence.cascade`, `intelligence.transport.cascade`) are
exported at INFO through the `EXPORTED_INFO_LOGGERS` registry in `shared/observability.py`,
which `configure_app_insights()` applies only when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set.
The root logger stays at WARNING, so request and library INFO noise is not exported. Local runs export nothing.

Events exist only from the deploy that shipped them forward. There is no backfill.

## Queries

Run in Application Insights > Logs. Adjust `ago(7d)` as needed.

**1. Per task: calls, accepted rate, needs-human rate**

```kql
traces
| where timestamp > ago(7d)
| where tostring(customDimensions.event_name) == "cascade.ladder.completed"
| extend task = tostring(customDimensions.task),
         accepted = tobool(customDimensions.accepted),
         needs_human = tobool(customDimensions.needs_human)
| summarize calls = count(),
            accepted_rate_pct = round(100.0 * countif(accepted) / count(), 1),
            needs_human_rate_pct = round(100.0 * countif(needs_human) / count(), 1)
          by task
| order by calls desc
```

**2. Per winning model: wins, share, latency, cost (accepted calls only)**

```kql
let accepted_ladder = traces
| where timestamp > ago(7d)
| where tostring(customDimensions.event_name) == "cascade.ladder.completed"
| where tobool(customDimensions.accepted)
| extend model = coalesce(tostring(customDimensions.winning_model), ""),
         latency_ms = toint(customDimensions.latency_ms_total),
         cost_usd = todouble(customDimensions.cost_usd);
accepted_ladder
| summarize wins = count(),
            avg_latency_ms = avg(latency_ms),
            p50_latency_ms = percentile(latency_ms, 50),
            p95_latency_ms = percentile(latency_ms, 95),
            ['known cost (USD)'] = sum(cost_usd),
            ['unpriced calls'] = countif(isempty(tostring(customDimensions.cost_usd)))
          by model
| extend share_pct = round(100.0 * wins / toscalar(accepted_ladder | count), 1)
| order by wins desc
```

`known cost (USD)` is a floor, because KQL `sum()` skips nulls and an unpriced call would otherwise drop out silently, so `unpriced calls` shows how many are missing.

**3. Agent-loop transport: accepted and fallback counts by rung and error**

```kql
traces
| where timestamp > ago(7d)
| where tostring(customDimensions.event_name) == "cascade.transport.rung"
| extend outcome = tostring(customDimensions.outcome),
         rung_index = toint(customDimensions.rung_index),
         model = coalesce(tostring(customDimensions.model), ""),
         error_code = coalesce(tostring(customDimensions.error_code), "")
| summarize events = count() by outcome, rung_index, model, error_code
| order by outcome asc, rung_index asc
```

Read `outcome == "accepted"` rows for wins per rung. Read `outcome == "fallback"`
rows grouped by `error_code` to see why rungs are escalating. `exhausted` rows have
no `rung_index`.

## Verification

After a deploy that includes U-649, call a cascade task, then run query 1. A new
`task` row confirms the ladder event is flowing. If nothing appears, check that
`APPLICATIONINSIGHTS_CONNECTION_STRING` is set on the App Service.
