# U-652d5 — Deterministic CodingSuggestion.ExpenseId; drop the slow fallback join

Approved by Chris at Gate 1, 2026-11-10. This covers TODO items U-652d (9) and (11), and absorbs (3), the OR-join, which was measured and folded in here.

## Problem

`dbo.CodingSuggestion.ExpenseId` is set only when a suggestion is seeded and an expense already exists. Nothing stamps it later, when the QBO purchase pull creates the expense.

Readers paper over the gap with an OR-join fallback that matches on purchase identity (`QboPurchaseQboId` + `RealmId`). Measured on prod read-only (2026-11-10): `ReadCodingSuggestionStateByExpenseIds` takes a median 296 ms per 100 expense ids with the OR-join, and 31 ms when joined by ExpenseId alone.

The fallback also hides the gap. A reader sees the row, so nothing ever re-seeds and fills ExpenseId.

Prod, 2026-11-10: 916 suggestions; 332 have a NULL ExpenseId.
- 331 of those have no `dbo.Expense`. That's legitimate long term: the pull permanently skips purchases with no payee, a customer/employee payee, or a line-less header.
- 1 has an expense and can be backfilled.

## Change

Round 1 (2026-11-10 EM review) fixed two P2/P3 gaps: the purchase pull is incremental, so "the next tick heals" was false, and a seed/pull race could insert a NULL row the pull never revisits. The changes now are:

1. **Write-time resolve** in `UpsertCodingSuggestionForPurchase` (dbo.coding_suggestion.sql). For a new or NULL expense-target row, ExpenseId comes from `dbo.Expense` by purchase identity (`QboId` + `RealmId`) when the caller did not pass one. An id another row already holds is never taken (`UQ_CodingSuggestion_ExpenseId`). The matched branch still fills only when NULL.
2. **Stamp procedure** `StampCodingSuggestionExpenseId(@RealmId, @QboPurchaseQboId, @ExpenseId)`: an UPDATE of expense-target rows where ExpenseId is NULL or dangling, with the same uniqueness guard. It returns `SELECT @@ROWCOUNT AS [Stamped]`. It does NOT bump `ModifiedDatetime`, because that would restart the `enqueued_stranded` age clock. Idempotent.
3. **Connector call site.** `PurchaseExpenseConnector.sync_from_qbo_purchase` calls the stamp in ONE place: after the fast path's `outcome.entity` resolves. Every successful outcome passes through it: create, update, the completed-expense auto-complete branch (which returns a refreshed entity), and the race-resolved path. It is best-effort, outside the create rollback, and logs ids plus the exception class only.
4. **Residual heal** `HealCodingSuggestionExpenseIds(@Limit = 500)`: a set-based UPDATE TOP (@Limit) of expense-target rows whose ExpenseId is NULL or dangling, matched by purchase identity, with the same guard (`x.[ExpenseId] = e.[Id]`). It returns `SELECT @@ROWCOUNT AS [Healed]` and does not bump `ModifiedDatetime`.
   - Run by the **stranded sweeper's apply run** every 15 min, after the zero-write gate, and failure-isolated. Reported as the additive key `expense_id_healed`.
   - Run by the backfill's `--apply`, which loops until a batch heals 0.
5. **Backfill** `scripts/backfill_coding_suggestion_expense_id.py`: dry-run default (SELECT-only). `--apply` EXECs the heal proc (no UPDATE text of its own). Exit 1 if NULL rows whose purchase has an expense OR dangling ExpenseIds remain.
6. **Drop the fallback**, after the backfill shows zero: `ReadCodingSuggestionStateByExpenseIds` joins `e.[Id] = cs.[ExpenseId]` only. `ReadUncodedCompletedExpenseCandidates` joins on ExpenseId; its RealmId and QboPurchaseQboId conjuncts are KEPT as a consistency guard (a mis-stamped ExpenseId cannot attach a foreign suggestion). The redrive script's arms lose their NULL branch.

## Keep

- ExpenseId stays nullable, and the check constraint is unchanged.
- The purchase-keyed reads (queue, metrics, exists-for-purchase, externally-resolved) are unchanged. That's how the 331 no-expense rows remain codable.

## Safety

- Removing the fallback can't hide a no-expense row from a by-expense-id read: those rows never had an expense to match.
- It could only hide a NULL row whose expense exists. Steps 2 and 3 eliminate those, and a pre-deploy query proves zero.

## Gate 2 order

Stage split (EM decision): the simplified reads live in the SAME base files as the step-(a) procs (`ReadCodingSuggestionStateByExpenseIds` in `entities/coding_suggestion/sql/dbo.coding_suggestion.sql`; `ReadUncodedCompletedExpenseCandidates` in `entities/expense/sql/dbo.expense.sql`). Applying a base file applies every proc in it. So the reads cannot wait for step (d) unless their edit is split out. EM decision: **recommended, split the read edits into their own commit**, applied only at step (d). Otherwise the window between (a) and (c) lets the old reads miss NULL rows until the backfill finishes.

(a) Apply `dbo.coding_suggestion.sql` and `dbo.expense.sql` WITHOUT the simplified reads, with the new stamp and heal procs and the MERGE write-time resolve. The old image is unaffected: the MERGE change only stops new NULL rows, and it reads no existing data differently.
(b) Deploy the image. It calls the stamp from the pull, and the sweeper heals residuals.
(c) Backfill: dry-run, then `.venv/bin/python scripts/backfill_coding_suggestion_expense_id.py --apply`. It must exit 0 (zero NULL rows with a purchase expense, zero dangling ExpenseIds).
(d) ONLY THEN apply STAGE B, the simplified reads (EM decision 2026-11-10: split out of this commit). Stage B is the patch scratchpad/u652d5_stageB.patch, applied on top of stage A (git apply, verified with git apply --check). It holds the read edits of ReadCodingSuggestionStateByExpenseIds (dbo.coding_suggestion.sql) and ReadUncodedCompletedExpenseCandidates (dbo.expense.sql), the redrive NULL-branch removal, and the pins for them. Stage A keeps the OR-join reads. `ReadCodingSuggestionStateByExpenseIds` (dbo.coding_suggestion.sql) and `ReadUncodedCompletedExpenseCandidates` (dbo.expense.sql). Then the redrive script's NULL branch drop goes live.
(e) Re-run the backfill dry-run after two pull ticks. Expect 0 / 0 / 0.
