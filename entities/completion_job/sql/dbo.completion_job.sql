-- ============================================================================
-- SINGLE CANONICAL SOURCE (U-065): durable CompletionJob rows + reclaim sprocs.
-- Build order: README.md (same directory). Enforced by tests/test_sproc_single_source.py.
-- ============================================================================

IF OBJECT_ID('dbo.CompletionJob', 'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[CompletionJob]
    (
        [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [EntityType] VARCHAR(20) NOT NULL,
        [EntityPublicId] UNIQUEIDENTIFIER NOT NULL,
        [Status] VARCHAR(20) NOT NULL DEFAULT ('processing'),
        [Attempts] INT NOT NULL DEFAULT (1),
        [MaxAttempts] INT NOT NULL DEFAULT (5),
        [ClaimedAt] DATETIME2(3) NOT NULL DEFAULT (SYSUTCDATETIME()),
        [LastError] NVARCHAR(MAX) NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT (SYSUTCDATETIME()),
        [ModifiedDatetime] DATETIME2(3) NOT NULL DEFAULT (SYSUTCDATETIME()),
        [CompanyId] BIGINT NOT NULL DEFAULT (1)
    );
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_CompletionJob_Active'
      AND object_id = OBJECT_ID('dbo.CompletionJob')
)
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX [UQ_CompletionJob_Active]
        ON [dbo].[CompletionJob] ([EntityType], [EntityPublicId])
        WHERE [Status] = 'processing';
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_CompletionJob_Reclaim'
      AND object_id = OBJECT_ID('dbo.CompletionJob')
)
BEGIN
    CREATE NONCLUSTERED INDEX [IX_CompletionJob_Reclaim]
        ON [dbo].[CompletionJob] ([ClaimedAt])
        WHERE [Status] = 'processing';
END
GO

CREATE OR ALTER PROCEDURE CreateCompletionJob
    @EntityType VARCHAR(20),
    @EntityPublicId UNIQUEIDENTIFIER,
    @CompanyId BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Company BIGINT = COALESCE(@CompanyId, 1);
    DECLARE @WasCreated BIT = 0;

    BEGIN TRY
        INSERT INTO [dbo].[CompletionJob] (
            [EntityType],
            [EntityPublicId],
            [Status],
            [Attempts],
            [MaxAttempts],
            [ClaimedAt],
            [CreatedDatetime],
            [ModifiedDatetime],
            [CompanyId]
        )
        VALUES (
            @EntityType,
            @EntityPublicId,
            'processing',
            1,
            5,
            @Now,
            @Now,
            @Now,
            @Company
        );
        SET @WasCreated = 1;
    END TRY
    BEGIN CATCH
        IF ERROR_NUMBER() NOT IN (2601, 2627)
            THROW;
        SET @WasCreated = 0;
    END CATCH

    SELECT
        [Id],
        [PublicId],
        [EntityType],
        [EntityPublicId],
        [Status],
        [Attempts],
        [MaxAttempts],
        CONVERT(VARCHAR(23), [ClaimedAt], 121) AS [ClaimedAt],
        [LastError],
        CONVERT(VARCHAR(23), [CreatedDatetime], 121) AS [CreatedDatetime],
        CONVERT(VARCHAR(23), [ModifiedDatetime], 121) AS [ModifiedDatetime],
        [CompanyId],
        @WasCreated AS [WasCreated]
    FROM [dbo].[CompletionJob]
    WHERE [EntityType] = @EntityType
      AND [EntityPublicId] = @EntityPublicId
      AND [Status] = 'processing';
END
GO

CREATE OR ALTER PROCEDURE ClaimNextStuckCompletionJob
    @ReclaimAfterSeconds INT = 1800,
    @MaxAttempts INT = 5
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH nxt AS (
        SELECT TOP (1) *
        FROM [dbo].[CompletionJob] WITH (UPDLOCK, READPAST)
        WHERE [Status] = 'processing'
          AND [Attempts] < @MaxAttempts
          AND [ClaimedAt] < DATEADD(SECOND, -@ReclaimAfterSeconds, SYSUTCDATETIME())
        ORDER BY [CreatedDatetime]
    )
    UPDATE nxt
    SET [ClaimedAt] = SYSUTCDATETIME(),
        [Attempts] = [Attempts] + 1,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        inserted.[Id],
        inserted.[PublicId],
        inserted.[EntityType],
        inserted.[EntityPublicId],
        inserted.[Status],
        inserted.[Attempts],
        inserted.[MaxAttempts],
        CONVERT(VARCHAR(23), inserted.[ClaimedAt], 121) AS [ClaimedAt],
        inserted.[LastError],
        CONVERT(VARCHAR(23), inserted.[CreatedDatetime], 121) AS [CreatedDatetime],
        CONVERT(VARCHAR(23), inserted.[ModifiedDatetime], 121) AS [ModifiedDatetime],
        inserted.[CompanyId];
END
GO

CREATE OR ALTER PROCEDURE MarkCompletionJobSuccess
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE [dbo].[CompletionJob]
    SET [Status] = 'completed',
        [ModifiedDatetime] = SYSUTCDATETIME()
    WHERE [PublicId] = @PublicId
      AND [Status] = 'processing';
END
GO

CREATE OR ALTER PROCEDURE MarkCompletionJobFailure
    @PublicId UNIQUEIDENTIFIER,
    @LastError NVARCHAR(MAX) = NULL,
    @MaxAttempts INT = 5
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE [dbo].[CompletionJob]
    SET [LastError] = @LastError,
        [ClaimedAt] = SYSUTCDATETIME(),
        [ModifiedDatetime] = SYSUTCDATETIME(),
        [Status] = CASE WHEN [Attempts] >= @MaxAttempts THEN 'failed' ELSE 'processing' END
    WHERE [PublicId] = @PublicId
      AND [Status] = 'processing';
END
GO
