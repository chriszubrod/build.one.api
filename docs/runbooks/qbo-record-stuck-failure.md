# Runbook: Specific QBO Record Stuck in Sync Failure

A particular bill/invoice/purchase keeps failing to push to QBO while
other records sync fine. The entity doesn't appear in QBO despite being
"completed" in the app.

## Symptom

- User reports: "I finalized this bill last night, it's still not in QBO."
- App Insights: single outbox row has `Attempts >= 3` and `Status = 'failed'`
  or `dead_letter`.
- `qbo.outbox.row.retry_exhausted` or `qbo.outbox.row.dead_lettered` fired
  for a specific row.

## Severity

**Medium** per-record; critical if many records accumulate in dead_letter.

## Diagnosis

### Step 1 — Find the outbox row

If you have the local bill public_id:

```sql
SELECT TOP 5 [Id], [PublicId], [Kind], [EntityPublicId], [Status], [Attempts],
             [LastError], [CreatedDatetime], [NextRetryAt],
             [CompletedAt], [DeadLetteredAt]
FROM [qbo].[Outbox]
WHERE [EntityType] = 'Bill'
  AND [EntityPublicId] = '<public_id>'
ORDER BY [CreatedDatetime] DESC;
```

If you have the outbox PublicId:

```sql
SELECT *
FROM [qbo].[Outbox]
WHERE [PublicId] = '<outbox_public_id>';
```

Read:
- `Status = 'dead_letter'` → retries exhausted, waiting for human.
- `Status = 'failed'` with recent `NextRetryAt` → still in backoff; may
  recover on its own.
- `Status = 'in_progress'` for hours → stranded (worker crashed); see
  `qbo-outbox-backlog-growing.md` Recovery C.

### Step 2 — Read `LastError`

```sql
SELECT [LastError] FROM [qbo].[Outbox] WHERE [Id] = <id>;
```

Common patterns:
- `QboValidationError: ...` → QBO rejected the payload. Permanent until
  the local record is fixed. Read the Detail for the specific issue.
- `QboDuplicateError: ...` (fault code 6140) → something with this
  DocNumber already exists in QBO for this vendor. Possible real duplicate,
  or stale DocNumber from a previous retry.
- `QboAuthError: ...` → token refresh failed during the attempt. Not
  record-specific; would affect all rows.
- `QboTimeoutError: ...` → intermittent; retry should recover unless
  exhausted.
- `Unexpected ValueError: Bill not found` → local record was deleted
  between enqueue and drain.

### Step 3 — Check entity state

```sql
-- for Bill
SELECT [Id], [PublicId], [IsDraft], [BillNumber], [TotalAmount]
FROM [dbo].[Bill]
WHERE [PublicId] = '<entity_public_id>';
```

- Row missing → entity was deleted; the outbox row is orphaned. Dead-letter
  it manually.
- `IsDraft = 1` → bill was reverted to draft; outbox row should never have
  been enqueued. Bug in the completion flow; dead-letter the row.

### Step 4 — App Insights trace

```kusto
traces
| where timestamp > ago(24h)
| where customDimensions.outbox_public_id == "<outbox_public_id>"
| project timestamp, message, customDimensions
| order by timestamp asc
```

Full lifecycle of this row — enqueued → drained → (retry scheduled)* →
completed or dead-lettered.

## Common causes (ranked)

1. **QboValidationError from a bad payload.** Some required field is missing
   or invalid in the local record. Examples:
   - Vendor not mapped to a QBO vendor.
   - BillLineItem has no `sub_cost_code_id`, so no QBO Item mapping exists.
   - DocNumber exceeds QBO's length limit.
2. **QboDuplicateError** (fault code 6140). A prior attempt succeeded on
   QBO's side but we didn't receive the response (network timeout). Retry
   without idempotency — or with a wrong `RequestId` — tries to create again.
3. **Local entity was deleted or reverted to draft** after enqueue. The
   handler can't build the QBO payload.
4. **Rate limit accumulation.** Many rows were failing 429 and hit their
   retry budget. Rare.

## Recovery

### Recovery A — Fix the validation error, re-enqueue

1. Identify the validation failure from `LastError`.
2. Fix the local record (set the missing field, map the vendor, etc.).
3. Manually reset the outbox row:

```sql
UPDATE [qbo].[Outbox]
SET [Status] = 'pending',
    [NextRetryAt] = SYSUTCDATETIME(),
    [Attempts] = 0,
    [ReadyAfter] = NULL,
    [LastError] = 'Manual recovery: fix applied, re-queuing'
WHERE [Id] = <id>;
```

Next drain tick picks it up.

### Recovery B — QboDuplicateError recovery

QBO already has this record. Two sub-cases:

**B1. The previous attempt succeeded but we didn't record the mapping.**

Find the QBO bill:

```
QBO → Expenses → Bills → search by vendor + date + amount
```

Capture the QBO Bill Id. Then create the local mapping:

```bash
.venv/bin/python -c "
from integrations.intuit.qbo.bill.connector.bill.persistence.repo import BillBillRepository
from integrations.intuit.qbo.bill.persistence.repo import QboBillRepository
local_qbo_bill = QboBillRepository().read_by_qbo_id('<qbo_bill_id>')
BillBillRepository().create(bill_id=<local_bill_id>, qbo_bill_id=local_qbo_bill.id)
"
```

Then mark the outbox row as done (the work is complete):

```sql
UPDATE [qbo].[Outbox]
SET [Status] = 'done',
    [CompletedAt] = SYSUTCDATETIME(),
    [LastError] = 'Manual recovery: existing QBO bill found and linked'
WHERE [Id] = <id>;
```

**B2. A true duplicate.** User entered the bill manually in QBO AND via the app.
Decide which is canonical, void/delete the other in QBO, then Recovery A.

### Recovery C — Local entity deleted or reverted

Dead-letter the orphaned row:

```sql
UPDATE [qbo].[Outbox]
SET [Status] = 'dead_letter',
    [DeadLetteredAt] = SYSUTCDATETIME(),
    [LastError] = 'Manual dead-letter: local entity no longer eligible'
WHERE [Id] = <id>;
```

### Recovery D — Retry after transient failure

If the row is `failed` but not dead-lettered yet and the transient issue has
cleared (e.g., QBO is back up), force an immediate retry:

```sql
UPDATE [qbo].[Outbox]
SET [NextRetryAt] = SYSUTCDATETIME(),
    [ReadyAfter] = NULL
WHERE [Id] = <id>;
```

## Verification

```sql
SELECT [Status], [Attempts], [CompletedAt]
FROM [qbo].[Outbox]
WHERE [Id] = <id>;
```

Expected: `Status = 'done'`, `CompletedAt` populated.

## Prevention

- Validate payloads before enqueue: BillLineItem must have SubCostCode
  mapping, vendor must be mapped, required fields present.
- Monitor `qbo.outbox.row.dead_lettered` counts — trend up means systemic
  issue, not isolated bugs.
- Add a lightweight UI for viewing dead-lettered rows and replaying them
  after fixes. Far better than SQL updates.
