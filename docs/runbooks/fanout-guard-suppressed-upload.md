# Runbook: Fan-Out Idempotency Guard Suppressed an Upload

A document that should be in SharePoint or Box is missing, and the logs show the
push was **skipped** rather than attempted. U-221 added two guards that suppress
a re-upload when the identity of a prior successful push matches exactly. This
runbook covers the case where a guard skipped something it should have sent.

The guards exist because the QBO pull's 60s watermark overlap re-projects records
every tick, and each re-projection used to re-run the whole fan-out — measured in
prod over 10 days before the fix: **695 redundant SharePoint uploads** and **358
junk Box versions**.

## Symptom

- A completed bill/expense/invoice's document is absent from the project's
  SharePoint module folder or Box folder, AND
- The API logs carry `ms.outbox.upload.skipped_already_uploaded` or
  `box.file.push.skipped_identical` for that entity, AND
- No `dead_letter` row and no `ReconciliationIssue` explains the absence.

## Severity

| Condition | Severity | Response |
|---|---|---|
| One document missing, guard logged a skip | High | Force the re-push (below) |
| Multiple documents missing across entities | Critical | Disable the guards globally, then investigate |
| A guard logged `*.guard.read_failed` repeatedly | Warning | Guards are failing OPEN (uploading) — no document is at risk; fix the read |

**The guards fail open by design.** Any missing row, NULL hash, absent id,
unparseable payload, or read that raises falls through to UPLOAD. A wrong upload
costs one PUT; a wrong skip loses a document. If you see `read_failed`, nothing
is being suppressed — you are paying the old redundant-upload cost until it is
fixed, which is the safe direction.

## Diagnosis

### Step 1 — Confirm a guard actually skipped it

```sql
-- SharePoint: the completed row whose identity suppressed the new enqueue.
SELECT TOP 20 Id, PublicId, Status,
       JSON_VALUE(Payload, '$.filename')       AS filename,
       JSON_VALUE(Payload, '$.parent_item_id') AS parent_item_id,
       JSON_VALUE(Payload, '$.blob_path')      AS blob_path,
       JSON_VALUE(Payload, '$.attachment_id')  AS attachment_id,
       CONVERT(VARCHAR(19), CreatedDatetime, 120) AS created
FROM ms.Outbox
WHERE Kind = 'upload_sharepoint_file'
  AND EntityPublicId = '<entity-public-id>'
ORDER BY Id DESC;
```

```sql
-- Box: the registry row whose folder+name+sha1+attachment_id suppressed the push.
SELECT BoxFileId, BoxFolderId, Name, Kind, AttachmentId, Sha1,
       CONVERT(VARCHAR(19), LastPushedAt, 120) AS last_pushed
FROM box.[File]
WHERE EntityPublicId = '<entity-public-id>';
```

A guard only skips on an **exact** match of every identity field. If a row above
matches what you expected to upload, the skip was correct in the sense that those
bytes were delivered once — the file went missing **after** delivery (a human
deleted or moved it), which the guard cannot see.

### Step 2 — Confirm the sproc is actually deployed

If **no** SharePoint upload is ever suppressed and the redundant-upload volume
has not dropped, the SQL step may have been skipped at deploy. The guard fails
open on a missing sproc, so the symptom is silence, not an error:

```sql
SELECT OBJECT_ID('ReadCompletedMsOutboxByEntity') AS sproc_id;  -- NULL = not deployed
```

Fix: `python scripts/run_sql.py integrations/ms/outbox/sql/ms.outbox.sql`.

## Recovery

### Option A — Operator force re-push (Box, invoices)

`POST /sync/invoice/{public_id}/box` sets `force` on the enqueued payload, which
bypasses the Box guard entirely. `force` is **sticky across outbox coalescing**:
a later non-forced enqueue for the same attachment cannot revoke it.

### Option B — Global kill switch (both surfaces, every entity)

For any surface without a force route (all SharePoint uploads, and Box pushes for
bill/expense/bill_credit), disable the guards, re-push, then re-enable:

```bash
az webapp config appsettings set --name <app> --resource-group buildone_group \
  --settings DISABLE_FANOUT_IDEMPOTENCY_GUARDS=true
az webapp restart --name <app> --resource-group buildone_group
```

Then re-run the completion (or wait one pull tick — the QBO pull re-projects and
re-pushes on its own). Then **unset it**:

```bash
az webapp config appsettings delete --name <app> --resource-group buildone_group \
  --setting-names DISABLE_FANOUT_IDEMPOTENCY_GUARDS
az webapp restart --name <app> --resource-group buildone_group
```

Only the exact string `true` (case-insensitive) disables the guards. Any other
value, and unset, leaves them enabled.

### Option C — Clear the single stale identity row

Surgical alternative to Option B when exactly one document is affected and you do
not want to pay the redundant-upload cost fleet-wide. Deleting the identity the
guard matches on makes the next push fall through:

```sql
-- Box: drop the registry row (the push re-creates it on the next successful upload).
DELETE FROM box.[File] WHERE BoxFileId = '<box-file-id>';
```

For SharePoint the equivalent is the `ms.Outbox` `done` row — prefer Option B
there, since that table is also the audit trail of what was dispatched.

## Verification

- The re-push logs `box.file.push.completed` / an `ms.Outbox` row reaching
  `Status='done'` with a **new** `Id`.
- The document is present in the target folder.
- After re-enabling the guards, the next pull tick logs a skip again for that
  entity (proof the guard is back on and the identity is registered).

## Prevention

- Box-side deletions are detected by the daily reconcile
  (`drift_type='registry_file_missing'`, high) — it flags the divergence but does
  **not** clear the registry row, so a re-push after a Box-side delete needs
  Option A or C. Wiring reconcile → registry invalidation is booked as a
  follow-up.
- SharePoint has no equivalent detector; a document deleted in SharePoint is not
  noticed by any job today. Also booked.
- Never widen a guard's match to fewer fields. Every identity field is covered by
  a mutation test in `tests/test_fanout_idempotency_guards.py`; dropping one is
  caught by the suite (17-mutation matrix, all caught).
