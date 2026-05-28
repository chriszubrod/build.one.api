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


-- =========================================================================
-- ReadReviewsByContractLaborId — full history, ascending
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadReviewsByContractLaborId
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [ContractLaborId] = @ContractLaborId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO


-- =========================================================================
-- ReadCurrentReviewByContractLaborId — TOP 1 latest, descending
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadCurrentReviewByContractLaborId
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [ContractLaborId] = @ContractLaborId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO


-- =========================================================================
-- DeleteReviewsByContractLaborId — for parent cascades
-- Mirrors DeleteReviewsByBillId. Required only if ContractLabor ever
-- hard-deletes parents (current ContractLaborService.delete is hard-delete).
-- =========================================================================

CREATE OR ALTER PROCEDURE DeleteReviewsByContractLaborId
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Review]
    WHERE [ContractLaborId] = @ContractLaborId;

    COMMIT TRANSACTION;
END;
GO


PRINT 'Review view + sprocs extended for contract_labor parent.';
