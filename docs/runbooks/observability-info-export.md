# Runbook: Which Loggers Reach App Insights at INFO

The Azure exporter is attached to the root logger, and the root stays at WARNING.
So a logger's INFO records reach App Insights only if it is listed in
`EXPORTED_INFO_LOGGERS` in `shared/observability.py`. That registry is the single
place that decides what INFO is exported. `configure_app_insights()` raises those
loggers to INFO after it configures Azure, and nothing else.

Local runs export nothing. Setup runs only when `APPLICATIONINSIGHTS_CONNECTION_STRING`
is set (prod App Service).

## What is exported at INFO

| Group | Loggers | What it emits |
|---|---|---|
| Cascade rung outcomes | `intelligence.cascade`, `intelligence.transport.cascade` | `cascade.ladder.completed`, `cascade.transport.rung` (see `cascade-rung-outcomes.md`) |
| Outbox row lifecycle | `intelligence.outbox`, `integrations.intuit.qbo.outbox`, `integrations.ms.outbox`, `integrations.box.outbox` | Enqueue, claim, send, and dead-letter for each outbox row |
| Reconciliation runs | `integrations.intuit.qbo.reconciliation` | `qbo.reconcile.run.started` and `qbo.reconcile.run.completed` |
| QBO sync pull and scheduler | `shared.scheduler` | `qbo.sync.pull.started` and `qbo.sync.pull.completed`, plus scheduler lifecycle |

## What is deliberately not exported

- **The HTTP clients** (`integrations/box/base/client.py`, `integrations/ms/base/client.py`,
  `integrations/intuit/qbo/base/client.py`). Each external API call writes two INFO
  events. The volume is unmeasured, and these calls are not recorded as App Insights
  dependencies, so the trace cost is unknown. Keep them out until it is measured.
- **The Box excel, folder and file services** (`integrations/box/excel`, `integrations/box/folder`,
  `integrations/box/file`). These log per-row detail, which would swamp the outbox and
  reconciliation signal.

Library and request INFO is also not exported, because the root stays at WARNING.

## How to add a logger

1. Add the logger name to `EXPORTED_INFO_LOGGERS` in `shared/observability.py`, under
   the group that describes what it emits. A package name covers every module below it.
2. Add the name to `BUSINESS_LOGGER_NAMES` in `tests/test_u650_observability.py`. The
   exact-set test pins the registry to the cascade and business names, so it fails until
   both lists agree. The client-exclusion test runs on every registry name without changes.
3. Run `./.venv/bin/python -m pytest -o addopts= -p no:cacheprovider tests/test_u650_observability.py`.
4. Size it before you rely on it (see below).

## Sizing trace volume

Run in Application Insights > Logs after a deploy. It counts traces per event over
the last 24 hours, so you can see which events dominate:

```kql
traces
| where timestamp > ago(1d)
| summarize n=count() by event=tostring(customDimensions.event_name), severityLevel
| order by n desc
```

Events without an `event_name` group under an empty `event`. Those are plain
human-readable INFO lines from the exported loggers.

## Verification

After a deploy that changes the registry, run the sizing query. A newly added
logger's events should appear within a few minutes. If they do not, check that
`APPLICATIONINSIGHTS_CONNECTION_STRING` is set on the App Service and that the logger
name matches the module path (`logging.getLogger(__name__)`).
