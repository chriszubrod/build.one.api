-- Cross-worker run-state tracker for the Process Folder flow.
-- Replaces the in-process _folder_processing_results dict which broke under
-- -w 2 gunicorn (POST on worker A, poll on worker B → 404).
--
-- Run: python scripts/run_sql.py entities/expense/sql/dbo.expensefolderrun.sql

GO

IF OBJECT_ID('dbo.ExpenseFolderRun', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ExpenseFolderRun]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Status] NVARCHAR(20) NOT NULL,          -- processing | completed | failed
    [Result] NVARCHAR(MAX) NULL,              -- JSON payload on completion/failure
    [StartedAt] DATETIME2(3) NOT NULL,
    [CompletedAt] DATETIME2(3) NULL,
    -- Matches the live dbo.BillFolderRun shape (gap2 CreatedByUserId threading,
    -- finalized NOT NULL DEFAULT 17). The folder_run_repo passes CreatedByUserId,
    -- so the sproc + column must exist or call_procedure raises "too many args".
    [CreatedByUserId] BIGINT NOT NULL CONSTRAINT [DF_ExpenseFolderRun_CreatedByUserId] DEFAULT (17)
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseFolderRun_PublicId')
BEGIN
    CREATE INDEX IX_ExpenseFolderRun_PublicId ON [dbo].[ExpenseFolderRun]([PublicId]);
END
GO

IF OBJECT_ID('dbo.ExpenseFolderRun', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ExpenseFolderRun_CreatedByUser')
    ALTER TABLE [dbo].[ExpenseFolderRun] ADD CONSTRAINT [FK_ExpenseFolderRun_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseFolderRun_CreatedByUserId' AND object_id = OBJECT_ID('dbo.ExpenseFolderRun'))
    CREATE INDEX [IX_ExpenseFolderRun_CreatedByUserId] ON [dbo].[ExpenseFolderRun]([CreatedByUserId]);
GO


CREATE OR ALTER PROCEDURE CreateExpenseFolderRun
(
    @PublicId UNIQUEIDENTIFIER = NULL,
    @Status NVARCHAR(20) = 'processing',
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @NewPublicId UNIQUEIDENTIFIER = ISNULL(@PublicId, NEWID());

    INSERT INTO dbo.[ExpenseFolderRun] ([PublicId], [CreatedDatetime], [ModifiedDatetime], [Status], [StartedAt], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Status],
        INSERTED.[Result],
        CONVERT(VARCHAR(19), INSERTED.[StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), INSERTED.[CompletedAt], 120) AS [CompletedAt]
    VALUES (@NewPublicId, @Now, @Now, @Status, @Now, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateExpenseFolderRunByPublicId
(
    @PublicId UNIQUEIDENTIFIER,
    @Status NVARCHAR(20),
    @Result NVARCHAR(MAX) = NULL,
    @SetCompleted BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ExpenseFolderRun]
    SET
        [Status] = @Status,
        [Result] = @Result,
        [ModifiedDatetime] = @Now,
        [CompletedAt] = CASE WHEN @SetCompleted = 1 THEN @Now ELSE [CompletedAt] END
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadExpenseFolderRunByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Status],
        [Result],
        CONVERT(VARCHAR(19), [StartedAt], 120) AS [StartedAt],
        CONVERT(VARCHAR(19), [CompletedAt], 120) AS [CompletedAt]
    FROM dbo.[ExpenseFolderRun]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO
