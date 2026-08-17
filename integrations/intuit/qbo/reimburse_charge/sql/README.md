# qbo.ReimburseCharge — durable RC staging (U-186)

`qbo.reimburse_charge.sql` is the **canonical single source of truth** for the
`qbo.ReimburseCharge` table + its stored procedures. Apply it (idempotent —
guarded `CREATE TABLE`, `CREATE OR ALTER` sprocs) with:

```
python scripts/run_sql.py integrations/intuit/qbo/reimburse_charge/sql/qbo.reimburse_charge.sql
```

## Why this table exists

QBO auto-creates a **ReimburseCharge (RC)** for every Bill/Purchase line marked
Billable with a `CustomerRef`. **Measured 2026-08-16 (U-242): QBO never exposes
a reverse Bill/Purchase `LinkedTxn`** — un-invoiced RCs carry no `LinkedTxn`;
invoiced RCs carry a forward Invoice pointer only. See
`docs/rc_source_linking_signal_2026_08_16.md` (supersedes the original KI-32
assumption).

This table captures RCs on scheduler cadence and **preserves any stored source
pointer across re-pulls** (defensive/forward-compatible), so Tier-0 linking can
resolve if/when a source signal becomes available:

```
qbo.InvoiceLine.LinkedTxnId  ->  qbo.ReimburseCharge.QboId
                             ->  SourceTxnId (source Bill/Purchase QBO id)
                             ->  qbo.Bill/qbo.Purchase -> line -> dbo line item
```

## Sprocs

| Sproc | Purpose |
|-------|---------|
| `CreateQboReimburseCharge` | insert one RC (OUTPUT INSERTED.*) |
| `ReadQboReimburseChargeByQboIdAndRealmId` | upsert existence check |
| `ReadQboReimburseChargesByRealmId` | realm-wide read |
| `UpdateQboReimburseChargeByQboId` | ROWVERSION-guarded upsert-update |

`UpdateQboReimburseChargeByQboId` uses **CASE-WHEN-preserve** on
`SourceTxnType` / `SourceTxnId` / `SourceTxnLineId` when re-pull carries NULL
(defensive/forward-compatible — QBO does not currently populate these fields;
see `docs/rc_source_linking_signal_2026_08_16.md`).

**Pull-only staging:** no delete / reconcile-delete sproc.

## Keyspace

`QboId`, `SourceTxnId`, `SourceTxnLineId` are QBO **string** ids, disjoint from
the `qbo.*.Id` BIGINT keyspace. Tier-0 joins go QBO-string → QBO-string; a
`qbo.Bill.Id` / `qbo.Purchase.Id` is never aliased as a dbo id.
