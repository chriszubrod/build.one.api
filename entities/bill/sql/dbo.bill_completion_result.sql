-- Bill completion result cache (shared across workers; TTL 1 hour)
IF OBJECT_ID('dbo.BillCompletionResult', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillCompletionResult]
(
    [BillPublicId] UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
    [ResultJson] NVARCHAR(MAX) NOT NULL,
    [ExpiresAt] DATETIME2(3) NOT NULL
);
END
GO

CREATE OR ALTER PROCEDURE UpsertBillCompletionResult
(
    @BillPublicId UNIQUEIDENTIFIER,
    @ResultJson NVARCHAR(MAX),
    @ExpiresAt DATETIME2(3)
)
AS
BEGIN
    SET NOCOUNT ON;
    MERGE dbo.[BillCompletionResult] AS t
    USING (SELECT @BillPublicId AS BillPublicId) AS s ON t.[BillPublicId] = s.BillPublicId
    WHEN MATCHED THEN
        UPDATE SET [ResultJson] = @ResultJson, [ExpiresAt] = @ExpiresAt
    WHEN NOT MATCHED THEN
        INSERT ([BillPublicId], [ResultJson], [ExpiresAt])
        VALUES (@BillPublicId, @ResultJson, @ExpiresAt);
END;
GO

CREATE OR ALTER PROCEDURE GetBillCompletionResult
(
    @BillPublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT [ResultJson], [ExpiresAt]
    FROM dbo.[BillCompletionResult]
    WHERE [BillPublicId] = @BillPublicId AND [ExpiresAt] > SYSUTCDATETIME();
END;
GO
