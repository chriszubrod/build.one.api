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
    [QboLineId] NVARCHAR(50) NOT NULL,
    [QboPurchaseQboId] NVARCHAR(50) NOT NULL,
    [RealmId] NVARCHAR(50) NOT NULL,
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

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UQ_ExpenseCodingItem_PurchaseLineIdentity' AND object_id = OBJECT_ID('dbo.ExpenseCodingItem'))
BEGIN
    CREATE UNIQUE INDEX UQ_ExpenseCodingItem_PurchaseLineIdentity
        ON dbo.[ExpenseCodingItem] ([QboPurchaseQboId], [RealmId], [QboLineId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseCodingItem_Status' AND object_id = OBJECT_ID('dbo.ExpenseCodingItem'))
BEGIN
    CREATE INDEX IX_ExpenseCodingItem_Status
        ON dbo.[ExpenseCodingItem] ([Status]);
END
GO
