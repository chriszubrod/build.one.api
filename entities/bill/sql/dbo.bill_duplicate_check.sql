-- Contract: dbo.BillDuplicateOverride + possible-duplicate read/write (U-723).
-- Owns: audit rows for confirmed/recorded duplicate pairs; ReadPossibleDuplicateBills (Rules A/B/C).
-- Invariants: BillDuplicateOverride is audit-only (no FK to Bill — bills with overrides remain deletable).
-- Sprocs: CreateBillDuplicateOverride, ReadBillDuplicateOverridesByBillId, ReadPossibleDuplicateBills
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/bill/sql/dbo.bill_duplicate_check.sql

GO

IF OBJECT_ID('dbo.BillDuplicateOverride', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[BillDuplicateOverride]
    (
        [Id]               BIGINT           IDENTITY(1,1) NOT NULL,
        [PublicId]         UNIQUEIDENTIFIER NOT NULL CONSTRAINT [DF_BillDuplicateOverride_PublicId] DEFAULT (NEWID()),
        [BillId]           BIGINT           NOT NULL,
        [MatchedBillId]    BIGINT           NULL,
        [MatchReason]      NVARCHAR(32)     NOT NULL,
        [Stage]            NVARCHAR(16)     NOT NULL,
        [Disposition]      NVARCHAR(32)     NOT NULL,
        [ActorUserId]      BIGINT           NOT NULL,
        [CreatedDatetime]  DATETIME2(3)     NOT NULL CONSTRAINT [DF_BillDuplicateOverride_CreatedDatetime] DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT [PK_BillDuplicateOverride] PRIMARY KEY ([Id])
    );
END
ELSE
BEGIN
    IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_BillDuplicateOverride_Bill')
        ALTER TABLE dbo.[BillDuplicateOverride] DROP CONSTRAINT [FK_BillDuplicateOverride_Bill];
    IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_BillDuplicateOverride_MatchedBill')
        ALTER TABLE dbo.[BillDuplicateOverride] DROP CONSTRAINT [FK_BillDuplicateOverride_MatchedBill];
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_BillDuplicateOverride_BillId_MatchedBillId'
      AND object_id = OBJECT_ID('dbo.BillDuplicateOverride')
)
BEGIN
    CREATE INDEX [IX_BillDuplicateOverride_BillId_MatchedBillId]
        ON dbo.[BillDuplicateOverride] ([BillId], [MatchedBillId]);
END
GO

CREATE OR ALTER FUNCTION dbo.NormalizeBillNumberForRuleC
(
    @BillNumber NVARCHAR(50)
)
RETURNS NVARCHAR(200)
WITH SCHEMABINDING
AS
BEGIN
    IF @BillNumber IS NULL
        RETURN NULL;

    DECLARE @n NVARCHAR(200) = @BillNumber;
    DECLARE @i INT = 8194;

    SET @n = REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(@n, CHAR(9), N' '), CHAR(10), N' '), CHAR(13), N' '), CHAR(11), N' '), CHAR(12), N' ');
    SET @n = REPLACE(@n, NCHAR(160), N' ');
    SET @n = REPLACE(@n, NCHAR(12288), N' ');
    WHILE @i <= 8203
    BEGIN
        SET @n = REPLACE(@n, NCHAR(@i), N' ');
        SET @i = @i + 1;
    END;

    SET @n = LOWER(LTRIM(RTRIM(@n)));
    WHILE CHARINDEX(N'  ', @n) > 0 SET @n = REPLACE(@n, N'  ', N' ');

    IF LEN(@n) = 0
        RETURN NULL;
    IF @n LIKE N'qbo-[0-9]%'
        RETURN NULL;

    RETURN @n;
END;
GO

CREATE OR ALTER PROCEDURE CreateBillDuplicateOverride
(
    @BillId BIGINT,
    @MatchedBillId BIGINT = NULL,
    @MatchReason NVARCHAR(32),
    @Stage NVARCHAR(16),
    @Disposition NVARCHAR(32),
    @ActorUserId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM dbo.[Bill] WHERE [Id] = @BillId)
    BEGIN
        SELECT TOP (0)
            CAST(NULL AS BIGINT) AS [Id],
            CAST(NULL AS UNIQUEIDENTIFIER) AS [PublicId];
        RETURN;
    END

    IF EXISTS (
        SELECT 1
        FROM dbo.[BillDuplicateOverride]
        WHERE [BillId] = @BillId
          AND (
                ([MatchedBillId] IS NULL AND @MatchedBillId IS NULL)
                OR [MatchedBillId] = @MatchedBillId
              )
          AND [MatchReason] = @MatchReason
          AND [Stage] = @Stage
          AND [Disposition] = @Disposition
    )
    BEGIN
        SELECT TOP (1)
            [Id],
            [PublicId],
            [BillId],
            [MatchedBillId],
            [MatchReason],
            [Stage],
            [Disposition],
            [ActorUserId],
            [CreatedDatetime]
        FROM dbo.[BillDuplicateOverride]
        WHERE [BillId] = @BillId
          AND (
                ([MatchedBillId] IS NULL AND @MatchedBillId IS NULL)
                OR [MatchedBillId] = @MatchedBillId
              )
          AND [MatchReason] = @MatchReason
          AND [Stage] = @Stage
          AND [Disposition] = @Disposition
        ORDER BY [Id] DESC;
        RETURN;
    END

    INSERT INTO dbo.[BillDuplicateOverride]
        ([BillId], [MatchedBillId], [MatchReason], [Stage], [Disposition], [ActorUserId])
    VALUES
        (@BillId, @MatchedBillId, @MatchReason, @Stage, @Disposition, @ActorUserId);

    SELECT
        [Id],
        [PublicId],
        [BillId],
        [MatchedBillId],
        [MatchReason],
        [Stage],
        [Disposition],
        [ActorUserId],
        [CreatedDatetime]
    FROM dbo.[BillDuplicateOverride]
    WHERE [Id] = SCOPE_IDENTITY();
END;
GO

CREATE OR ALTER PROCEDURE ReadBillDuplicateOverridesByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [BillId],
        [MatchedBillId],
        [MatchReason],
        [Stage],
        [Disposition],
        [ActorUserId],
        [CreatedDatetime]
    FROM dbo.[BillDuplicateOverride]
    WHERE [BillId] = @BillId;
END;
GO

CREATE OR ALTER PROCEDURE ReadPossibleDuplicateBills
(
    @VendorId BIGINT = NULL,
    @BillDate DATE = NULL,
    @TotalAmount DECIMAL(18, 2) = NULL,
    @BillNumber NVARCHAR(50) = NULL,
    @TextFingerprints NVARCHAR(MAX) = NULL,
    @FingerprintVersion NVARCHAR(16) = NULL,
    @ExcludeBillId BIGINT = NULL,
    @ProjectIds NVARCHAR(MAX) = NULL,
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @IncomingNormNumber NVARCHAR(200) = NULL;
    DECLARE @IncomingHasProjects BIT = 0;
    DECLARE @ExcludeIsContractLabor BIT = 0;
    DECLARE @BillDateEnd DATE = NULL;

    SET @IncomingNormNumber = dbo.NormalizeBillNumberForRuleC(@BillNumber);

    IF @BillDate IS NOT NULL
        SET @BillDateEnd = DATEADD(DAY, 1, @BillDate);

    IF @ExcludeBillId IS NOT NULL
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM dbo.[BillLineItem] bli
            INNER JOIN dbo.[ContractLaborLineItem] clli ON clli.[BillLineItemId] = bli.[Id]
            WHERE bli.[BillId] = @ExcludeBillId
        )
            SET @ExcludeIsContractLabor = 1;
        IF EXISTS (
            SELECT 1
            FROM dbo.[BillLineItem] bli
            INNER JOIN dbo.[ContractLabor] cl ON cl.[BillLineItemId] = bli.[Id]
            WHERE bli.[BillId] = @ExcludeBillId
        )
            SET @ExcludeIsContractLabor = 1;
    END

    CREATE TABLE #IncomingProjects (ProjectId BIGINT NOT NULL PRIMARY KEY);
    IF @ProjectIds IS NOT NULL AND LTRIM(RTRIM(@ProjectIds)) <> N''
    BEGIN
        INSERT INTO #IncomingProjects (ProjectId)
        SELECT DISTINCT TRY_CAST(LTRIM(RTRIM(value)) AS BIGINT)
        FROM STRING_SPLIT(@ProjectIds, N',')
        WHERE TRY_CAST(LTRIM(RTRIM(value)) AS BIGINT) IS NOT NULL;
    END
    IF EXISTS (SELECT 1 FROM #IncomingProjects)
        SET @IncomingHasProjects = 1;

    CREATE TABLE #Fingerprints (Fingerprint CHAR(64) NOT NULL PRIMARY KEY);
    IF @TextFingerprints IS NOT NULL AND LTRIM(RTRIM(@TextFingerprints)) <> N''
    BEGIN
        INSERT INTO #Fingerprints (Fingerprint)
        SELECT DISTINCT LTRIM(RTRIM(value))
        FROM STRING_SPLIT(@TextFingerprints, N',')
        WHERE LEN(LTRIM(RTRIM(value))) = 64;
    END

    CREATE TABLE #Matches (
        BillId BIGINT NOT NULL,
        Reason NVARCHAR(32) NOT NULL,
        MatchedAmount DECIMAL(18, 2) NULL,
        PRIMARY KEY (BillId, Reason)
    );

    IF @TotalAmount IS NOT NULL AND @TotalAmount <> 0 AND @BillDate IS NOT NULL
    BEGIN
        ;WITH DateFiltered AS (
            SELECT
                b.[Id] AS BillId,
                b.[VendorId],
                b.[BillDate],
                b.[BillNumber],
                b.[TotalAmount] AS HeaderAmount,
                (
                    SELECT SUM(bli.[Amount])
                    FROM dbo.[BillLineItem] bli
                    WHERE bli.[BillId] = b.[Id]
                ) AS LineSum
            FROM dbo.[Bill] b
            WHERE (@ExcludeBillId IS NULL OR b.[Id] <> @ExcludeBillId)
              AND b.[BillDate] >= @BillDate
              AND b.[BillDate] < @BillDateEnd
        ),
        CandidateBills AS (
            SELECT
                df.*,
                CASE
                    WHEN df.HeaderAmount = @TotalAmount THEN df.HeaderAmount
                    WHEN df.LineSum = @TotalAmount THEN df.LineSum
                    ELSE NULL
                END AS MatchedAmount
            FROM DateFiltered df
            WHERE (df.HeaderAmount = @TotalAmount OR df.LineSum = @TotalAmount)
        ),
        CandidateProjects AS (
            SELECT bli.[BillId], bli.[ProjectId]
            FROM dbo.[BillLineItem] bli
            WHERE bli.[ProjectId] IS NOT NULL
        ),
        CandidateProjectAgg AS (
            SELECT BillId, COUNT(DISTINCT ProjectId) AS ProjectCount
            FROM CandidateProjects
            GROUP BY BillId
        ),
        ContractLaborBills AS (
            SELECT DISTINCT bli.[BillId]
            FROM dbo.[BillLineItem] bli
            INNER JOIN dbo.[ContractLaborLineItem] clli ON clli.[BillLineItemId] = bli.[Id]
            UNION
            SELECT DISTINCT bli.[BillId]
            FROM dbo.[BillLineItem] bli
            INNER JOIN dbo.[ContractLabor] cl ON cl.[BillLineItemId] = bli.[Id]
        )
        INSERT INTO #Matches (BillId, Reason, MatchedAmount)
        SELECT cb.BillId, N'vendor_date_amount', cb.MatchedAmount
        FROM CandidateBills cb
        LEFT JOIN CandidateProjectAgg cpa ON cpa.BillId = cb.BillId
        WHERE @VendorId IS NOT NULL
          AND cb.[VendorId] = @VendorId
          AND @ExcludeIsContractLabor = 0
          AND NOT EXISTS (SELECT 1 FROM ContractLaborBills cl WHERE cl.BillId = cb.BillId)
          AND (
                @IncomingHasProjects = 0
                OR ISNULL(cpa.ProjectCount, 0) = 0
                OR EXISTS (
                    SELECT 1
                    FROM CandidateProjects cp
                    INNER JOIN #IncomingProjects ip ON ip.ProjectId = cp.ProjectId
                    WHERE cp.BillId = cb.BillId
                )
          );

        ;WITH DateFilteredC AS (
            SELECT
                b.[Id] AS BillId,
                b.[BillDate],
                b.[BillNumber],
                b.[TotalAmount] AS HeaderAmount,
                (
                    SELECT SUM(bli.[Amount])
                    FROM dbo.[BillLineItem] bli
                    WHERE bli.[BillId] = b.[Id]
                ) AS LineSum
            FROM dbo.[Bill] b
            WHERE (@ExcludeBillId IS NULL OR b.[Id] <> @ExcludeBillId)
              AND b.[BillDate] >= @BillDate
              AND b.[BillDate] < @BillDateEnd
        ),
        CandidateBillsC AS (
            SELECT
                df.*,
                CASE
                    WHEN df.HeaderAmount = @TotalAmount THEN df.HeaderAmount
                    WHEN df.LineSum = @TotalAmount THEN df.LineSum
                    ELSE NULL
                END AS MatchedAmount
            FROM DateFilteredC df
            WHERE (df.HeaderAmount = @TotalAmount OR df.LineSum = @TotalAmount)
        ),
        ContractLaborBills AS (
            SELECT DISTINCT bli.[BillId]
            FROM dbo.[BillLineItem] bli
            INNER JOIN dbo.[ContractLaborLineItem] clli ON clli.[BillLineItemId] = bli.[Id]
            UNION
            SELECT DISTINCT bli.[BillId]
            FROM dbo.[BillLineItem] bli
            INNER JOIN dbo.[ContractLabor] cl ON cl.[BillLineItemId] = bli.[Id]
        )
        INSERT INTO #Matches (BillId, Reason, MatchedAmount)
        SELECT cb.BillId, N'same_number_amount_date', cb.MatchedAmount
        FROM CandidateBillsC cb
        WHERE @IncomingNormNumber IS NOT NULL
          AND @ExcludeIsContractLabor = 0
          AND NOT EXISTS (SELECT 1 FROM ContractLaborBills cl WHERE cl.BillId = cb.BillId)
          AND dbo.NormalizeBillNumberForRuleC(cb.[BillNumber]) = @IncomingNormNumber
          AND NOT EXISTS (
                SELECT 1 FROM #Matches m
                WHERE m.BillId = cb.BillId AND m.Reason = N'same_number_amount_date'
          );
    END

    IF EXISTS (SELECT 1 FROM #Fingerprints)
    BEGIN
        INSERT INTO #Matches (BillId, Reason, MatchedAmount)
        SELECT DISTINCT b.[Id], N'same_document_text', NULL
        FROM dbo.[Bill] b
        INNER JOIN dbo.[BillLineItem] bli ON bli.[BillId] = b.[Id]
        INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[BillLineItemId] = bli.[Id]
        INNER JOIN dbo.[AttachmentTextFingerprint] atf ON atf.[AttachmentId] = blia.[AttachmentId]
        INNER JOIN #Fingerprints fp ON fp.Fingerprint = atf.[Fingerprint]
        WHERE (@ExcludeBillId IS NULL OR b.[Id] <> @ExcludeBillId)
          AND (@FingerprintVersion IS NULL OR atf.[Version] = @FingerprintVersion)
          AND NOT EXISTS (
                SELECT 1 FROM #Matches m
                WHERE m.BillId = b.[Id] AND m.Reason = N'same_document_text'
          );
    END

    IF @ExcludeBillId IS NOT NULL
    BEGIN
        DELETE m
        FROM #Matches m
        WHERE EXISTS (
            SELECT 1
            FROM dbo.[BillDuplicateOverride] o
            WHERE o.[Disposition] = N'confirmed_not_duplicate'
              AND (
                    (o.[BillId] = @ExcludeBillId AND o.[MatchedBillId] = m.BillId)
                    OR (o.[BillId] = m.BillId AND o.[MatchedBillId] = @ExcludeBillId)
                  )
        );
    END

    ;WITH Grouped AS (
        SELECT
            m.BillId,
            STRING_AGG(m.Reason, N',') WITHIN GROUP (ORDER BY m.Reason) AS ReasonsCsv,
            MAX(m.MatchedAmount) AS MatchedAmount
        FROM #Matches m
        GROUP BY m.BillId
    )
    SELECT
        b.[Id] AS [BillId],
        b.[PublicId],
        b.[BillNumber],
        CONVERT(VARCHAR(10), CAST(b.[BillDate] AS DATE), 23) AS [BillDate],
        COALESCE(g.MatchedAmount, b.[TotalAmount]) AS [TotalAmount],
        b.[Status],
        v.[Name] AS [VendorName],
        (
            SELECT STRING_AGG(p.[Name], N',') WITHIN GROUP (ORDER BY p.[Name])
            FROM (
                SELECT DISTINCT pr.[Name]
                FROM dbo.[BillLineItem] bli
                INNER JOIN dbo.[Project] pr ON pr.[Id] = bli.[ProjectId]
                WHERE bli.[BillId] = b.[Id] AND bli.[ProjectId] IS NOT NULL
            ) p
        ) AS [ProjectNamesCsv],
        b.[CreatedDatetime],
        g.ReasonsCsv,
        dbo.UserCanAccessBill(@ActorUserId, @ActorIsSystemAdmin, b.[Id]) AS [CallerCanAccess]
    FROM Grouped g
    INNER JOIN dbo.[Bill] b ON b.[Id] = g.BillId
    LEFT JOIN dbo.[Vendor] v ON v.[Id] = b.[VendorId]
    ORDER BY b.[CreatedDatetime] DESC;

    DROP TABLE #Matches;
    DROP TABLE #Fingerprints;
    DROP TABLE #IncomingProjects;
END;
GO
