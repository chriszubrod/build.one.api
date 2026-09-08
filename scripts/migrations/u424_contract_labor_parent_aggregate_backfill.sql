-- =============================================================================
-- U-424 — ContractLabor parent-aggregate backfill.
--
-- HAND-OFF ONLY. Do not run this from a build session. Per the umbrella rule
-- `feedback_builders_never_mutate_prod_data.md`, /em reviews and executes it.
--
-- ⛔ DO NOT RUN THIS THROUGH `python scripts/run_sql.py`. That helper executes
--    every GO-separated batch in order and then DISCARDS the results
--    (`while cursor.nextset(): pass`), so it would swallow STEP 1's preview
--    rows AND STEP 2's per-batch PRINT progress — while still executing the
--    apply. You would mutate prod having seen nothing. Run the steps by hand,
--    one at a time, in a client that shows result sets and messages (Azure
--    Data Studio / SSMS / `sqlcmd`). Note `scripts/` needs the runner's IP
--    allowlisted on the SQL server either way.
--
-- WHY --------------------------------------------------------------------
-- Until U-424, two write paths mutated ContractLaborLineItem without
-- recomputing the parent:
--   * ContractLaborService._apply_decision_to_single_cl (reviewer approval)
--   * dbo.AggregateTimeEntryOnSubmit (a re-submit rewrote only the line it
--     owns, then stamped the parent with THIS TimeEntry's bucket totals)
-- so ContractLabor.TotalAmount / TotalHours / HourlyRate / Markup drifted from
-- the children.
--
-- MEASURED SCOPE (prod, read-only preview, 2026-09-08):
--   @IncludeBilled = 0  ->    6 rows  (5 pending_review, 1 submitted)
--   @IncludeBilled = 1  ->  416 rows  (410 of them billed)
-- At = 1 the net TotalAmount change is +$76,799.24, and 340 of the 416 carry a
-- NULL parent TotalAmount today — mostly Jan-Mar 2026 rows never populated,
-- rather than rows that drifted off a good value. Column split: TotalAmount
-- 366, HourlyRate 329, Markup 360, TotalHours 249.
--
-- NOTE on the two CLs this unit was opened against (1289, 1260): they are NOT
-- in the drift set. The $675.01 they were compared against is the sum of ALL
-- their line items; this backfill and the sproc both sum BILLABLE lines only.
-- Under that semantic each parent already equals its children. Do not expect
-- them in STEP 1's output.
--
-- The code fix stops NEW drift. This heals the rows already wrong.
--
-- SEMANTIC ---------------------------------------------------------------
-- Byte-for-byte the arithmetic of dbo.UpdateContractLaborAggregates, including
-- its intermediate CASTs — the casts are load-bearing, not cosmetic: dividing
-- raw SUMs instead would land a different 4th decimal on HourlyRate/Markup and
-- the sproc would then "re-drift" every healed row on its next run. Expressed
-- set-based rather than looped per row (`feedback_backfill_setbased_under_load
-- .md`: per-row backfills TCP-drop under load).
--
-- If you change dbo.UpdateContractLaborAggregates, change this to match.
--   TotalHours  = SUM(Hours) over ALL lines
--   TotalAmount = SUM(Price) over BILLABLE lines only
--   HourlyRate  = SUM(Hours*Rate) / SUM(Hours), billable lines with both set
--   Markup      = (TotalAmount - SUM(Hours*Rate)) / SUM(Hours*Rate)
--
-- SAFETY -----------------------------------------------------------------
--  * CHILDLESS ContractLabor rows are NEVER touched. The sproc ISNULLs its
--    sums to 0, so running it on a parent with no line items would ZERO a
--    legitimately parent-only row (import_service.py creates those). The INNER
--    JOIN below excludes them structurally.
--  * Idempotent. Only rows that actually differ are updated, so a second run
--    reports 0 and an interrupted run resumes cleanly.
--  * Batched with a COMMIT per batch — no long-held transaction under load.
--    The batch predicate is the difference itself, so each batch strictly
--    shrinks the remaining set and the loop cannot spin.
--  * Does NOT touch Bill, BillLineItem, or any QBO / Box / SharePoint
--    artifact. Bill totals were always computed by summing line items
--    (bill_service.py), so no invoice or vendor payment changes.
--
-- ⚠ ONE DECISION FOR /em — @IncludeBilled ---------------------------------
-- Default 0: entries already at Status='billed' are LEFT ALONE (6 rows).
--   ⚠ Inspected 2026-09-08: those 6 are NOT a mechanical cleanup. FIVE change
--     no money at all — only HourlyRate in the 4th decimal (CL 1332 46.2500 ->
--     46.2497; 1334/1340/1341 +0.0005; 1339 +0.0003), the effective-markup
--     artifact noted above. The ONLY money change is CL 1252 ($390.00 ->
--     $0.00), one of the four zeroing rows listed below. So the entire
--     substance of a = 0 run is that single judgment call.
-- Set to 1 to heal historical billed rows too (416 rows).
--   FOR: ContractLaborPDFService.generate_pdfs_for_billed_entries builds the
--        client time-log PDF from the PARENT TotalAmount/TotalHours/HourlyRate,
--        so billed rows with stale parents still emit a wrong PDF on any
--        regeneration. 340 of the 416 are simply NULL parents being populated.
--   AGAINST: it rewrites rows sitting behind already-issued bills. The money
--        billed does not change either way — only the parent's display copy.
--
--   ⚠ FOUR ROWS WOULD BE ZEROED at = 1, because every one of their lines is
--     non-billable and TotalAmount counts billable lines only:
--         CL 571  billed     2026-06-04  Emilson Cordova    555.00 -> 0.00
--         CL 1252 submitted  2026-08-17  Wilmer Diaz        390.00 -> 0.00
--         CL 570  billed     2026-06-04  Ricky Moreno       390.00 -> 0.00
--         CL 496  billed     2026-03-03  Michael Jacobson    78.75 -> 0.00
--     That is CORRECT under the billable-only semantic — the contractor is
--     still paid those hours through the A/P bill, since
--     generate_bills_for_vendor bills non-billable lines at cost — but the
--     row then reads as $0 to a human scanning for "what we paid". 47 billed
--     CLs have children but no billable child. Bless this explicitly.
--
-- Set the SAME value in STEP 1 and STEP 2.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ── STEP 1 — PREVIEW (read-only). Run this ALONE first. ──────────────────
-- Every row it returns is one STEP 2 would change. Eyeball the before/after
-- money before applying. As of 2026-09-08 this returned 6 rows at
-- @IncludeBilled = 0 and 416 at = 1; a materially different count means the
-- data moved since, so re-read the numbers before applying.

DECLARE @IncludeBilled BIT = 0;

;WITH agg AS (
    SELECT
        li.[ContractLaborId] AS ClId,
        CAST(SUM(ISNULL(li.[Hours], 0)) AS DECIMAL(6,2))            AS TotalHours,
        CAST(SUM(CASE WHEN li.[IsBillable] = 1
                      THEN ISNULL(li.[Price], 0) ELSE 0 END)
             AS DECIMAL(18,2))                                      AS TotalAmount,
        CAST(SUM(CASE WHEN li.[IsBillable] = 1
                       AND li.[Hours] IS NOT NULL
                       AND li.[Rate]  IS NOT NULL
                      THEN li.[Hours] ELSE 0 END)
             AS DECIMAL(18,4))                                      AS BillableHours,
        CAST(SUM(CASE WHEN li.[IsBillable] = 1
                       AND li.[Hours] IS NOT NULL
                       AND li.[Rate]  IS NOT NULL
                      THEN li.[Hours] * li.[Rate] ELSE 0 END)
             AS DECIMAL(18,2))                                      AS BillablePreMarkup
    FROM dbo.[ContractLaborLineItem] li
    GROUP BY li.[ContractLaborId]
),
calc AS (
    SELECT
        cl.[Id], cl.[EmployeeName], cl.[WorkDate], cl.[Status],
        cl.[TotalHours]  AS OldTotalHours,
        cl.[TotalAmount] AS OldTotalAmount,
        cl.[HourlyRate]  AS OldHourlyRate,
        cl.[Markup]      AS OldMarkup,
        agg.TotalHours   AS NewTotalHours,
        agg.TotalAmount  AS NewTotalAmount,
        CASE WHEN agg.BillableHours > 0
             THEN CAST(agg.BillablePreMarkup / agg.BillableHours AS DECIMAL(18,4))
             END         AS NewHourlyRate,
        CASE WHEN agg.BillablePreMarkup > 0
             THEN CAST((agg.TotalAmount - agg.BillablePreMarkup)
                       / agg.BillablePreMarkup AS DECIMAL(18,4))
             END         AS NewMarkup
    FROM dbo.[ContractLabor] cl
    INNER JOIN agg ON agg.ClId = cl.[Id]      -- childless parents excluded
    WHERE (@IncludeBilled = 1 OR cl.[Status] <> 'billed')
)
SELECT
    [Id], [EmployeeName], [WorkDate], [Status],
    OldTotalAmount, NewTotalAmount,
    NewTotalAmount - ISNULL(OldTotalAmount, 0) AS AmountDelta,
    OldTotalHours,  NewTotalHours,
    OldHourlyRate,  NewHourlyRate,
    OldMarkup,      NewMarkup
FROM calc
WHERE ISNULL(OldTotalHours,  -1) <> ISNULL(NewTotalHours,  -1)
   OR ISNULL(OldTotalAmount, -1) <> ISNULL(NewTotalAmount, -1)
   OR ISNULL(OldHourlyRate,  -1) <> ISNULL(NewHourlyRate,  -1)
   OR ISNULL(OldMarkup,      -1) <> ISNULL(NewMarkup,      -1)
ORDER BY ABS(NewTotalAmount - ISNULL(OldTotalAmount, 0)) DESC, [Id];
GO


-- ── STEP 2 — APPLY (batched, idempotent, resumable). ─────────────────────
-- Identical predicate to STEP 1. TOP is applied AFTER the difference filter,
-- so every batch does real work and the loop terminates exactly when the set
-- is converged.

DECLARE @IncludeBilled BIT = 0;
DECLARE @BatchSize     INT = 500;
DECLARE @Updated       INT = 1;
DECLARE @Total         INT = 0;

WHILE @Updated > 0
BEGIN
    BEGIN TRANSACTION;

    ;WITH agg AS (
        SELECT
            li.[ContractLaborId] AS ClId,
            CAST(SUM(ISNULL(li.[Hours], 0)) AS DECIMAL(6,2))        AS TotalHours,
            CAST(SUM(CASE WHEN li.[IsBillable] = 1
                          THEN ISNULL(li.[Price], 0) ELSE 0 END)
                 AS DECIMAL(18,2))                                  AS TotalAmount,
            CAST(SUM(CASE WHEN li.[IsBillable] = 1
                           AND li.[Hours] IS NOT NULL
                           AND li.[Rate]  IS NOT NULL
                          THEN li.[Hours] ELSE 0 END)
                 AS DECIMAL(18,4))                                  AS BillableHours,
            CAST(SUM(CASE WHEN li.[IsBillable] = 1
                           AND li.[Hours] IS NOT NULL
                           AND li.[Rate]  IS NOT NULL
                          THEN li.[Hours] * li.[Rate] ELSE 0 END)
                 AS DECIMAL(18,2))                                  AS BillablePreMarkup
        FROM dbo.[ContractLaborLineItem] li
        GROUP BY li.[ContractLaborId]
    ),
    calc AS (
        SELECT
            cl.[Id],
            cl.[TotalHours]  AS OldTotalHours,
            cl.[TotalAmount] AS OldTotalAmount,
            cl.[HourlyRate]  AS OldHourlyRate,
            cl.[Markup]      AS OldMarkup,
            agg.TotalHours   AS NewTotalHours,
            agg.TotalAmount  AS NewTotalAmount,
            CASE WHEN agg.BillableHours > 0
                 THEN CAST(agg.BillablePreMarkup / agg.BillableHours AS DECIMAL(18,4))
                 END         AS NewHourlyRate,
            CASE WHEN agg.BillablePreMarkup > 0
                 THEN CAST((agg.TotalAmount - agg.BillablePreMarkup)
                           / agg.BillablePreMarkup AS DECIMAL(18,4))
                 END         AS NewMarkup
        FROM dbo.[ContractLabor] cl
        INNER JOIN agg ON agg.ClId = cl.[Id]  -- childless parents excluded
        WHERE (@IncludeBilled = 1 OR cl.[Status] <> 'billed')
    ),
    batch AS (
        SELECT TOP (@BatchSize) *
        FROM calc
        WHERE ISNULL(OldTotalHours,  -1) <> ISNULL(NewTotalHours,  -1)
           OR ISNULL(OldTotalAmount, -1) <> ISNULL(NewTotalAmount, -1)
           OR ISNULL(OldHourlyRate,  -1) <> ISNULL(NewHourlyRate,  -1)
           OR ISNULL(OldMarkup,      -1) <> ISNULL(NewMarkup,      -1)
        ORDER BY [Id]
    )
    UPDATE batch
    SET OldTotalHours  = NewTotalHours,
        OldTotalAmount = NewTotalAmount,
        OldHourlyRate  = NewHourlyRate,
        OldMarkup      = NewMarkup;

    SET @Updated = @@ROWCOUNT;
    SET @Total   = @Total + @Updated;

    COMMIT TRANSACTION;

    IF @Updated > 0
        PRINT CONCAT('U-424 backfill: batch updated ', @Updated,
                     ' ContractLabor row(s); running total ', @Total, '.');
END

PRINT CONCAT('U-424 backfill COMPLETE: ', @Total,
             ' ContractLabor parent row(s) reconciled to their line items.');
GO


-- ── STEP 3 — VERIFY. Re-run STEP 1. It must return ZERO rows. ────────────
