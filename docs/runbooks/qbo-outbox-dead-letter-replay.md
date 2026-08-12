# Runbook: QBO Outbox Dead-Letter Replay

One or more rows in `[qbo].[Outbox]` reached `Status = 'dead_letter'` after
retries exhausted or a park ceiling tripped. The write did not complete (or
may have partially completed — unlike write-refused parks where nothing left
the process).

## Symptom

- App Insights: `qbo.outbox.row.dead_lettered` or
  `qbo.outbox.row.write_refused_park_ceiling` events.
- SQL: rows with `Status = 'dead_letter'` and populated `DeadLetteredAt`.
- User report: "Bill finalized but never appeared in QBO" after all automatic
  retries failed.

## Severity

**Medium** per row; **High** if many rows dead-letter in a short window
(systemic outage or misconfiguration).

## Diagnosis

### Step 1 — Inventory dead letters

```sql
SELECT TOP 50 [Id], [PublicId], [Kind], [EntityType], [EntityPublicId],
             [Status], [Attempts], [LastError],
             CONVERT(VARCHAR(19), [DeadLetteredAt], 120) AS DeadLetteredAt
FROM [qbo].[Outbox]
WHERE [Status] = 'dead_letter'
ORDER BY [DeadLetteredAt] DESC;
```

Filter by kind when investigating a single pipeline:

```sql
WHERE [Status] = 'dead_letter' AND [Kind] = 'sync_bill_to_qbo'
```

### Step 2 — Read `LastError`

Common patterns:

- `QboValidationError: ...` — fix the local entity, then replay.
- `Write-refused park ceiling exceeded` — `ALLOW_QBO_WRITES` was not true
  for ~24h; fix the env var, then replay.
- `QboDuplicateError` / duplicate DocNumber — see `qbo-duplicate-bill.md`;
  do **not** blind replay without linking or voiding the QBO side.
- `Retries exhausted after N: QboAuthError` — auth was broken; confirm token
  health before replay.

### Step 3 — Confirm entity still eligible

For `sync_bill_to_qbo`:

```sql
SELECT [Id], [PublicId], [IsDraft], [BillNumber]
FROM [dbo].[Bill]
WHERE [PublicId] = '<entity_public_id>';
```

- Missing row → dead-letter is orphaned; do not replay.
- `IsDraft = 1` → do not replay; fix the completion flow.

### Step 4 — Check QBO for partial completion

Search QBO by vendor + doc number + amount. If the bill **already exists**
in QBO but local mapping is missing, replay will duplicate — use manual
mapping recovery in `qbo-record-stuck-failure.md` Recovery B instead.

## Recovery

### Recovery A — Scoped replay via script (preferred)

Dry-run first, always with `--kind`:

```bash
.venv/bin/python scripts/retry_qbo_outbox_dead_letters.py \
  --kind sync_bill_to_qbo

.venv/bin/python scripts/retry_qbo_outbox_dead_letters.py \
  --kind sync_bill_to_qbo --apply
```

The script **preserves `RequestId`**. Do **not** use
`QboOutboxService.enqueue()` as a substitute — enqueue mints a fresh
request id and forfeits Intuit dedup.

### Recovery B — Manual SQL (when script unavailable)

```sql
UPDATE [qbo].[Outbox]
SET [Status] = 'pending',
    [Attempts] = 0,
    [NextRetryAt] = SYSUTCDATETIME(),
    [LastError] = NULL,
    [DeadLetteredAt] = NULL
WHERE [Id] = <id>
  AND [Status] = 'dead_letter';
```

Do not clear `RequestId`.

### Recovery C — Write-refused park (not dead-letter)

Rows parked with `LastError LIKE 'Parked: QBO writes disabled%'` are still
`Status = 'failed'`, not dead-letter. Fix `ALLOW_QBO_WRITES=true` on App
Service and wait for `NextRetryAt`, or adjust `NextRetryAt` manually.

## Verification

```sql
SELECT [Status], [Attempts], [LastError], [CompletedAt]
FROM [qbo].[Outbox]
WHERE [PublicId] = '<outbox_public_id>';
```

Expected after successful drain: `Status = 'done'`, `CompletedAt` populated.

For bill push, confirm mapping exists:

```sql
SELECT bb.*
FROM [qbo].[BillBill] bb
JOIN [dbo].[Bill] b ON b.[Id] = bb.[BillId]
WHERE b.[PublicId] = '<entity_public_id>';
```

## Prevention

- Keep `ALLOW_QBO_WRITES=true` in prod App Service settings.
- Monitor `qbo.outbox.row.dead_lettered` trend — spikes indicate systemic
  issues, not isolated bad rows.
- Prefer bill completion (sanctioned outbox enqueue) over ad-hoc push paths.
- Scope replay with `--kind`; never blind-reset all dead letters without
  checking for partial QBO completion.
