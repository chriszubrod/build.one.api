# Runbooks

Operational playbooks for diagnosing and recovering from production incidents.
Each runbook is standalone, scannable, and lists specific commands and queries
to run — no need to reverse-engineer the system under pressure.

## When to open a runbook

- An App Insights alert fires and the alert description names one of these.
- A user reports a problem that matches a **Symptom** section below.
- You're doing a post-incident review and want the history of what works.

## Available runbooks

| Runbook | Covers |
|---|---|
| [qbo-token-expiration.md](qbo-token-expiration.md) | QBO OAuth access/refresh token expiring or failing to refresh |
| [qbo-outbox-backlog-growing.md](qbo-outbox-backlog-growing.md) | Outbox rows accumulating faster than the worker can drain them |
| [qbo-duplicate-bill.md](qbo-duplicate-bill.md) | Bill appears more than once in QBO (or local mirror) |
| [qbo-sync-lag-exceeded.md](qbo-sync-lag-exceeded.md) | Local mirror is stale; lag exceeds the SLA threshold |
| [qbo-record-stuck-failure.md](qbo-record-stuck-failure.md) | A single record keeps failing to push to QBO (retry exhausted / dead-lettered) |
| [qbo-reconciliation-drift.md](qbo-reconciliation-drift.md) | Reconciliation job is flagging growing amounts of drift |
| [ms-token-expiration.md](ms-token-expiration.md) | MS 365 delegated OAuth access/refresh token expiring or failing to refresh |
| [ms-graph-503-storm.md](ms-graph-503-storm.md) | Cascading Graph 5xx failures during a Microsoft service incident; dead-letter recovery |
| [ms-excel-conflict-storm.md](ms-excel-conflict-storm.md) | Excel workbook writes blocked by a human editor or stuck session lock |
| [ms-permissions-revoked.md](ms-permissions-revoked.md) | Azure AD revoked the app's Graph permissions (403 everywhere) |

## Runbook format

Every runbook follows the same structure. Keeping the format consistent means
readers know where to look under pressure.

1. **Symptom** — what observable signal indicates this runbook applies.
2. **Severity** — warning / critical, and the expected response SLA.
3. **Immediate action** — stop-the-bleeding steps, if any.
4. **Diagnosis** — numbered checklist of what to inspect and in what order.
5. **Common causes** — ranked roughly by likelihood, most common first.
6. **Recovery** — per-cause procedures with copy-paste commands.
7. **Verification** — how to confirm the fix took.
8. **Prevention** — any follow-up learnings to reduce recurrence.

## How to write a new runbook

- Keep it short. A runbook that takes 30 minutes to read gets skipped.
- Include the exact SQL / KQL / shell commands. Don't paraphrase.
- Name the files `kebab-case.md` matching the symptom or system.
- Link from this README.
- Update after every incident that exercised the runbook — add missed cases,
  remove fixes that no longer apply, refine diagnosis order.
