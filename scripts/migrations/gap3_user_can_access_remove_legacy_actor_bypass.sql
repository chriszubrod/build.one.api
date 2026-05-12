-- =====================================================================
-- Gap 3 (2026-05-12) — remove the `@ActorUserId IS NULL THEN 1` bypass
-- from dbo.UserCanAccess{Bill, BillCredit, Expense, Project} UDFs.
-- Replaces `gap2_user_can_access_creator_clause.sql`.
--
-- Background: Gap 1/2 included `WHEN @ActorUserId IS NULL THEN 1` so that
-- pre-rollout callers (services that hadn't yet learned to thread actor
-- context) would keep working during the staged deploy. After a leak was
-- discovered on the TimeEntry side where this bypass turned into a
-- silent "no actor → access granted" path on by-id read gates, we removed
-- it across every user-scoped access check to fail closed.
--
-- New rule for these UDFs:
--   Admin (@ActorIsSystemAdmin = 1) → 1
--   Creator (parent.CreatedByUserId = @ActorUserId) → 1
--   UserProject membership reaches the row → 1
--   Else → 0 (including NULL @ActorUserId — fail closed)
--
-- Scheduler / system callers populate `current_is_system_admin = True`
-- (via `shared/api/admin.py::_require_drain_secret`) so the
-- @ActorIsSystemAdmin = 1 branch grants access via the intended path.
--
-- Preserves the gap2 "creator clause" shape — the only delta is the
-- removal of one `WHEN @ActorUserId IS NULL` branch per UDF.
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

PRINT 'Gap 2 follow-up: UserCanAccess{Bill, BillCredit, Expense, Project} now include CreatedByUserId clause';
GO
