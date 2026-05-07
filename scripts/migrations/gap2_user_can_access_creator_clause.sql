-- =====================================================================
-- Gap 2 follow-up — extend dbo.UserCanAccess{Bill, BillCredit, Expense, Project}
-- UDFs with a "creator can access their own row" clause (matches the
-- list-path filter shape from Gap 1 v3).
--
-- Why: Without this, a non-admin who creates a parent row that has not
-- yet had any child line items attached gets EntityNotAccessibleError on
-- subsequent reads (UserProject join finds no line items, so EXISTS=false).
-- That made BillService.create() fail for non-admin actors during the
-- auto-line-item-attach flow that runs in the same request as the Bill
-- INSERT (the new Bill has no line items yet).
--
-- Also closes the "Gap 1 empty-bill edge case" tracked in TODO.md —
-- empty drafts created by a user remain visible to that user.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =====================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
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
            WHEN @ActorUserId IS NULL THEN CONVERT(BIT, 1)
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
            WHEN @ActorUserId IS NULL THEN CONVERT(BIT, 1)
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
            WHEN @ActorUserId IS NULL THEN CONVERT(BIT, 1)
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
            WHEN @ActorUserId IS NULL THEN CONVERT(BIT, 1)
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

PRINT 'Gap 2 follow-up: UserCanAccess{Bill, BillCredit, Expense, Project} now include CreatedByUserId clause';
GO
