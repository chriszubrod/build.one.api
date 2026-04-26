GO

IF OBJECT_ID('dbo.Review', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Review]
(
    [Id]               BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId]         UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion]       ROWVERSION NOT NULL,
    [CreatedDatetime]  DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ReviewStatusId]   BIGINT NOT NULL,
    [UserId]           BIGINT NOT NULL,
    [Comments]         NVARCHAR(MAX) NULL,
    [BillId]           BIGINT NULL,
    [ExpenseId]        BIGINT NULL,
    [BillCreditId]     BIGINT NULL,
    [InvoiceId]        BIGINT NULL,
    CONSTRAINT [FK_Review_ReviewStatus] FOREIGN KEY ([ReviewStatusId]) REFERENCES dbo.[ReviewStatus]([Id]),
    CONSTRAINT [FK_Review_User]         FOREIGN KEY ([UserId])         REFERENCES dbo.[User]([Id]),
    CONSTRAINT [FK_Review_Bill]         FOREIGN KEY ([BillId])         REFERENCES dbo.[Bill]([Id]),
    CONSTRAINT [FK_Review_Expense]      FOREIGN KEY ([ExpenseId])      REFERENCES dbo.[Expense]([Id]),
    CONSTRAINT [FK_Review_BillCredit]   FOREIGN KEY ([BillCreditId])   REFERENCES dbo.[BillCredit]([Id]),
    CONSTRAINT [FK_Review_Invoice]      FOREIGN KEY ([InvoiceId])      REFERENCES dbo.[Invoice]([Id]),
    CONSTRAINT [CK_Review_OneParent] CHECK (
        (CASE WHEN [BillId]       IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [ExpenseId]    IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [BillCreditId] IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [InvoiceId]    IS NOT NULL THEN 1 ELSE 0 END) = 1
    )
);
END
GO


-- =========================================================================
-- Indexes (filtered, one per parent FK)
-- =========================================================================

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_BillId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_BillId] ON [dbo].[Review]([BillId]) WHERE [BillId] IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_ExpenseId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_ExpenseId] ON [dbo].[Review]([ExpenseId]) WHERE [ExpenseId] IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_BillCreditId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_BillCreditId] ON [dbo].[Review]([BillCreditId]) WHERE [BillCreditId] IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_InvoiceId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_InvoiceId] ON [dbo].[Review]([InvoiceId]) WHERE [InvoiceId] IS NOT NULL;
END
GO


-- =========================================================================
-- View — denormalized JOIN to ReviewStatus + User
-- Every read sproc selects from this view.
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
-- CreateReview
-- =========================================================================

CREATE OR ALTER PROCEDURE CreateReview
(
    @ReviewStatusId BIGINT,
    @UserId         BIGINT,
    @Comments       NVARCHAR(MAX) = NULL,
    @BillId         BIGINT = NULL,
    @ExpenseId      BIGINT = NULL,
    @BillCreditId   BIGINT = NULL,
    @InvoiceId      BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Review] (
        [CreatedDatetime], [ModifiedDatetime],
        [ReviewStatusId], [UserId], [Comments],
        [BillId], [ExpenseId], [BillCreditId], [InvoiceId]
    )
    VALUES (
        @Now, @Now,
        @ReviewStatusId, @UserId, @Comments,
        @BillId, @ExpenseId, @BillCreditId, @InvoiceId
    );

    SELECT * FROM dbo.[vw_Review] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO


-- =========================================================================
-- ReadReviewByPublicId
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadReviewByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review] WHERE [PublicId] = @PublicId;
END;
GO


-- =========================================================================
-- ReadReviewsByXId — full history, ascending
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadReviewsByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [BillId] = @BillId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadReviewsByExpenseId
(
    @ExpenseId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [ExpenseId] = @ExpenseId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadReviewsByBillCreditId
(
    @BillCreditId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [BillCreditId] = @BillCreditId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadReviewsByInvoiceId
(
    @InvoiceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [InvoiceId] = @InvoiceId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO


-- =========================================================================
-- ReadCurrentReviewByXId — TOP 1 latest, descending. Tiebreak by Id DESC.
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadCurrentReviewByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [BillId] = @BillId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadCurrentReviewByExpenseId
(
    @ExpenseId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [ExpenseId] = @ExpenseId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadCurrentReviewByBillCreditId
(
    @BillCreditId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [BillCreditId] = @BillCreditId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadCurrentReviewByInvoiceId
(
    @InvoiceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [InvoiceId] = @InvoiceId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO
