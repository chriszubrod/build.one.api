# U-483 pre-reconcile snapshot — rollback record

Captured 2026-09-18, immediately before marking 134 ExpenseCodingItem rows `resolved_externally`.

These rows were recoded OUTSIDE the cockpit (in QBO directly, or by Ramp), which assigns a new QBO
`Line.Id`; the pull then replaced the staging line and the coding item lost its anchor. The work is done —
verified: no parent purchase retains a `NEED TO CATEGORIZE` line — but the tracker still read open.

`u483_pre_reconcile_snapshot.csv` holds Id, PublicId, Status, WriteError and ModifiedDatetime as they
were BEFORE the reconcile. Status is overwritten by the mark, so this file is the only record of the
prior values.

**Rollback:** for each row, `UPDATE dbo.ExpenseCodingItem SET [Status] = <snapshot Status>,
[WriteError] = <snapshot WriteError> WHERE [Id] = <snapshot Id>`.

Pre-state counts: pending 62 · suggested 54 · flagged 14 · changed_in_qbo 4 = 134.
