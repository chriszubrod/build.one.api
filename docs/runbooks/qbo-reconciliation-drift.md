# Runbook: QBO Reconciliation Drift Growing

The daily reconciliation job is flagging more drift over time. Our local
view of QBO is diverging from QBO's view of QBO, and we're not catching
it fast enough.

## Symptom

- App Insights alert: `qbo.reconcile.flagged_issues.count` trending up
  day-over-day.
- Manual reconciliation reveals more missing or mismatched records than
  expected.
- Accountant reports discrepancies that weren't caught by the app.

## Severity

| Condition | Severity | Response |
|---|---|---|
| Flagged count grew +5 in 24h | Warning | Review during business hours |
| Flagged count grew +20 in 24h | Critical | Investigate same day |
| High-severity flags present (duplicate_mapping) | Critical | Review same day |

## Diagnosis

### Step 1 — See what drift types are being flagged

```sql
SELECT [DriftType], [Severity], [Action], [Status], COUNT(*) AS [Count]
FROM [qbo].[ReconciliationIssue]
WHERE [CreatedDatetime] >= DATEADD(day, -7, SYSUTCDATETIME())
GROUP BY [DriftType], [Severity], [Action], [Status]
ORDER BY [DriftType], [Severity];
```

Read:
- `qbo_missing_locally / auto_fixed` high count → delta sync is missing records;
  reconciliation catches them. Not necessarily a problem, but investigate why.
- `duplicate_mapping / flagged` → data bug. See `qbo-duplicate-bill.md`.
- `field_mismatch / flagged` → needs field-level source-of-truth rules
  (task #19); interim fix is manual resolution.
- `local_missing_qbo / flagged` → local has records QBO doesn't. Usually
  means the QBO record was voided/deleted; could also mean local entered
  a record that never synced.

### Step 2 — Look at recent flagged issues

```sql
SELECT TOP 20 [CreatedDatetime], [DriftType], [Severity], [EntityType],
              [EntityPublicId], [QboId], [Details]
FROM [qbo].[ReconciliationIssue]
WHERE [Status] = 'open'
  AND [Action] = 'flagged'
ORDER BY [CreatedDatetime] DESC;
```

The `Details` field has human-readable context for each finding.

### Step 3 — Check reconcile job heartbeat

```kusto
traces
| where timestamp > ago(7d)
| where customDimensions.event_name == "qbo.reconcile.run.completed"
| project timestamp, customDimensions.entity_type,
          customDimensions.auto_fixed, customDimensions.flagged
| order by timestamp desc
```

Expected: one completion per entity type per day. If gaps → job isn't
firing consistently.

### Step 4 — Identify the pattern

Is the drift concentrated in:
- One entity type? → that entity's delta sync has a bug.
- One time window? → an outage during that window stranded records.
- One vendor/customer? → a specific workflow is missing a mapping step.

## Common causes

1. **Delta sync watermark corruption.** A failed sync advanced the watermark
   without processing records. Subsequent ticks skip them forever. Reconciliation
   catches it but shouldn't need to.
2. **QBO-side deletions not handled.** Records deleted/voided in QBO don't
   appear in delta results (MetaData.LastUpdatedTime filter misses deletions).
3. **Required mappings missing.** Records come from QBO with items/vendors/
   customers that aren't mapped locally; sync fails silently and reconciliation
   catches them as missing.
4. **Reconciliation job itself failing.** Job runs but the detector errors
   out on specific records.

## Recovery

### Recovery A — Auto-fixable drift, already applied

Nothing to do. Reconciliation did its job. Monitor the trend over a few
days; if it's stable or decreasing, the system is healing.

### Recovery B — Flagged drift requires manual review

For each open flagged issue:

1. Open the issue: find the `EntityPublicId` or `QboId`.
2. Decide the correct state. Look at:
   - Local record (if exists)
   - QBO record (if exists)
   - Attachment PDFs (if available)
   - User who last edited
3. Apply the fix manually:
   - Missing QBO mapping → create it (see `qbo-record-stuck-failure.md` B1).
   - Field mismatch → decide which side is canonical, update the other.
   - Duplicate mapping → unlink the incorrect one (never both).
4. Mark the issue resolved:

```sql
UPDATE [qbo].[ReconciliationIssue]
SET [Status] = 'resolved',
    [ResolvedAt] = SYSUTCDATETIME()
WHERE [Id] = <issue_id>;
```

### Recovery C — Watermark corruption suspected

Reset the delta watermark for the affected entity so the next sync does a
fuller scan:

```sql
UPDATE [dbo].[Sync]
SET [LastSyncDatetime] = DATEADD(day, -30, GETUTCDATE())
WHERE [Provider] = 'qbo' AND [Entity] = '<entity>';
```

Next scheduled sync fetches 30 days of changes and catches anything missed.

### Recovery D — Root cause fix

For recurring drift, fix the underlying issue:

- **Missing vendor/customer mappings:** bulk-create mappings for the problem
  entities.
- **Deletion handling:** add void/delete detection to the connector's pull
  path (task #21).
- **Field-level source-of-truth:** implement the explicit rules (task #19).

## Verification

After fixes, re-run the reconciliation manually:

```bash
.venv/bin/python -c "
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.reconciliation.business.service import ReconciliationService
auth = QboAuthService().ensure_valid_token()
result = ReconciliationService().reconcile_bills(realm_id=auth.realm_id)
print(result)
"
```

Expected: `flagged` count drops after your fixes land.

## Prevention

- Tune reconciliation to run daily (current default). More frequent wastes
  API quota; less frequent lets drift accumulate.
- Monitor both `auto_fixed` and `flagged` counters over time. A baseline
  level of auto-fixes is OK; a baseline of flags is a bug.
- Implement the remaining drift detectors (stale_sync_token, field_mismatch,
  duplicate_mapping) to broaden coverage.
- Schedule 15-minute review windows weekly to clear the open `flagged` queue
  rather than letting it accumulate.
