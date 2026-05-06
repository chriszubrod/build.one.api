-- Gap 1 — UserProject access-check helper functions.
--
-- Four scalar UDFs that return BIT (1=accessible, 0=denied). All four
-- return 1 when @ActorIsSystemAdmin = 1 OR @ActorUserId IS NULL —
-- matching the Phase 3 NULL-bypass back-compat pattern.
--
-- Used by the per-entity read sprocs in gap1 follow-up migrations to
-- compress the EXISTS clauses into a single function call.
--
-- WITH SCHEMABINDING on the read-only ones lets SQL Server's Intelligent
-- Query Processing (2019+) inline them, so the perf hit vs inline
-- EXISTS is minimal.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- 1. Project — direct ProjectId on parent (also used for Invoice + ContractLabor).
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
            WHEN @ActorUserId IS NULL THEN CONVERT(BIT, 1)
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
--    line item's project is in the user's UserProject set.
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
            WHEN @ActorUserId IS NULL THEN CONVERT(BIT, 1)
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
            WHEN @ActorUserId IS NULL THEN CONVERT(BIT, 1)
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
            WHEN @ActorUserId IS NULL THEN CONVERT(BIT, 1)
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

PRINT 'Gap 1 access helper functions installed.';
