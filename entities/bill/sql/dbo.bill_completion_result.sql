-- Bill completion result (permanent record of completion outcome)
IF OBJECT_ID('dbo.BillCompletionResult', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillCompletionResult]
(
    [BillPublicId] UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
    [ResultJson] NVARCHAR(MAX) NOT NULL,
    [CompletedAt] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME()
);
END
GO

-- Migrate from ExpiresAt to CompletedAt if needed
IF COL_LENGTH('dbo.BillCompletionResult', 'ExpiresAt') IS NOT NULL
   AND COL_LENGTH('dbo.BillCompletionResult', 'CompletedAt') IS NULL
    ALTER TABLE [dbo].[BillCompletionResult] ADD [CompletedAt] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME();
GO

IF COL_LENGTH('dbo.BillCompletionResult', 'ExpiresAt') IS NOT NULL
BEGIN
    EXEC sp_executesql N'UPDATE [dbo].[BillCompletionResult] SET [CompletedAt] = [ExpiresAt]';
    ALTER TABLE [dbo].[BillCompletionResult] DROP COLUMN [ExpiresAt];
END
GO

CREATE OR ALTER PROCEDURE UpsertBillCompletionResult
(
    @BillPublicId UNIQUEIDENTIFIER,
    @ResultJson NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;
    MERGE dbo.[BillCompletionResult] AS t
    USING (SELECT @BillPublicId AS BillPublicId) AS s ON t.[BillPublicId] = s.BillPublicId
    WHEN MATCHED THEN
        UPDATE SET [ResultJson] = @ResultJson, [CompletedAt] = SYSUTCDATETIME()
    WHEN NOT MATCHED THEN
        INSERT ([BillPublicId], [ResultJson])
        VALUES (@BillPublicId, @ResultJson);
END;
GO

CREATE OR ALTER PROCEDURE GetBillCompletionResult
(
    @BillPublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT [ResultJson], [CompletedAt]
    FROM dbo.[BillCompletionResult]
    WHERE [BillPublicId] = @BillPublicId;
END;
GO
