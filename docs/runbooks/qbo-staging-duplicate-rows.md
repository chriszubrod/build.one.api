# Runbook: QBO Staging Duplicate Rows

Duplicate `(QboId, RealmId)` pairs in `qbo.*` staging tables break downstream
module projections and can silently destroy connector mappings on cleanup.

## Symptom

- A QBO pull reports success but expense/bill/vendor projections behave oddly.
- Duplicate-detection queries return rows:

```sql
SELECT 'Purchase' AS [Table], QboId, RealmId, COUNT(*) AS DupCount
FROM qbo.Purchase
GROUP BY QboId, RealmId
HAVING COUNT(*) > 1;
```

- U-218d index migration fails with **"Cannot create unique index… duplicate key"**.
- `dbo.ExpenseCodingItem` rows point at `QboPurchaseId` values that no longer
  exist after a naive dedupe (silent orphan — no FK).

## Severity

**High** for Purchase duplicates that carry `qbo.PurchaseExpense` mappings.
A positional keep-MIN or keep-MAX dedupe destroys 5–11 mappings via
`ON DELETE CASCADE` without raising an error. Investigate before any cleanup.

## Why duplicates happen

Eight QBO staging tables (`Bill`, `Purchase`, `Vendor`, `Customer`, `Item`,
`Account`, `Term`, `ReimburseCharge`) had no unique constraint on
`(QboId, RealmId)` when a pull double-ran. Invoice, VendorCredit, and
Attachable already had uniqueness; the rest did not.

The prod incident (2026-08): one purchase pull double-ran, inserting 17
consecutive Id pairs in block 11265–11305 (QboIds 69248–69339) plus one
Vendor pair (Ids 1139/1140). Identical row content; only the staging PK differs.

## The CASCADE trap — survivor is NOT positional

Both FKs into `qbo.Purchase` are **`ON DELETE CASCADE`**:

- `FK_PurchaseExpense_QboPurchase` (`qbo.PurchaseExpense.QboPurchaseId`)
- `FK_QboPurchaseLine_QboPurchase` (`qbo.PurchaseLine.QboPurchaseId`)

`qbo.PurchaseLineExpenseLineItem` cascades one level deeper from
`PurchaseLine`.

`qbo.PurchaseExpense` sits on the **MIN** Id in 11 duplicate groups and the
**MAX** Id in 5 others. Measured damage:

| Rule | Mappings destroyed |
|------|-------------------|
| keep-MIN | 5 `PurchaseExpense` rows |
| keep-MAX | 11 `PurchaseExpense` rows |

Neither raises an error. The correct survivor is **whichever member holds the
`qbo.PurchaseExpense` row** (or SyncToken then MIN Id when neither has one —
QboId 69333 in prod).

Also orphan silently (no FK):

- `dbo.ExpenseCodingItem.QboPurchaseId`
- `qbo.VendorVendor.QboVendorId`

Delete `ExpenseCodingItem` rows explicitly before removing doomed Purchase rows.

## Diagnosis

### Step 1 — Inventory duplicate groups (all 8 tables)

```sql
SELECT 'Purchase' AS [Table], QboId, RealmId, COUNT(*) AS DupCount
FROM qbo.Purchase GROUP BY QboId, RealmId HAVING COUNT(*) > 1
UNION ALL
SELECT 'Vendor', QboId, RealmId, COUNT(*) FROM qbo.Vendor
GROUP BY QboId, RealmId HAVING COUNT(*) > 1
-- repeat for Bill, Customer, Item, Account, Term, ReimburseCharge
ORDER BY [Table], DupCount DESC;
```

### Step 2 — For each Purchase duplicate pair, find the mapping holder

```sql
SELECT p.Id, p.QboId, p.RealmId, p.SyncToken,
       CASE WHEN pe.QboPurchaseId IS NOT NULL THEN 1 ELSE 0 END AS HasPurchaseExpense
FROM qbo.Purchase p
LEFT JOIN qbo.PurchaseExpense pe ON pe.QboPurchaseId = p.Id
WHERE p.QboId = '<QboId>' AND p.RealmId = '<RealmId>'
ORDER BY p.Id;
```

**Keep** the row with `HasPurchaseExpense = 1`. If both are 0, prefer higher
`SyncToken`, then lower `Id`.

### Step 3 — Check ExpenseCodingItem orphans and human work

```sql
SELECT eci.*
FROM dbo.ExpenseCodingItem eci
WHERE eci.QboPurchaseId IN (<doomed_ids>)
  AND (
    eci.ConfirmedProjectId IS NOT NULL
    OR eci.ConfirmedSubCostCodeId IS NOT NULL
    OR eci.ConfirmedDescription IS NOT NULL
    OR eci.ConfirmedAt IS NOT NULL
    OR eci.WrittenAt IS NOT NULL
    OR eci.ClaimedByUserId IS NOT NULL
  );
```

Any row returned blocks automated dedupe until reviewed.

## Recovery — apply order

**Critical window:** `QboPurchaseService._upsert_purchase` is read-then-insert with
no idempotency key. Overlapping purchase pulls are live in prod. A purchase pull
tick landing **between** dedupe apply and unique-index apply can re-create a
duplicate; `CREATE UNIQUE INDEX` then fails and rolls back, and the dedupe
file's hardcoded 17-Id list **cannot** clean the new duplicate — it would need
a new migration.

Apply **in this order**, with no purchase pull in the gap:

1. **Pause the QBO purchase pull timer** on `build-one-scheduler` (disable
   `sync_qbo_purchase` or the equivalent admin timer) so no overlapping pull
   can insert between the two migration files.

2. **`scripts/migrations/u218d_qbo_staging_dedupe.sql`** via
   `python scripts/run_sql.py`
   - Uses an explicit doomed-Id list (not derived at apply time).
   - Pre-flight guards `RAISERROR` severity 16 on safety violations.
   - Idempotent: a second run is a clean no-op.

3. **Verify immediately** — re-run the duplicate-group query from Step 1;
   expect zero rows. (`run_sql.py` never prints SELECT results; this manual
   check is mandatory.)

4. **`scripts/migrations/u218d_qbo_staging_unique_indexes.sql`** via
   `python scripts/run_sql.py` — run **back-to-back** with step 2, before
   re-enabling the purchase timer.
   - Adds filtered `UQ_Qbo*_QboId_RealmId` indexes on all 8 tables.
   - Drops redundant `IX_QboReimburseCharge_QboId_RealmId` before creating
     the unique index on the same pair.

5. **Re-enable the QBO purchase pull timer** after both files succeed and
   verification passes.

## If index creation fails

**Symptom:** `CREATE UNIQUE INDEX` error 1505 or "duplicate key was found".

**Cause:** dedupe did not run, did not complete, or new duplicates landed
between dedupe and index apply.

**Recovery:**

1. Do **not** skip index creation with a guard — `run_sql.py` reports success
   even when a skipped step leaves indexes absent.
2. Re-run the duplicate inventory query; identify surviving groups.
3. For **new** duplicates not covered by the U-218d explicit Id list, resolve
   manually using Step 2 (mapping-holder rule), then extend the migration or
   run a one-off delete before retrying indexes.
4. Re-apply dedupe (no-op if already clean), verify zero duplicates, retry
   unique-index migration.

## Prevention

**Before indexes exist:** pause purchase pulls during the dedupe → index apply
window (see Recovery apply order). The root cause — read-then-insert upsert
with no idempotency and overlapping scheduler pulls — remains live until the
unique indexes land.

**After indexes exist:** U-218d filtered unique indexes
(`WHERE QboId IS NOT NULL AND RealmId IS NOT NULL`) prevent a future
double-pull from inserting a second row. A repeat attempt hits SQL error 2601;
the per-item purchase pull handler turns that into a recorded staging failure
rather than a crashed pull, so the tick completes and the watermark advances
without accumulating silent duplicates.

Base SQL files under `integrations/intuit/qbo/*/sql/` carry the same guarded
index blocks for from-scratch applies. Guard test:
`tests/test_u218d_staging_unique_indexes.py`.

## Verification

After both migrations:

```sql
SELECT i.name, OBJECT_NAME(i.object_id) AS TableName
FROM sys.indexes i
WHERE i.name LIKE 'UQ_Qbo%_QboId_RealmId'
ORDER BY TableName;
-- expect 11 rows (8 new + Invoice + VendorCredit + Attachable uses UX_ prefix)
```

Re-run duplicate-group query — zero rows across all 8 tables.
