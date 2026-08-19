# qbo.ReimburseCharge — durable RC staging (U-186)

`qbo.reimburse_charge.sql` is the **canonical single source of truth** for the
`qbo.ReimburseCharge` table + its stored procedures. Apply it (idempotent —
guarded `CREATE TABLE`, `CREATE OR ALTER` sprocs) with:

```
python scripts/run_sql.py integrations/intuit/qbo/reimburse_charge/sql/qbo.reimburse_charge.sql
```

## Why this table exists

QBO auto-creates a **ReimburseCharge (RC)** for every Bill/Purchase line marked
Billable with a `CustomerRef`. This table captures RCs on scheduler cadence for
invoice-line linking (matching `qbo.InvoiceLine.LinkedTxnId` against
`qbo.ReimburseCharge.QboId`).

**Retired (U-280):** the table originally also carried `SourceTxnType`/
`SourceTxnId`/`SourceTxnLineId` — a reverse Bill/Purchase pointer intended to
feed a Tier-0 `ProposeInvoiceSourceLinks` linking arm. Measured 2026-08-16
(U-242): QBO never exposes that reverse `LinkedTxn` at any lifecycle stage
(100% NULL across all live rows, re-confirmed 2026-08-19); the Tier-0 arm that
would have read it was already removed as provably dead by U-244. The columns
and their preserve-on-repull handling were dropped as dead weight. See
`docs/rc_source_linking_signal_2026_08_16.md`.

## Sprocs

| Sproc | Purpose |
|-------|---------|
| `CreateQboReimburseCharge` | insert one RC (OUTPUT INSERTED.*) |
| `ReadQboReimburseChargeByQboIdAndRealmId` | upsert existence check |
| `ReadQboReimburseChargesByRealmId` | realm-wide read |
| `UpdateQboReimburseChargeByQboId` | ROWVERSION-guarded upsert-update |

**Pull-only staging:** no delete / reconcile-delete sproc.

## Keyspace

`QboId` is a QBO **string** id, disjoint from the `qbo.*.Id` BIGINT keyspace.
