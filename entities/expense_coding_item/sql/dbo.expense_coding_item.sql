-- ExpenseCodingItem — one instrumentation row per target QBO purchase line
-- in the 58999 NEED TO CATEGORIZE queue. Tracks claim, suggestion, confirm,
-- and write-back lifecycle (write-back sprocs land in Phase C).
--
-- Run: python scripts/run_sql.py entities/expense_coding_item/sql/dbo.expense_coding_item.sql

GO

IF OBJECT_ID('dbo.ExpenseCodingItem', 'U') IS NULL
BEGIN
-- QboPurchaseId / QboPurchaseLineId intentionally have NO FK to the volatile
-- qbo.* staging tables (re-pulled/replaced by sync). The coding item survives
-- staging churn and is correlated by QboLineId. VendorId is a best-effort soft
-- reference to dbo.Vendor.
CREATE TABLE [dbo].[ExpenseCodingItem]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [QboPurchaseId] BIGINT NOT NULL,
    [QboPurchaseLineId] BIGINT NOT NULL,
    [QboLineId] NVARCHAR(50) NULL,
    [QboPurchaseQboId] NVARCHAR(50) NULL,
    [RealmId] NVARCHAR(50) NULL,
    [VendorId] BIGINT NULL,
    [SyncTokenAtSuggest] NVARCHAR(50) NULL,
    [Status] NVARCHAR(30) NOT NULL DEFAULT 'pending',
    [SuggestedProjectId] BIGINT NULL,
    [SuggestedSubCostCodeId] BIGINT NULL,
    [SuggestedDescription] NVARCHAR(1024) NULL,
    [SuggestionSource] NVARCHAR(50) NULL,
    [SuggestionReason] NVARCHAR(1024) NULL,
    [SuggestionConfidence] DECIMAL(5,4) NULL,
    [SuggestedAt] DATETIME2(3) NULL,
    [ConfirmedProjectId] BIGINT NULL,
    [ConfirmedSubCostCodeId] BIGINT NULL,
    [ConfirmedDescription] NVARCHAR(1024) NULL,
    [WasOverridden] BIT NULL,
    [ConfirmedByUserId] BIGINT NULL,
    [ConfirmedAt] DATETIME2(3) NULL,
    [FlagReason] NVARCHAR(1024) NULL,
    [FlaggedAt] DATETIME2(3) NULL,
    [WrittenAt] DATETIME2(3) NULL,
    [WriteError] NVARCHAR(1024) NULL,
    [ClaimedByUserId] BIGINT NULL,
    [ClaimedAt] DATETIME2(3) NULL,
    [CompanyId] BIGINT NOT NULL DEFAULT 1,
    [CreatedByUserId] BIGINT NOT NULL DEFAULT 17,
    [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    [ModifiedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    [RowVersion] ROWVERSION NOT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_ExpenseCodingItem_PublicId' AND object_id = OBJECT_ID('dbo.ExpenseCodingItem'))
BEGIN
    CREATE UNIQUE INDEX UQ_ExpenseCodingItem_PublicId
        ON dbo.[ExpenseCodingItem] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_ExpenseCodingItem_QboPurchaseLineId' AND object_id = OBJECT_ID('dbo.ExpenseCodingItem'))
BEGIN
    CREATE UNIQUE INDEX UQ_ExpenseCodingItem_QboPurchaseLineId
        ON dbo.[ExpenseCodingItem] ([QboPurchaseLineId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseCodingItem_Status' AND object_id = OBJECT_ID('dbo.ExpenseCodingItem'))
BEGIN
    CREATE INDEX IX_ExpenseCodingItem_Status
        ON dbo.[ExpenseCodingItem] ([Status]);
END
GO


CREATE OR ALTER PROCEDURE UpsertExpenseCodingItem
(
    @QboPurchaseId BIGINT,
    @QboPurchaseLineId BIGINT,
    @QboLineId NVARCHAR(50) = NULL,
    @QboPurchaseQboId NVARCHAR(50) = NULL,
    @RealmId NVARCHAR(50) = NULL,
    @VendorId BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE dbo.[ExpenseCodingItem] WITH (HOLDLOCK) AS target
    USING (SELECT @QboPurchaseLineId AS QboPurchaseLineId) AS source
    ON target.[QboPurchaseLineId] = source.QboPurchaseLineId
    WHEN MATCHED THEN
        UPDATE SET
            [QboPurchaseId] = CASE WHEN @QboPurchaseId IS NOT NULL THEN @QboPurchaseId ELSE target.[QboPurchaseId] END,
            [QboLineId] = CASE WHEN @QboLineId IS NOT NULL THEN @QboLineId ELSE target.[QboLineId] END,
            [QboPurchaseQboId] = CASE WHEN @QboPurchaseQboId IS NOT NULL THEN @QboPurchaseQboId ELSE target.[QboPurchaseQboId] END,
            [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE target.[RealmId] END,
            [VendorId] = CASE WHEN @VendorId IS NOT NULL THEN @VendorId ELSE target.[VendorId] END,
            [ModifiedDatetime] = @Now
    WHEN NOT MATCHED THEN
        INSERT
            ([QboPurchaseId], [QboPurchaseLineId], [QboLineId], [QboPurchaseQboId],
             [RealmId], [VendorId], [Status], [CreatedByUserId], [CreatedDatetime], [ModifiedDatetime])
        VALUES
            (@QboPurchaseId, @QboPurchaseLineId, @QboLineId, @QboPurchaseQboId,
             @RealmId, @VendorId, N'pending', COALESCE(@CreatedByUserId, 17), @Now, @Now)
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[QboPurchaseId],
        INSERTED.[QboPurchaseLineId],
        INSERTED.[QboLineId],
        INSERTED.[QboPurchaseQboId],
        INSERTED.[RealmId],
        INSERTED.[VendorId],
        INSERTED.[SyncTokenAtSuggest],
        INSERTED.[Status],
        INSERTED.[SuggestedProjectId],
        INSERTED.[SuggestedSubCostCodeId],
        INSERTED.[SuggestedDescription],
        INSERTED.[SuggestionSource],
        INSERTED.[SuggestionReason],
        INSERTED.[SuggestionConfidence],
        INSERTED.[SuggestedAt],
        INSERTED.[ConfirmedProjectId],
        INSERTED.[ConfirmedSubCostCodeId],
        INSERTED.[ConfirmedDescription],
        INSERTED.[WasOverridden],
        INSERTED.[ConfirmedByUserId],
        INSERTED.[ConfirmedAt],
        INSERTED.[FlagReason],
        INSERTED.[FlaggedAt],
        INSERTED.[WrittenAt],
        INSERTED.[WriteError],
        INSERTED.[ClaimedByUserId],
        INSERTED.[ClaimedAt],
        INSERTED.[CompanyId],
        INSERTED.[CreatedByUserId],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime];

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadExpenseCodingItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        eci.[Id],
        eci.[PublicId],
        eci.[RowVersion],
        eci.[QboPurchaseId],
        eci.[QboPurchaseLineId],
        eci.[QboLineId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[VendorId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        CONVERT(VARCHAR(19), eci.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), eci.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE RecordExpenseCodingSuggestion
(
    @PublicId UNIQUEIDENTIFIER,
    @SuggestedProjectId BIGINT = NULL,
    @SuggestedSubCostCodeId BIGINT = NULL,
    @SuggestedDescription NVARCHAR(1024) = NULL,
    @SuggestionSource NVARCHAR(50) = NULL,
    @SuggestionReason NVARCHAR(1024) = NULL,
    @SuggestionConfidence DECIMAL(5,4) = NULL,
    @SyncTokenAtSuggest NVARCHAR(50) = NULL,
    @Status NVARCHAR(30) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE eci
    SET
        [SuggestedProjectId] = CASE WHEN @SuggestedProjectId IS NOT NULL THEN @SuggestedProjectId ELSE eci.[SuggestedProjectId] END,
        [SuggestedSubCostCodeId] = CASE WHEN @SuggestedSubCostCodeId IS NOT NULL THEN @SuggestedSubCostCodeId ELSE eci.[SuggestedSubCostCodeId] END,
        [SuggestedDescription] = CASE WHEN @SuggestedDescription IS NOT NULL THEN @SuggestedDescription ELSE eci.[SuggestedDescription] END,
        [SuggestionSource] = CASE WHEN @SuggestionSource IS NOT NULL THEN @SuggestionSource ELSE eci.[SuggestionSource] END,
        [SuggestionReason] = CASE WHEN @SuggestionReason IS NOT NULL THEN @SuggestionReason ELSE eci.[SuggestionReason] END,
        [SuggestionConfidence] = CASE WHEN @SuggestionConfidence IS NOT NULL THEN @SuggestionConfidence ELSE eci.[SuggestionConfidence] END,
        [SyncTokenAtSuggest] = CASE WHEN @SyncTokenAtSuggest IS NOT NULL THEN @SyncTokenAtSuggest ELSE eci.[SyncTokenAtSuggest] END,
        [Status] = COALESCE(@Status, eci.[Status]),
        [SuggestedAt] = @Now,
        [ModifiedDatetime] = @Now,
        @UpdatedId = eci.[Id]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[PublicId] = @PublicId;

    SELECT
        eci.[Id],
        eci.[PublicId],
        eci.[RowVersion],
        eci.[QboPurchaseId],
        eci.[QboPurchaseLineId],
        eci.[QboLineId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[VendorId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        CONVERT(VARCHAR(19), eci.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), eci.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE RecordExpenseCodingFlag
(
    @PublicId UNIQUEIDENTIFIER,
    @FlagReason NVARCHAR(1024),
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE eci
    SET
        [Status] = N'flagged',
        [FlagReason] = @FlagReason,
        [FlaggedAt] = @Now,
        [ModifiedDatetime] = @Now,
        @UpdatedId = eci.[Id]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[PublicId] = @PublicId;

    SELECT
        eci.[Id],
        eci.[PublicId],
        eci.[RowVersion],
        eci.[QboPurchaseId],
        eci.[QboPurchaseLineId],
        eci.[QboLineId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[VendorId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        CONVERT(VARCHAR(19), eci.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), eci.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ClaimExpenseCodingItem
(
    @PublicId UNIQUEIDENTIFIER,
    @UserId BIGINT,
    @ReclaimAfterSeconds INT = 900
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @ClaimedId BIGINT;

    UPDATE eci
    SET
        [ClaimedByUserId] = @UserId,
        [ClaimedAt] = @Now,
        [ModifiedDatetime] = @Now,
        @ClaimedId = eci.[Id]
    FROM dbo.[ExpenseCodingItem] eci WITH (UPDLOCK, ROWLOCK)
    WHERE eci.[PublicId] = @PublicId
      AND (
          eci.[ClaimedByUserId] IS NULL
          OR eci.[ClaimedByUserId] = @UserId
          OR DATEDIFF(SECOND, eci.[ClaimedAt], @Now) > @ReclaimAfterSeconds
      );

    SELECT
        eci.[Id],
        eci.[PublicId],
        eci.[RowVersion],
        eci.[QboPurchaseId],
        eci.[QboPurchaseLineId],
        eci.[QboLineId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[VendorId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        CONVERT(VARCHAR(19), eci.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), eci.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[Id] = @ClaimedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReleaseExpenseCodingItem
(
    @PublicId UNIQUEIDENTIFIER,
    @UserId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @ReleasedId BIGINT;

    UPDATE eci
    SET
        [ClaimedByUserId] = NULL,
        [ClaimedAt] = NULL,
        [ModifiedDatetime] = @Now,
        @ReleasedId = eci.[Id]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[PublicId] = @PublicId
      AND eci.[ClaimedByUserId] = @UserId;

    SELECT
        eci.[Id],
        eci.[PublicId],
        eci.[RowVersion],
        eci.[QboPurchaseId],
        eci.[QboPurchaseLineId],
        eci.[QboLineId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[VendorId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        CONVERT(VARCHAR(19), eci.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), eci.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[Id] = @ReleasedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE RecordExpenseCodingConfirmation
(
    @PublicId UNIQUEIDENTIFIER,
    @ConfirmedProjectId BIGINT = NULL,
    @ConfirmedSubCostCodeId BIGINT = NULL,
    @ConfirmedDescription NVARCHAR(1024) = NULL,
    @WasOverridden BIT = NULL,
    @ConfirmedByUserId BIGINT = NULL,
    @ExpectedSyncToken NVARCHAR(50) = NULL,
    @Status NVARCHAR(30) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE eci
    SET
        [ConfirmedProjectId] = CASE WHEN @ConfirmedProjectId IS NOT NULL THEN @ConfirmedProjectId ELSE eci.[ConfirmedProjectId] END,
        [ConfirmedSubCostCodeId] = CASE WHEN @ConfirmedSubCostCodeId IS NOT NULL THEN @ConfirmedSubCostCodeId ELSE eci.[ConfirmedSubCostCodeId] END,
        [ConfirmedDescription] = CASE WHEN @ConfirmedDescription IS NOT NULL THEN @ConfirmedDescription ELSE eci.[ConfirmedDescription] END,
        [WasOverridden] = CASE WHEN @WasOverridden IS NOT NULL THEN @WasOverridden ELSE eci.[WasOverridden] END,
        [ConfirmedByUserId] = CASE WHEN @ConfirmedByUserId IS NOT NULL THEN @ConfirmedByUserId ELSE eci.[ConfirmedByUserId] END,
        [ConfirmedAt] = @Now,
        [SyncTokenAtSuggest] = CASE WHEN @ExpectedSyncToken IS NOT NULL THEN @ExpectedSyncToken ELSE eci.[SyncTokenAtSuggest] END,
        [Status] = COALESCE(@Status, eci.[Status]),
        [ModifiedDatetime] = @Now,
        @UpdatedId = eci.[Id]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[PublicId] = @PublicId;

    SELECT
        eci.[Id],
        eci.[PublicId],
        eci.[RowVersion],
        eci.[QboPurchaseId],
        eci.[QboPurchaseLineId],
        eci.[QboLineId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[VendorId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        CONVERT(VARCHAR(19), eci.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), eci.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE MarkExpenseCodingEnqueued
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE eci
    SET
        [Status] = N'enqueued',
        [ModifiedDatetime] = @Now,
        @UpdatedId = eci.[Id]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[PublicId] = @PublicId;

    SELECT
        eci.[Id],
        eci.[PublicId],
        eci.[RowVersion],
        eci.[QboPurchaseId],
        eci.[QboPurchaseLineId],
        eci.[QboLineId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[VendorId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        CONVERT(VARCHAR(19), eci.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), eci.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE MarkExpenseCodingWritten
(
    @PublicId UNIQUEIDENTIFIER,
    @SyncToken NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE eci
    SET
        [Status] = N'written',
        [WrittenAt] = @Now,
        [WriteError] = NULL,
        [ModifiedDatetime] = @Now,
        @UpdatedId = eci.[Id]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[PublicId] = @PublicId;

    SELECT
        eci.[Id],
        eci.[PublicId],
        eci.[RowVersion],
        eci.[QboPurchaseId],
        eci.[QboPurchaseLineId],
        eci.[QboLineId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[VendorId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        CONVERT(VARCHAR(19), eci.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), eci.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE MarkExpenseCodingChangedInQbo
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE eci
    SET
        [Status] = N'changed_in_qbo',
        [ModifiedDatetime] = @Now,
        @UpdatedId = eci.[Id]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[PublicId] = @PublicId;

    SELECT
        eci.[Id],
        eci.[PublicId],
        eci.[RowVersion],
        eci.[QboPurchaseId],
        eci.[QboPurchaseLineId],
        eci.[QboLineId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[VendorId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        CONVERT(VARCHAR(19), eci.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), eci.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE MarkExpenseCodingError
(
    @PublicId UNIQUEIDENTIFIER,
    @WriteError NVARCHAR(1024) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE eci
    SET
        [Status] = N'error',
        -- COALESCE guard: a NULL @WriteError never erases an existing diagnostic
        [WriteError] = COALESCE(@WriteError, eci.[WriteError]),
        [ModifiedDatetime] = @Now,
        @UpdatedId = eci.[Id]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[PublicId] = @PublicId;

    SELECT
        eci.[Id],
        eci.[PublicId],
        eci.[RowVersion],
        eci.[QboPurchaseId],
        eci.[QboPurchaseLineId],
        eci.[QboLineId],
        eci.[QboPurchaseQboId],
        eci.[RealmId],
        eci.[VendorId],
        eci.[SyncTokenAtSuggest],
        eci.[Status],
        eci.[SuggestedProjectId],
        eci.[SuggestedSubCostCodeId],
        eci.[SuggestedDescription],
        eci.[SuggestionSource],
        eci.[SuggestionReason],
        eci.[SuggestionConfidence],
        eci.[SuggestedAt],
        eci.[ConfirmedProjectId],
        eci.[ConfirmedSubCostCodeId],
        eci.[ConfirmedDescription],
        eci.[WasOverridden],
        eci.[ConfirmedByUserId],
        eci.[ConfirmedAt],
        eci.[FlagReason],
        eci.[FlaggedAt],
        eci.[WrittenAt],
        eci.[WriteError],
        eci.[ClaimedByUserId],
        eci.[ClaimedAt],
        eci.[CompanyId],
        eci.[CreatedByUserId],
        CONVERT(VARCHAR(19), eci.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), eci.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[ExpenseCodingItem] eci
    WHERE eci.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO
