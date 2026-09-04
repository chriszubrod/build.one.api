GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ExpenseLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ExpenseId] BIGINT NOT NULL,
    [SubCostCodeId] BIGINT NULL,
    [ProjectId] BIGINT NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Quantity] DECIMAL(18,4) NULL,
    [Rate] DECIMAL(18,4) NULL,
    [Amount] DECIMAL(18,2) NULL,
    [IsBillable] BIT NULL,
    [IsBilled] BIT NULL,
    [Markup] DECIMAL(18,4) NULL,
    [Price] DECIMAL(18,2) NULL,
    [IsDraft] BIT NOT NULL DEFAULT 1,
    CONSTRAINT [FK_ExpenseLineItem_Expense] FOREIGN KEY ([ExpenseId]) REFERENCES [dbo].[Expense]([Id]),
    CONSTRAINT [FK_ExpenseLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]),
    CONSTRAINT [FK_ExpenseLineItem_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id])
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.ExpenseLineItem') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItem] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_ExpenseLineItem_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ExpenseLineItem_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItem] ADD CONSTRAINT [FK_ExpenseLineItem_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseLineItem_ExpenseId' AND object_id = OBJECT_ID('dbo.ExpenseLineItem'))
BEGIN
CREATE INDEX IX_ExpenseLineItem_ExpenseId ON [dbo].[ExpenseLineItem] ([ExpenseId]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseLineItem_SubCostCodeId' AND object_id = OBJECT_ID('dbo.ExpenseLineItem'))
BEGIN
CREATE INDEX IX_ExpenseLineItem_SubCostCodeId ON [dbo].[ExpenseLineItem] ([SubCostCodeId]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseLineItem_ProjectId' AND object_id = OBJECT_ID('dbo.ExpenseLineItem'))
BEGIN
CREATE INDEX IX_ExpenseLineItem_ProjectId ON [dbo].[ExpenseLineItem] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ExpenseLineItem_PublicId' AND object_id = OBJECT_ID('dbo.ExpenseLineItem'))
BEGIN
CREATE INDEX IX_ExpenseLineItem_PublicId ON [dbo].[ExpenseLineItem] ([PublicId]);
END
GO

-- U-238b added QboId/RealmId + UQ_ExpenseLineItem_ExpenseId_QboId live via
-- scripts/migrations/238b_qbo_identity_lines.sql but never ported the DDL into
-- this base file (the same from-scratch-build gap U-277/U-290/U-293 found and
-- fixed for company/address/vendor/bill_line_item) — SetExpenseLineItemQboIdentity
-- below has silently depended on columns this file never declared. Closed here
-- (U-293b), verbatim against the live migration so a from-scratch build matches
-- prod exactly.
IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.ExpenseLineItem') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItem] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.ExpenseLineItem') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[ExpenseLineItem] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.ExpenseLineItem', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_ExpenseLineItem_ExpenseId_QboId' AND object_id = OBJECT_ID('dbo.ExpenseLineItem')
)
BEGIN
    CREATE UNIQUE INDEX UQ_ExpenseLineItem_ExpenseId_QboId ON [dbo].[ExpenseLineItem] ([ExpenseId], [QboId]) WHERE [QboId] IS NOT NULL;
END
GO


GO

CREATE OR ALTER PROCEDURE CreateExpenseLineItem
(
    @ExpenseId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @IsBilled BIT NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = 1,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ExpenseLineItem] ([CreatedDatetime], [ModifiedDatetime], [ExpenseId], [SubCostCodeId], [ProjectId], [Description], [Quantity], [Rate], [Amount], [IsBillable], [IsBilled], [Markup], [Price], [IsDraft], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ExpenseId],
        INSERTED.[SubCostCodeId],
        INSERTED.[ProjectId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[Rate],
        INSERTED.[Amount],
        INSERTED.[IsBillable],
        INSERTED.[IsBilled],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsDraft]
    VALUES (@Now, @Now, @ExpenseId, @SubCostCodeId, @ProjectId, @Description, @Quantity, @Rate, @Amount, @IsBillable, @IsBilled, @Markup, @Price, @IsDraft, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItems
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [ExpenseId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft]
    FROM dbo.[ExpenseLineItem]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemById
(
    @Id BIGINT
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
        [ExpenseId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft],
        [QboId],
        [RealmId]
    FROM dbo.[ExpenseLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemByPublicId
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
        [ExpenseId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft],
        [QboId],
        [RealmId]
    FROM dbo.[ExpenseLineItem]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE ReadExpenseLineItemsByExpenseId
(
    @ExpenseId BIGINT
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
        [ExpenseId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft],
        [QboId],
        [RealmId]
    FROM dbo.[ExpenseLineItem]
    WHERE [ExpenseId] = @ExpenseId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE UpdateExpenseLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @ExpenseId BIGINT,
    @SubCostCodeId BIGINT NULL,
    @ProjectId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Quantity DECIMAL(18,4) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Amount DECIMAL(18,2) NULL,
    @IsBillable BIT NULL,
    @IsBilled BIT NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsDraft BIT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ExpenseLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [ExpenseId] = @ExpenseId,
        [SubCostCodeId] = @SubCostCodeId,
        [ProjectId] = @ProjectId,
        [Description] = @Description,
        [Quantity] = @Quantity,
        [Rate] = @Rate,
        [Amount] = @Amount,
        [IsBillable] = @IsBillable,
        [IsBilled] = @IsBilled,
        [Markup] = @Markup,
        [Price] = @Price,
        [IsDraft] = CASE WHEN @IsDraft IS NULL THEN [IsDraft] ELSE @IsDraft END
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ExpenseId],
        INSERTED.[SubCostCodeId],
        INSERTED.[ProjectId],
        INSERTED.[Description],
        INSERTED.[Quantity],
        INSERTED.[Rate],
        INSERTED.[Amount],
        INSERTED.[IsBillable],
        INSERTED.[IsBilled],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsDraft],
        INSERTED.[QboId],
        INSERTED.[RealmId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

GO

CREATE OR ALTER PROCEDURE DeleteExpenseLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ExpenseLineItem]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ExpenseId],
        DELETED.[SubCostCodeId],
        DELETED.[ProjectId],
        DELETED.[Description],
        DELETED.[Quantity],
        DELETED.[Rate],
        DELETED.[Amount],
        DELETED.[IsBillable],
        DELETED.[IsBilled],
        DELETED.[Markup],
        DELETED.[Price],
        DELETED.[IsDraft]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE SetExpenseLineItemQboIdentity
(
    @Id BIGINT,
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Stolen BIT = 0;

    -- U-293-dw: QboId is only unique within its own parent transaction, so it
    -- is not a complete identity without RealmId. Only ever set QboId to a
    -- NEW value when RealmId will end up populated, either from this call or
    -- from the row's own already-stamped value — mirrors
    -- dbo.bill_line_item.sql's SetBillLineItemQboIdentity guard exactly.
    DECLARE @ExistingQboId NVARCHAR(50), @ExistingRealmId NVARCHAR(50);
    SELECT @ExistingQboId = [QboId], @ExistingRealmId = [RealmId] FROM dbo.[ExpenseLineItem] WHERE [Id] = @Id;
    DECLARE @RealmComplete BIT = CASE WHEN @RealmId IS NOT NULL OR @ExistingRealmId IS NOT NULL THEN 1 ELSE 0 END;

    IF @QboId IS NOT NULL AND @RealmComplete = 1
    BEGIN
        UPDATE sib SET sib.[QboId] = NULL, sib.[RealmId] = NULL, sib.[ModifiedDatetime] = SYSUTCDATETIME()
        FROM dbo.[ExpenseLineItem] sib
        INNER JOIN dbo.[ExpenseLineItem] tgt ON tgt.[ExpenseId] = sib.[ExpenseId]
        WHERE tgt.[Id] = @Id AND sib.[Id] <> @Id AND sib.[QboId] = @QboId;

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[ExpenseLineItem]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL AND @RealmComplete = 1 THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND @RealmComplete = 1 AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
      );

    DECLARE @FinalQboId NVARCHAR(50) = CASE WHEN @QboId IS NOT NULL AND @RealmComplete = 1 THEN @QboId ELSE @ExistingQboId END;
    DECLARE @FinalRealmId NVARCHAR(50) = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE @ExistingRealmId END;
    SELECT @Id AS [Id], @FinalQboId AS [QboId], @FinalRealmId AS [RealmId], @Stolen AS [Stolen];
END;
GO

-- U-293b: parent-scoped direct identity read for the line fast path, mirroring
-- dbo.bill_line_item.sql's ReadBillLineItemByBillIdAndQboId. A QBO line id is
-- unique only within its parent transaction (confirmed against live prod: real
-- cross-parent QboId collisions exist for every line family), matching the live
-- UQ_ExpenseLineItem_ExpenseId_QboId index this keys against — never look up a
-- line by QboId alone.
CREATE OR ALTER PROCEDURE ReadExpenseLineItemByExpenseIdAndQboId
(
    @ExpenseId BIGINT,
    @QboId NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [ExpenseId],
        [SubCostCodeId],
        [ProjectId],
        [Description],
        [Quantity],
        [Rate],
        [Amount],
        [IsBillable],
        [IsBilled],
        [Markup],
        [Price],
        [IsDraft],
        [QboId],
        [RealmId]
    FROM dbo.[ExpenseLineItem]
    WHERE [ExpenseId] = @ExpenseId AND [QboId] = @QboId;
END;
GO
