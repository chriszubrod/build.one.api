-- Bill text-tier coding suggestion queue (U-654b BITE 1).
-- Depends on dbo.BillDominantVoteForVendor from bill_coding_suggestion.sql (apply that file first).
--
-- Build order: bill_coding_suggestion.sql → bill_text_coding_suggestion.sql
-- Run: ./.venv/bin/python scripts/run_sql.py entities/coding_suggestion/sql/bill_text_coding_suggestion.sql

GO

IF OBJECT_ID('dbo.BillTextCodingSuggestion', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[BillTextCodingSuggestion]
    (
        [Id]                 BIGINT             IDENTITY(1,1) NOT NULL PRIMARY KEY,
        [PublicId]           UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
        [CreatedDatetime]    DATETIME2(3)       NOT NULL,
        [ModifiedDatetime]   DATETIME2(3)       NOT NULL,
        [BillId]             BIGINT             NOT NULL,
        [AttachmentId]       BIGINT             NOT NULL,
        [ExtractedDatetime]  DATETIME2(3)       NOT NULL,
        [Status]             NVARCHAR(20)       NOT NULL,
        [SubCostCodeId]      BIGINT             NULL,
        [ProjectId]          BIGINT             NULL,
        [Confidence]         DECIMAL(5,4)       NULL,
        [Mode]               CHAR(1)            NULL,
        [WinningModel]       NVARCHAR(100)      NULL,
        [ModelVersion]       NVARCHAR(50)       NOT NULL,
        [FailureReason]      NVARCHAR(50)       NULL,
        [CallDatetime]       DATETIME2(3)       NULL,
        CONSTRAINT [FK_BillTextCodingSuggestion_Bill] FOREIGN KEY ([BillId]) REFERENCES dbo.[Bill]([Id]),
        CONSTRAINT [FK_BillTextCodingSuggestion_Attachment] FOREIGN KEY ([AttachmentId]) REFERENCES dbo.[Attachment]([Id]),
        CONSTRAINT [FK_BillTextCodingSuggestion_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES dbo.[SubCostCode]([Id]),
        CONSTRAINT [FK_BillTextCodingSuggestion_Project] FOREIGN KEY ([ProjectId]) REFERENCES dbo.[Project]([Id]),
        CONSTRAINT [CK_BillTextCodingSuggestion_Status] CHECK ([Status] IN ('pending','done','failed','skipped'))
    );
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'UQ_BillTextCodingSuggestion_BillId'
      AND object_id = OBJECT_ID('dbo.BillTextCodingSuggestion')
)
BEGIN
    CREATE UNIQUE INDEX [UQ_BillTextCodingSuggestion_BillId]
        ON dbo.[BillTextCodingSuggestion] ([BillId]);
END
GO

IF COL_LENGTH('dbo.BillTextCodingSuggestion', 'CallDatetime') IS NULL
BEGIN
    ALTER TABLE dbo.[BillTextCodingSuggestion]
        ADD [CallDatetime] DATETIME2(3) NULL;
END
GO

IF COL_LENGTH('dbo.BillTextCodingSuggestion', 'DecisionSource') IS NULL
BEGIN
    ALTER TABLE dbo.[BillTextCodingSuggestion]
        ADD [DecisionSource] NVARCHAR(20) NULL;
END
GO

IF COL_LENGTH('dbo.BillTextCodingSuggestion', 'PrimaryModel') IS NULL
BEGIN
    ALTER TABLE dbo.[BillTextCodingSuggestion]
        ADD [PrimaryModel] NVARCHAR(100) NULL,
            [PrimarySubCostCodeId] BIGINT NULL,
            [PrimaryProjectId] BIGINT NULL,
            [PrimaryConfidence] DECIMAL(5,4) NULL,
            [PrimaryError] NVARCHAR(50) NULL;
END
GO

IF COL_LENGTH('dbo.BillTextCodingSuggestion', 'EscalateModel') IS NULL
BEGIN
    ALTER TABLE dbo.[BillTextCodingSuggestion]
        ADD [EscalateModel] NVARCHAR(100) NULL,
            [EscalateSubCostCodeId] BIGINT NULL,
            [EscalateProjectId] BIGINT NULL,
            [EscalateConfidence] DECIMAL(5,4) NULL,
            [EscalateError] NVARCHAR(50) NULL;
END
GO

IF COL_LENGTH('dbo.BillTextCodingSuggestion', 'FallbackModel') IS NULL
BEGIN
    ALTER TABLE dbo.[BillTextCodingSuggestion]
        ADD [FallbackModel] NVARCHAR(100) NULL,
            [FallbackSubCostCodeId] BIGINT NULL,
            [FallbackConfidence] DECIMAL(5,4) NULL;
END
GO

IF COL_LENGTH('dbo.BillTextCodingSuggestion', 'SimilarVoteSubCostCodeId') IS NULL
BEGIN
    ALTER TABLE dbo.[BillTextCodingSuggestion]
        ADD [SimilarVoteSubCostCodeId] BIGINT NULL,
            [SimilarVoteConfidence] DECIMAL(5,4) NULL,
            [SimilarNeighbourCount] TINYINT NULL,
            [PrimaryFromIntake] BIT NULL,
            [CostUsd] DECIMAL(12,6) NULL;
END
GO

IF OBJECT_ID('dbo.BillTextCodingSuggestion', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.check_constraints
       WHERE name = 'CK_BillTextCodingSuggestion_DecisionSource'
         AND parent_object_id = OBJECT_ID('dbo.BillTextCodingSuggestion')
   )
BEGIN
    ALTER TABLE dbo.[BillTextCodingSuggestion]
        ADD CONSTRAINT [CK_BillTextCodingSuggestion_DecisionSource]
        CHECK ([DecisionSource] IS NULL OR [DecisionSource] IN (
            'history', 'primary', 'escalate', 'fallback', 'similar_vote', 'reused_intake', 'none'
        ));
END
GO

-- Contract: top-N vendor (× optional project) SCC counts from non-draft bill history; holding codes excluded.
CREATE OR ALTER PROCEDURE ReadVendorSccCountsForBills
(
    @VendorId BIGINT,
    @ProjectId BIGINT = NULL,
    @AsOf DATETIME2(3) = NULL,
    @Top INT = 5,
    @ExcludeBillId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH BillVote AS (
        SELECT
            bv.[BillId],
            bv.[SubCostCodeId],
            bv.[DominantProjectId]
        FROM dbo.BillDominantVoteForVendor(@VendorId, @AsOf, @ExcludeBillId) bv
    ),
    FilteredVotes AS (
        SELECT bv.[SubCostCodeId]
        FROM BillVote bv
        INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = bv.[SubCostCodeId]
        WHERE (@ProjectId IS NULL OR bv.[DominantProjectId] = @ProjectId)
          AND ISNULL(scc.[IsHoldingCode], 0) = 0
    ),
    VoteCounts AS (
        SELECT
            fv.[SubCostCodeId],
            COUNT(*) AS [VoteCount]
        FROM FilteredVotes fv
        GROUP BY fv.[SubCostCodeId]
    ),
    Ranked AS (
        SELECT
            vc.[SubCostCodeId],
            vc.[VoteCount],
            ROW_NUMBER() OVER (
                ORDER BY vc.[VoteCount] DESC, vc.[SubCostCodeId] ASC
            ) AS [RankN]
        FROM VoteCounts vc
    )
    SELECT TOP (@Top)
        r.[SubCostCodeId],
        scc.[Number],
        scc.[Name],
        r.[VoteCount]
    FROM Ranked r
    INNER JOIN dbo.[SubCostCode] scc ON scc.[Id] = r.[SubCostCodeId]
    ORDER BY r.[RankN];
END;
GO

CREATE OR ALTER PROCEDURE CountBillTextCodingCallsToday
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @UtcMidnight DATETIME2(3) =
        CAST(CAST(SYSUTCDATETIME() AS DATE) AS DATETIME2(3));

    SELECT COUNT(*) AS [CallCount]
    FROM dbo.[BillTextCodingSuggestion] bt
    WHERE bt.[CallDatetime] IS NOT NULL
      AND bt.[CallDatetime] >= @UtcMidnight;
END;
GO

CREATE OR ALTER PROCEDURE ReadBillTextCodingSuggestionByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        bt.[Id],
        bt.[PublicId],
        bt.[BillId],
        bt.[AttachmentId],
        bt.[ExtractedDatetime],
        bt.[Status],
        bt.[SubCostCodeId],
        bt.[ProjectId],
        bt.[Confidence],
        bt.[Mode],
        bt.[WinningModel],
        bt.[ModelVersion],
        bt.[FailureReason],
        bt.[CreatedDatetime],
        bt.[ModifiedDatetime],
        bt.[DecisionSource],
        bt.[PrimaryModel],
        bt.[PrimarySubCostCodeId],
        bt.[PrimaryProjectId],
        bt.[PrimaryConfidence],
        bt.[PrimaryError],
        bt.[EscalateModel],
        bt.[EscalateSubCostCodeId],
        bt.[EscalateProjectId],
        bt.[EscalateConfidence],
        bt.[EscalateError],
        bt.[FallbackModel],
        bt.[FallbackSubCostCodeId],
        bt.[FallbackConfidence],
        bt.[SimilarVoteSubCostCodeId],
        bt.[SimilarVoteConfidence],
        bt.[SimilarNeighbourCount],
        bt.[PrimaryFromIntake],
        bt.[CostUsd]
    FROM dbo.[BillTextCodingSuggestion] bt
    WHERE bt.[BillId] = @BillId;
END;
GO

CREATE OR ALTER PROCEDURE CompleteBillTextCodingSuggestion
(
    @BillId BIGINT,
    @AttachmentId BIGINT,
    @ExtractedDatetime DATETIME2(3),
    @Status NVARCHAR(20),
    @SubCostCodeId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @Confidence DECIMAL(5,4) = NULL,
    @Mode CHAR(1) = NULL,
    @WinningModel NVARCHAR(100) = NULL,
    @ModelVersion NVARCHAR(50),
    @FailureReason NVARCHAR(50) = NULL,
    @RecordCall BIT = 0,
    @DecisionSource NVARCHAR(20) = NULL,
    @PrimaryModel NVARCHAR(100) = NULL,
    @PrimarySubCostCodeId BIGINT = NULL,
    @PrimaryProjectId BIGINT = NULL,
    @PrimaryConfidence DECIMAL(5,4) = NULL,
    @PrimaryError NVARCHAR(50) = NULL,
    @EscalateModel NVARCHAR(100) = NULL,
    @EscalateSubCostCodeId BIGINT = NULL,
    @EscalateProjectId BIGINT = NULL,
    @EscalateConfidence DECIMAL(5,4) = NULL,
    @EscalateError NVARCHAR(50) = NULL,
    @FallbackModel NVARCHAR(100) = NULL,
    @FallbackSubCostCodeId BIGINT = NULL,
    @FallbackConfidence DECIMAL(5,4) = NULL,
    @SimilarVoteSubCostCodeId BIGINT = NULL,
    @SimilarVoteConfidence DECIMAL(5,4) = NULL,
    @SimilarNeighbourCount TINYINT = NULL,
    @PrimaryFromIntake BIT = NULL,
    @CostUsd DECIMAL(12,6) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @RowId BIGINT;

    SELECT @RowId = bt.[Id]
    FROM dbo.[BillTextCodingSuggestion] bt WITH (UPDLOCK, HOLDLOCK)
    WHERE bt.[BillId] = @BillId
      AND bt.[AttachmentId] = @AttachmentId
      AND bt.[ExtractedDatetime] = @ExtractedDatetime;

    IF @RowId IS NULL
    BEGIN
        COMMIT TRANSACTION;
        SELECT
            CAST(NULL AS BIGINT) AS [Id],
            CAST(NULL AS UNIQUEIDENTIFIER) AS [PublicId],
            CAST(NULL AS BIGINT) AS [BillId],
            CAST(NULL AS BIGINT) AS [AttachmentId],
            CAST(NULL AS DATETIME2(3)) AS [ExtractedDatetime],
            CAST(NULL AS NVARCHAR(20)) AS [Status],
            CAST(NULL AS BIGINT) AS [SubCostCodeId],
            CAST(NULL AS BIGINT) AS [ProjectId],
            CAST(NULL AS DECIMAL(5,4)) AS [Confidence],
            CAST(NULL AS CHAR(1)) AS [Mode],
            CAST(NULL AS NVARCHAR(100)) AS [WinningModel],
            CAST(NULL AS NVARCHAR(50)) AS [ModelVersion],
            CAST(NULL AS NVARCHAR(50)) AS [FailureReason],
            CAST(NULL AS DATETIME2(3)) AS [CreatedDatetime],
            CAST(NULL AS DATETIME2(3)) AS [ModifiedDatetime],
            CAST(NULL AS NVARCHAR(20)) AS [DecisionSource],
            CAST(NULL AS NVARCHAR(100)) AS [PrimaryModel],
            CAST(NULL AS BIGINT) AS [PrimarySubCostCodeId],
            CAST(NULL AS BIGINT) AS [PrimaryProjectId],
            CAST(NULL AS DECIMAL(5,4)) AS [PrimaryConfidence],
            CAST(NULL AS NVARCHAR(50)) AS [PrimaryError],
            CAST(NULL AS NVARCHAR(100)) AS [EscalateModel],
            CAST(NULL AS BIGINT) AS [EscalateSubCostCodeId],
            CAST(NULL AS BIGINT) AS [EscalateProjectId],
            CAST(NULL AS DECIMAL(5,4)) AS [EscalateConfidence],
            CAST(NULL AS NVARCHAR(50)) AS [EscalateError],
            CAST(NULL AS NVARCHAR(100)) AS [FallbackModel],
            CAST(NULL AS BIGINT) AS [FallbackSubCostCodeId],
            CAST(NULL AS DECIMAL(5,4)) AS [FallbackConfidence],
            CAST(NULL AS BIGINT) AS [SimilarVoteSubCostCodeId],
            CAST(NULL AS DECIMAL(5,4)) AS [SimilarVoteConfidence],
            CAST(NULL AS TINYINT) AS [SimilarNeighbourCount],
            CAST(NULL AS BIT) AS [PrimaryFromIntake],
            CAST(NULL AS DECIMAL(12,6)) AS [CostUsd]
        WHERE 1 = 0;
        RETURN;
    END;

    UPDATE dbo.[BillTextCodingSuggestion]
    SET
        [Status] = @Status,
        [SubCostCodeId] = @SubCostCodeId,
        [ProjectId] = @ProjectId,
        [Confidence] = @Confidence,
        [Mode] = @Mode,
        [WinningModel] = @WinningModel,
        [ModelVersion] = @ModelVersion,
        [FailureReason] = @FailureReason,
        [ModifiedDatetime] = @Now,
        [CallDatetime] = CASE WHEN @RecordCall = 1 THEN @Now ELSE [CallDatetime] END,
        [DecisionSource] = @DecisionSource,
        [PrimaryModel] = @PrimaryModel,
        [PrimarySubCostCodeId] = @PrimarySubCostCodeId,
        [PrimaryProjectId] = @PrimaryProjectId,
        [PrimaryConfidence] = @PrimaryConfidence,
        [PrimaryError] = @PrimaryError,
        [EscalateModel] = @EscalateModel,
        [EscalateSubCostCodeId] = @EscalateSubCostCodeId,
        [EscalateProjectId] = @EscalateProjectId,
        [EscalateConfidence] = @EscalateConfidence,
        [EscalateError] = @EscalateError,
        [FallbackModel] = @FallbackModel,
        [FallbackSubCostCodeId] = @FallbackSubCostCodeId,
        [FallbackConfidence] = @FallbackConfidence,
        [SimilarVoteSubCostCodeId] = @SimilarVoteSubCostCodeId,
        [SimilarVoteConfidence] = @SimilarVoteConfidence,
        [SimilarNeighbourCount] = @SimilarNeighbourCount,
        [PrimaryFromIntake] = @PrimaryFromIntake,
        [CostUsd] = @CostUsd
    WHERE [Id] = @RowId;

    COMMIT TRANSACTION;

    EXEC dbo.ReadBillTextCodingSuggestionByBillId @BillId = @BillId;
END;
GO

CREATE OR ALTER PROCEDURE ClaimNextBillForTextCodingSuggestion
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Candidates TABLE (
        [Seq] INT NOT NULL PRIMARY KEY,
        [BillId] BIGINT NOT NULL,
        [AttachmentId] BIGINT NOT NULL,
        [ExtractedDatetime] DATETIME2(3) NOT NULL,
        [ExtractedTextBlobUrl] NVARCHAR(2048) NOT NULL,
        [VendorId] BIGINT NOT NULL,
        [ChosenProjectId] BIGINT NULL,
        [BillDate] DATETIME2(3) NULL,
        [CreatedDatetime] DATETIME2(3) NOT NULL
    );

    INSERT INTO @Candidates (
        [Seq], [BillId], [AttachmentId], [ExtractedDatetime], [ExtractedTextBlobUrl],
        [VendorId], [ChosenProjectId], [BillDate], [CreatedDatetime]
    )
    SELECT
        ROW_NUMBER() OVER (ORDER BY b.[CreatedDatetime] ASC, b.[Id] ASC),
        b.[Id],
        att.[AttachmentId],
        att.[ExtractedDatetime],
        att.[ExtractedTextBlobUrl],
        b.[VendorId],
        proj.[ChosenProjectId],
        b.[BillDate],
        b.[CreatedDatetime]
    FROM dbo.[Bill] b
    CROSS APPLY (
        SELECT TOP (1)
            a.[Id] AS [AttachmentId],
            a.[ExtractedDatetime],
            a.[ExtractedTextBlobUrl]
        FROM dbo.[BillLineItem] bli
        INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[BillLineItemId] = bli.[Id]
        INNER JOIN dbo.[Attachment] a ON a.[Id] = blia.[AttachmentId]
        WHERE bli.[BillId] = b.[Id]
          AND a.[ExtractionStatus] = 'completed'
          AND a.[ExtractedTextBlobUrl] IS NOT NULL
          AND LTRIM(RTRIM(a.[ExtractedTextBlobUrl])) <> ''
        ORDER BY a.[ExtractedDatetime] DESC, a.[Id] DESC
    ) att
    OUTER APPLY (
        SELECT CASE
            WHEN COUNT(DISTINCT bli.[ProjectId]) = 1 THEN MIN(bli.[ProjectId])
            ELSE NULL
        END AS [ChosenProjectId]
        FROM dbo.[BillLineItem] bli
        WHERE bli.[BillId] = b.[Id]
          AND bli.[ProjectId] IS NOT NULL
    ) proj
    LEFT JOIN dbo.[BillTextCodingSuggestion] bt ON bt.[BillId] = b.[Id]
    WHERE b.[Status] IN ('draft', 'submitted', 'in_review', 'declined')
      AND b.[VendorId] IS NOT NULL
      AND (
            bt.[Id] IS NULL
            OR att.[ExtractedDatetime] > bt.[ExtractedDatetime]
            OR (
                bt.[Status] = 'pending'
                AND bt.[ModifiedDatetime] < DATEADD(MINUTE, -30, SYSUTCDATETIME())
            )
          );

    DECLARE @Seq INT = 0;
    DECLARE @BillId BIGINT;
    DECLARE @AttachmentId BIGINT;
    DECLARE @ExtractedDatetime DATETIME2(3);
    DECLARE @BlobUrl NVARCHAR(2048);
    DECLARE @VendorId BIGINT;
    DECLARE @ChosenProjectId BIGINT;
    DECLARE @BillDate DATETIME2(3);
    DECLARE @CreatedDatetime DATETIME2(3);
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @ModelVersion NVARCHAR(50) = N'u654b-text-v1';

    WHILE 1 = 1
    BEGIN
        SET @BillId = NULL;

        SELECT TOP (1)
            @Seq = c.[Seq],
            @BillId = c.[BillId],
            @AttachmentId = c.[AttachmentId],
            @ExtractedDatetime = c.[ExtractedDatetime],
            @BlobUrl = c.[ExtractedTextBlobUrl],
            @VendorId = c.[VendorId],
            @ChosenProjectId = c.[ChosenProjectId],
            @BillDate = c.[BillDate],
            @CreatedDatetime = c.[CreatedDatetime]
        FROM @Candidates c
        WHERE c.[Seq] > @Seq
        ORDER BY c.[Seq];

        IF @BillId IS NULL
            BREAK;

        BEGIN TRANSACTION;
        IF EXISTS (
            SELECT 1
            FROM dbo.[Bill] b WITH (UPDLOCK, READPAST, READCOMMITTEDLOCK)
            WHERE b.[Id] = @BillId
              AND b.[Status] IN ('draft', 'submitted', 'in_review', 'declined')
        )
        AND NOT EXISTS (
            SELECT 1
            FROM dbo.[BillTextCodingSuggestion] bt WITH (UPDLOCK, HOLDLOCK)
            WHERE bt.[BillId] = @BillId
              AND bt.[ExtractedDatetime] >= @ExtractedDatetime
              AND NOT (
                  bt.[Status] = 'pending'
                  AND bt.[ModifiedDatetime] < DATEADD(MINUTE, -30, SYSUTCDATETIME())
              )
        )
        BEGIN
            MERGE dbo.[BillTextCodingSuggestion] WITH (HOLDLOCK) AS target
            USING (SELECT @BillId AS [BillId]) AS source
            ON target.[BillId] = source.[BillId]
            WHEN MATCHED THEN
                UPDATE SET
                    [AttachmentId] = @AttachmentId,
                    [ExtractedDatetime] = @ExtractedDatetime,
                    [Status] = 'pending',
                    [SubCostCodeId] = NULL,
                    [ProjectId] = NULL,
                    [Confidence] = NULL,
                    [Mode] = NULL,
                    [WinningModel] = NULL,
                    [FailureReason] = NULL,
                    [DecisionSource] = NULL,
                    [PrimaryModel] = NULL,
                    [PrimarySubCostCodeId] = NULL,
                    [PrimaryProjectId] = NULL,
                    [PrimaryConfidence] = NULL,
                    [PrimaryError] = NULL,
                    [EscalateModel] = NULL,
                    [EscalateSubCostCodeId] = NULL,
                    [EscalateProjectId] = NULL,
                    [EscalateConfidence] = NULL,
                    [EscalateError] = NULL,
                    [FallbackModel] = NULL,
                    [FallbackSubCostCodeId] = NULL,
                    [FallbackConfidence] = NULL,
                    [SimilarVoteSubCostCodeId] = NULL,
                    [SimilarVoteConfidence] = NULL,
                    [SimilarNeighbourCount] = NULL,
                    [PrimaryFromIntake] = NULL,
                    [CostUsd] = NULL,
                    [ModifiedDatetime] = @Now
            WHEN NOT MATCHED THEN
                INSERT (
                    [CreatedDatetime], [ModifiedDatetime], [BillId], [AttachmentId],
                    [ExtractedDatetime], [Status], [ModelVersion]
                )
                VALUES (
                    @Now, @Now, @BillId, @AttachmentId,
                    @ExtractedDatetime, 'pending', @ModelVersion
                );
            COMMIT TRANSACTION;

            SELECT
                @BillId AS [BillId],
                @AttachmentId AS [AttachmentId],
                @BlobUrl AS [ExtractedTextBlobUrl],
                @ExtractedDatetime AS [ExtractedDatetime],
                @VendorId AS [VendorId],
                @ChosenProjectId AS [ChosenProjectId],
                @BillDate AS [BillDate],
                @CreatedDatetime AS [CreatedDatetime];
            RETURN;
        END
        COMMIT TRANSACTION;
    END;

    SELECT
        CAST(NULL AS BIGINT) AS [BillId],
        CAST(NULL AS BIGINT) AS [AttachmentId],
        CAST(NULL AS NVARCHAR(2048)) AS [ExtractedTextBlobUrl],
        CAST(NULL AS DATETIME2(3)) AS [ExtractedDatetime],
        CAST(NULL AS BIGINT) AS [VendorId],
        CAST(NULL AS BIGINT) AS [ChosenProjectId],
        CAST(NULL AS DATETIME2(3)) AS [BillDate],
        CAST(NULL AS DATETIME2(3)) AS [CreatedDatetime]
    WHERE 1 = 0;
END;
GO

-- Contract: the bill's creator (similar-bills scope for the V2 tick, U-701b3), company and total; no row when the bill is gone.
CREATE OR ALTER PROCEDURE ReadBillCodingActorByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        b.[CreatedByUserId] AS [CreatedByUserId],
        ISNULL(u.[IsSystemAdmin], 0) AS [IsSystemAdmin],
        b.[CompanyId] AS [CompanyId],
        b.[TotalAmount] AS [TotalAmount]
    FROM dbo.[Bill] b
    LEFT JOIN dbo.[User] u ON u.[Id] = b.[CreatedByUserId]
    WHERE b.[Id] = @BillId;
END;
GO

CREATE OR ALTER PROCEDURE ReadBillLatestTextExtractionForCoding
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @LatestCompleted DATETIME2(3) = NULL;
    DECLARE @LatestAttachmentId BIGINT = NULL;
    DECLARE @HasIncomplete BIT = 0;

    SELECT TOP (1)
        @LatestAttachmentId = a.[Id],
        @LatestCompleted = a.[ExtractedDatetime]
    FROM dbo.[BillLineItem] bli
    INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[BillLineItemId] = bli.[Id]
    INNER JOIN dbo.[Attachment] a ON a.[Id] = blia.[AttachmentId]
    WHERE bli.[BillId] = @BillId
      AND a.[ExtractionStatus] = 'completed'
      AND a.[ExtractedTextBlobUrl] IS NOT NULL
      AND LTRIM(RTRIM(a.[ExtractedTextBlobUrl])) <> ''
    ORDER BY a.[ExtractedDatetime] DESC, a.[Id] DESC;

    IF EXISTS (
        SELECT 1
        FROM dbo.[BillLineItem] bli
        INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[BillLineItemId] = bli.[Id]
        INNER JOIN dbo.[Attachment] a ON a.[Id] = blia.[AttachmentId]
        WHERE bli.[BillId] = @BillId
          AND a.[ExtractionStatus] IN ('pending', 'processing')
    )
        SET @HasIncomplete = 1;

    SELECT
        @LatestAttachmentId AS [LatestCompletedAttachmentId],
        @LatestCompleted AS [LatestCompletedExtractedDatetime],
        @HasIncomplete AS [HasIncompleteExtraction];
END;
GO
