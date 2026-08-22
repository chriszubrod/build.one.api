IF OBJECT_ID('dbo.BillLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[BillLineItem]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [BillId] BIGINT NOT NULL,
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
    CONSTRAINT [FK_BillLineItem_Bill] FOREIGN KEY ([BillId]) REFERENCES [dbo].[Bill]([Id]),
    CONSTRAINT [FK_BillLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]),
    CONSTRAINT [FK_BillLineItem_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id])
);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_BillId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_BillId ON [dbo].[BillLineItem] ([BillId]);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_SubCostCodeId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_SubCostCodeId ON [dbo].[BillLineItem] ([SubCostCodeId]);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_ProjectId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_ProjectId ON [dbo].[BillLineItem] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_BillLineItem_PublicId' AND object_id = OBJECT_ID('dbo.BillLineItem'))
BEGIN
CREATE INDEX IX_BillLineItem_PublicId ON [dbo].[BillLineItem] ([PublicId]);
END
GO

-- U-238b added QboId/RealmId + UQ_BillLineItem_BillId_QboId live via
-- scripts/migrations/238b_qbo_identity_lines.sql but never ported the DDL into
-- this base file (the same from-scratch-build gap U-277/U-290 found and fixed
-- for company/address/vendor) — SetBillLineItemQboIdentity below has silently
-- depended on columns this file never declared. Closed here (U-293), verbatim
-- against the live migration so a from-scratch build matches prod exactly.
IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.BillLineItem') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[BillLineItem] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.BillLineItem') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[BillLineItem] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.BillLineItem', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_BillLineItem_BillId_QboId' AND object_id = OBJECT_ID('dbo.BillLineItem')
)
BEGIN
    CREATE UNIQUE INDEX UQ_BillLineItem_BillId_QboId ON [dbo].[BillLineItem] ([BillId], [QboId]) WHERE [QboId] IS NOT NULL;
END
GO



CREATE OR ALTER PROCEDURE CreateBillLineItem
(
    @BillId BIGINT,
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

    INSERT INTO dbo.[BillLineItem] ([CreatedDatetime], [ModifiedDatetime], [BillId], [SubCostCodeId], [ProjectId], [Description], [Quantity], [Rate], [Amount], [IsBillable], [IsBilled], [Markup], [Price], [IsDraft], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[BillId],
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
    VALUES (@Now, @Now, @BillId, @SubCostCodeId, @ProjectId, @Description, @Quantity, @Rate, @Amount, @IsBillable, @IsBilled, @Markup, @Price, @IsDraft, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadBillLineItems
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [BillId],
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
    FROM dbo.[BillLineItem]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO




CREATE OR ALTER PROCEDURE ReadBillLineItemById
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
        [BillId],
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
    FROM dbo.[BillLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO



CREATE OR ALTER PROCEDURE ReadBillLineItemByPublicId
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
        [BillId],
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
    FROM dbo.[BillLineItem]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO





CREATE OR ALTER PROCEDURE ReadBillLineItemsByBillId
(
    @BillId BIGINT
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
        [BillId],
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
    FROM dbo.[BillLineItem]
    WHERE [BillId] = @BillId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO





CREATE OR ALTER PROCEDURE UpdateBillLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @BillId BIGINT,
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

    UPDATE dbo.[BillLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [BillId] = @BillId,
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
        INSERTED.[BillId],
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
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO





CREATE OR ALTER PROCEDURE DeleteBillLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[BillLineItem]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[BillId],
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



CREATE OR ALTER PROCEDURE ReadBillLineItemsByProjectId
(
    @ProjectId BIGINT
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
        [BillId],
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
    FROM dbo.[BillLineItem]
    WHERE [ProjectId] = @ProjectId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO

-- =====================================================================
-- ReadBillLineItemBoxLinks — per-line-item (multi-project bills supported).
-- =====================================================================
-- Returns one row per dbo.BillLineItem on the bill, including line items
-- whose project has no Box mapping (LEFT JOIN — Box columns NULL in that
-- case). The router merges per-row results back into the line-item list
-- by BillLineItemId so the React table can show or hide icons per row.
--
-- Doc class for bills is fixed at 'invoices' (the project's `14 - Invoices`
-- Box folder). Workbook is one per project (UNIQUE on ProjectId).

CREATE OR ALTER PROCEDURE dbo.ReadBillLineItemBoxLinks (@BillId BIGINT)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        bli.[Id]                AS BillLineItemId,
        f.[BoxFolderId]         AS BoxInvoicesFolderId,
        pw.[BoxFileId]          AS BoxWorkbookFileId,
        pw.[WorksheetName]      AS BoxWorkbookWorksheetName
    FROM dbo.[BillLineItem] bli
    LEFT JOIN [box].[ProjectFolder] pf
        ON pf.[ProjectId] = bli.[ProjectId]
       AND pf.[DocClass]  = N'invoices'
    LEFT JOIN [box].[Folder] f
        ON f.[Id] = pf.[BoxFolderId]
    LEFT JOIN [box].[ProjectWorkbook] pw
        ON pw.[ProjectId] = bli.[ProjectId]
    WHERE bli.[BillId] = @BillId;
END;
GO
CREATE OR ALTER PROCEDURE SetBillLineItemQboIdentity
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
    -- is not a complete identity without RealmId. Defense-in-depth alongside
    -- the Python-layer guard in stamp_line_identity_or_warn — only ever set
    -- QboId to a NEW value when RealmId will end up populated, either from
    -- this call or from the row's own already-stamped value. A row with no
    -- realm anywhere (this call or prior) stays fully unstamped rather than
    -- landing in the QboId-set/RealmId-NULL half state found live in prod.
    DECLARE @ExistingQboId NVARCHAR(50), @ExistingRealmId NVARCHAR(50);
    SELECT @ExistingQboId = [QboId], @ExistingRealmId = [RealmId] FROM dbo.[BillLineItem] WHERE [Id] = @Id;
    DECLARE @RealmComplete BIT = CASE WHEN @RealmId IS NOT NULL OR @ExistingRealmId IS NOT NULL THEN 1 ELSE 0 END;

    IF @QboId IS NOT NULL AND @RealmComplete = 1
    BEGIN
        UPDATE sib SET sib.[QboId] = NULL, sib.[RealmId] = NULL, sib.[ModifiedDatetime] = SYSUTCDATETIME()
        FROM dbo.[BillLineItem] sib
        INNER JOIN dbo.[BillLineItem] tgt ON tgt.[BillId] = sib.[BillId]
        WHERE tgt.[Id] = @Id AND sib.[Id] <> @Id AND sib.[QboId] = @QboId;

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[BillLineItem]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL AND @RealmComplete = 1 THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND @RealmComplete = 1 AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
      );

    -- Reflect what's actually stored, not the raw input params — @RealmComplete
    -- can skip the QboId write above, and echoing @QboId regardless would tell
    -- a caller a stamp succeeded when it didn't (no current caller reads these
    -- 2 columns from this result, only [Stolen] — but a future one shouldn't
    -- be misled). Computed from the pre-read + the same CASE logic as the
    -- UPDATE above rather than a second table read — the values are already
    -- fully known at this point.
    DECLARE @FinalQboId NVARCHAR(50) = CASE WHEN @QboId IS NOT NULL AND @RealmComplete = 1 THEN @QboId ELSE @ExistingQboId END;
    DECLARE @FinalRealmId NVARCHAR(50) = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE @ExistingRealmId END;
    SELECT @Id AS [Id], @FinalQboId AS [QboId], @FinalRealmId AS [RealmId], @Stolen AS [Stolen];
END;
GO

-- U-293: parent-scoped direct identity read for the line fast path. A QBO line
-- id is unique only within its parent transaction (confirmed against live prod:
-- real cross-parent QboId collisions exist for every line family), matching the
-- live UQ_BillLineItem_BillId_QboId index this keys against — never look up a
-- line by QboId alone.
CREATE OR ALTER PROCEDURE ReadBillLineItemByBillIdAndQboId
(
    @BillId BIGINT,
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
        [BillId],
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
    FROM dbo.[BillLineItem]
    WHERE [BillId] = @BillId AND [QboId] = @QboId;
END;
GO
