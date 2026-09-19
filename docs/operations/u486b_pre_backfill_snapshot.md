# U-486 Phase B — pre-backfill snapshot (rollback record)

Captured 2026-09-19, immediately before flipping 316 `completed` expenses to `draft` so they form the
GL-coding work queue (design: `docs/design/u486-expense-draft-coding-workflow.md`).

**Selection** — the predicate IS the safety property:

```sql
e.[Status] = N'completed'
  AND pl.[AccountRefName] LIKE N'%NEED TO CATEGORIZE%'
  AND pl.[ItemRefValue] IS NULL
```

The `ItemRefValue IS NULL` clause is load-bearing. The cockpit's recode sets `ItemRef` and deliberately
leaves `AccountRef` on the 58999 placeholder (U-484), so **38** already-coded expenses still read as 58999.
Selecting on the account label alone would have re-opened finished work and invited a duplicate QBO write.
Verified at apply time: 354 label-matching, **316** selected, **38** correctly excluded.

`u486b_pre_backfill_snapshot.csv` / `.json` hold Id, PublicId, Status, StatusOrigin, StatusDatetime,
StatusSourceRef and ModifiedDatetime as they were BEFORE the flip. All 316 were `completed` with
`StatusOrigin = 'qbo_pull'`.

**Rollback:** for each row,

```sql
UPDATE dbo.[Expense]
SET [Status] = N'completed', [StatusOrigin] = N'qbo_pull',
    [StatusDatetime] = <snapshot>, [StatusSourceRef] = <snapshot>
WHERE [Id] = <snapshot Id>;
```

`IsDraft` is a PERSISTED COMPUTED column over `Status` — it follows automatically, never set it directly.

**Note on provenance:** the backfill stamps `StatusOrigin = 'coding_backfill'`, which is the only thing
distinguishing these 316 from the 10 genuine manual drafts that already existed. That distinction cannot be
reconstructed after the fact, which is why it is stamped at write time.
