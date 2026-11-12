# App Service config + required env vars

Production settings and the full env-var surface, including every write gate and tuning knob.

> Extracted verbatim from `CLAUDE.md` on 2026-11-11 (U-788) to take it off the
> per-session load. **These are standing rules, not history** — they bind the same as
> before. Read this file when touching the area; CLAUDE.md keeps the one-line rule and
> links here.

---

## App Service Config — Required Production Settings

- **Always On = true**: critical. Without it, the container gets unloaded after ~20 min of inactivity and cold starts take 10–15 seconds. Enable via `az webapp config set --name <app> --resource-group <rg> --always-on true`.
- **`-w 2`**: configured in `startup.sh`. Two gunicorn/uvicorn workers on B2. Fits in memory now that APScheduler is out of the web process. `-w 4` is feasible but unnecessary at current load.
- **Env vars** that must exist on App Service: `ENABLE_SCHEDULER=false` (in-process scheduler disabled), `DRAIN_SECRET` (matches Function App), `ALLOW_QBO_WRITES=true`, `ALLOW_MS_WRITES=true`, `ALLOW_EXPENSE_RECODE_WRITES=false` (U-005 expense-coding recode gate; default off — flip to true only at Gate-P2 go-live), `ENCRYPTION_KEY`, plus DB creds. `azure_encryption_key` is an optional dev-only override that takes precedence over `encryption_key` so a developer can decrypt prod-encrypted DB rows locally without touching the dev-only key (see `shared/encryption.py::_get_encryption_key`). **Time-entry digest additions:** `TIME_ENTRY_DIGEST_MODE` (off|draft|send; default `off`) is the kill switch + mode for the daily per-worker time-entry digest; optional `TIME_ENTRY_DIGEST_LOOKBACK_DAYS` (default `2` = two days ago; `1` = yesterday; values `< 1` floored to the default) sets which day the sweep targets, `BUSINESS_TIMEZONE` (default `America/Chicago`, used to compute the target day; clock times are rendered verbatim as stored local wall-clock — no conversion) and `TIME_ENTRY_DIGEST_BCC` (archive BCC; falls back to `invoice_inbox_email`). **Optional QBO sync / fan-out tuning:** `QBO_SYNC_WATERMARK_OVERLAP_SECONDS` (default `60` — overlap re-pulled on every QBO watermark commit to cover host/QBO clock skew; see `integrations/intuit/qbo/base/watermark.py::_watermark_overlap_seconds`), `QBO_WATERMARK_HOLD_BOUND_SECONDS` (default `7200` = 2h — max hold before force-advancing with a recorded `ReconciliationIssue`; see `integrations/intuit/qbo/base/watermark.py::_watermark_hold_bound_seconds`), `DISABLE_FANOUT_IDEMPOTENCY_GUARDS` (default off/false = guards ON — kill switch for Box/SharePoint fan-out idempotency guards added in U-221; see `shared/fanout_guard.py`).
