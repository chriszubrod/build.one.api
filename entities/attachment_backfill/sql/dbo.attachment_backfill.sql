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
--   b2 (review API): UpdateAttachmentBackfillCandidateReview approves/rejects only a pending, unlinked candidate of a 'ready'
--   run at the caller's RowVersion (reject needs an allowlisted RejectReason, approve forbids one; approve is refused
--   'bill_missing' when the bill no longer exists, reject of such a stale candidate is allowed; approve is refused
--   'bill_claimed_elsewhere' while another candidate for the bill is pending/approved in a different non-cancelled run, and
--   undo of a REJECTED row is refused the same way, so one bill can never hold two approvals); UndoAttachmentBackfillCandidateReview
--   returns an approved/rejected, unlinked candidate of a 'ready' run to pending at the caller's RowVersion. Both write only the
--   review columns. The evidence group (clean / flagged / invoice_only / amount_only / neither) is defined once, in
--   dbo.AttachmentBackfillEvidenceGroup, and used by both the facets and the queue; it is a filter label, not a conclusion.
--   b4: UndoAttachmentBackfillCandidateReview also records UndoneByUserId / UndoneDatetime (additive columns) in the same UPDATE.
--   The b4 apply/undo objects (link writes, AttachmentBackfillCandidateLink) live in b4_apply.sql, applied after this file.
--   b5: ApprovalMethod (NULL = individual, 'bulk') and BulkApprovalId are additive columns here; the review and review-undo
--   procs set both to NULL, so only BulkApproveAttachmentBackfillCandidates (b5_bulk.sql, applied after b4_apply.sql) marks a
--   row 'bulk'. The queue accepts @EvidenceGroup = 'sample' (members of the run's OPEN sample) and the facets add 'sample' rows
--   (the members counted again, same shape); both read the sample tables only when they exist, other groups are unchanged.
--   b5 (sticky rejection): RejectCount counts every reject the review proc ever wrote (never decremented, not even by an undo);
--   b5_bulk.sql compares it with the count at draw time, so a reject later undone still blocks the bulk / a redraw.
-- Depends (read-only): dbo.Bill, dbo.Vendor, dbo.BillLineItem, dbo.BillLineItemAttachment, dbo.Project.
-- Sprocs: CreateAttachmentBackfillRun, DeleteStaleAttachmentBackfillCandidates, InsertAttachmentBackfillCandidate, ReadAttachmentBackfillCandidateDetail, ReadAttachmentBackfillCandidatesForVerification, ReadAttachmentBackfillQueue, ReadAttachmentBackfillRunByPublicId, ReadAttachmentBackfillRunFacets, ReadAttachmentBackfillRunsForReview, ReadAttachmentBackfillRunSummary, ReadBillsForAttachmentBackfill, ReadOpenAttachmentBackfillRuns, UndoAttachmentBackfillCandidateReview, UpdateAttachmentBackfillCandidateReview, UpdateAttachmentBackfillCandidateVerification, UpdateAttachmentBackfillRunStatus
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
        -- Same claim rule (hinted) in UpdateAttachmentBackfillCandidateReview and UndoAttachmentBackfillCandidateReview.
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

-- ============================================================================================================================
-- U-535b b2: review API reads + the approve/reject/undo writes. Nothing below writes Attachment, BillLineItemAttachment or Bill.
-- ============================================================================================================================

-- The evidence group: the ONE definition, used by the facets and the queue. Pure (no table reference), so it can be created
-- before or without the tables it is applied to. Exact semantics:
--   flagged      OtherInvoiceLabelCount >= 1 OR StatementLike = 1
--   clean        not flagged, InvoiceSeenCount >= 1 AND AmountSeenCount >= 1 (OtherInvoiceLabelCount = 0, StatementLike = 0)
--   invoice_only not flagged, InvoiceSeenCount >= 1, AmountSeenCount 0 or NULL
--   amount_only  not flagged, InvoiceSeenCount 0 or NULL, AmountSeenCount >= 1
--   neither      everything else (unchecked, unreadable and error rows included)
-- A group name is a filter label over neutral counts, not a conclusion about the link.
CREATE OR ALTER FUNCTION dbo.AttachmentBackfillEvidenceGroup
(
    @InvoiceSeenCount       INT,
    @AmountSeenCount        INT,
    @OtherInvoiceLabelCount INT,
    @StatementLike          BIT
)
RETURNS TABLE
AS
RETURN
(
    SELECT CAST(CASE
        WHEN ISNULL(@OtherInvoiceLabelCount, 0) >= 1 OR ISNULL(@StatementLike, 0) = 1 THEN N'flagged'
        WHEN ISNULL(@InvoiceSeenCount, 0) >= 1 AND ISNULL(@AmountSeenCount, 0) >= 1
             AND @OtherInvoiceLabelCount = 0 AND @StatementLike = 0 THEN N'clean'
        WHEN ISNULL(@InvoiceSeenCount, 0) >= 1 AND ISNULL(@AmountSeenCount, 0) = 0 THEN N'invoice_only'
        WHEN ISNULL(@InvoiceSeenCount, 0) = 0 AND ISNULL(@AmountSeenCount, 0) >= 1 THEN N'amount_only'
        ELSE N'neither'
    END AS NVARCHAR(16)) AS [EvidenceGroup]
);
GO

-- Open ('generating' / 'ready') runs of any source, newest first, with review counts. Neutral counts only.
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillRunsForReview
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        r.[PublicId], r.[Source], r.[Status], r.[CreatedDatetime], r.[ModifiedDatetime],
        COUNT_BIG(c.[Id]) AS [CandidateCount],
        SUM(CASE WHEN c.[ReviewStatus] = N'pending' THEN 1 ELSE 0 END) AS [Pending],
        SUM(CASE WHEN c.[ReviewStatus] = N'approved' THEN 1 ELSE 0 END) AS [Approved],
        SUM(CASE WHEN c.[ReviewStatus] = N'rejected' THEN 1 ELSE 0 END) AS [Rejected],
        SUM(CASE WHEN c.[Id] IS NOT NULL AND c.[CheckStatus] IS NULL THEN 1 ELSE 0 END) AS [Unchecked],
        SUM(CASE WHEN c.[AttachmentId] IS NOT NULL THEN 1 ELSE 0 END) AS [Linked]
    FROM dbo.[AttachmentBackfillRun] AS r
    LEFT JOIN dbo.[AttachmentBackfillCandidate] AS c ON c.[RunId] = r.[Id]
    WHERE r.[Status] IN (N'generating', N'ready')
    GROUP BY r.[Id], r.[PublicId], r.[Source], r.[Status], r.[CreatedDatetime], r.[ModifiedDatetime]
    ORDER BY r.[Id] DESC;
END
GO

-- Batch-picker counts for one run: one row per (EvidenceGroup, primary project, bill year, ReviewStatus).
-- Primary project = the bill's line project with the lowest Name (then Id); NULL when the bill has no project line or the
-- bill is gone (stale). ReadAttachmentBackfillQueue derives it the same way, so facet totals equal queue totals.
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillRunFacets
    @RunPublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;

    -- b5: members of the run's OPEN review sample (b5_bulk.sql) are counted a second time under the group 'sample'. The
    -- sample tables are created by b5_bulk.sql, applied after this file: the read is guarded, so this proc works without them.
    DECLARE @Sample TABLE ([CandidateId] BIGINT NOT NULL PRIMARY KEY);
    IF OBJECT_ID(N'dbo.AttachmentBackfillSampleMember', N'U') IS NOT NULL
        INSERT INTO @Sample ([CandidateId])
        SELECT m.[CandidateId]
        FROM dbo.[AttachmentBackfillSampleMember] AS m
        INNER JOIN dbo.[AttachmentBackfillSample] AS s ON s.[Id] = m.[SampleId] AND s.[Status] = N'open'
        INNER JOIN dbo.[AttachmentBackfillRun] AS sr ON sr.[Id] = s.[RunId]
        WHERE sr.[PublicId] = @RunPublicId;

    SELECT
        eg.[EvidenceGroup],
        pp.[ProjectPublicId],
        pp.[ProjectName],
        YEAR(b.[BillDate]) AS [BillYear],
        c.[ReviewStatus],
        COUNT_BIG(*) AS [CandidateCount]
    FROM dbo.[AttachmentBackfillRun] AS r
    INNER JOIN dbo.[AttachmentBackfillCandidate] AS c ON c.[RunId] = r.[Id]
    LEFT JOIN dbo.[Bill] AS b ON b.[Id] = c.[BillId]
    OUTER APPLY (
        -- Primary project, kept inline (see ReadAttachmentBackfillQueue); sibling copies: ReadAttachmentBackfillQueue,
        -- ReadAttachmentBackfillCandidateDetail. All three must stay identical (facet totals = queue totals).
        SELECT TOP (1) p.[Id] AS [ProjectId], p.[PublicId] AS [ProjectPublicId], p.[Name] AS [ProjectName]
        FROM dbo.[BillLineItem] AS li
        INNER JOIN dbo.[Project] AS p ON p.[Id] = li.[ProjectId]
        WHERE li.[BillId] = b.[Id]
        ORDER BY p.[Name], p.[Id]
    ) AS pp
    CROSS APPLY (
        -- The candidate's evidence group, plus 'sample' when it is a member of the open sample ('sample' sorts after
        -- every evidence group, so the other groups' rows and their order are unchanged).
        SELECT g.[EvidenceGroup]
        FROM dbo.AttachmentBackfillEvidenceGroup(
            c.[InvoiceSeenCount], c.[AmountSeenCount], c.[OtherInvoiceLabelCount], c.[StatementLike]
        ) AS g
        UNION ALL
        SELECT CAST(N'sample' AS NVARCHAR(16)) WHERE EXISTS (SELECT 1 FROM @Sample AS sm WHERE sm.[CandidateId] = c.[Id])
    ) AS eg
    WHERE r.[PublicId] = @RunPublicId
    GROUP BY eg.[EvidenceGroup], pp.[ProjectId], pp.[ProjectPublicId], pp.[ProjectName], YEAR(b.[BillDate]), c.[ReviewStatus]
    ORDER BY eg.[EvidenceGroup], pp.[ProjectName], pp.[ProjectId], YEAR(b.[BillDate]), c.[ReviewStatus];
END
GO

-- Keyset page of one run's candidates. Filters (NULL = any): @EvidenceGroup; @ProjectPublicId (primary project) or
-- @NoProject = 1 (no primary project); @BillYear or @NoBillYear = 1 (bill gone: the NULL year the facets emit);
-- @ReviewStatus. Order: rows with a project first, primary project Name, its Id, BillDate (a stale candidate -- bill gone -- sorts last), candidate Id. @AfterPublicId is the last row of the previous
-- page: its sort key is re-derived here, so the cursor stays valid when that row's review status changes. An unknown
-- @AfterPublicId returns no rows.
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillQueue
    @RunPublicId     UNIQUEIDENTIFIER,
    @EvidenceGroup   NVARCHAR(16) = NULL,
    @ProjectPublicId UNIQUEIDENTIFIER = NULL,
    @NoProject       BIT = 0,
    @BillYear        INT = NULL,
    @NoBillYear      BIT = 0,
    @ReviewStatus    NVARCHAR(16) = NULL,
    @AfterPublicId   UNIQUEIDENTIFIER = NULL,
    @Top             INT = 50
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @RunId BIGINT = (SELECT r.[Id] FROM dbo.[AttachmentBackfillRun] AS r WHERE r.[PublicId] = @RunPublicId);

    -- b5: @EvidenceGroup = 'sample' selects the members of the run's OPEN review sample (any evidence group / review
    -- status filter still applies on top). Guarded: the sample tables come from b5_bulk.sql, applied after this file.
    DECLARE @Sample TABLE ([CandidateId] BIGINT NOT NULL PRIMARY KEY);
    IF @EvidenceGroup = N'sample' AND OBJECT_ID(N'dbo.AttachmentBackfillSampleMember', N'U') IS NOT NULL
        INSERT INTO @Sample ([CandidateId])
        SELECT m.[CandidateId]
        FROM dbo.[AttachmentBackfillSampleMember] AS m
        INNER JOIN dbo.[AttachmentBackfillSample] AS s ON s.[Id] = m.[SampleId] AND s.[Status] = N'open'
        WHERE s.[RunId] = @RunId;

    -- k: filter + sort keys for every candidate of the run; page: the TOP (@Top) keys after the cursor. Display columns
    -- (vendor, reviewer, the ProjectNames STRING_AGG) are joined back for the page rows only.
    WITH k AS (
        SELECT
            c.[Id], c.[PublicId], c.[ReviewStatus], YEAR(b.[BillDate]) AS [BillYear], eg.[EvidenceGroup],
            pp.[ProjectPublicId], pp.[ProjectName],
            CASE WHEN pp.[ProjectId] IS NULL THEN 1 ELSE 0 END AS [SortNoProject],
            ISNULL(pp.[ProjectName], N'') AS [SortProjectName],
            ISNULL(pp.[ProjectId], 0) AS [SortProjectId],
            ISNULL(b.[BillDate], CAST('9999-12-31' AS DATETIME2(3))) AS [SortBillDate]
        FROM dbo.[AttachmentBackfillCandidate] AS c
        LEFT JOIN dbo.[Bill] AS b ON b.[Id] = c.[BillId]
        OUTER APPLY (
            -- Primary project. Kept inline (an inline TVF referencing tables cannot be created where dbo.Project is absent,
            -- e.g. b1's test fixture); sibling copies: ReadAttachmentBackfillRunFacets, ReadAttachmentBackfillCandidateDetail.
            SELECT TOP (1) p.[Id] AS [ProjectId], p.[PublicId] AS [ProjectPublicId], p.[Name] AS [ProjectName]
            FROM dbo.[BillLineItem] AS li
            INNER JOIN dbo.[Project] AS p ON p.[Id] = li.[ProjectId]
            WHERE li.[BillId] = b.[Id]
            ORDER BY p.[Name], p.[Id]
        ) AS pp
        CROSS APPLY dbo.AttachmentBackfillEvidenceGroup(
            c.[InvoiceSeenCount], c.[AmountSeenCount], c.[OtherInvoiceLabelCount], c.[StatementLike]
        ) AS eg
        WHERE c.[RunId] = @RunId
    ),
    a AS (
        SELECT k.[SortNoProject], k.[SortProjectName], k.[SortProjectId], k.[SortBillDate], k.[Id]
        FROM k
        WHERE k.[PublicId] = @AfterPublicId
    ),
    page AS (
        SELECT TOP (@Top) k.*
        FROM k
        LEFT JOIN a ON 1 = 1
        WHERE (
              @EvidenceGroup IS NULL
              OR k.[EvidenceGroup] = @EvidenceGroup
              OR (@EvidenceGroup = N'sample' AND EXISTS (SELECT 1 FROM @Sample AS sm WHERE sm.[CandidateId] = k.[Id]))
          )
          AND (@ProjectPublicId IS NULL OR k.[ProjectPublicId] = @ProjectPublicId)
          AND (ISNULL(@NoProject, 0) = 0 OR k.[ProjectPublicId] IS NULL)
          AND (@BillYear IS NULL OR k.[BillYear] = @BillYear)
          AND (ISNULL(@NoBillYear, 0) = 0 OR k.[BillYear] IS NULL)
          AND (@ReviewStatus IS NULL OR k.[ReviewStatus] = @ReviewStatus)
          AND (
              @AfterPublicId IS NULL
              OR k.[SortNoProject] > a.[SortNoProject]
              OR (k.[SortNoProject] = a.[SortNoProject] AND (
                     k.[SortProjectName] > a.[SortProjectName]
                  OR (k.[SortProjectName] = a.[SortProjectName] AND (
                         k.[SortProjectId] > a.[SortProjectId]
                      OR (k.[SortProjectId] = a.[SortProjectId] AND (
                             k.[SortBillDate] > a.[SortBillDate]
                          OR (k.[SortBillDate] = a.[SortBillDate] AND k.[Id] > a.[Id])
                      ))
                  ))
              ))
          )
        ORDER BY k.[SortNoProject], k.[SortProjectName], k.[SortProjectId], k.[SortBillDate], k.[Id]
    )
    SELECT
        c.[Id], c.[PublicId], c.[RowVersion], r.[PublicId] AS [RunPublicId], r.[Status] AS [RunStatus],
        b.[PublicId] AS [BillPublicId], b.[BillNumber], b.[BillDate], pg.[BillYear], b.[TotalAmount],
        v.[Name] AS [VendorName], pg.[ProjectPublicId], pg.[ProjectName],
        (
            SELECT STRING_AGG(d.[Name], N', ') WITHIN GROUP (ORDER BY d.[Name])
            FROM (
                SELECT DISTINCT p2.[Name]
                FROM dbo.[BillLineItem] AS li2
                INNER JOIN dbo.[Project] AS p2 ON p2.[Id] = li2.[ProjectId]
                WHERE li2.[BillId] = b.[Id]
            ) AS d
        ) AS [ProjectNames],
        c.[SourceFileName], c.[SourceFileSize], c.[MatchTier], c.[CheckStatus], c.[InvoiceSeenCount], c.[AmountSeenCount],
        c.[VendorSeen], c.[OtherInvoiceLabelCount], c.[StatementLike], c.[UsedOcr], pg.[EvidenceGroup], c.[ReviewStatus],
        c.[RejectReason], ru.[PublicId] AS [ReviewedByUserPublicId], c.[ReviewedDatetime],
        CAST(CASE WHEN c.[AttachmentId] IS NULL THEN 0 ELSE 1 END AS BIT) AS [Linked]
    FROM page AS pg
    INNER JOIN dbo.[AttachmentBackfillCandidate] AS c ON c.[Id] = pg.[Id]
    INNER JOIN dbo.[AttachmentBackfillRun] AS r ON r.[Id] = c.[RunId]
    LEFT JOIN dbo.[Bill] AS b ON b.[Id] = c.[BillId]
    LEFT JOIN dbo.[Vendor] AS v ON v.[Id] = b.[VendorId]
    LEFT JOIN dbo.[User] AS ru ON ru.[Id] = c.[ReviewedByUserId]
    ORDER BY pg.[SortNoProject], pg.[SortProjectName], pg.[SortProjectId], pg.[SortBillDate], pg.[Id]
    OPTION (RECOMPILE);
END
GO

-- One candidate by PublicId: the queue row's fields plus SourceFileId (server-side only: the PDF fetch reads it from here,
-- never from a request), SourceSha256 (the hash of the bytes b1 checked, so a reader can tell whether Box's current version
-- is the same content) and the run's PublicId/Status. No row for an unknown PublicId.
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillCandidateDetail
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        c.[Id], c.[PublicId], c.[RowVersion], r.[PublicId] AS [RunPublicId], r.[Status] AS [RunStatus],
        b.[PublicId] AS [BillPublicId], b.[BillNumber], b.[BillDate], YEAR(b.[BillDate]) AS [BillYear], b.[TotalAmount],
        v.[Name] AS [VendorName],
        pp.[ProjectPublicId], pp.[ProjectName],
        (
            SELECT STRING_AGG(d.[Name], N', ') WITHIN GROUP (ORDER BY d.[Name])
            FROM (
                SELECT DISTINCT p2.[Name]
                FROM dbo.[BillLineItem] AS li2
                INNER JOIN dbo.[Project] AS p2 ON p2.[Id] = li2.[ProjectId]
                WHERE li2.[BillId] = b.[Id]
            ) AS d
        ) AS [ProjectNames],
        c.[SourceFileId], c.[SourceSha256], c.[SourceFileName], c.[SourceFileSize], c.[MatchTier], c.[CheckStatus],
        c.[InvoiceSeenCount], c.[AmountSeenCount], c.[VendorSeen], c.[OtherInvoiceLabelCount], c.[StatementLike],
        c.[UsedOcr], eg.[EvidenceGroup],
        c.[ReviewStatus], c.[RejectReason], ru.[PublicId] AS [ReviewedByUserPublicId], c.[ReviewedDatetime],
        CAST(CASE WHEN c.[AttachmentId] IS NULL THEN 0 ELSE 1 END AS BIT) AS [Linked]
    FROM dbo.[AttachmentBackfillCandidate] AS c
    INNER JOIN dbo.[AttachmentBackfillRun] AS r ON r.[Id] = c.[RunId]
    LEFT JOIN dbo.[Bill] AS b ON b.[Id] = c.[BillId]
    LEFT JOIN dbo.[Vendor] AS v ON v.[Id] = b.[VendorId]
    LEFT JOIN dbo.[User] AS ru ON ru.[Id] = c.[ReviewedByUserId]
    OUTER APPLY (
        -- Primary project, kept inline (see ReadAttachmentBackfillQueue); sibling copies: ReadAttachmentBackfillQueue,
        -- ReadAttachmentBackfillRunFacets.
        SELECT TOP (1) p.[Id] AS [ProjectId], p.[PublicId] AS [ProjectPublicId], p.[Name] AS [ProjectName]
        FROM dbo.[BillLineItem] AS li
        INNER JOIN dbo.[Project] AS p ON p.[Id] = li.[ProjectId]
        WHERE li.[BillId] = b.[Id]
        ORDER BY p.[Name], p.[Id]
    ) AS pp
    CROSS APPLY dbo.AttachmentBackfillEvidenceGroup(
        c.[InvoiceSeenCount], c.[AmountSeenCount], c.[OtherInvoiceLabelCount], c.[StatementLike]
    ) AS eg
    WHERE c.[PublicId] = @PublicId;
END
GO

-- b5 (bulk approval, b5_bulk.sql): how a candidate was approved. NULL = an individual decision (b2); 'bulk' = by
-- BulkApproveAttachmentBackfillCandidates, with BulkApprovalId naming the dbo.AttachmentBackfillBulkApproval row (its FK
-- is added by b5_bulk.sql). Additive, nullable, idempotent. Here because the b2 review and review-undo procs below clear
-- them: an individual decision or an undo always leaves ApprovalMethod/BulkApprovalId NULL, so a bulk undo can never
-- revert a decision a human made by hand.
IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.AttachmentBackfillCandidate', 'ApprovalMethod') IS NULL
BEGIN
    ALTER TABLE dbo.[AttachmentBackfillCandidate] ADD [ApprovalMethod] NVARCHAR(16) NULL
        CONSTRAINT [CK_AttachmentBackfillCandidate_ApprovalMethod] CHECK ([ApprovalMethod] IS NULL OR [ApprovalMethod] = N'bulk');
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.AttachmentBackfillCandidate', 'BulkApprovalId') IS NULL
BEGIN
    ALTER TABLE dbo.[AttachmentBackfillCandidate] ADD [BulkApprovalId] BIGINT NULL;
END
GO

-- b5 (sticky rejection): how many times this candidate was ever rejected by the review proc. Additive, idempotent; only
-- ever incremented (the undo leaves it), so b5_bulk.sql can tell a sampled candidate was rejected while its sample was open.
IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.AttachmentBackfillCandidate', 'RejectCount') IS NULL
BEGIN
    ALTER TABLE dbo.[AttachmentBackfillCandidate] ADD [RejectCount] INT NOT NULL
        CONSTRAINT [DF_AttachmentBackfillCandidate_RejectCount] DEFAULT 0;
END
GO

-- Approve or reject one candidate. Writes ReviewStatus, RejectReason, ReviewedByUserId, ReviewedDatetime only -- no link, no
-- Attachment, no Bill. Allowed only from ReviewStatus 'pending', on a 'ready' run, while AttachmentId IS NULL, at the caller's
-- RowVersion. Returns (Id, PublicId, RowVersion, ReviewStatus, RejectReason, ReviewedByUserPublicId, ReviewedDatetime,
-- RefusedReason).
-- Refused (one row, Id NULL, RefusedReason), checked in this order: 'invalid' (decision not approved/rejected, a reject reason
-- missing / not allowed / given with an approval, or no reviewer); 'not_found'; 'run_not_ready'; 'bill_missing' (APPROVE only:
-- the candidate's bill no longer exists -- a stale candidate may still be rejected); 'bill_claimed_elsewhere' (APPROVE only:
-- another candidate for the same bill is pending or approved in a different non-cancelled run -- one bill, one approval);
-- 'linked'; 'not_pending'; 'stale' (RowVersion differs). The bill and claim checks sit in the UPDATE's WHERE (the claim under
-- UPDLOCK, HOLDLOCK), so they are atomic with the write; the refusal reason is diagnosed afterwards.
CREATE OR ALTER PROCEDURE dbo.UpdateAttachmentBackfillCandidateReview
    @PublicId         UNIQUEIDENTIFIER,
    @Decision         NVARCHAR(16),
    @RejectReason     NVARCHAR(32) = NULL,
    @RowVersion       BINARY(8),
    @ReviewedByUserId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Updated TABLE (
        [Id] BIGINT, [PublicId] UNIQUEIDENTIFIER, [RowVersion] BINARY(8), [ReviewStatus] NVARCHAR(16),
        [RejectReason] NVARCHAR(200), [ReviewedByUserId] BIGINT, [ReviewedDatetime] DATETIME2(3)
    );

    DECLARE @Invalid BIT = CASE
        WHEN @ReviewedByUserId IS NULL THEN 1
        WHEN @Decision = N'approved' AND @RejectReason IS NULL THEN 0
        WHEN @Decision = N'rejected' AND @RejectReason IN
             (N'wrong_bill', N'statement', N'multiple_invoices', N'duplicate', N'unreadable', N'other') THEN 0
        ELSE 1
    END;

    IF @Invalid = 0
    BEGIN
        UPDATE c
        SET [ReviewStatus]     = @Decision,
            [RejectReason]     = CASE WHEN @Decision = N'rejected' THEN @RejectReason ELSE NULL END,
            [ReviewedByUserId] = @ReviewedByUserId,
            [ReviewedDatetime] = SYSUTCDATETIME(),
            [ApprovalMethod]   = NULL,  -- b5: an individual decision
            [BulkApprovalId]   = NULL,
            [RejectCount]      = c.[RejectCount] + CASE WHEN @Decision = N'rejected' THEN 1 ELSE 0 END,  -- b5: sticky
            [ModifiedDatetime] = SYSUTCDATETIME()
        OUTPUT INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion], INSERTED.[ReviewStatus], INSERTED.[RejectReason],
               INSERTED.[ReviewedByUserId], INSERTED.[ReviewedDatetime]
            INTO @Updated
        FROM dbo.[AttachmentBackfillCandidate] AS c
        -- UPDLOCK: under READ_COMMITTED_SNAPSHOT a plain read could see a stale 'ready' while
        -- CloseAttachmentBackfillRunIfDone commits 'applied'. A U lock on the run row conflicts with the closure's U/X
        -- lock whatever plan is chosen, so this waits and then sees 'applied' (b4 R8 / S2).
        INNER JOIN dbo.[AttachmentBackfillRun] AS r WITH (UPDLOCK) ON r.[Id] = c.[RunId] AND r.[Status] = N'ready'
        WHERE c.[PublicId] = @PublicId
          AND c.[ReviewStatus] = N'pending'
          AND c.[AttachmentId] IS NULL
          AND c.[RowVersion] = @RowVersion
          AND (@Decision <> N'approved' OR EXISTS (SELECT 1 FROM dbo.[Bill] AS b WHERE b.[Id] = c.[BillId]))
          -- Same claim rule (hinted) in InsertAttachmentBackfillCandidate and UndoAttachmentBackfillCandidateReview.
          AND (@Decision <> N'approved' OR NOT EXISTS (
                  SELECT 1
                  FROM dbo.[AttachmentBackfillCandidate] AS o WITH (UPDLOCK, HOLDLOCK)
                  INNER JOIN dbo.[AttachmentBackfillRun] AS orun ON orun.[Id] = o.[RunId]
                  WHERE o.[BillId] = c.[BillId]
                    AND o.[RunId] <> c.[RunId]
                    AND o.[ReviewStatus] IN (N'pending', N'approved')
                    AND orun.[Status] <> N'cancelled'
              ));
    END

    IF EXISTS (SELECT 1 FROM @Updated)
    BEGIN
        SELECT u.[Id], u.[PublicId], u.[RowVersion], u.[ReviewStatus], u.[RejectReason], ru.[PublicId] AS [ReviewedByUserPublicId],
               u.[ReviewedDatetime], CAST(NULL AS NVARCHAR(32)) AS [RefusedReason]
        FROM @Updated AS u
        LEFT JOIN dbo.[User] AS ru ON ru.[Id] = u.[ReviewedByUserId];
    END
    ELSE
    BEGIN
        -- Diagnose the refusal from one read of the candidate (the UPDATE above already decided; this only names why).
        DECLARE @Found BIT = 0, @RunReady BIT = 0, @BillExists BIT = 0, @Linked BIT = 0,
                @Status NVARCHAR(16) = NULL, @ClaimedElsewhere BIT = 0;
        SELECT
            @Found            = 1,
            @RunReady         = CASE WHEN r.[Status] = N'ready' THEN 1 ELSE 0 END,
            @BillExists       = CASE WHEN EXISTS (SELECT 1 FROM dbo.[Bill] AS b WHERE b.[Id] = c.[BillId]) THEN 1 ELSE 0 END,
            @Linked           = CASE WHEN c.[AttachmentId] IS NOT NULL THEN 1 ELSE 0 END,
            @Status           = c.[ReviewStatus],
            @ClaimedElsewhere = CASE WHEN EXISTS (
                SELECT 1
                FROM dbo.[AttachmentBackfillCandidate] AS o
                INNER JOIN dbo.[AttachmentBackfillRun] AS orun ON orun.[Id] = o.[RunId]
                WHERE o.[BillId] = c.[BillId]
                  AND o.[RunId] <> c.[RunId]
                  AND o.[ReviewStatus] IN (N'pending', N'approved')
                  AND orun.[Status] <> N'cancelled'
            ) THEN 1 ELSE 0 END
        FROM dbo.[AttachmentBackfillCandidate] AS c
        INNER JOIN dbo.[AttachmentBackfillRun] AS r ON r.[Id] = c.[RunId]
        WHERE c.[PublicId] = @PublicId;

        SELECT CAST(NULL AS BIGINT) AS [Id], CAST(NULL AS UNIQUEIDENTIFIER) AS [PublicId], CAST(NULL AS BINARY(8)) AS [RowVersion],
               CAST(NULL AS NVARCHAR(16)) AS [ReviewStatus], CAST(NULL AS NVARCHAR(200)) AS [RejectReason],
               CAST(NULL AS UNIQUEIDENTIFIER) AS [ReviewedByUserPublicId], CAST(NULL AS DATETIME2(3)) AS [ReviewedDatetime],
               CAST(CASE
                   WHEN @Invalid = 1 THEN N'invalid'
                   WHEN @Found = 0 THEN N'not_found'
                   WHEN @RunReady = 0 THEN N'run_not_ready'
                   WHEN @Decision = N'approved' AND @BillExists = 0 THEN N'bill_missing'
                   WHEN @Decision = N'approved' AND @ClaimedElsewhere = 1 THEN N'bill_claimed_elsewhere'
                   WHEN @Linked = 1 THEN N'linked'
                   WHEN @Status <> N'pending' THEN N'not_pending'
                   ELSE N'stale'
               END AS NVARCHAR(32)) AS [RefusedReason];
    END
END
GO

-- b4 (undo audit trail): who last undid a review, and when. Additive, nullable, idempotent. Written only by
-- UndoAttachmentBackfillCandidateReview, from the reviewer it is about to clear; a later approve/reject leaves them as is.
IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.AttachmentBackfillCandidate', 'UndoneByUserId') IS NULL
BEGIN
    ALTER TABLE dbo.[AttachmentBackfillCandidate] ADD [UndoneByUserId] BIGINT NULL;
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.AttachmentBackfillCandidate', 'UndoneDatetime') IS NULL
BEGIN
    ALTER TABLE dbo.[AttachmentBackfillCandidate] ADD [UndoneDatetime] DATETIME2(3) NULL;
END
GO

-- Undo one review: approved/rejected -> pending, clearing RejectReason, ReviewedByUserId and ReviewedDatetime. Only on a
-- 'ready' run, while AttachmentId IS NULL, at the caller's RowVersion. Same row shape as the review proc. Refused, in this
-- order: 'not_found'; 'run_not_ready'; 'bill_claimed_elsewhere' (undo of a REJECTED row only, when another candidate for the
-- same bill is pending or approved in a different non-cancelled run -- checked inside the UPDATE's WHERE; undo of an approved
-- row is unaffected); 'linked'; 'not_reviewed' (already pending); 'stale'.
-- b4: the same UPDATE records UndoneByUserId (the caller; NULL when the caller passes none) and UndoneDatetime before it
-- clears the reviewer, so the review history keeps who undid what.
CREATE OR ALTER PROCEDURE dbo.UndoAttachmentBackfillCandidateReview
    @PublicId       UNIQUEIDENTIFIER,
    @RowVersion     BINARY(8),
    @UndoneByUserId BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Updated TABLE (
        [Id] BIGINT, [PublicId] UNIQUEIDENTIFIER, [RowVersion] BINARY(8), [ReviewStatus] NVARCHAR(16),
        [RejectReason] NVARCHAR(200), [ReviewedByUserId] BIGINT, [ReviewedDatetime] DATETIME2(3)
    );

    UPDATE c
    SET [ReviewStatus]     = N'pending',
        [RejectReason]     = NULL,
        [UndoneByUserId]   = @UndoneByUserId,
        [UndoneDatetime]   = SYSUTCDATETIME(),
        [ReviewedByUserId] = NULL,
        [ReviewedDatetime] = NULL,
        [ApprovalMethod]   = NULL,  -- b5: an undone bulk approval is no longer the bulk's to revert
        [BulkApprovalId]   = NULL,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion], INSERTED.[ReviewStatus], INSERTED.[RejectReason],
           INSERTED.[ReviewedByUserId], INSERTED.[ReviewedDatetime]
        INTO @Updated
    FROM dbo.[AttachmentBackfillCandidate] AS c
    -- UPDLOCK: see UpdateAttachmentBackfillCandidateReview (b4 R8 / S2).
    INNER JOIN dbo.[AttachmentBackfillRun] AS r WITH (UPDLOCK) ON r.[Id] = c.[RunId] AND r.[Status] = N'ready'
    WHERE c.[PublicId] = @PublicId
      AND c.[ReviewStatus] IN (N'approved', N'rejected')
      AND c.[AttachmentId] IS NULL
      AND c.[RowVersion] = @RowVersion
      -- Same claim rule (hinted) in InsertAttachmentBackfillCandidate and UpdateAttachmentBackfillCandidateReview.
      AND (c.[ReviewStatus] <> N'rejected' OR NOT EXISTS (
              SELECT 1
              FROM dbo.[AttachmentBackfillCandidate] AS o WITH (UPDLOCK, HOLDLOCK)
              INNER JOIN dbo.[AttachmentBackfillRun] AS orun ON orun.[Id] = o.[RunId]
              WHERE o.[BillId] = c.[BillId]
                AND o.[RunId] <> c.[RunId]
                AND o.[ReviewStatus] IN (N'pending', N'approved')
                AND orun.[Status] <> N'cancelled'
          ));

    IF EXISTS (SELECT 1 FROM @Updated)
    BEGIN
        SELECT u.[Id], u.[PublicId], u.[RowVersion], u.[ReviewStatus], u.[RejectReason], ru.[PublicId] AS [ReviewedByUserPublicId],
               u.[ReviewedDatetime], CAST(NULL AS NVARCHAR(32)) AS [RefusedReason]
        FROM @Updated AS u
        LEFT JOIN dbo.[User] AS ru ON ru.[Id] = u.[ReviewedByUserId];
    END
    ELSE
    BEGIN
        -- Diagnose the refusal from one read of the candidate (the UPDATE above already decided; this only names why).
        DECLARE @Found BIT = 0, @RunReady BIT = 0, @BillExists BIT = 0, @Linked BIT = 0,
                @Status NVARCHAR(16) = NULL, @ClaimedElsewhere BIT = 0;
        SELECT
            @Found            = 1,
            @RunReady         = CASE WHEN r.[Status] = N'ready' THEN 1 ELSE 0 END,
            @BillExists       = CASE WHEN EXISTS (SELECT 1 FROM dbo.[Bill] AS b WHERE b.[Id] = c.[BillId]) THEN 1 ELSE 0 END,
            @Linked           = CASE WHEN c.[AttachmentId] IS NOT NULL THEN 1 ELSE 0 END,
            @Status           = c.[ReviewStatus],
            @ClaimedElsewhere = CASE WHEN EXISTS (
                SELECT 1
                FROM dbo.[AttachmentBackfillCandidate] AS o
                INNER JOIN dbo.[AttachmentBackfillRun] AS orun ON orun.[Id] = o.[RunId]
                WHERE o.[BillId] = c.[BillId]
                  AND o.[RunId] <> c.[RunId]
                  AND o.[ReviewStatus] IN (N'pending', N'approved')
                  AND orun.[Status] <> N'cancelled'
            ) THEN 1 ELSE 0 END
        FROM dbo.[AttachmentBackfillCandidate] AS c
        INNER JOIN dbo.[AttachmentBackfillRun] AS r ON r.[Id] = c.[RunId]
        WHERE c.[PublicId] = @PublicId;

        SELECT CAST(NULL AS BIGINT) AS [Id], CAST(NULL AS UNIQUEIDENTIFIER) AS [PublicId], CAST(NULL AS BINARY(8)) AS [RowVersion],
               CAST(NULL AS NVARCHAR(16)) AS [ReviewStatus], CAST(NULL AS NVARCHAR(200)) AS [RejectReason],
               CAST(NULL AS UNIQUEIDENTIFIER) AS [ReviewedByUserPublicId], CAST(NULL AS DATETIME2(3)) AS [ReviewedDatetime],
               CAST(CASE
                   WHEN @Found = 0 THEN N'not_found'
                   WHEN @RunReady = 0 THEN N'run_not_ready'
                   WHEN @Status = N'rejected' AND @ClaimedElsewhere = 1 THEN N'bill_claimed_elsewhere'
                   WHEN @Linked = 1 THEN N'linked'
                   WHEN @Status = N'pending' THEN N'not_reviewed'
                   ELSE N'stale'
               END AS NVARCHAR(32)) AS [RefusedReason];
    END
END
GO
