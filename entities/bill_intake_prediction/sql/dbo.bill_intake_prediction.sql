-- Contract: dbo.BillIntakePrediction
-- Owns: one AI intake snapshot per Attachment (header + coding prediction, optional final on complete).
-- Invariants: unique AttachmentId; BillId bound once; Final* written once when FinalizedDatetime was null.
-- No FK constraints (telemetry): parent Attachment/Bill/Vendor rows may be deleted; orphan ids are acceptable
--   (IDENTITY keys are never reused). Keeps bill delete, attachment delete, and vendor merge from SQL 547.
-- Residual: UpsertBillIntakePrediction stamps ExtractedDatetime from the attachment row at upsert time; a
--   concurrent re-extraction of the same attachment between that read and the MERGE is a known, accepted race.
-- Sprocs: BindBillIntakePredictionToBill, ReadBillIntakePredictionByAttachmentId, ReadBillIntakePredictionByBillId, ReadBillIntakePredictionsForAccuracy, RecordBillIntakePredictionFinal, UpsertBillIntakePrediction
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/bill_intake_prediction/sql/dbo.bill_intake_prediction.sql

GO

IF OBJECT_ID('dbo.BillIntakePrediction', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[BillIntakePrediction]
    (
        [Id]                       BIGINT             IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [PublicId]                 UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
        [RowVersion]               ROWVERSION         NOT NULL,
        [CreatedDatetime]          DATETIME2(3)       NOT NULL,
        [ModifiedDatetime]         DATETIME2(3)       NOT NULL,
        [CreatedByUserId]          BIGINT             NULL,
        [AttachmentId]             BIGINT             NOT NULL,
        [ExtractedDatetime]        DATETIME2(3)       NULL,
        [BillId]                   BIGINT             NULL,
        [IntakeSource]             NVARCHAR(20)       NOT NULL,
        [ModelVersion]             NVARCHAR(50)       NOT NULL,
        [Status]                   NVARCHAR(20)       NOT NULL,
        [PredVendorId]             BIGINT             NULL,
        [PredVendorExtractedName]  NVARCHAR(255)      NULL,
        [PredBillNumber]           NVARCHAR(100)      NULL,
        [PredBillDate]             DATE               NULL,
        [PredTotalAmount]          DECIMAL(18,2)      NULL,
        [PredDescription]          NVARCHAR(500)      NULL,
        [PredSubCostCodeId]        BIGINT             NULL,
        [PredProjectId]            BIGINT             NULL,
        [CodingMode]               CHAR(1)            NULL,
        [CodingChosenProjectId]    BIGINT             NULL,
        [HeaderConfidence]         DECIMAL(5,4)       NULL,
        [HeaderModel]              NVARCHAR(100)      NULL,
        [CodingConfidence]         DECIMAL(5,4)       NULL,
        [CodingModel]              NVARCHAR(100)      NULL,
        [LatencyMsTotal]           INT                NULL,
        [TimingJson]               NVARCHAR(2000)     NULL,
        [InputTokens]              INT                NULL,
        [OutputTokens]             INT                NULL,
        [CostUsd]                  DECIMAL(12,6)      NULL,
        [FinalVendorId]            BIGINT             NULL,
        [FinalBillNumber]          NVARCHAR(50)       NULL,
        [FinalBillDate]            DATE               NULL,
        [FinalTotalAmount]         DECIMAL(18,2)      NULL,
        [FinalSubCostCodeId]       BIGINT             NULL,
        [FinalProjectId]           BIGINT             NULL,
        [FinalizedDatetime]        DATETIME2(3)       NULL,
        -- PLACEHOLDER (U-676m): nothing writes RejectReason in this unit.
        [RejectReason]             NVARCHAR(500)      NULL,
        CONSTRAINT [CK_BillIntakePrediction_IntakeSource] CHECK ([IntakeSource] IN ('pdf_drop','pdf_upload','list_drop','reread')),
        CONSTRAINT [CK_BillIntakePrediction_Status] CHECK ([Status] IN ('ok','partial','unreadable','timeout','ai_unavailable'))
    );
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_BillIntakePrediction_AttachmentId'
      AND object_id = OBJECT_ID('dbo.BillIntakePrediction')
)
BEGIN
    CREATE UNIQUE INDEX [UQ_BillIntakePrediction_AttachmentId]
        ON dbo.[BillIntakePrediction] ([AttachmentId]);
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_BillIntakePrediction_BillId'
      AND object_id = OBJECT_ID('dbo.BillIntakePrediction')
)
BEGIN
    CREATE INDEX [IX_BillIntakePrediction_BillId]
        ON dbo.[BillIntakePrediction] ([BillId])
        WHERE [BillId] IS NOT NULL;
END
GO

CREATE OR ALTER PROCEDURE UpsertBillIntakePrediction
(
    @AttachmentId BIGINT,
    @IntakeSource NVARCHAR(20),
    @ModelVersion NVARCHAR(50),
    @Status NVARCHAR(20),
    @PredVendorId BIGINT = NULL,
    @PredVendorExtractedName NVARCHAR(255) = NULL,
    @PredBillNumber NVARCHAR(100) = NULL,
    @PredBillDate DATE = NULL,
    @PredTotalAmount DECIMAL(18,2) = NULL,
    @PredDescription NVARCHAR(500) = NULL,
    @PredSubCostCodeId BIGINT = NULL,
    @PredProjectId BIGINT = NULL,
    @CodingMode CHAR(1) = NULL,
    @CodingChosenProjectId BIGINT = NULL,
    @HeaderConfidence DECIMAL(5,4) = NULL,
    @HeaderModel NVARCHAR(100) = NULL,
    @CodingConfidence DECIMAL(5,4) = NULL,
    @CodingModel NVARCHAR(100) = NULL,
    @LatencyMsTotal INT = NULL,
    @TimingJson NVARCHAR(2000) = NULL,
    @InputTokens INT = NULL,
    @OutputTokens INT = NULL,
    @CostUsd DECIMAL(12,6) = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @ExtractedDatetime DATETIME2(3);

    SELECT @ExtractedDatetime = a.[ExtractedDatetime]
    FROM dbo.[Attachment] a
    WHERE a.[Id] = @AttachmentId;

    IF @@ROWCOUNT = 0
    BEGIN
        COMMIT TRANSACTION;
        SELECT TOP (0)
            CAST(NULL AS BIGINT) AS [Id],
            CAST(NULL AS UNIQUEIDENTIFIER) AS [PublicId],
            CAST(NULL AS VARBINARY(8)) AS [RowVersion],
            CAST(NULL AS DATETIME2(3)) AS [CreatedDatetime],
            CAST(NULL AS DATETIME2(3)) AS [ModifiedDatetime],
            CAST(NULL AS BIGINT) AS [CreatedByUserId],
            CAST(NULL AS BIGINT) AS [AttachmentId],
            CAST(NULL AS DATETIME2(3)) AS [ExtractedDatetime],
            CAST(NULL AS BIGINT) AS [BillId],
            CAST(NULL AS NVARCHAR(20)) AS [IntakeSource],
            CAST(NULL AS NVARCHAR(50)) AS [ModelVersion],
            CAST(NULL AS NVARCHAR(20)) AS [Status],
            CAST(NULL AS BIGINT) AS [PredVendorId],
            CAST(NULL AS NVARCHAR(255)) AS [PredVendorExtractedName],
            CAST(NULL AS NVARCHAR(100)) AS [PredBillNumber],
            CAST(NULL AS DATE) AS [PredBillDate],
            CAST(NULL AS DECIMAL(18,2)) AS [PredTotalAmount],
            CAST(NULL AS NVARCHAR(500)) AS [PredDescription],
            CAST(NULL AS BIGINT) AS [PredSubCostCodeId],
            CAST(NULL AS BIGINT) AS [PredProjectId],
            CAST(NULL AS CHAR(1)) AS [CodingMode],
            CAST(NULL AS BIGINT) AS [CodingChosenProjectId],
            CAST(NULL AS DECIMAL(5,4)) AS [HeaderConfidence],
            CAST(NULL AS NVARCHAR(100)) AS [HeaderModel],
            CAST(NULL AS DECIMAL(5,4)) AS [CodingConfidence],
            CAST(NULL AS NVARCHAR(100)) AS [CodingModel],
            CAST(NULL AS INT) AS [LatencyMsTotal],
            CAST(NULL AS NVARCHAR(2000)) AS [TimingJson],
            CAST(NULL AS INT) AS [InputTokens],
            CAST(NULL AS INT) AS [OutputTokens],
            CAST(NULL AS DECIMAL(12,6)) AS [CostUsd],
            CAST(NULL AS BIGINT) AS [FinalVendorId],
            CAST(NULL AS NVARCHAR(50)) AS [FinalBillNumber],
            CAST(NULL AS DATE) AS [FinalBillDate],
            CAST(NULL AS DECIMAL(18,2)) AS [FinalTotalAmount],
            CAST(NULL AS BIGINT) AS [FinalSubCostCodeId],
            CAST(NULL AS BIGINT) AS [FinalProjectId],
            CAST(NULL AS DATETIME2(3)) AS [FinalizedDatetime],
            CAST(NULL AS NVARCHAR(500)) AS [RejectReason];
        RETURN;
    END

    -- Same deterministic link lookup as the post-MERGE re-bind below; read here so the
    -- returned row is right in the normal case.
    DECLARE @LinkedBillId BIGINT = (
        SELECT TOP (1) li.[BillId]
        FROM dbo.[BillLineItemAttachment] blia WITH (READCOMMITTEDLOCK)
        INNER JOIN dbo.[BillLineItem] li ON li.[Id] = blia.[BillLineItemId]
        WHERE blia.[AttachmentId] = @AttachmentId
        ORDER BY li.[Id] DESC
    );

    MERGE dbo.[BillIntakePrediction] WITH (HOLDLOCK) AS target
    USING (SELECT @AttachmentId AS AttachmentId) AS source
    ON target.[AttachmentId] = source.AttachmentId
    WHEN MATCHED AND target.[BillId] IS NULL AND target.[FinalizedDatetime] IS NULL THEN
        UPDATE SET
            [ModifiedDatetime] = @Now,
            [BillId] = @LinkedBillId,
            [ExtractedDatetime] = @ExtractedDatetime,
            [IntakeSource] = @IntakeSource,
            [ModelVersion] = @ModelVersion,
            [Status] = @Status,
            [PredVendorId] = @PredVendorId,
            [PredVendorExtractedName] = @PredVendorExtractedName,
            [PredBillNumber] = @PredBillNumber,
            [PredBillDate] = @PredBillDate,
            [PredTotalAmount] = @PredTotalAmount,
            [PredDescription] = @PredDescription,
            [PredSubCostCodeId] = @PredSubCostCodeId,
            [PredProjectId] = @PredProjectId,
            [CodingMode] = @CodingMode,
            [CodingChosenProjectId] = @CodingChosenProjectId,
            [HeaderConfidence] = @HeaderConfidence,
            [HeaderModel] = @HeaderModel,
            [CodingConfidence] = @CodingConfidence,
            [CodingModel] = @CodingModel,
            [LatencyMsTotal] = @LatencyMsTotal,
            [TimingJson] = @TimingJson,
            [InputTokens] = @InputTokens,
            [OutputTokens] = @OutputTokens,
            [CostUsd] = @CostUsd
    WHEN NOT MATCHED THEN
        INSERT
            ([CreatedDatetime], [ModifiedDatetime], [CreatedByUserId], [AttachmentId],
             [BillId], [ExtractedDatetime], [IntakeSource], [ModelVersion], [Status],
             [PredVendorId], [PredVendorExtractedName], [PredBillNumber], [PredBillDate],
             [PredTotalAmount], [PredDescription], [PredSubCostCodeId], [PredProjectId],
             [CodingMode], [CodingChosenProjectId], [HeaderConfidence], [HeaderModel],
             [CodingConfidence], [CodingModel], [LatencyMsTotal], [TimingJson],
             [InputTokens], [OutputTokens], [CostUsd])
        VALUES
            (@Now, @Now, COALESCE(@CreatedByUserId, 17), @AttachmentId,
             @LinkedBillId, @ExtractedDatetime, @IntakeSource, @ModelVersion, @Status,
             @PredVendorId, @PredVendorExtractedName, @PredBillNumber, @PredBillDate,
             @PredTotalAmount, @PredDescription, @PredSubCostCodeId, @PredProjectId,
             @CodingMode, @CodingChosenProjectId, @HeaderConfidence, @HeaderModel,
             @CodingConfidence, @CodingModel, @LatencyMsTotal, @TimingJson,
             @InputTokens, @OutputTokens, @CostUsd)
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[CreatedDatetime],
        INSERTED.[ModifiedDatetime],
        INSERTED.[CreatedByUserId],
        INSERTED.[AttachmentId],
        INSERTED.[ExtractedDatetime],
        INSERTED.[BillId],
        INSERTED.[IntakeSource],
        INSERTED.[ModelVersion],
        INSERTED.[Status],
        INSERTED.[PredVendorId],
        INSERTED.[PredVendorExtractedName],
        INSERTED.[PredBillNumber],
        INSERTED.[PredBillDate],
        INSERTED.[PredTotalAmount],
        INSERTED.[PredDescription],
        INSERTED.[PredSubCostCodeId],
        INSERTED.[PredProjectId],
        INSERTED.[CodingMode],
        INSERTED.[CodingChosenProjectId],
        INSERTED.[HeaderConfidence],
        INSERTED.[HeaderModel],
        INSERTED.[CodingConfidence],
        INSERTED.[CodingModel],
        INSERTED.[LatencyMsTotal],
        INSERTED.[TimingJson],
        INSERTED.[InputTokens],
        INSERTED.[OutputTokens],
        INSERTED.[CostUsd],
        INSERTED.[FinalVendorId],
        INSERTED.[FinalBillNumber],
        INSERTED.[FinalBillDate],
        INSERTED.[FinalTotalAmount],
        INSERTED.[FinalSubCostCodeId],
        INSERTED.[FinalProjectId],
        INSERTED.[FinalizedDatetime],
        INSERTED.[RejectReason];

    -- Re-bind after the MERGE (U-676p): a link that committed between the lookup above and
    -- the MERGE would otherwise leave BillId NULL for good (the create path's
    -- BindBillIntakePredictionToBill already ran as a no-op). READCOMMITTEDLOCK so an RCSI
    -- snapshot can't miss it; same TOP (1) ordering as above.
    UPDATE dbo.[BillIntakePrediction]
    SET [BillId] = (
            SELECT TOP (1) li.[BillId]
            FROM dbo.[BillLineItemAttachment] blia WITH (READCOMMITTEDLOCK)
            INNER JOIN dbo.[BillLineItem] li ON li.[Id] = blia.[BillLineItemId]
            WHERE blia.[AttachmentId] = @AttachmentId
            ORDER BY li.[Id] DESC
        ),
        [ModifiedDatetime] = @Now
    WHERE [AttachmentId] = @AttachmentId
      AND [BillId] IS NULL
      AND [FinalizedDatetime] IS NULL
      AND EXISTS (
            SELECT 1
            FROM dbo.[BillLineItemAttachment] blia WITH (READCOMMITTEDLOCK)
            INNER JOIN dbo.[BillLineItem] li ON li.[Id] = blia.[BillLineItemId]
            WHERE blia.[AttachmentId] = @AttachmentId
        );

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE BindBillIntakePredictionToBill
(
    @AttachmentId BIGINT,
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    IF NOT EXISTS (SELECT 1 FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = @BillId)
       OR NOT EXISTS (SELECT 1 FROM dbo.[BillIntakePrediction] WITH (UPDLOCK, HOLDLOCK) WHERE [AttachmentId] = @AttachmentId)
    BEGIN
        COMMIT TRANSACTION;
        SELECT TOP (0)
            CAST(NULL AS BIGINT) AS [Id],
            CAST(NULL AS UNIQUEIDENTIFIER) AS [PublicId],
            CAST(NULL AS VARBINARY(8)) AS [RowVersion],
            CAST(NULL AS BIGINT) AS [AttachmentId],
            CAST(NULL AS BIGINT) AS [BillId];
        RETURN;
    END

    UPDATE dbo.[BillIntakePrediction]
    SET [BillId] = @BillId,
        [ModifiedDatetime] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        INSERTED.[AttachmentId],
        INSERTED.[BillId]
    WHERE [AttachmentId] = @AttachmentId
      AND [BillId] IS NULL;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE RecordBillIntakePredictionFinal
(
    @BillId BIGINT,
    @FinalVendorId BIGINT = NULL,
    @FinalBillNumber NVARCHAR(50) = NULL,
    @FinalBillDate DATE = NULL,
    @FinalTotalAmount DECIMAL(18,2) = NULL,
    @FinalSubCostCodeId BIGINT = NULL,
    @FinalProjectId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    IF NOT EXISTS (SELECT 1 FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = @BillId)
    BEGIN
        COMMIT TRANSACTION;
        SELECT TOP (0)
            CAST(NULL AS BIGINT) AS [Id],
            CAST(NULL AS BIGINT) AS [BillId],
            CAST(NULL AS DATETIME2(3)) AS [FinalizedDatetime];
        RETURN;
    END

    UPDATE dbo.[BillIntakePrediction]
    SET [FinalVendorId] = @FinalVendorId,
        [FinalBillNumber] = @FinalBillNumber,
        [FinalBillDate] = @FinalBillDate,
        [FinalTotalAmount] = @FinalTotalAmount,
        [FinalSubCostCodeId] = @FinalSubCostCodeId,
        [FinalProjectId] = @FinalProjectId,
        [FinalizedDatetime] = @Now,
        [ModifiedDatetime] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[BillId],
        INSERTED.[FinalVendorId],
        INSERTED.[FinalBillNumber],
        INSERTED.[FinalBillDate],
        INSERTED.[FinalTotalAmount],
        INSERTED.[FinalSubCostCodeId],
        INSERTED.[FinalProjectId],
        INSERTED.[FinalizedDatetime]
    WHERE [BillId] = @BillId
      AND [FinalizedDatetime] IS NULL;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadBillIntakePredictionByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (1)
        p.[Id],
        p.[PublicId],
        p.[RowVersion],
        p.[CreatedDatetime],
        p.[ModifiedDatetime],
        p.[CreatedByUserId],
        p.[AttachmentId],
        p.[ExtractedDatetime],
        p.[BillId],
        p.[IntakeSource],
        p.[ModelVersion],
        p.[Status],
        p.[PredVendorId],
        p.[PredVendorExtractedName],
        p.[PredBillNumber],
        p.[PredBillDate],
        p.[PredTotalAmount],
        p.[PredDescription],
        p.[PredSubCostCodeId],
        p.[PredProjectId],
        p.[CodingMode],
        p.[CodingChosenProjectId],
        p.[HeaderConfidence],
        p.[HeaderModel],
        p.[CodingConfidence],
        p.[CodingModel],
        p.[LatencyMsTotal],
        p.[TimingJson],
        p.[InputTokens],
        p.[OutputTokens],
        p.[CostUsd],
        p.[FinalVendorId],
        p.[FinalBillNumber],
        p.[FinalBillDate],
        p.[FinalTotalAmount],
        p.[FinalSubCostCodeId],
        p.[FinalProjectId],
        p.[FinalizedDatetime],
        p.[RejectReason]
    FROM dbo.[BillIntakePrediction] p
    WHERE p.[BillId] = @BillId
    ORDER BY p.[Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadBillIntakePredictionByAttachmentId
(
    @AttachmentId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (1)
        p.[Id],
        p.[PublicId],
        p.[RowVersion],
        p.[CreatedDatetime],
        p.[ModifiedDatetime],
        p.[CreatedByUserId],
        p.[AttachmentId],
        p.[ExtractedDatetime],
        p.[BillId],
        p.[IntakeSource],
        p.[ModelVersion],
        p.[Status],
        p.[PredVendorId],
        p.[PredVendorExtractedName],
        p.[PredBillNumber],
        p.[PredBillDate],
        p.[PredTotalAmount],
        p.[PredDescription],
        p.[PredSubCostCodeId],
        p.[PredProjectId],
        p.[CodingMode],
        p.[CodingChosenProjectId],
        p.[HeaderConfidence],
        p.[HeaderModel],
        p.[CodingConfidence],
        p.[CodingModel],
        p.[LatencyMsTotal],
        p.[TimingJson],
        p.[InputTokens],
        p.[OutputTokens],
        p.[CostUsd],
        p.[FinalVendorId],
        p.[FinalBillNumber],
        p.[FinalBillDate],
        p.[FinalTotalAmount],
        p.[FinalSubCostCodeId],
        p.[FinalProjectId],
        p.[FinalizedDatetime],
        p.[RejectReason]
    FROM dbo.[BillIntakePrediction] p
    WHERE p.[AttachmentId] = @AttachmentId;
END;
GO

CREATE OR ALTER PROCEDURE ReadBillIntakePredictionsForAccuracy
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        p.[Id],
        p.[BillId],
        p.[IntakeSource],
        p.[Status],
        p.[PredVendorId],
        p.[PredBillNumber],
        p.[PredBillDate],
        p.[PredTotalAmount],
        p.[PredSubCostCodeId],
        p.[PredProjectId],
        p.[FinalVendorId],
        p.[FinalBillNumber],
        p.[FinalBillDate],
        p.[FinalTotalAmount],
        p.[FinalSubCostCodeId],
        p.[FinalProjectId],
        p.[CostUsd],
        p.[LatencyMsTotal],
        v.[Name] AS [VendorName]
    FROM dbo.[BillIntakePrediction] p
    LEFT JOIN dbo.[Vendor] v ON v.[Id] = p.[FinalVendorId]
    WHERE p.[FinalizedDatetime] IS NOT NULL;
END;
GO
