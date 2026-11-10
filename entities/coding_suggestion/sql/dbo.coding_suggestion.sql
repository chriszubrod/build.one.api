-- CodingSuggestion — one row per document (Bill, BillCredit, Expense) for coding
-- queue + suggestion lifecycle. Expense rows carry QBO Purchase write state.
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/coding_suggestion/sql/dbo.coding_suggestion.sql

GO

IF OBJECT_ID('dbo.CodingSuggestion', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[CodingSuggestion]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CompanyId] BIGINT NOT NULL DEFAULT 1,
    [CreatedByUserId] BIGINT NOT NULL DEFAULT 17,
    [CreatedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    [ModifiedDatetime] DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
    [TargetKind] NVARCHAR(20) NOT NULL,
    [BillId] BIGINT NULL,
    [BillCreditId] BIGINT NULL,
    [ExpenseId] BIGINT NULL,
    [AttachmentId] BIGINT NULL,
    [VendorId] BIGINT NULL,
    [SuggestedProjectId] BIGINT NULL,
    [SuggestedSubCostCodeId] BIGINT NULL,
    [SuggestedDescription] NVARCHAR(1024) NULL,
    [SuggestionSource] NVARCHAR(50) NULL,
    [SuggestionReason] NVARCHAR(1024) NULL,
    [SuggestionConfidence] DECIMAL(5,4) NULL,
    [SuggestedAt] DATETIME2(3) NULL,
    -- FeaturesJson..ModelVersion: U-639 M1 learning record (design §5.3). Written by the M1
    -- resolver/backtest units; NULL until then.
    [FeaturesJson] NVARCHAR(MAX) NULL,
    [BudgetCheck] NVARCHAR(20) NULL,
    [BudgetRemaining] DECIMAL(18,2) NULL,
    [Route] NVARCHAR(10) NULL,
    [ModelVersion] NVARCHAR(50) NULL,
    [ConfirmedProjectId] BIGINT NULL,
    [ConfirmedSubCostCodeId] BIGINT NULL,
    [ConfirmedDescription] NVARCHAR(1024) NULL,
    [WasOverridden] BIT NULL,
    [ConfirmedByUserId] BIGINT NULL,
    [ConfirmedAt] DATETIME2(3) NULL,
    [ConfirmedMixedCodes] BIT NULL,
    [PrefillState] NVARCHAR(12) NULL,
    [ConfirmationSource] NVARCHAR(20) NULL,   -- 'review_submit' | 'confirm_endpoint' (U-652) | 'complete_repoint' (U-551b-f1): review_submit writes per-line coding; complete_repoint repoints OUR written lines
    [ConfirmationSeq] INT NOT NULL DEFAULT 0, -- U-652c: bumped on each confirmation; terminal marks guard on it
    [Status] NVARCHAR(30) NOT NULL DEFAULT 'pending',
    [FlagReason] NVARCHAR(1024) NULL,
    [FlaggedAt] DATETIME2(3) NULL,
    [ClaimedByUserId] BIGINT NULL,
    [ClaimedAt] DATETIME2(3) NULL,
    [QboPurchaseId] BIGINT NULL,
    [QboPurchaseQboId] NVARCHAR(50) NULL,
    [RealmId] NVARCHAR(50) NULL,
    [SyncTokenAtSuggest] NVARCHAR(50) NULL,
    [WrittenAt] DATETIME2(3) NULL,
    [WriteError] NVARCHAR(1024) NULL,
    [WrittenSyncToken] NVARCHAR(50) NULL,     -- U-551b-f1: QBO SyncToken returned by OUR last successful recode write
    [WrittenLinesJson] NVARCHAR(MAX) NULL,    -- U-551b-f1: [{qbo_line_id,item_ref_value,sub_cost_code_id}] the write coded
    [RepointTargetsJson] NVARCHAR(MAX) NULL,  -- U-551b-f1: the coding COMPLETION approved for a repoint (set per confirmation)
    CONSTRAINT [CK_CodingSuggestion_Target] CHECK (
        (
            [TargetKind] = N'bill'
            AND [BillId] IS NOT NULL
            AND [BillCreditId] IS NULL
            AND [ExpenseId] IS NULL
            AND [QboPurchaseQboId] IS NULL
        )
        OR (
            [TargetKind] = N'bill_credit'
            AND [BillCreditId] IS NOT NULL
            AND [BillId] IS NULL
            AND [ExpenseId] IS NULL
            AND [QboPurchaseQboId] IS NULL
        )
        OR (
            [TargetKind] = N'expense'
            AND [BillId] IS NULL
            AND [BillCreditId] IS NULL
            AND (
                [ExpenseId] IS NOT NULL
                OR ([QboPurchaseQboId] IS NOT NULL AND [RealmId] IS NOT NULL)
            )
        )
    )
);
END
GO

IF COL_LENGTH('dbo.CodingSuggestion', 'ConfirmationSource') IS NULL
    ALTER TABLE dbo.[CodingSuggestion] ADD [ConfirmationSource] NVARCHAR(20) NULL;
GO

IF COL_LENGTH('dbo.CodingSuggestion', 'ConfirmationSeq') IS NULL
    ALTER TABLE dbo.[CodingSuggestion] ADD [ConfirmationSeq] INT NOT NULL CONSTRAINT DF_CodingSuggestion_ConfirmationSeq DEFAULT 0;
GO

IF COL_LENGTH('dbo.CodingSuggestion', 'WrittenSyncToken') IS NULL
    ALTER TABLE dbo.[CodingSuggestion] ADD [WrittenSyncToken] NVARCHAR(50) NULL;
GO

IF COL_LENGTH('dbo.CodingSuggestion', 'WrittenLinesJson') IS NULL
    ALTER TABLE dbo.[CodingSuggestion] ADD [WrittenLinesJson] NVARCHAR(MAX) NULL;
GO

IF COL_LENGTH('dbo.CodingSuggestion', 'RepointTargetsJson') IS NULL
    ALTER TABLE dbo.[CodingSuggestion] ADD [RepointTargetsJson] NVARCHAR(MAX) NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_CodingSuggestion_PublicId' AND object_id = OBJECT_ID('dbo.CodingSuggestion'))
BEGIN
    CREATE UNIQUE INDEX UQ_CodingSuggestion_PublicId
        ON dbo.[CodingSuggestion] ([PublicId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_CodingSuggestion_BillId' AND object_id = OBJECT_ID('dbo.CodingSuggestion'))
BEGIN
    CREATE UNIQUE INDEX UQ_CodingSuggestion_BillId
        ON dbo.[CodingSuggestion] ([BillId])
        WHERE [BillId] IS NOT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_CodingSuggestion_BillCreditId' AND object_id = OBJECT_ID('dbo.CodingSuggestion'))
BEGIN
    CREATE UNIQUE INDEX UQ_CodingSuggestion_BillCreditId
        ON dbo.[CodingSuggestion] ([BillCreditId])
        WHERE [BillCreditId] IS NOT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_CodingSuggestion_ExpenseId' AND object_id = OBJECT_ID('dbo.CodingSuggestion'))
BEGIN
    CREATE UNIQUE INDEX UQ_CodingSuggestion_ExpenseId
        ON dbo.[CodingSuggestion] ([ExpenseId])
        WHERE [ExpenseId] IS NOT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_CodingSuggestion_PurchaseIdentity' AND object_id = OBJECT_ID('dbo.CodingSuggestion'))
BEGIN
    CREATE UNIQUE INDEX UQ_CodingSuggestion_PurchaseIdentity
        ON dbo.[CodingSuggestion] ([RealmId], [QboPurchaseQboId])
        WHERE [QboPurchaseQboId] IS NOT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_CodingSuggestion_Status' AND object_id = OBJECT_ID('dbo.CodingSuggestion'))
BEGIN
    CREATE INDEX IX_CodingSuggestion_Status
        ON dbo.[CodingSuggestion] ([Status]);
END
GO


CREATE OR ALTER PROCEDURE UpsertCodingSuggestionForPurchase
(
    @QboPurchaseId BIGINT,
    @QboPurchaseQboId NVARCHAR(50),
    @RealmId NVARCHAR(50),
    @VendorId BIGINT = NULL,
    @ExpenseId BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE dbo.[CodingSuggestion] WITH (HOLDLOCK) AS target
    USING (
        SELECT
            @QboPurchaseQboId AS QboPurchaseQboId,
            @RealmId AS RealmId
    ) AS source
    ON target.[QboPurchaseQboId] = source.QboPurchaseQboId
       AND target.[RealmId] = source.RealmId
       AND target.[TargetKind] = N'expense'
    WHEN MATCHED THEN
        UPDATE SET
            [QboPurchaseId] = CASE WHEN @QboPurchaseId IS NOT NULL THEN @QboPurchaseId ELSE target.[QboPurchaseId] END,
            [VendorId] = CASE WHEN @VendorId IS NOT NULL THEN @VendorId ELSE target.[VendorId] END,
            [ExpenseId] = CASE WHEN @ExpenseId IS NOT NULL AND target.[ExpenseId] IS NULL THEN @ExpenseId ELSE target.[ExpenseId] END,
            [ModifiedDatetime] = @Now
    WHEN NOT MATCHED THEN
        INSERT
            ([TargetKind], [QboPurchaseId], [QboPurchaseQboId], [RealmId], [VendorId], [ExpenseId],
             [Status], [CreatedByUserId], [CreatedDatetime], [ModifiedDatetime])
        VALUES
            (N'expense', @QboPurchaseId, @QboPurchaseQboId, @RealmId, @VendorId, @ExpenseId,
             N'pending', COALESCE(@CreatedByUserId, 17), @Now, @Now)
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[TargetKind],
        INSERTED.[BillId],
        INSERTED.[BillCreditId],
        INSERTED.[ExpenseId],
        INSERTED.[AttachmentId],
        INSERTED.[VendorId],
        INSERTED.[QboPurchaseId],
        INSERTED.[QboPurchaseQboId],
        INSERTED.[RealmId],
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
        INSERTED.[ConfirmedMixedCodes],
        INSERTED.[PrefillState],
        INSERTED.[ConfirmationSource],
        INSERTED.[ConfirmationSeq],
        INSERTED.[FeaturesJson],
        INSERTED.[ModelVersion],
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


CREATE OR ALTER PROCEDURE ReadCodingSuggestionByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[WrittenSyncToken],
        cs.[WrittenLinesJson],
        cs.[RepointTargetsJson],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE RecordCodingSuggestion
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

    UPDATE cs
    SET
        [SuggestedProjectId] = CASE WHEN @SuggestedProjectId IS NOT NULL THEN @SuggestedProjectId ELSE cs.[SuggestedProjectId] END,
        [SuggestedSubCostCodeId] = CASE WHEN @SuggestedSubCostCodeId IS NOT NULL THEN @SuggestedSubCostCodeId ELSE cs.[SuggestedSubCostCodeId] END,
        [SuggestedDescription] = CASE WHEN @SuggestedDescription IS NOT NULL THEN @SuggestedDescription ELSE cs.[SuggestedDescription] END,
        [SuggestionSource] = CASE WHEN @SuggestionSource IS NOT NULL THEN @SuggestionSource ELSE cs.[SuggestionSource] END,
        [SuggestionReason] = CASE WHEN @SuggestionReason IS NOT NULL THEN @SuggestionReason ELSE cs.[SuggestionReason] END,
        [SuggestionConfidence] = CASE WHEN @SuggestionConfidence IS NOT NULL THEN @SuggestionConfidence ELSE cs.[SuggestionConfidence] END,
        [SyncTokenAtSuggest] = CASE WHEN @SyncTokenAtSuggest IS NOT NULL THEN @SyncTokenAtSuggest ELSE cs.[SyncTokenAtSuggest] END,
        [Status] = COALESCE(@Status, cs.[Status]),
        [SuggestedAt] = @Now,
        [ModifiedDatetime] = @Now,
        @UpdatedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId
      AND cs.[Status] IN (N'pending', N'suggested', N'flagged');

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE RecordCodingSuggestionFlag
(
    @PublicId UNIQUEIDENTIFIER,
    @FlagReason NVARCHAR(1024),
    @ModifiedByUserId BIGINT = NULL,
    @OnlyFromPendingLike BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE cs
    SET
        [Status] = N'flagged',
        [FlagReason] = @FlagReason,
        [FlaggedAt] = @Now,
        [ModifiedDatetime] = @Now,
        @UpdatedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId
      AND (@OnlyFromPendingLike = 0 OR cs.[Status] IN (N'pending', N'suggested', N'flagged'));

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ClaimCodingSuggestion
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

    UPDATE cs
    SET
        [ClaimedByUserId] = @UserId,
        [ClaimedAt] = @Now,
        [ModifiedDatetime] = @Now,
        @ClaimedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs WITH (UPDLOCK, ROWLOCK)
    WHERE cs.[PublicId] = @PublicId
      AND (
          cs.[ClaimedByUserId] IS NULL
          OR cs.[ClaimedByUserId] = @UserId
          OR DATEDIFF(SECOND, cs.[ClaimedAt], @Now) > @ReclaimAfterSeconds
      );

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @ClaimedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReleaseCodingSuggestion
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

    UPDATE cs
    SET
        [ClaimedByUserId] = NULL,
        [ClaimedAt] = NULL,
        [ModifiedDatetime] = @Now,
        @ReleasedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId
      AND cs.[ClaimedByUserId] = @UserId;

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @ReleasedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE RecordCodingSuggestionConfirmation
(
    @PublicId UNIQUEIDENTIFIER,
    @ConfirmedProjectId BIGINT = NULL,
    @ConfirmedSubCostCodeId BIGINT = NULL,
    @ConfirmedDescription NVARCHAR(1024) = NULL,
    @WasOverridden BIT = NULL,
    @ConfirmedByUserId BIGINT = NULL,
    @ExpectedSyncToken NVARCHAR(50) = NULL,
    @Status NVARCHAR(30) = NULL,
    @ConfirmedMixedCodes BIT = NULL,
    @PrefillState NVARCHAR(12) = NULL,
    @ConfirmationSource NVARCHAR(20) = NULL,
    @RepointTargetsJson NVARCHAR(MAX) = NULL,
    -- U-551b-f1 R4b: when given, the UPDATE lands only on the revision the caller read.
    -- U-551b-f1-f5: that fenced branch also admits Status 'error', so the system-admin redrive of an
    -- `error` repoint can re-record the SAME approved revision fenced on the seq it read. The unfenced
    -- branch (seq NULL) is unchanged.
    @ExpectedConfirmationSeq INT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE cs
    SET
        [ConfirmedProjectId] = CASE WHEN @ConfirmedProjectId IS NOT NULL THEN @ConfirmedProjectId ELSE cs.[ConfirmedProjectId] END,
        [ConfirmedSubCostCodeId] = CASE WHEN @ConfirmedSubCostCodeId IS NOT NULL THEN @ConfirmedSubCostCodeId ELSE cs.[ConfirmedSubCostCodeId] END,
        [ConfirmedDescription] = CASE WHEN @ConfirmedDescription IS NOT NULL THEN @ConfirmedDescription ELSE cs.[ConfirmedDescription] END,
        [WasOverridden] = CASE WHEN @WasOverridden IS NOT NULL THEN @WasOverridden ELSE cs.[WasOverridden] END,
        [ConfirmedByUserId] = CASE WHEN @ConfirmedByUserId IS NOT NULL THEN @ConfirmedByUserId ELSE cs.[ConfirmedByUserId] END,
        [ConfirmedMixedCodes] = CASE WHEN @ConfirmedMixedCodes IS NOT NULL THEN @ConfirmedMixedCodes ELSE cs.[ConfirmedMixedCodes] END,
        [PrefillState] = CASE WHEN @PrefillState IS NOT NULL THEN @PrefillState ELSE cs.[PrefillState] END,
        [ConfirmationSource] = @ConfirmationSource,
        -- U-551b-f1: UNCONDITIONAL (deliberately NOT the CASE-preserve pattern): every confirmation
        -- redefines its repoint targets, so a first write (NULL) clears a previous repoint's targets.
        [RepointTargetsJson] = @RepointTargetsJson,
        [ConfirmationSeq] = cs.[ConfirmationSeq] + 1,
        [ConfirmedAt] = @Now,
        [SyncTokenAtSuggest] = CASE WHEN @ExpectedSyncToken IS NOT NULL THEN @ExpectedSyncToken ELSE cs.[SyncTokenAtSuggest] END,
        [Status] = COALESCE(@Status, cs.[Status]),
        [ModifiedDatetime] = @Now,
        @UpdatedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId
      AND (
          @ExpectedConfirmationSeq IS NULL
          OR (
              cs.[ConfirmationSeq] = @ExpectedConfirmationSeq
              AND cs.[Status] IN (N'written', N'confirmed', N'enqueued', N'error')
          )
      );

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[RepointTargetsJson],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE MarkCodingSuggestionEnqueued
(
    @PublicId UNIQUEIDENTIFIER,
    @ExpectedConfirmationSeq INT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE cs
    SET
        [Status] = N'enqueued',
        [ModifiedDatetime] = @Now,
        @UpdatedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId
      AND (
          @ExpectedConfirmationSeq IS NULL
          OR (
              cs.[ConfirmationSeq] = @ExpectedConfirmationSeq
              AND cs.[Status] IN (N'confirmed', N'enqueued')
          )
      );

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE MarkCodingSuggestionWritten
(
    @PublicId UNIQUEIDENTIFIER,
    @SyncToken NVARCHAR(50) = NULL,
    @ExpectedConfirmationSeq INT = NULL,
    @WrittenLinesJson NVARCHAR(MAX) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE cs
    SET
        [Status] = N'written',
        [WrittenAt] = @Now,
        [WriteError] = NULL,
        [WrittenSyncToken] = CASE WHEN @SyncToken IS NOT NULL THEN @SyncToken ELSE cs.[WrittenSyncToken] END,
        [WrittenLinesJson] = CASE WHEN @WrittenLinesJson IS NOT NULL THEN @WrittenLinesJson ELSE cs.[WrittenLinesJson] END,
        [ModifiedDatetime] = @Now,
        @UpdatedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId
      AND (
          @ExpectedConfirmationSeq IS NULL
          OR (
              cs.[ConfirmationSeq] = @ExpectedConfirmationSeq
              AND cs.[Status] IN (N'confirmed', N'enqueued')
          )
      );

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[WrittenSyncToken],
        cs.[WrittenLinesJson],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE MarkCodingSuggestionChangedInQbo
(
    @PublicId UNIQUEIDENTIFIER,
    @ExpectedConfirmationSeq INT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE cs
    SET
        [Status] = N'changed_in_qbo',
        [ModifiedDatetime] = @Now,
        @UpdatedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId
      AND (
          @ExpectedConfirmationSeq IS NULL
          OR (
              cs.[ConfirmationSeq] = @ExpectedConfirmationSeq
              AND cs.[Status] IN (N'confirmed', N'enqueued')
          )
      );

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE MarkCodingSuggestionError
(
    @PublicId UNIQUEIDENTIFIER,
    @WriteError NVARCHAR(1024) = NULL,
    @ExpectedConfirmationSeq INT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE cs
    SET
        [Status] = N'error',
        [WriteError] = COALESCE(@WriteError, cs.[WriteError]),
        [ModifiedDatetime] = @Now,
        @UpdatedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId
      AND (
          @ExpectedConfirmationSeq IS NULL
          OR (
              cs.[ConfirmationSeq] = @ExpectedConfirmationSeq
              AND cs.[Status] IN (N'confirmed', N'enqueued')
          )
      );

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadCodingSuggestionStateByExpenseIds
(
    @ExpenseIds NVARCHAR(MAX),
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        e.[Id] AS [ExpenseId],
        cs.[PublicId] AS [CodingItemPublicId],
        cs.[Status],
        cs.[SuggestionConfidence],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[FlagReason],
        cs.[ConfirmationSource]
    FROM dbo.[CodingSuggestion] cs
    INNER JOIN dbo.[Expense] e
        ON (
            cs.[ExpenseId] IS NOT NULL
            AND e.[Id] = cs.[ExpenseId]
        )
        OR (
            cs.[ExpenseId] IS NULL
            AND cs.[QboPurchaseQboId] IS NOT NULL
            AND cs.[RealmId] IS NOT NULL
            AND e.[QboId] = cs.[QboPurchaseQboId]
            AND e.[RealmId] = cs.[RealmId]
        )
    INNER JOIN STRING_SPLIT(ISNULL(@ExpenseIds, ''), ',') s
        ON s.value <> ''
       AND e.[Id] = TRY_CAST(LTRIM(RTRIM(s.value)) AS BIGINT)
    WHERE
      EXISTS (
          SELECT 1
          FROM dbo.[ExpenseLineItem] eli
          INNER JOIN qbo.[Purchase] p
              ON p.[QboId] = e.[QboId]
             AND p.[RealmId] = e.[RealmId]
          INNER JOIN qbo.[PurchaseLine] pl
              ON pl.[QboPurchaseId] = p.[Id]
             AND pl.[QboLineId] = eli.[QboId]
             AND eli.[RealmId] = p.[RealmId]
          WHERE eli.[ExpenseId] = e.[Id]
      )
      AND (
          @ActorIsSystemAdmin = 1
          OR COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId]) IS NULL
          OR dbo.UserCanAccessProject(
                 @ActorUserId, @ActorIsSystemAdmin,
                 COALESCE(cs.[ConfirmedProjectId], cs.[SuggestedProjectId])) = 1
      );
END;
GO


-- ReadCodingSuggestionExistsForPurchase — unscoped purchase-grain existence (U-715 C2).
-- Returns Id/PublicId when a row exists for (RealmId, QboPurchaseQboId); no actor filter.
-- Used only to decide whether to seed via UpsertCodingSuggestionForPurchase; never expose
-- the row to API callers (scoped reads remain ReadCodingSuggestionStateByExpenseIds).
CREATE OR ALTER PROCEDURE ReadCodingSuggestionExistsForPurchase
(
    @RealmId NVARCHAR(50),
    @QboPurchaseQboId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (1)
        cs.[Id],
        cs.[PublicId]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[TargetKind] = N'expense'
      AND cs.[RealmId] = @RealmId
      AND cs.[QboPurchaseQboId] = @QboPurchaseQboId;
END;
GO


CREATE OR ALTER PROCEDURE ReadExternallyResolvedCodingSuggestionCandidates
AS
BEGIN
    SET NOCOUNT ON;

    -- U-652: purchase grain needs ONE arm. A purchase is externally resolved when no GENUINE
    -- placeholder remains on it (58999 label AND no ItemRef). A line that keeps the label but
    -- carries an Item was recoded in QBO, so it does not block. The pre-unit second arm was
    -- per line; at purchase grain it would close a purchase whose other line is still open.

    SELECT
        cs.[PublicId],
        cs.[Id],
        cs.[Status],
        cs.[QboPurchaseQboId]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[TargetKind] = N'expense'
      AND cs.[QboPurchaseQboId] IS NOT NULL
      AND cs.[RealmId] IS NOT NULL
      AND cs.[Status] IN (N'pending', N'suggested', N'flagged', N'changed_in_qbo')
      AND EXISTS (
          SELECT 1
          FROM [qbo].[Purchase] p
          WHERE p.[QboId] = cs.[QboPurchaseQboId]
            AND p.[RealmId] = cs.[RealmId]
      )
      AND NOT EXISTS (
          SELECT 1
          FROM [qbo].[Purchase] p
          INNER JOIN [qbo].[PurchaseLine] pl ON pl.[QboPurchaseId] = p.[Id]
          WHERE p.[QboId] = cs.[QboPurchaseQboId]
            AND p.[RealmId] = cs.[RealmId]
            AND pl.[IsUncodedPlaceholder] = 1
      );
END;
GO


CREATE OR ALTER PROCEDURE MarkCodingSuggestionResolvedExternally
(
    @PublicId UNIQUEIDENTIFIER,
    @WriteError NVARCHAR(1024) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE cs
    SET
        [Status] = N'resolved_externally',
        [WriteError] = COALESCE(
            @WriteError,
            N'recoded outside the cockpit; staging line replaced'
        ),
        [ModifiedDatetime] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[TargetKind],
        INSERTED.[BillId],
        INSERTED.[BillCreditId],
        INSERTED.[ExpenseId],
        INSERTED.[AttachmentId],
        INSERTED.[VendorId],
        INSERTED.[QboPurchaseId],
        INSERTED.[QboPurchaseQboId],
        INSERTED.[RealmId],
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
        INSERTED.[ConfirmedMixedCodes],
        INSERTED.[PrefillState],
        INSERTED.[ConfirmationSource],
        INSERTED.[ConfirmationSeq],
        INSERTED.[FeaturesJson],
        INSERTED.[ModelVersion],
        INSERTED.[FlagReason],
        INSERTED.[FlaggedAt],
        INSERTED.[WrittenAt],
        INSERTED.[WriteError],
        INSERTED.[ClaimedByUserId],
        INSERTED.[ClaimedAt],
        INSERTED.[CompanyId],
        INSERTED.[CreatedByUserId],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId
      AND cs.[Status] IN (N'pending', N'suggested', N'flagged', N'changed_in_qbo');

    COMMIT TRANSACTION;
END;
GO


-- U-551b-f1-f5: confirmed rows whose enqueue was lost (crash/raise between RecordCodingSuggestionConfirmation
-- and the outbox enqueue). "Live" outbox = pending / failed / in_progress; `done` is NOT live (a done
-- row for a still-`confirmed` suggestion means the drain finished without marking it). HasDeadLetter
-- lets the caller refuse (an operator replays dead letters), never re-enqueue behind one.
CREATE OR ALTER PROCEDURE ReadStrandedConfirmedCodingSuggestions
(
    @OlderThanSeconds INT = 300,
    @Limit INT = 100
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@Limit)
        cs.[PublicId],
        cs.[RealmId],
        cs.[ConfirmationSeq],
        cs.[ConfirmationSource],
        CONVERT(VARCHAR(23), cs.[ConfirmedAt], 121) AS [ConfirmedAt],
        CAST(
            CASE WHEN EXISTS (
                SELECT 1
                FROM [qbo].[Outbox] d
                WHERE d.[Kind] = N'recode_purchase_line'
                  AND d.[EntityType] = N'CodingSuggestion'
                  AND d.[EntityPublicId] = cs.[PublicId]
                  AND d.[Status] = N'dead_letter'
            ) THEN 1 ELSE 0 END AS BIT
        ) AS [HasDeadLetter]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Status] = N'confirmed'
      AND cs.[ConfirmedAt] <= DATEADD(SECOND, -@OlderThanSeconds, SYSUTCDATETIME())
      AND NOT EXISTS (
          SELECT 1
          FROM [qbo].[Outbox] o
          WHERE o.[Kind] = N'recode_purchase_line'
            AND o.[EntityType] = N'CodingSuggestion'
            AND o.[EntityPublicId] = cs.[PublicId]
            AND o.[Status] IN (N'pending', N'failed', N'in_progress')
      )
    ORDER BY cs.[ConfirmedAt];
END;
GO


-- U-551b-f1-f5: report-only recovery visibility. (a) `error` repoints (admin redrive); (b) `written` rows
-- with no written record (adoption is f3b, open). Legacy pre-record rows are excluded by the caller's
-- @RecordRequiredSince.
-- U-652d4: (c) `enqueued_stranded` - an `enqueued` row older than @EnqueuedOlderThanSeconds (ModifiedDatetime)
-- with NO live outbox row (pending / failed / in_progress). A dead-lettered row has no live row, so it is
-- listed here too. REPORT ONLY: the sweeper never enqueues this category (a re-enqueue could re-send a write
-- QBO already applied). Outbox EntityType matches 'CodingSuggestion', as in the stranded arm.
CREATE OR ALTER PROCEDURE ReadCodingSuggestionRecoveryCandidates
(
    @RecordRequiredSince DATETIME2(3) = NULL,
    @Limit INT = 100,
    @EnqueuedOlderThanSeconds INT = 300
)
AS
BEGIN
    SET NOCOUNT ON;

    -- U-652d4 (round 4): each arm carries its OWN TOP (@Limit) and ORDER BY, so one category cannot crowd out
    -- another (a single TOP over the UNION let enqueued_stranded rows displace error_repoint / written rows).
    SELECT
        u.[PublicId],
        u.[RealmId],
        u.[ConfirmationSeq],
        u.[Category],
        u.[WriteError]
    FROM (
        SELECT e.* FROM (
            SELECT TOP (@Limit)
                cs.[PublicId],
                cs.[RealmId],
                cs.[ConfirmationSeq],
                N'error_repoint' AS [Category],
                cs.[WriteError] AS [WriteError],
                cs.[ModifiedDatetime] AS [SortAt]
            FROM dbo.[CodingSuggestion] cs
            WHERE cs.[Status] = N'error'
              AND cs.[ConfirmationSource] = N'complete_repoint'
            ORDER BY cs.[ModifiedDatetime]
        ) e

        UNION ALL

        SELECT w.* FROM (
            SELECT TOP (@Limit)
                cs.[PublicId],
                cs.[RealmId],
                cs.[ConfirmationSeq],
                N'written_without_record' AS [Category],
                cs.[WriteError] AS [WriteError],
                cs.[ModifiedDatetime] AS [SortAt]
            FROM dbo.[CodingSuggestion] cs
            WHERE cs.[Status] = N'written'
              AND cs.[WrittenSyncToken] IS NULL
              AND cs.[WrittenLinesJson] IS NULL
              AND (@RecordRequiredSince IS NULL OR cs.[WrittenAt] >= @RecordRequiredSince)
            ORDER BY cs.[ModifiedDatetime]
        ) w

        UNION ALL

        SELECT n.* FROM (
            SELECT TOP (@Limit)
                cs.[PublicId],
                cs.[RealmId],
                cs.[ConfirmationSeq],
                N'enqueued_stranded' AS [Category],
                cs.[WriteError] AS [WriteError],
                cs.[ModifiedDatetime] AS [SortAt]
            FROM dbo.[CodingSuggestion] cs
            WHERE cs.[Status] = N'enqueued'
              AND cs.[ModifiedDatetime] <= DATEADD(SECOND, -@EnqueuedOlderThanSeconds, SYSUTCDATETIME())
              AND NOT EXISTS (
                  SELECT 1
                  FROM [qbo].[Outbox] o
                  WHERE o.[Kind] = N'recode_purchase_line'
                    AND o.[EntityType] = N'CodingSuggestion'
                    AND o.[EntityPublicId] = cs.[PublicId]
                    AND o.[Status] IN (N'pending', N'failed', N'in_progress')
              )
            ORDER BY cs.[ModifiedDatetime]
        ) n
    ) u
    ORDER BY u.[SortAt];
END;
GO


-- U-551b-f1-f3b: ADOPT the written record of a first write that crashed between the QBO POST and the mark.
-- The API (system-admin action) has already GET-verified every coded line against the live Purchase; this
-- only persists the result. Guard IS the refusal set: written, NO record at all, same revision the caller
-- read (a revision recorded since loses -> 0 rows -> the service answers `moved`). SET is unconditional and
-- both params are required: never the CASE-preserve pattern (that is how a record-less `written` was made).
-- WrittenAt / Status / ConfirmationSeq are untouched.
CREATE OR ALTER PROCEDURE AdoptCodingSuggestionWrittenRecord
(
    @PublicId UNIQUEIDENTIFIER,
    @WrittenSyncToken NVARCHAR(50),
    @WrittenLinesJson NVARCHAR(MAX),
    @ExpectedConfirmationSeq INT
)
AS
BEGIN
    SET NOCOUNT ON;

    IF @WrittenSyncToken IS NULL OR @WrittenLinesJson IS NULL OR @ExpectedConfirmationSeq IS NULL
        THROW 50000, 'AdoptCodingSuggestionWrittenRecord requires @WrittenSyncToken, @WrittenLinesJson and @ExpectedConfirmationSeq', 1;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @UpdatedId BIGINT;

    UPDATE cs
    SET
        [WrittenSyncToken] = @WrittenSyncToken,
        [WrittenLinesJson] = @WrittenLinesJson,
        [ModifiedDatetime] = @Now,
        @UpdatedId = cs.[Id]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[PublicId] = @PublicId
      AND cs.[Status] = N'written'
      AND cs.[WrittenSyncToken] IS NULL
      AND cs.[WrittenLinesJson] IS NULL
      AND cs.[ConfirmationSeq] = @ExpectedConfirmationSeq;

    SELECT
        cs.[Id],
        cs.[PublicId],
        cs.[RowVersion],
        cs.[TargetKind],
        cs.[BillId],
        cs.[BillCreditId],
        cs.[ExpenseId],
        cs.[AttachmentId],
        cs.[VendorId],
        cs.[QboPurchaseId],
        cs.[QboPurchaseQboId],
        cs.[RealmId],
        cs.[SyncTokenAtSuggest],
        cs.[Status],
        cs.[SuggestedProjectId],
        cs.[SuggestedSubCostCodeId],
        cs.[SuggestedDescription],
        cs.[SuggestionSource],
        cs.[SuggestionReason],
        cs.[SuggestionConfidence],
        cs.[SuggestedAt],
        cs.[FeaturesJson],
        cs.[ModelVersion],
        cs.[ConfirmedProjectId],
        cs.[ConfirmedSubCostCodeId],
        cs.[ConfirmedDescription],
        cs.[WasOverridden],
        cs.[ConfirmedByUserId],
        cs.[ConfirmedAt],
        cs.[ConfirmedMixedCodes],
        cs.[PrefillState],
        cs.[ConfirmationSource],
        cs.[ConfirmationSeq],
        cs.[FlagReason],
        cs.[FlaggedAt],
        cs.[WrittenAt],
        cs.[WriteError],
        cs.[WrittenSyncToken],
        cs.[WrittenLinesJson],
        cs.[ClaimedByUserId],
        cs.[ClaimedAt],
        cs.[CompanyId],
        cs.[CreatedByUserId],
        CONVERT(VARCHAR(19), cs.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), cs.[ModifiedDatetime], 120) AS [ModifiedDatetime]
    FROM dbo.[CodingSuggestion] cs
    WHERE cs.[Id] = @UpdatedId;

    COMMIT TRANSACTION;
END;
GO
