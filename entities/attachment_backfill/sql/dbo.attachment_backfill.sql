-- Contract: dbo.AttachmentBackfillRun, dbo.AttachmentBackfillCandidate
-- Owns: U-535b staging for the Bill -> PDF link backfill: one Run per generation pass, one Candidate per proposed (Box file, Bill) pair.
-- Invariants: unique (RunId, SourceFileId) and (RunId, BillId); InsertAttachmentBackfillCandidate is idempotent on (RunId, SourceFileId)
--   and refuses (RefusedReason) when the run is not 'generating', another file claims the bill in this run, the same (SourceFileId,
--   BillId) pair was rejected in any run, or the bill has a pending/approved candidate in another non-cancelled run (an 'applied' run
--   counts: a pending candidate left in an applied run blocks that bill until b4 resolves it); run status moves generating -> ready ->
--   applied or generating/ready -> cancelled, never out of applied/cancelled; UpdateAttachmentBackfillCandidateVerification writes only a
--   pending, unlinked candidate of a 'ready' run and never touches ReviewStatus; DeleteStaleAttachmentBackfillCandidates deletes only
--   a 'generating' run's pending, unchecked, unlinked candidates absent from the new plan (resume).
--   The per-PDF check stores no conclusion: CheckStatus (checked / unreadable / error) plus neutral evidence counts/flags
--   (InvoiceSeenCount, AmountSeenCount, VendorSeen, OtherInvoiceLabelCount, StatementLike, UsedOcr).
--   BillId has NO foreign key: deleting a bill leaves a stale candidate, which b2/b4 treat as stale. b1 writes ONLY these two tables:
--   no Attachment, BillLineItemAttachment, blob or outbox row. Approve/reject (ReviewStatus) is b2; AttachmentId/LinkedDatetime are b4.
--   No PDF text, memo or line description is stored.
-- Depends (read-only): dbo.Bill, dbo.Vendor, dbo.BillLineItem, dbo.BillLineItemAttachment.
-- Sprocs: CreateAttachmentBackfillRun, DeleteStaleAttachmentBackfillCandidates, InsertAttachmentBackfillCandidate, ReadAttachmentBackfillCandidatesForVerification, ReadAttachmentBackfillRunByPublicId, ReadAttachmentBackfillRunSummary, ReadBillsForAttachmentBackfill, ReadOpenAttachmentBackfillRuns, UpdateAttachmentBackfillCandidateVerification, UpdateAttachmentBackfillRunStatus
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/attachment_backfill/sql/dbo.attachment_backfill.sql

GO

IF OBJECT_ID('dbo.AttachmentBackfillRun', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[AttachmentBackfillRun]
    (
        [Id]               BIGINT            IDENTITY(1,1) NOT NULL,
        [PublicId]         UNIQUEIDENTIFIER  NOT NULL CONSTRAINT [DF_AttachmentBackfillRun_PublicId] DEFAULT NEWID(),
        [RowVersion]       ROWVERSION        NOT NULL,
        [Source]           NVARCHAR(16)      NOT NULL,
        [Status]           NVARCHAR(16)      NOT NULL CONSTRAINT [DF_AttachmentBackfillRun_Status] DEFAULT N'generating',
        [Notes]            NVARCHAR(400)     NULL,
        [CreatedByUserId]  BIGINT            NOT NULL,
        [CreatedDatetime]  DATETIME2(3)      NOT NULL CONSTRAINT [DF_AttachmentBackfillRun_CreatedDatetime] DEFAULT SYSUTCDATETIME(),
        [ModifiedDatetime] DATETIME2(3)      NOT NULL CONSTRAINT [DF_AttachmentBackfillRun_ModifiedDatetime] DEFAULT SYSUTCDATETIME(),
        CONSTRAINT [PK_AttachmentBackfillRun] PRIMARY KEY CLUSTERED ([Id]),
        CONSTRAINT [UQ_AttachmentBackfillRun_PublicId] UNIQUE ([PublicId]),
        CONSTRAINT [CK_AttachmentBackfillRun_Source] CHECK ([Source] IN (N'box', N'qbo')),
        CONSTRAINT [CK_AttachmentBackfillRun_Status] CHECK ([Status] IN (N'generating', N'ready', N'applied', N'cancelled'))
    );
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[AttachmentBackfillCandidate]
    (
        [Id]                     BIGINT            IDENTITY(1,1) NOT NULL,
        [PublicId]               UNIQUEIDENTIFIER  NOT NULL CONSTRAINT [DF_AttachmentBackfillCandidate_PublicId] DEFAULT NEWID(),
        [RowVersion]             ROWVERSION        NOT NULL,
        [RunId]                  BIGINT            NOT NULL,
        [BillId]                 BIGINT            NOT NULL,
        [SourceFileId]           NVARCHAR(64)      NOT NULL,
        [SourceFileName]         NVARCHAR(400)     NOT NULL,
        [SourceFolderId]         NVARCHAR(64)      NULL,
        [SourceFileSize]         BIGINT            NULL,
        [SourceSha256]           CHAR(64)          NULL,
        [MatchTier]              NVARCHAR(32)      NOT NULL,
        [CheckStatus]            NVARCHAR(16)      NULL,
        [InvoiceSeenCount]       INT               NULL,
        [AmountSeenCount]        INT               NULL,
        [VendorSeen]             BIT               NULL,
        [OtherInvoiceLabelCount] INT               NULL,
        [StatementLike]          BIT               NULL,
        [UsedOcr]                BIT               NULL,
        [CheckedDatetime]        DATETIME2(3)      NULL,
        [ReviewStatus]           NVARCHAR(16)      NOT NULL CONSTRAINT [DF_AttachmentBackfillCandidate_ReviewStatus] DEFAULT N'pending',
        [ReviewedByUserId]       BIGINT            NULL,
        [ReviewedDatetime]       DATETIME2(3)      NULL,
        [RejectReason]           NVARCHAR(200)     NULL,
        [AttachmentId]           BIGINT            NULL,
        [LinkedDatetime]         DATETIME2(3)      NULL,
        [CreatedDatetime]        DATETIME2(3)      NOT NULL CONSTRAINT [DF_AttachmentBackfillCandidate_CreatedDatetime] DEFAULT SYSUTCDATETIME(),
        [ModifiedDatetime]       DATETIME2(3)      NOT NULL CONSTRAINT [DF_AttachmentBackfillCandidate_ModifiedDatetime] DEFAULT SYSUTCDATETIME(),
        CONSTRAINT [PK_AttachmentBackfillCandidate] PRIMARY KEY CLUSTERED ([Id]),
        CONSTRAINT [UQ_AttachmentBackfillCandidate_PublicId] UNIQUE ([PublicId]),
        CONSTRAINT [UQ_AttachmentBackfillCandidate_Run_SourceFile] UNIQUE ([RunId], [SourceFileId]),
        CONSTRAINT [UQ_AttachmentBackfillCandidate_Run_Bill] UNIQUE ([RunId], [BillId]),
        CONSTRAINT [FK_AttachmentBackfillCandidate_Run] FOREIGN KEY ([RunId]) REFERENCES dbo.[AttachmentBackfillRun]([Id]),
        CONSTRAINT [CK_AttachmentBackfillCandidate_CheckStatus]
            CHECK ([CheckStatus] IS NULL OR [CheckStatus] IN (N'checked', N'unreadable', N'error')),
        CONSTRAINT [CK_AttachmentBackfillCandidate_ReviewStatus]
            CHECK ([ReviewStatus] IN (N'pending', N'approved', N'rejected'))
    );
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.indexes
       WHERE name = N'IX_AttachmentBackfillCandidate_Run_Review_Check'
         AND object_id = OBJECT_ID(N'dbo.AttachmentBackfillCandidate')
   )
BEGIN
    CREATE NONCLUSTERED INDEX [IX_AttachmentBackfillCandidate_Run_Review_Check]
        ON dbo.[AttachmentBackfillCandidate] ([RunId], [ReviewStatus], [CheckStatus]);
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.indexes
       WHERE name = N'IX_AttachmentBackfillCandidate_BillId'
         AND object_id = OBJECT_ID(N'dbo.AttachmentBackfillCandidate')
   )
BEGIN
    CREATE NONCLUSTERED INDEX [IX_AttachmentBackfillCandidate_BillId]
        ON dbo.[AttachmentBackfillCandidate] ([BillId]) INCLUDE ([RunId], [ReviewStatus]);
END
GO

CREATE OR ALTER PROCEDURE dbo.CreateAttachmentBackfillRun
    @Source          NVARCHAR(16),
    @Notes           NVARCHAR(400) = NULL,
    @CreatedByUserId BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    INSERT INTO dbo.[AttachmentBackfillRun] ([Source], [Status], [Notes], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[Source], INSERTED.[Status], INSERTED.[Notes],
        INSERTED.[CreatedByUserId], INSERTED.[CreatedDatetime], INSERTED.[ModifiedDatetime]
    VALUES (@Source, N'generating', @Notes, COALESCE(@CreatedByUserId, 17));
END
GO

CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillRunByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;

    SELECT r.[Id], r.[PublicId], r.[Source], r.[Status], r.[Notes], r.[CreatedByUserId],
           r.[CreatedDatetime], r.[ModifiedDatetime]
    FROM dbo.[AttachmentBackfillRun] AS r
    WHERE r.[PublicId] = @PublicId;
END
GO

-- Open ('generating' or 'ready') runs of one source, newest first. The job refuses to open a second one.
CREATE OR ALTER PROCEDURE dbo.ReadOpenAttachmentBackfillRuns
    @Source NVARCHAR(16)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT r.[Id], r.[PublicId], r.[Source], r.[Status], r.[CreatedDatetime]
    FROM dbo.[AttachmentBackfillRun] AS r
    WHERE r.[Source] = @Source AND r.[Status] IN (N'generating', N'ready')
    ORDER BY r.[Id] DESC;
END
GO

-- Allowed: generating -> ready, ready -> applied, generating/ready -> cancelled. Anything else (including any move out of
-- 'applied' or 'cancelled') changes nothing and returns an empty result set.
CREATE OR ALTER PROCEDURE dbo.UpdateAttachmentBackfillRunStatus
    @Id     BIGINT,
    @Status NVARCHAR(16)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.[AttachmentBackfillRun]
    SET [Status] = @Status,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[Source], INSERTED.[Status], INSERTED.[Notes],
        INSERTED.[CreatedByUserId], INSERTED.[CreatedDatetime], INSERTED.[ModifiedDatetime]
    WHERE [Id] = @Id
      AND (
             (@Status = N'ready'     AND [Status] = N'generating')
          OR (@Status = N'applied'   AND [Status] = N'ready')
          OR (@Status = N'cancelled' AND [Status] IN (N'generating', N'ready'))
      );
END
GO

-- Idempotent on (RunId, SourceFileId): an existing row is returned unchanged with Inserted = 0.
-- Refused (one row, Id NULL, Inserted 0, RefusedReason), checked in this order: 'run_not_generating'; 'bill_claimed_in_run'
-- (another file already claims this bill in the run); 'previously_rejected' (this exact (SourceFileId, BillId) pair was rejected in
-- any run); 'bill_claimed_elsewhere' (the bill has a pending/approved candidate in another run whose Status <> 'cancelled' -- an
-- 'applied' run included, so no review work is duplicated across runs).
CREATE OR ALTER PROCEDURE dbo.InsertAttachmentBackfillCandidate
    @RunId          BIGINT,
    @BillId         BIGINT,
    @SourceFileId   NVARCHAR(64),
    @SourceFileName NVARCHAR(400),
    @SourceFolderId NVARCHAR(64) = NULL,
    @MatchTier      NVARCHAR(32),
    @SourceFileSize BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @ExistingId BIGINT;
    DECLARE @RefusedReason NVARCHAR(32) = NULL;

    SELECT @ExistingId = c.[Id]
    FROM dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK)
    WHERE c.[RunId] = @RunId AND c.[SourceFileId] = @SourceFileId;

    IF @ExistingId IS NULL
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM dbo.[AttachmentBackfillRun] AS r WITH (UPDLOCK, HOLDLOCK)
            WHERE r.[Id] = @RunId AND r.[Status] = N'generating'
        )
            SET @RefusedReason = N'run_not_generating';
        ELSE IF EXISTS (
            SELECT 1 FROM dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK)
            WHERE c.[RunId] = @RunId AND c.[BillId] = @BillId
        )
            SET @RefusedReason = N'bill_claimed_in_run';
        ELSE IF EXISTS (
            SELECT 1 FROM dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK)
            WHERE c.[SourceFileId] = @SourceFileId AND c.[BillId] = @BillId AND c.[ReviewStatus] = N'rejected'
        )
            SET @RefusedReason = N'previously_rejected';
        ELSE IF EXISTS (
            SELECT 1
            FROM dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK)
            INNER JOIN dbo.[AttachmentBackfillRun] AS r ON r.[Id] = c.[RunId]
            WHERE c.[BillId] = @BillId
              AND c.[RunId] <> @RunId
              AND c.[ReviewStatus] IN (N'pending', N'approved')
              AND r.[Status] <> N'cancelled'
        )
            SET @RefusedReason = N'bill_claimed_elsewhere';

        IF @RefusedReason IS NULL
        BEGIN
            INSERT INTO dbo.[AttachmentBackfillCandidate]
                ([RunId], [BillId], [SourceFileId], [SourceFileName], [SourceFolderId], [SourceFileSize], [MatchTier])
            VALUES (@RunId, @BillId, @SourceFileId, @SourceFileName, @SourceFolderId, @SourceFileSize, @MatchTier);

            SELECT c.[Id], c.[PublicId], c.[RunId], c.[BillId], c.[SourceFileId], c.[MatchTier],
                   c.[CheckStatus], c.[ReviewStatus], CAST(1 AS BIT) AS [Inserted],
                   CAST(NULL AS NVARCHAR(32)) AS [RefusedReason]
            FROM dbo.[AttachmentBackfillCandidate] AS c
            WHERE c.[Id] = SCOPE_IDENTITY();
        END
        ELSE
        BEGIN
            SELECT CAST(NULL AS BIGINT) AS [Id], CAST(NULL AS UNIQUEIDENTIFIER) AS [PublicId],
                   @RunId AS [RunId], @BillId AS [BillId], @SourceFileId AS [SourceFileId],
                   @MatchTier AS [MatchTier], CAST(NULL AS NVARCHAR(16)) AS [CheckStatus],
                   CAST(NULL AS NVARCHAR(16)) AS [ReviewStatus], CAST(0 AS BIT) AS [Inserted],
                   @RefusedReason AS [RefusedReason];
        END
    END
    ELSE
    BEGIN
        SELECT c.[Id], c.[PublicId], c.[RunId], c.[BillId], c.[SourceFileId], c.[MatchTier],
               c.[CheckStatus], c.[ReviewStatus], CAST(0 AS BIT) AS [Inserted],
               CAST(NULL AS NVARCHAR(32)) AS [RefusedReason]
        FROM dbo.[AttachmentBackfillCandidate] AS c
        WHERE c.[Id] = @ExistingId;
    END

    COMMIT TRANSACTION;
END
GO

-- Keyset page of a 'ready' run's unchecked (CheckStatus IS NULL; 'error' too when @IncludeErrors = 1), pending, unlinked
-- candidates, with the bill fields the check needs. Id > @AfterId keeps a pass finite.
-- INNER JOIN dbo.Bill: a candidate whose bill was deleted (stale) is never checked.
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillCandidatesForVerification
    @RunId         BIGINT,
    @Top           INT = 50,
    @AfterId       BIGINT = 0,
    @IncludeErrors BIT = 0
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@Top)
        c.[Id], c.[PublicId], c.[RunId], c.[BillId], c.[SourceFileId], c.[SourceFileSize], c.[MatchTier], c.[CheckStatus],
        b.[BillNumber], b.[TotalAmount], v.[Name] AS [VendorName]
    FROM dbo.[AttachmentBackfillCandidate] AS c
    INNER JOIN dbo.[AttachmentBackfillRun] AS r ON r.[Id] = c.[RunId] AND r.[Status] = N'ready'
    INNER JOIN dbo.[Bill] AS b ON b.[Id] = c.[BillId]
    LEFT JOIN dbo.[Vendor] AS v ON v.[Id] = b.[VendorId]
    WHERE c.[RunId] = @RunId
      AND c.[Id] > @AfterId
      AND c.[ReviewStatus] = N'pending'
      AND c.[AttachmentId] IS NULL
      AND (c.[CheckStatus] IS NULL OR (@IncludeErrors = 1 AND c.[CheckStatus] = N'error'))
    ORDER BY c.[Id];
END
GO

-- Writes the check status and neutral evidence only. Never touches ReviewStatus / review / link columns. Refused (one row,
-- Id NULL, RefusedReason): 'not_found'; 'run_not_ready' (the candidate's run is not 'ready'); 'reviewed_or_linked'.
CREATE OR ALTER PROCEDURE dbo.UpdateAttachmentBackfillCandidateVerification
    @Id                     BIGINT,
    @SourceSha256           CHAR(64) = NULL,
    @CheckStatus            NVARCHAR(16),
    @InvoiceSeenCount       INT = NULL,
    @AmountSeenCount        INT = NULL,
    @VendorSeen             BIT = NULL,
    @OtherInvoiceLabelCount INT = NULL,
    @StatementLike          BIT = NULL,
    @UsedOcr                BIT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Updated TABLE (
        [Id] BIGINT, [PublicId] UNIQUEIDENTIFIER, [CheckStatus] NVARCHAR(16), [ReviewStatus] NVARCHAR(16),
        [SourceSha256] CHAR(64)
    );

    UPDATE c
    SET [SourceSha256]           = @SourceSha256,
        [CheckStatus]            = @CheckStatus,
        [InvoiceSeenCount]       = @InvoiceSeenCount,
        [AmountSeenCount]        = @AmountSeenCount,
        [VendorSeen]             = @VendorSeen,
        [OtherInvoiceLabelCount] = @OtherInvoiceLabelCount,
        [StatementLike]          = @StatementLike,
        [UsedOcr]                = @UsedOcr,
        [CheckedDatetime]        = SYSUTCDATETIME(),
        [ModifiedDatetime]       = SYSUTCDATETIME()
    OUTPUT INSERTED.[Id], INSERTED.[PublicId], INSERTED.[CheckStatus], INSERTED.[ReviewStatus], INSERTED.[SourceSha256]
        INTO @Updated
    FROM dbo.[AttachmentBackfillCandidate] AS c
    INNER JOIN dbo.[AttachmentBackfillRun] AS r ON r.[Id] = c.[RunId] AND r.[Status] = N'ready'
    WHERE c.[Id] = @Id
      AND c.[ReviewStatus] = N'pending'
      AND c.[AttachmentId] IS NULL;

    IF EXISTS (SELECT 1 FROM @Updated)
    BEGIN
        SELECT u.[Id], u.[PublicId], u.[CheckStatus], u.[ReviewStatus], u.[SourceSha256],
               CAST(NULL AS NVARCHAR(32)) AS [RefusedReason]
        FROM @Updated AS u;
    END
    ELSE
    BEGIN
        SELECT CAST(NULL AS BIGINT) AS [Id], CAST(NULL AS UNIQUEIDENTIFIER) AS [PublicId],
               CAST(NULL AS NVARCHAR(16)) AS [CheckStatus], CAST(NULL AS NVARCHAR(16)) AS [ReviewStatus],
               CAST(NULL AS CHAR(64)) AS [SourceSha256],
               CAST(CASE
                   WHEN NOT EXISTS (SELECT 1 FROM dbo.[AttachmentBackfillCandidate] WHERE [Id] = @Id) THEN N'not_found'
                   WHEN NOT EXISTS (
                       SELECT 1 FROM dbo.[AttachmentBackfillCandidate] AS c
                       INNER JOIN dbo.[AttachmentBackfillRun] AS r ON r.[Id] = c.[RunId]
                       WHERE c.[Id] = @Id AND r.[Status] = N'ready'
                   ) THEN N'run_not_ready'
                   ELSE N'reviewed_or_linked'
               END AS NVARCHAR(32)) AS [RefusedReason];
    END
END
GO

-- Resume: delete this run's candidates that are pending, unchecked (CheckStatus NULL), unlinked and NOT in the new plan
-- (@KeepJson = [{"f": SourceFileId, "b": BillId}, ...]). Only while the run is 'generating'. Returns the deleted count.
CREATE OR ALTER PROCEDURE dbo.DeleteStaleAttachmentBackfillCandidates
    @RunId    BIGINT,
    @KeepJson NVARCHAR(MAX)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Keep TABLE (
        [SourceFileId] NVARCHAR(64) NOT NULL,
        [BillId]       BIGINT NOT NULL,
        PRIMARY KEY ([SourceFileId], [BillId])
    );
    INSERT INTO @Keep ([SourceFileId], [BillId])
    SELECT DISTINCT k.[f], k.[b]
    FROM OPENJSON(COALESCE(@KeepJson, N'[]')) WITH ([f] NVARCHAR(64) '$.f', [b] BIGINT '$.b') AS k
    WHERE k.[f] IS NOT NULL AND k.[b] IS NOT NULL;

    DELETE c
    FROM dbo.[AttachmentBackfillCandidate] AS c
    INNER JOIN dbo.[AttachmentBackfillRun] AS r ON r.[Id] = c.[RunId] AND r.[Status] = N'generating'
    WHERE c.[RunId] = @RunId
      AND c.[ReviewStatus] = N'pending'
      AND c.[CheckStatus] IS NULL
      AND c.[AttachmentId] IS NULL
      AND NOT EXISTS (
          SELECT 1 FROM @Keep AS k WHERE k.[SourceFileId] = c.[SourceFileId] AND k.[BillId] = c.[BillId]
      );

    SELECT CAST(@@ROWCOUNT AS INT) AS [Deleted];
END
GO

-- Non-draft bills of @CompanyId with NO BillLineItemAttachment on any line, plus the distinct ProjectIds of their lines as a CSV.
-- Only what the matcher keys on: no memo, no line description, no vendor.
CREATE OR ALTER PROCEDURE dbo.ReadBillsForAttachmentBackfill
    @CompanyId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        b.[Id], b.[BillNumber], b.[TotalAmount],
        (
            SELECT STRING_AGG(CAST(p.[ProjectId] AS NVARCHAR(20)), N',')
            FROM (
                SELECT DISTINCT li.[ProjectId]
                FROM dbo.[BillLineItem] AS li
                WHERE li.[BillId] = b.[Id] AND li.[ProjectId] IS NOT NULL
            ) AS p
        ) AS [ProjectIds]
    FROM dbo.[Bill] AS b
    WHERE b.[CompanyId] = @CompanyId
      AND b.[IsDraft] = 0
      AND NOT EXISTS (
          SELECT 1
          FROM dbo.[BillLineItem] AS li
          INNER JOIN dbo.[BillLineItemAttachment] AS blia ON blia.[BillLineItemId] = li.[Id]
          WHERE li.[BillId] = b.[Id]
      )
    ORDER BY b.[Id];
END
GO

-- Distributions for one run, one row per (MatchTier, ReviewStatus). Neutral counts only -- no conclusion.
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillRunSummary
    @RunId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        c.[MatchTier],
        c.[ReviewStatus],
        COUNT_BIG(*) AS [CandidateCount],
        SUM(CASE WHEN c.[CheckStatus] IS NULL THEN 1 ELSE 0 END) AS [Unchecked],
        SUM(CASE WHEN c.[CheckStatus] = N'checked' THEN 1 ELSE 0 END) AS [Checked],
        SUM(CASE WHEN c.[CheckStatus] = N'unreadable' THEN 1 ELSE 0 END) AS [Unreadable],
        SUM(CASE WHEN c.[CheckStatus] = N'error' THEN 1 ELSE 0 END) AS [Errored],
        SUM(CASE WHEN c.[CheckStatus] = N'checked' AND c.[InvoiceSeenCount] IS NULL THEN 1 ELSE 0 END) AS [InvoiceNotCheckable],
        SUM(CASE WHEN c.[InvoiceSeenCount] = 0 THEN 1 ELSE 0 END) AS [InvoiceSeen0],
        SUM(CASE WHEN c.[InvoiceSeenCount] = 1 THEN 1 ELSE 0 END) AS [InvoiceSeen1],
        SUM(CASE WHEN c.[InvoiceSeenCount] >= 2 THEN 1 ELSE 0 END) AS [InvoiceSeen2Plus],
        SUM(CASE WHEN c.[AmountSeenCount] = 0 THEN 1 ELSE 0 END) AS [AmountSeen0],
        SUM(CASE WHEN c.[AmountSeenCount] >= 1 THEN 1 ELSE 0 END) AS [AmountSeen1Plus],
        SUM(CASE WHEN c.[OtherInvoiceLabelCount] = 0 THEN 1 ELSE 0 END) AS [OtherLabel0],
        SUM(CASE WHEN c.[OtherInvoiceLabelCount] >= 1 THEN 1 ELSE 0 END) AS [OtherLabel1Plus],
        SUM(CASE WHEN c.[StatementLike] = 1 THEN 1 ELSE 0 END) AS [StatementLike],
        SUM(CASE WHEN c.[VendorSeen] = 1 THEN 1 ELSE 0 END) AS [VendorSeen],
        SUM(CASE WHEN c.[UsedOcr] = 1 THEN 1 ELSE 0 END) AS [UsedOcr],
        SUM(CASE WHEN c.[AttachmentId] IS NOT NULL THEN 1 ELSE 0 END) AS [Linked]
    FROM dbo.[AttachmentBackfillCandidate] AS c
    WHERE c.[RunId] = @RunId
    GROUP BY c.[MatchTier], c.[ReviewStatus]
    ORDER BY c.[MatchTier], c.[ReviewStatus];
END
GO
