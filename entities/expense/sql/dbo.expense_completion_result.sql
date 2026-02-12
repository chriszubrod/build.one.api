-- Expense completion result cache (shared across workers; TTL 1 hour)
IF OBJECT_ID('dbo.ExpenseCompletionResult', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ExpenseCompletionResult]
(
    [ExpensePublicId] UNIQUEIDENTIFIER NOT NULL PRIMARY KEY,
    [ResultJson] NVARCHAR(MAX) NOT NULL,
    [ExpiresAt] DATETIME2(3) NOT NULL
);
END
GO

CREATE OR ALTER PROCEDURE UpsertExpenseCompletionResult
(
    @ExpensePublicId UNIQUEIDENTIFIER,
    @ResultJson NVARCHAR(MAX),
    @ExpiresAt DATETIME2(3)
)
AS
BEGIN
    SET NOCOUNT ON;
    MERGE dbo.[ExpenseCompletionResult] AS t
    USING (SELECT @ExpensePublicId AS ExpensePublicId) AS s ON t.[ExpensePublicId] = s.ExpensePublicId
    WHEN MATCHED THEN
        UPDATE SET [ResultJson] = @ResultJson, [ExpiresAt] = @ExpiresAt
    WHEN NOT MATCHED THEN
        INSERT ([ExpensePublicId], [ResultJson], [ExpiresAt])
        VALUES (@ExpensePublicId, @ResultJson, @ExpiresAt);
END;
GO

CREATE OR ALTER PROCEDURE GetExpenseCompletionResult
(
    @ExpensePublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT [ResultJson], [ExpiresAt]
    FROM dbo.[ExpenseCompletionResult]
    WHERE [ExpensePublicId] = @ExpensePublicId AND [ExpiresAt] > SYSUTCDATETIME();
END;
GO
