-- =============================================================================
-- 2026-05-28 — Review sprocs + view: contract_labor parent support.
--
-- Extends the view to include ContractLaborId, the CreateReview sproc to
-- accept it, and adds ReadReviewsByContractLaborId / ReadCurrentReview-
-- ByContractLaborId / DeleteReviewsByContractLaborId — same shape as the
-- existing Bill/Expense/BillCredit/Invoice triple.
--
-- Run AFTER 003_add_contract_labor_parent.sql (which adds the column).
--
-- Idempotent — all CREATE OR ALTER.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- =========================================================================
-- View — add ContractLaborId column
-- =========================================================================

CREATE OR ALTER VIEW [dbo].[vw_Review]
AS
    SELECT
        r.[Id],
        r.[PublicId],
        r.[RowVersion],
        CONVERT(VARCHAR(19), r.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), r.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        r.[ReviewStatusId],
        r.[UserId],
        r.[Comments],
        r.[BillId],
        r.[ExpenseId],
        r.[BillCreditId],
        r.[InvoiceId],
        r.[ContractLaborId],
        r.[EmailMessageId],
        rs.[Name]       AS [StatusName],
        rs.[SortOrder]  AS [StatusSortOrder],
        rs.[IsFinal]    AS [StatusIsFinal],
        rs.[IsDeclined] AS [StatusIsDeclined],
        rs.[Color]      AS [StatusColor],
        u.[Firstname]   AS [UserFirstname],
        u.[Lastname]    AS [UserLastname]
    FROM dbo.[Review] r
    INNER JOIN dbo.[ReviewStatus] rs ON r.[ReviewStatusId] = rs.[Id]
    INNER JOIN dbo.[User] u          ON r.[UserId]         = u.[Id];
GO


-- =========================================================================
-- CreateReview — accept @ContractLaborId
-- =========================================================================

CREATE OR ALTER PROCEDURE CreateReview
(
    @ReviewStatusId   BIGINT,
    @UserId           BIGINT,
    @Comments         NVARCHAR(MAX) = NULL,
    @BillId           BIGINT = NULL,
    @ExpenseId        BIGINT = NULL,
    @BillCreditId     BIGINT = NULL,
    @InvoiceId        BIGINT = NULL,
    @ContractLaborId  BIGINT = NULL,
    @EmailMessageId   BIGINT = NULL,
    @CreatedByUserId  BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Review] (
        [CreatedDatetime], [ModifiedDatetime],
        [ReviewStatusId], [UserId], [Comments],
        [BillId], [ExpenseId], [BillCreditId], [InvoiceId], [ContractLaborId],
        [EmailMessageId],
        [CreatedByUserId]
    )
    VALUES (
        @Now, @Now,
        @ReviewStatusId, @UserId, @Comments,
        @BillId, @ExpenseId, @BillCreditId, @InvoiceId, @ContractLaborId,
        @EmailMessageId,
        COALESCE(@CreatedByUserId, 17)
    );

    SELECT * FROM dbo.[vw_Review] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO


-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-126, 2026-07-23) — sproc bodies removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Contract-labor parent review read/delete sprocs matching Bill/Expense shape.
--
-- The canonical definition of these sprocs now lives in exactly ONE place:
--   entities/review/sql/dbo.review.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.ReadReviewsByContractLaborId
--   dbo.ReadCurrentReviewByContractLaborId
--   dbo.DeleteReviewsByContractLaborId
--
-- Re-running this file is now a no-op for these sprocs. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------


PRINT 'Review view + CreateReview extended for contract_labor parent.';
