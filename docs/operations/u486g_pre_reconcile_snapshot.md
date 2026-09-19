# U-486 Phase G — pre-reconcile snapshot (rollback record)

Captured 2026-09-19, before closing 14 coding items whose QBO line was recoded **in place**, outside the app.

U-483 closed 134 items whose staging line was REPLACED by an external recode (new `Line.Id`, staging row
gone). These 14 were edited **in place** — same `Line.Id`, line still present — so U-483's orphan-only
detection could not see them. Verified: all 14 now sit on real accounts (Other Construction, Auto & Truck
Maintenance, Natural Gas, Office supplies, Tools & Supplies) with `ItemRefName` NULL, i.e. **account-based**
recodes done directly in QuickBooks. The work is done; only the tracker was stale. Their local Expense is
already `completed`, so the new draft→review→complete flow could not reach them either.

Closed via a SECOND arm on `ReadExternallyResolvedCodingItemCandidates`. Arm 1 (orphaned line) is unchanged.
Arm 2 is deliberately **per-line**: if THIS line is coded, THIS item is done, regardless of siblings — a
multi-line purchase with one coded and one uncoded line closes only the coded one.

The placeholder test needs BOTH halves — `AccountRefName LIKE '%NEED TO CATEGORIZE%'` AND `ItemRefValue IS
NULL`. The cockpit's own recode set `ItemRef` and left `AccountRef` on the placeholder (U-484), so a
label-only test would leave item-based recodes permanently open, and an ItemRef-only test would wrongly
close an account-based line still sitting on 58999.

`u486g_pre_reconcile_snapshot.csv` / `.json` hold Id, PublicId, Status, WriteError, ModifiedDatetime and the
line's account/item as they were BEFORE. `Status` is overwritten by the mark, so this is the only record of
the prior values.

**Rollback:** `UPDATE dbo.[ExpenseCodingItem] SET [Status] = <snapshot>, [WriteError] = <snapshot>
WHERE [Id] = <snapshot Id>;`
