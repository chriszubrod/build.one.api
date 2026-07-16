-- ============================================================================
-- SINGLE CANONICAL SOURCE (U-051, 2026-07-16): this file is the ONE home for
-- all five dbo.UserCanAccess* UDFs. No migration and no entity base file may
-- redefine them. Change this file and apply it. Enforced by
-- tests/test_sproc_single_source.py.
--
-- Homed here, NOT in the entity base files, on purpose. For the four
-- creator-clause UDFs (Project/Bill/BillCredit/Expense) an entity-local home is
-- structurally impossible: each schema-binds a sibling package's table whose own
-- file FKs back, so an entity-local home is a from-scratch build CYCLE.
-- UserCanAccessTimeEntry is different — it has no cycle (it lived in
-- entities/time_entry/sql/dbo.time_entry.sql until U-051) and is homed here so
-- the family has ONE canonical location under ONE guard. Do not "fix" either by
-- moving it back. Rationale + prerequisites: shared/sql/README.md.
--
-- Before running this file on a fresh DB, note the one non-obvious
-- prerequisite: SCHEMABINDING binds COLUMNS, and the four creator-clause UDFs
-- bind {Project,Bill,BillCredit,Expense}.CreatedByUserId, which those tables'
-- base CREATE TABLE blocks do NOT declare —
-- scripts/migrations/gap2_created_by_user_id.sql adds it. Without it this file
-- fails with "Invalid column name 'CreatedByUserId'".
--
-- PROVENANCE: the four UserCanAccess{Project,Bill,BillCredit,Expense} bodies
-- are byte-identical to scripts/migrations/gap3_user_can_access_remove_legacy_
-- actor_bypass.sql, which was verified against LIVE prod on 2026-07-16
-- (body-normalized match). The UserCanAccessTimeEntry body is relocated
-- byte-identical from entities/time_entry/sql/dbo.time_entry.sql (canonical
-- there since U-045). This file is net-zero to prod.
-- ============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ----------------------------------------------------------------------------
-- UDF: dbo.UserCanAccessTimeEntry
--
-- Returns 1 iff the actor is system admin, OR the actor created (owns) the
-- entry, OR (actor holds CanViewTeam AND any of the entry's TimeLog rows
-- has a ProjectId in the actor's UserProject set).
--
-- Used by service-layer post-fetch checks on by-id reads + mutation gating.
-- Single-row UDF calls are cheap; the gap-1 perf concern only applies to
-- per-row list-path use.
-- ----------------------------------------------------------------------------
CREATE OR ALTER FUNCTION dbo.UserCanAccessTimeEntry
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @ActorCanViewTeam BIT,
    @TimeEntryId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[TimeEntry] te
                WHERE te.[Id] = @TimeEntryId
                  AND te.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN @ActorCanViewTeam = 1 AND EXISTS (
                SELECT 1
                FROM dbo.[TimeLog] tl
                INNER JOIN dbo.[UserProject] up
                    ON up.[ProjectId] = tl.[ProjectId]
                WHERE tl.[TimeEntryId] = @TimeEntryId
                  AND up.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO

-- 1. Project — direct ProjectId on parent.
CREATE OR ALTER FUNCTION dbo.UserCanAccessProject
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @ProjectId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[Project] p
                WHERE p.[Id] = @ProjectId
                  AND p.[CreatedByUserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1 FROM dbo.[UserProject] up
                WHERE up.[UserId] = @ActorUserId
                  AND up.[ProjectId] = @ProjectId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO

-- 2. Bill — line items carry ProjectId; parent is accessible if ANY
--    line item's project is in the user's UserProject set, OR if the
--    user created the bill (covers empty drafts).
CREATE OR ALTER FUNCTION dbo.UserCanAccessBill
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @BillId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[Bill] b
                WHERE b.[Id] = @BillId
                  AND b.[CreatedByUserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[BillLineItem] bli
                INNER JOIN dbo.[UserProject] up
                    ON up.[ProjectId] = bli.[ProjectId]
                WHERE bli.[BillId] = @BillId
                  AND up.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO

-- 3. BillCredit — same shape as Bill.
CREATE OR ALTER FUNCTION dbo.UserCanAccessBillCredit
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @BillCreditId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[BillCredit] bc
                WHERE bc.[Id] = @BillCreditId
                  AND bc.[CreatedByUserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[BillCreditLineItem] bcli
                INNER JOIN dbo.[UserProject] up
                    ON up.[ProjectId] = bcli.[ProjectId]
                WHERE bcli.[BillCreditId] = @BillCreditId
                  AND up.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO

-- 4. Expense — same shape as Bill.
CREATE OR ALTER FUNCTION dbo.UserCanAccessExpense
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @ExpenseId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[Expense] e
                WHERE e.[Id] = @ExpenseId
                  AND e.[CreatedByUserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[ExpenseLineItem] eli
                INNER JOIN dbo.[UserProject] up
                    ON up.[ProjectId] = eli.[ProjectId]
                WHERE eli.[ExpenseId] = @ExpenseId
                  AND up.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO
