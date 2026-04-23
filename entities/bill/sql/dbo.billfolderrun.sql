-- Cross-worker run-state tracker for the Process Folder flow.
-- Replaces the in-process _folder_processing_results dict which broke under
-- -w 2 gunicorn (POST on worker A, poll on worker B → 404).
--
-- Run: python scripts/run_sql.py entities/bill/sql/dbo.billfolderrun.sql

GO

IF OBJECT_ID('dbo.BillFolderRun', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillFolderRun]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Status] NVARCHAR(20) NOT NULL,          -- processing | completed | failed
    [Result] NVARCHAR(MAX) NULL,              -- JSON payload on completion/failure
    [StartedAt] DATETIME2(3) NOT NULL,
    [CompletedAt] DATETIME2(3) NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillFolderRun_PublicId')
BEGIN
    CREATE INDEX IX_BillFolderRun_PublicId ON [dbo].[BillFolderRun]([PublicId]);
END
GO


CREATE OR ALTER PROCEDURE CreateBillFolderRun
(
    @PublicId UNIQUEIDENTIFIER = NULL,
    @Status NVARCHAR(20) = 'processing'
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @NewPublicId UNIQUEIDENTIFIER = ISNULL(@PublicId, NEWID());

    INSERT INTO dbo.[BillFolderRun] ([PublicId], [CreatedDatetime], [ModifiedDatetime], [Status], [StartedAt])
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
    VALUES (@NewPublicId, @Now, @Now, @Status, @Now);

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateBillFolderRunByPublicId
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

    UPDATE dbo.[BillFolderRun]
    SET
        [Status] = @Status,
        [Result] = @Result,
        [ModifiedDatetime] = @Now,
        [CompletedAt] = CASE WHEN @SetCompleted = 1 THEN @Now ELSE [CompletedAt] END
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadBillFolderRunByPublicId
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
    FROM dbo.[BillFolderRun]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO
