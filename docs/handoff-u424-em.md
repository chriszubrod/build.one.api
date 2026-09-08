# U-424 → /em hand-off

Copy the block below into an `/em` session.

```text
========================= BEGIN /em HAND-OFF: U-424 =========================

UNIT
  U-424 — ContractLabor parent aggregates go stale.
  Repo:    build.one.api
  Commits: 1c6ff774 (fix + tests + backfill), 0dad02e9 (backfill run_sql.py warning)
  Status:  pushed to origin/master. Suite 3312 green. Nothing applied to prod.
  Detail:  build.one.api/SESSION_NOTES.md § U-424

WHAT YOU ARE APPROVING
  Three prod actions, none of which I ran:
    1. Apply entities/contract_labor/sql/dbo.contract_labor.sql
    2. Apply entities/time_entry/sql/dbo.time_entry.sql
    3. Deploy the API image, then run a data backfill
  Per feedback_builders_never_mutate_prod_data.md these are yours to execute.

WHY
  ContractLabor.TotalAmount / TotalHours / HourlyRate / Markup drifted from
  the line items they summarize, because two write paths mutated
  ContractLaborLineItem without recomputing the parent:
    * the reviewer-approval path (_apply_decision_to_single_cl)
    * dbo.AggregateTimeEntryOnSubmit, which stamps the parent from THIS
      TimeEntry's buckets alone — so once a PM splits a day in PUT /{id}/bill
      (split siblings carry SourceTimeEntryId = NULL), a re-submit rewrites
      only the original line but still stamps the parent with bucket totals.
  Prod: CL 1289 carried $590.63 while its children summed to $675.01;
        CL 1260 carried $0.00 against the same $675.01.
  Exposure beyond the CL list page: ContractLaborPDFService builds the
  CLIENT-FACING time-log PDF (attached to BillLineItems, pushed to
  SharePoint/Box) from the PARENT totals, while that same PDF's header shows
  the BillLineItem price — a stale parent makes it contradict itself.
  Bill generation is NOT affected (bill_service.py sums line items).

--------------------------------------------------------------------------
STEP 1 — APPLY dbo.contract_labor.sql   ⚠ MUST BE FIRST
--------------------------------------------------------------------------
  python scripts/run_sql.py entities/contract_labor/sql/dbo.contract_labor.sql

  Changes to dbo.UpdateContractLaborAggregates:
    * new @ReturnRow BIT = 1 — suppresses the trailing SELECT for SQL callers
      (a nested EXEC emitting a row would prepend a result set to the caller's
      cursor: the pyodbc break class the SET NOCOUNT ON pins guard). Gated
      ISNULL(@ReturnRow, 1) = 1 so an explicit NULL still returns the row.
    * three scans of the same @Id predicate collapsed to one conditional pass.
    * arithmetic UNCHANGED.

  ⚠ ORDER IS LOAD-BEARING. Step 2's sproc EXECs this one with @ReturnRow.
    Deferred name resolution lets step 2 COMPILE against the missing
    parameter, then EVERY iOS time-entry submit fails at runtime with
    SQL 8145 — the U-037 break class. Do not reverse these.

  Backward compatible: @ReturnRow defaults to 1 and no Python caller passes
  it, so applying this before the API deploy changes nothing for live code.

  VERIFY (read-only):
    SELECT name FROM sys.parameters
     WHERE object_id = OBJECT_ID('dbo.UpdateContractLaborAggregates');
    -- expect: @Id, @ReturnRow

--------------------------------------------------------------------------
STEP 2 — APPLY dbo.time_entry.sql
--------------------------------------------------------------------------
  python scripts/run_sql.py entities/time_entry/sql/dbo.time_entry.sql

  Changes to dbo.AggregateTimeEntryOnSubmit:
    * the ContractLabor parent UPDATE no longer sets TotalHours / HourlyRate /
      Markup / TotalAmount. A new tail
        EXEC dbo.UpdateContractLaborAggregates @Id = @ParentRowId, @ReturnRow = 0
      is their sole writer on the update path. This removes a dead store AND a
      second ROWVERSION bump per submit that was invalidating a client's
      optimistic-concurrency token twice.
    * the INSERT branch still sets them (no row to recompute from yet).
    * the EmployeeLabor branch is untouched — it has no recompute sproc.
      Booked in api/TODO.md § U-424.

  This is the hot iOS submit path. Deliberate semantic shift: multi-project
  entries no longer keep those columns NULL — they get SUM(Price) and the
  billable weighted-average rate, which is what PUT /{id}/bill has always
  produced and what the time-log PDF needs (NULL silently understated it).

  VERIFY (read-only):
    SELECT CASE WHEN definition LIKE '%@ReturnRow = 0%' THEN 'OK' ELSE 'STALE' END
      FROM sys.sql_modules WHERE object_id = OBJECT_ID('dbo.AggregateTimeEntryOnSubmit');
  SMOKE: submit one iOS time entry and confirm it succeeds (this is the
  statement that would raise 8145 if step 1 were skipped).

--------------------------------------------------------------------------
STEP 3 — DEPLOY THE API IMAGE
--------------------------------------------------------------------------
  Standard flow (DEPLOY.md). Tag MUST be :latest. Use stop + start, NOT
  `az webapp restart` — restart can relaunch the CACHED image and still
  report success. A sub-30s "up" is a cache-hit tell, not a fast deploy.

  Code changes: new choke point ContractLaborService.recompute_aggregates,
  called by the reviewer-approval path, update_by_public_id, and the router
  (which no longer reaches through service.repo).
  BEHAVIOR CHANGE WORTH KNOWING: PUT /{id}/bill no longer 500s when the
  recompute fails — it logs and returns the pre-recompute row. The line-item
  writes have already committed at that point, so raising handed the caller a
  retryable-looking error for work that succeeded.

  Order vs steps 1-2 is free (the API never passes @ReturnRow), but deploy
  BEFORE step 4 so nothing re-drifts between the backfill and the fix.

--------------------------------------------------------------------------
STEP 4 — BACKFILL THE ROWS THAT ARE ALREADY WRONG
--------------------------------------------------------------------------
  File: scripts/migrations/u424_contract_labor_parent_aggregate_backfill.sql

  ⛔ DO NOT run it through scripts/run_sql.py. That helper runs every
     GO-separated batch and then DISCARDS results (`while cursor.nextset():
     pass`) and never surfaces PRINT — it would swallow the preview and the
     progress output while still executing the apply. Run the three steps BY
     HAND in Azure Data Studio / SSMS / sqlcmd. (scripts/ also needs the
     runner's IP allowlisted on the SQL server.)

  STEP 1 (preview, read-only) — run alone first. Every row returned is one
    the apply would change. Expect CL 1260 and CL 1289.
  STEP 2 (apply) — batched 500/loop, COMMIT per batch, idempotent, resumable.
    The batch predicate is the difference itself, so each batch strictly
    shrinks the set. Set-based per feedback_backfill_setbased_under_load.md.
  STEP 3 (verify) — re-run the preview; must return ZERO rows.

  SAFETY: childless ContractLabor rows are excluded structurally by an INNER
  JOIN — the sproc ISNULLs its sums to 0 and would ZERO a parent-only row
  (import_service.py legitimately creates those). Touches no Bill,
  BillLineItem, QBO, Box or SharePoint artifact; bill totals were always
  computed by summing line items, so no invoice or vendor payment changes.

  ⚠ ONE DECISION IS YOURS — @IncludeBilled (default 0, set the SAME value in
    both steps):
      0 = leave Status='billed' rows alone.
      1 = heal them too. FOR: generate_pdfs_for_billed_entries builds the
          client time-log PDF from the PARENT, so billed rows with stale
          parents still emit a wrong PDF on any regeneration. AGAINST: it
          rewrites rows sitting behind already-issued bills. The money billed
          does not change either way — only the parent's display copy of it.
    My read: run with 0 first, look at how many billed rows step 1 reports at
    @IncludeBilled = 1, and decide with that count in hand.

--------------------------------------------------------------------------
ROLLBACK
--------------------------------------------------------------------------
  SQL: both files are CREATE OR ALTER and idempotent; revert by applying the
  pre-1c6ff774 versions, in the REVERSE order (time_entry first, then
  contract_labor — otherwise the old time_entry body would still be EXECing a
  parameter you just removed).
  Backfill: not reversible in place. It only moves parents toward the sum of
  their children, so the "undo" is the drift itself — capture STEP 1's
  preview output before applying if you want a before-image.

--------------------------------------------------------------------------
KNOWN-REMAINING, BOOKED NOT FIXED (api/TODO.md § U-424)
--------------------------------------------------------------------------
  * AggregateTimeEntryOnSubmit resolves its line item with a scalar
    assignment from a potentially multi-row SELECT. If a CL holds two lines
    sharing (ContractLaborId, SourceTimeEntryId, ProjectId) it silently
    updates one. Post-U-424 the PARENT is no longer a casualty, but the
    CHILDREN still go wrong there — which means the BILL goes wrong. Higher
    priority than it looks; needs its own unit.
  * That sproc never DELETEs line items for project buckets that have
    disappeared from the TimeEntry. generate_bills already billed the orphan;
    post-U-424 the parent reports it too (honestly matching the bill instead
    of masking it).
  * EmployeeLabor has no equivalent recompute sproc — same shape, same gap.

========================== END /em HAND-OFF: U-424 ==========================
```
