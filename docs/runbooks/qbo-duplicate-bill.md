# Runbook: Duplicate Bill in QBO

A bill appears more than once in QBO (or its local mirror) when it should
appear once. Accountants or reconciliation reports flag this.

## Symptom

- Two QBO Bills with the same DocNumber for the same vendor, OR
- Two local `BillLineItem` rows mapping to the same `QboBillLine`, OR
- User reports "I see this bill twice in QBO."

## Severity

Financial data integrity issue. **Critical** regardless of count — investigate
same day.

## Diagnosis

### Step 1 — Confirm duplicates exist in QBO

Log into QuickBooks Online → Expenses → Bills. Search for the vendor + amount.
If two bills have the same vendor, date, and amount, you've confirmed it.

### Step 2 — Identify when they were created

```sql
SELECT b.[Id], b.[QboId], b.[DocNumber], b.[TxnDate], b.[TotalAmt],
       b.[CreatedDatetime], b.[ModifiedDatetime]
FROM [qbo].[Bill] b
WHERE b.[DocNumber] = '<DocNumber>'
ORDER BY b.[CreatedDatetime];
```

- Both rows created within seconds of each other → retry duplicate (outbox
  misbehavior or missing idempotency key).
- Created hours/days apart → likely a different cause; manual duplicate
  entry, or a bug in the push path.

### Step 3 — Check outbox history for this bill

```sql
SELECT [Id], [EntityPublicId], [Attempts], [Status], [RequestId],
       [CreatedDatetime], [CompletedAt], [LastError]
FROM [qbo].[Outbox]
WHERE [EntityType] = 'Bill'
  AND [EntityPublicId] = '<local_bill_public_id>'
ORDER BY [CreatedDatetime];
```

- **Two rows with different `RequestId`** → someone enqueued twice; the
  idempotency coalesce logic in `QboOutboxService.enqueue` failed or was
  bypassed.
- **One row, multiple attempts, all succeeded with same RequestId** →
  shouldn't happen; QBO should have deduped. Check the request log for
  the actual `?requestid=` value sent.
- **No rows** → the duplicate was created by a non-outbox path (legacy code,
  manual entry, or a bug).

### Step 4 — Check App Insights for the push history

```kusto
traces
| where timestamp > ago(24h)
| where customDimensions.operation_name == "qbo.bill.create"
| where customDimensions.entity_public_id == "<local_bill_public_id>"
| project timestamp, message, customDimensions.http_status, customDimensions.qbo_fault_code
| order by timestamp desc
```

Pattern match:
- Two successful POSTs with different `requestid` → confirms duplicate-create.
- One POST with retries that all completed → QBO ignored the idempotency key?
  Check that `?requestid=` was actually in the URL (see Dependencies table
  in App Insights → target URL).

## Common causes

1. **Idempotency key not propagated on retry.** Outbox worker's retry didn't
   use the stored `RequestId`. Check `QboHttpClient._execute` uses the
   context var from `idempotency_key_context`.
2. **Concurrent enqueue that didn't coalesce.** `QboOutboxService.enqueue`
   found no existing row but a near-simultaneous enqueue inserted one too.
   Rare but possible without a DB-level uniqueness constraint.
3. **Manual creation in QBO + also via the app.** User entered the bill in
   QBO directly, then the app pushed it too — not a bug, a process issue.
4. **Legacy pre-outbox code path still active.** Something is still calling
   `sync_to_qbo_bill` directly instead of enqueuing. Search: `grep -rn 'sync_to_qbo_bill' entities/`.

## Recovery

### Recovery A — Merge duplicate bills in QBO

There is no safe automated merge. Do this manually in QBO:

1. Identify which bill is the "real" one (the one with correct data, or the
   older one if identical).
2. In QBO: open the duplicate, click **More** → **Delete** or **Void**.
   (Void preserves the history; Delete is permanent.)
3. If any payments or invoices were linked to the duplicate, relink them to
   the surviving bill first. QBO will warn you.

### Recovery B — Clean up the local cache

After QBO is deduplicated, pull fresh:

```bash
.venv/bin/python scripts/sync_qbo_bill.py
```

The sync will detect the deleted/voided duplicate and the reconciliation
job will also clean up stale mappings.

### Recovery C — Fix the root cause

Based on Step 3/4 diagnosis:

- **Idempotency not propagating:** verify `QboOutboxWorker._process` wraps
  handler calls in `idempotency_key_context(row.request_id)` and that
  `QboHttpClient._execute` reads from the context var.
- **Coalesce race:** if this becomes frequent, add a unique index on
  `(EntityType, EntityPublicId, Kind) WHERE Status IN ('pending', 'failed')`
  to force serialization at the DB layer.
- **Legacy code path:** delete the offending caller or route it through
  the outbox.

## Verification

```sql
-- Should return one row per bill
SELECT [DocNumber], COUNT(*) AS dupes
FROM [qbo].[Bill]
GROUP BY [DocNumber]
HAVING COUNT(*) > 1;
```

Empty result = no duplicates.

## Prevention

- The outbox's `RequestId` is the first line of defense. QBO dedups on
  `?requestid=<uuid>` for 24 hours.
- Monitor `qbo.outbox.row.enqueued` events — count enqueues per
  `(entity_type, entity_public_id)` per minute. Two enqueues in quick
  succession for the same bill, with different `RequestId`, is the signal
  to investigate the coalesce path.
- Consider a DB-level unique index on pending outbox rows if race conditions
  become a pattern.
