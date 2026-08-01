# qbo.ReimburseCharge — durable RC staging (U-186)

`qbo.reimburse_charge.sql` is the **canonical single source of truth** for the
`qbo.ReimburseCharge` table + its stored procedures. Apply it (idempotent —
guarded `CREATE TABLE`, `CREATE OR ALTER` sprocs) with:

```
python scripts/run_sql.py integrations/intuit/qbo/reimburse_charge/sql/qbo.reimburse_charge.sql
```

## Why this table exists

QBO auto-creates a **ReimburseCharge (RC)** for every Bill/Purchase line marked
Billable with a `CustomerRef`. Each RC carries a reverse `LinkedTxn` back to its
source Bill/Purchase (and that source's line). **QBO drops that reverse
`LinkedTxn` once the RC is consumed by an invoice (`HasBeenInvoiced=true`)** —
see KI-32. Because the invoice-linking playbook runs *after* the invoice is
completed in QBO, the RC→source hop is structurally unavailable at link time.

This table captures RCs on **scheduler cadence while still un-invoiced** and
**preserves the captured source pointer across the invoiced-flip re-pull**, so
deterministic Tier-0 linking can resolve:

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
`SourceTxnType` / `SourceTxnId` / `SourceTxnLineId`: the invoiced-flip re-pull
carries NULL for those, and must NOT null a captured pointer.

**Pull-only staging:** no delete / reconcile-delete sproc — a captured pointer
must survive; nothing removes staged RCs.

## Keyspace

`QboId`, `SourceTxnId`, `SourceTxnLineId` are QBO **string** ids, disjoint from
the `qbo.*.Id` BIGINT keyspace. Tier-0 joins go QBO-string → QBO-string; a
`qbo.Bill.Id` / `qbo.Purchase.Id` is never aliased as a dbo id.
