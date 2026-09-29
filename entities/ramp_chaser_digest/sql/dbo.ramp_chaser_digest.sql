-- RampChaserDigest — per-cardholder weekly draft state for U-549 chaser (§5.2).
-- Run: ./.venv/bin/python scripts/run_sql.py entities/ramp_chaser_digest/sql/dbo.ramp_chaser_digest.sql

GO

IF OBJECT_ID('dbo.RampChaserDigest', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[RampChaserDigest]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [CardHolderRampUserId] NVARCHAR(64) NOT NULL,
    [WeekOf] DATE NOT NULL,
    [DraftMessageId] NVARCHAR(512) NULL,
    [ConversationId] NVARCHAR(512) NULL,
    [InternetMessageId] NVARCHAR(512) NULL,
    [LastDraftedAt] DATETIME2(3) NULL,
    [LastNotifiedAt] DATETIME2(3) NULL,
    [NotifyCount] INT NOT NULL DEFAULT 0,
    [Outcome] NVARCHAR(32) NULL,
    [CreatedAt] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    [UpdatedAt] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    [RowVersion] ROWVERSION NOT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_RampChaserDigest_PublicId' AND object_id = OBJECT_ID('dbo.RampChaserDigest'))
BEGIN
    CREATE UNIQUE INDEX UQ_RampChaserDigest_PublicId
        ON dbo.[RampChaserDigest] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_RampChaserDigest_CardHolder_WeekOf' AND object_id = OBJECT_ID('dbo.RampChaserDigest'))
BEGIN
    CREATE UNIQUE INDEX UQ_RampChaserDigest_CardHolder_WeekOf
        ON dbo.[RampChaserDigest] ([CardHolderRampUserId], [WeekOf]);
END
GO


CREATE OR ALTER PROCEDURE UpsertRampChaserDigest
(
    @CardHolderRampUserId NVARCHAR(64),
    @WeekOf DATE
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE dbo.[RampChaserDigest] WITH (HOLDLOCK) AS target
    USING (
        SELECT
            @CardHolderRampUserId AS CardHolderRampUserId,
            @WeekOf AS WeekOf
    ) AS source
    ON target.[CardHolderRampUserId] = source.CardHolderRampUserId
       AND target.[WeekOf] = source.WeekOf
    WHEN MATCHED THEN
        UPDATE SET
            [UpdatedAt] = @Now
    WHEN NOT MATCHED THEN
        INSERT
            ([CardHolderRampUserId], [WeekOf], [CreatedAt], [UpdatedAt])
        VALUES
            (@CardHolderRampUserId, @WeekOf, @Now, @Now)
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[CardHolderRampUserId],
        INSERTED.[WeekOf],
        INSERTED.[DraftMessageId],
        INSERTED.[ConversationId],
        INSERTED.[InternetMessageId],
        CONVERT(VARCHAR(30), INSERTED.[LastDraftedAt], 126) AS [LastDraftedAt],
        CONVERT(VARCHAR(30), INSERTED.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        INSERTED.[NotifyCount],
        INSERTED.[Outcome],
        CONVERT(VARCHAR(30), INSERTED.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), INSERTED.[UpdatedAt], 126) AS [UpdatedAt];

    COMMIT TRANSACTION;
END
GO


CREATE OR ALTER PROCEDURE ReadRampChaserDigestByCardHolderAndWeek
(
    @CardHolderRampUserId NVARCHAR(64),
    @WeekOf DATE
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 1
        d.[Id],
        d.[PublicId],
        d.[RowVersion],
        d.[CardHolderRampUserId],
        d.[WeekOf],
        d.[DraftMessageId],
        d.[ConversationId],
        d.[InternetMessageId],
        CONVERT(VARCHAR(30), d.[LastDraftedAt], 126) AS [LastDraftedAt],
        CONVERT(VARCHAR(30), d.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        d.[NotifyCount],
        d.[Outcome],
        CONVERT(VARCHAR(30), d.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), d.[UpdatedAt], 126) AS [UpdatedAt]
    FROM dbo.[RampChaserDigest] d
    WHERE d.[CardHolderRampUserId] = @CardHolderRampUserId
      AND d.[WeekOf] = @WeekOf;
END
GO


CREATE OR ALTER PROCEDURE ReadOutstandingRampChaserDigests
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        d.[Id],
        d.[PublicId],
        d.[RowVersion],
        d.[CardHolderRampUserId],
        d.[WeekOf],
        d.[DraftMessageId],
        d.[ConversationId],
        d.[InternetMessageId],
        CONVERT(VARCHAR(30), d.[LastDraftedAt], 126) AS [LastDraftedAt],
        CONVERT(VARCHAR(30), d.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        d.[NotifyCount],
        d.[Outcome],
        CONVERT(VARCHAR(30), d.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), d.[UpdatedAt], 126) AS [UpdatedAt]
    FROM dbo.[RampChaserDigest] d
    WHERE d.[DraftMessageId] IS NOT NULL
      AND d.[LastNotifiedAt] IS NULL
    ORDER BY d.[CardHolderRampUserId], d.[WeekOf];
END
GO


CREATE OR ALTER PROCEDURE StampRampChaserDigestDrafted
(
    @CardHolderRampUserId NVARCHAR(64),
    @WeekOf DATE,
    @DraftMessageId NVARCHAR(512),
    @ConversationId NVARCHAR(512) = NULL,
    @InternetMessageId NVARCHAR(512) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[RampChaserDigest]
    SET [DraftMessageId] = @DraftMessageId,
        [ConversationId] = CASE WHEN @ConversationId IS NOT NULL THEN @ConversationId ELSE [ConversationId] END,
        [InternetMessageId] = CASE WHEN @InternetMessageId IS NOT NULL THEN @InternetMessageId ELSE [InternetMessageId] END,
        [LastDraftedAt] = @Now,
        [UpdatedAt] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[CardHolderRampUserId],
        INSERTED.[WeekOf],
        INSERTED.[DraftMessageId],
        INSERTED.[ConversationId],
        INSERTED.[InternetMessageId],
        CONVERT(VARCHAR(30), INSERTED.[LastDraftedAt], 126) AS [LastDraftedAt],
        CONVERT(VARCHAR(30), INSERTED.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        INSERTED.[NotifyCount],
        INSERTED.[Outcome],
        CONVERT(VARCHAR(30), INSERTED.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), INSERTED.[UpdatedAt], 126) AS [UpdatedAt]
    WHERE [CardHolderRampUserId] = @CardHolderRampUserId
      AND [WeekOf] = @WeekOf;

    COMMIT TRANSACTION;
END
GO


CREATE OR ALTER PROCEDURE StampRampChaserDigestOutcome
(
    @CardHolderRampUserId NVARCHAR(64),
    @WeekOf DATE,
    @Outcome NVARCHAR(32)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[RampChaserDigest]
    SET [Outcome] = @Outcome,
        [UpdatedAt] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[CardHolderRampUserId],
        INSERTED.[WeekOf],
        INSERTED.[DraftMessageId],
        INSERTED.[ConversationId],
        INSERTED.[InternetMessageId],
        CONVERT(VARCHAR(30), INSERTED.[LastDraftedAt], 126) AS [LastDraftedAt],
        CONVERT(VARCHAR(30), INSERTED.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        INSERTED.[NotifyCount],
        INSERTED.[Outcome],
        CONVERT(VARCHAR(30), INSERTED.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), INSERTED.[UpdatedAt], 126) AS [UpdatedAt]
    WHERE [CardHolderRampUserId] = @CardHolderRampUserId
      AND [WeekOf] = @WeekOf;

    COMMIT TRANSACTION;
END
GO


CREATE OR ALTER PROCEDURE StampRampChaserDigestNotified
(
    @CardHolderRampUserId NVARCHAR(64),
    @WeekOf DATE,
    @Outcome NVARCHAR(32)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[RampChaserDigest]
    SET [LastNotifiedAt] = @Now,
        [NotifyCount] = [NotifyCount] + 1,
        [Outcome] = @Outcome,
        [UpdatedAt] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[CardHolderRampUserId],
        INSERTED.[WeekOf],
        INSERTED.[DraftMessageId],
        INSERTED.[ConversationId],
        INSERTED.[InternetMessageId],
        CONVERT(VARCHAR(30), INSERTED.[LastDraftedAt], 126) AS [LastDraftedAt],
        CONVERT(VARCHAR(30), INSERTED.[LastNotifiedAt], 126) AS [LastNotifiedAt],
        INSERTED.[NotifyCount],
        INSERTED.[Outcome],
        CONVERT(VARCHAR(30), INSERTED.[CreatedAt], 126) AS [CreatedAt],
        CONVERT(VARCHAR(30), INSERTED.[UpdatedAt], 126) AS [UpdatedAt]
    WHERE [CardHolderRampUserId] = @CardHolderRampUserId
      AND [WeekOf] = @WeekOf;

    COMMIT TRANSACTION;
END
GO
