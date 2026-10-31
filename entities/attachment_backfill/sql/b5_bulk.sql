-- Contract: dbo.AttachmentBackfillSample, dbo.AttachmentBackfillSamplePopulation, dbo.AttachmentBackfillSampleMember,
--   dbo.AttachmentBackfillBulkApproval (+ the FK of AttachmentBackfillCandidate.BulkApprovalId; the ApprovalMethod / BulkApprovalId
--   columns themselves are in dbo.attachment_backfill.sql)
-- Owns: U-535b b5 sample-gated bulk approval: one random review sample per run (server-seeded, reproducible from the stored
--   seed), the population it was drawn from, a blind second review of each sampled candidate, and the bulk approval of the
--   rest of that population once BOTH reviews approved every sampled candidate -- marked as bulk and undoable.
-- Invariants:
--   DrawAttachmentBackfillSample draws from a 'ready' run only, at most ONE open sample per run (refused 'sample_open', and a
--   filtered unique index). The population is read ONCE under UPDLOCK, HOLDLOCK and stored (AttachmentBackfillSamplePopulation):
--   the run's candidates that are 'clean' (dbo.AttachmentBackfillEvidenceGroup), unlinked, whose bill exists, whose bill no
--   other non-cancelled run holds pending/approved, and that were never bulk-approved (ApprovalMethod NULL) -- any human review
--   status (an earlier individual decision counts as the human review). Members = the first @SampleSize of that population by
--   HASHBYTES('SHA2_256', CONCAT(Seed, CONVERT(NVARCHAR(36), PublicId))) then Id. The seed is server-generated
--   (CRYPT_GEN_RANDOM) unless a caller passes one (test-only path; the CLI never does) and is stored. Refused
--   'prior_sample_rejection' when an earlier (closed) sample of the run recorded or now shows any human rejection or any
--   second 'reject' (no sample shopping); 'too_few_clean' when the population is smaller than the size.
--   CloseAttachmentBackfillSample records HumanRejectedAtClose / SecondRejectedAtClose / HumanPendingAtClose /
--   SecondMissingAtClose. Rejections are STICKY per member (Candidate.RejectCount, incremented by the b2 review proc on every
--   reject and never decremented, exceeds the member's RejectCountAtDraw; EverRejectedSecond set by any recorded second
--   'reject'), and the close snapshots count them: the bulk gate (sample_has_rejection) and
--   prior_sample_rejection count them, so a reject later undone or overwritten still blocks. A draw is refused
--   'prior_sample_incomplete' after a sample was closed incomplete without an active bulk approval.
--   RecordAttachmentBackfillSampleSecondReviews writes a whole batch (JSON) of second verdicts ALL OR NOTHING, only to members
--   of an OPEN sample with no active bulk approval, and refuses 'second_review_complete' once every member has a verdict:
--   verdicts FREEZE when the second review completes, before the human side becomes visible.
--   ReadAttachmentBackfillSampleStatus returns the human counts / disagreements / linked as NULL until every member has a
--   second verdict (SecondComplete); BulkApproveAttachmentBackfillCandidates refuses with the generic
--   'second_review_incomplete' until then. The blind export (ReadAttachmentBackfillSampleMembers) never returns the human side.
--   BulkApproveAttachmentBackfillCandidates is ONE transaction per batch, locks run (UPDLOCK, HOLDLOCK; the b2 review procs read
--   the run WITH (UPDLOCK), so no human decision changes mid-batch) -> sample -> members/candidates, re-checks the gate, then
--   approves up to @BatchSize targets. Refusals, in order: invalid, not_found, run_not_ready, no_open_sample,
--   second_review_incomplete, prior_sample_rejection, sample_size_mismatch (members <> SampleSize), sample_has_rejection,
--   sample_incomplete (human pending or a member approved by bulk), bulk_partially_undone, bulk_not_found.
--   Targets = the open sample's POPULATION minus its members, still pending, unlinked, 'clean' now, bill exists, bill not
--   claimed by another non-cancelled run (NOT EXISTS under UPDLOCK, HOLDLOCK): a candidate that became clean after the draw is
--   never bulk-approved. Each is set approved with ApprovalMethod 'bulk' + BulkApprovalId (RowVersion bumps).
--   UndoAttachmentBackfillBulkApproval returns that approval's unlinked bulk-approved candidates to pending, subtracts them from
--   ApprovedCount, keeps linked ones and then marks the approval PartiallyUndone (bulk refuses until the undo completes);
--   the approval is marked undone only when no candidate still carries it. The b2 review / undo procs clear ApprovalMethod and
--   BulkApprovalId, so a bulk undo never reverts a decision a human made by hand.
--   Nothing here writes Attachment, BillLineItemAttachment, Bill, blob storage or any outbox.
-- Depends: dbo.attachment_backfill.sql and b4_apply.sql (applied first), dbo.Bill, dbo.Vendor, dbo.BillLineItem, dbo.Project.
-- Sprocs: BulkApproveAttachmentBackfillCandidates, CloseAttachmentBackfillSample, DrawAttachmentBackfillSample, ReadAttachmentBackfillSampleMembers, ReadAttachmentBackfillSampleStatus, RecordAttachmentBackfillSampleSecondReviews, UndoAttachmentBackfillBulkApproval
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/attachment_backfill/sql/b5_bulk.sql   (after b4_apply.sql)

GO

IF OBJECT_ID('dbo.AttachmentBackfillSample', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[AttachmentBackfillSample]
    (
        [Id]                    BIGINT           IDENTITY(1,1) NOT NULL,
        [PublicId]              UNIQUEIDENTIFIER NOT NULL CONSTRAINT [DF_AttachmentBackfillSample_PublicId] DEFAULT NEWID(),
        [RowVersion]            ROWVERSION       NOT NULL,
        [RunId]                 BIGINT           NOT NULL,
        [SampleSize]            INT              NOT NULL,
        [Seed]                  NVARCHAR(64)     NOT NULL,
        [Status]                NVARCHAR(16)     NOT NULL CONSTRAINT [DF_AttachmentBackfillSample_Status] DEFAULT N'open',
        [CreatedByUserId]       BIGINT           NOT NULL,
        [CreatedDatetime]       DATETIME2(3)     NOT NULL CONSTRAINT [DF_AttachmentBackfillSample_CreatedDatetime] DEFAULT SYSUTCDATETIME(),
        [ClosedDatetime]        DATETIME2(3)     NULL,
        [HumanRejectedAtClose]  INT              NULL,
        [SecondRejectedAtClose] INT              NULL,
        [HumanPendingAtClose]   INT              NULL,
        [SecondMissingAtClose]  INT              NULL,
        CONSTRAINT [PK_AttachmentBackfillSample] PRIMARY KEY CLUSTERED ([Id]),
        CONSTRAINT [UQ_AttachmentBackfillSample_PublicId] UNIQUE ([PublicId]),
        CONSTRAINT [FK_AttachmentBackfillSample_Run] FOREIGN KEY ([RunId]) REFERENCES dbo.[AttachmentBackfillRun]([Id]),
        CONSTRAINT [CK_AttachmentBackfillSample_Size] CHECK ([SampleSize] >= 1),
        CONSTRAINT [CK_AttachmentBackfillSample_Status] CHECK ([Status] IN (N'open', N'closed'))
    );
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillSample', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.indexes
       WHERE name = N'UX_AttachmentBackfillSample_OpenPerRun' AND object_id = OBJECT_ID(N'dbo.AttachmentBackfillSample')
   )
BEGIN
    CREATE UNIQUE NONCLUSTERED INDEX [UX_AttachmentBackfillSample_OpenPerRun]
        ON dbo.[AttachmentBackfillSample] ([RunId]) WHERE [Status] = N'open';
END
GO

-- The population a sample was drawn from (snapshot at draw time). Bulk targets come only from here.
IF OBJECT_ID('dbo.AttachmentBackfillSamplePopulation', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[AttachmentBackfillSamplePopulation]
    (
        [SampleId]    BIGINT NOT NULL,
        [CandidateId] BIGINT NOT NULL,
        CONSTRAINT [PK_AttachmentBackfillSamplePopulation] PRIMARY KEY CLUSTERED ([SampleId], [CandidateId]),
        CONSTRAINT [FK_AttachmentBackfillSamplePopulation_Sample] FOREIGN KEY ([SampleId]) REFERENCES dbo.[AttachmentBackfillSample]([Id]),
        CONSTRAINT [FK_AttachmentBackfillSamplePopulation_Candidate] FOREIGN KEY ([CandidateId]) REFERENCES dbo.[AttachmentBackfillCandidate]([Id])
    );
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillSampleMember', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[AttachmentBackfillSampleMember]
    (
        [Id]                     BIGINT           IDENTITY(1,1) NOT NULL,
        [SampleId]               BIGINT           NOT NULL,
        [CandidateId]            BIGINT           NOT NULL,
        [Ordinal]                INT              NOT NULL,
        [SecondReviewVerdict]    NVARCHAR(16)     NULL,
        [SecondReviewReason]     NVARCHAR(200)    NULL,
        [SecondReviewer]         NVARCHAR(50)     NULL,
        [SecondReviewedDatetime] DATETIME2(3)     NULL,
        -- Sticky rejections, never cleared: the candidate's RejectCount at draw time (a human reject while the sample is open
        -- makes Candidate.RejectCount exceed it -- the b2 review proc increments it, its undo does not decrement) and
        -- EverRejectedSecond (set by any recorded second 'reject', kept through an overwrite).
        [RejectCountAtDraw]      INT              NOT NULL CONSTRAINT [DF_AttachmentBackfillSampleMember_RejectCountAtDraw] DEFAULT 0,
        [EverRejectedSecond]     BIT              NOT NULL CONSTRAINT [DF_AttachmentBackfillSampleMember_EverRejectedSecond] DEFAULT 0,
        CONSTRAINT [PK_AttachmentBackfillSampleMember] PRIMARY KEY CLUSTERED ([Id]),
        CONSTRAINT [UQ_AttachmentBackfillSampleMember_Sample_Candidate] UNIQUE ([SampleId], [CandidateId]),
        CONSTRAINT [FK_AttachmentBackfillSampleMember_Sample] FOREIGN KEY ([SampleId]) REFERENCES dbo.[AttachmentBackfillSample]([Id]),
        CONSTRAINT [FK_AttachmentBackfillSampleMember_Candidate] FOREIGN KEY ([CandidateId]) REFERENCES dbo.[AttachmentBackfillCandidate]([Id]),
        CONSTRAINT [CK_AttachmentBackfillSampleMember_Verdict]
            CHECK ([SecondReviewVerdict] IS NULL OR [SecondReviewVerdict] IN (N'approve', N'reject'))
    );
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillSampleMember', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.indexes
       WHERE name = N'IX_AttachmentBackfillSampleMember_CandidateId'
         AND object_id = OBJECT_ID(N'dbo.AttachmentBackfillSampleMember')
   )
BEGIN
    CREATE NONCLUSTERED INDEX [IX_AttachmentBackfillSampleMember_CandidateId]
        ON dbo.[AttachmentBackfillSampleMember] ([CandidateId]);
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillBulkApproval', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[AttachmentBackfillBulkApproval]
    (
        [Id]               BIGINT           IDENTITY(1,1) NOT NULL,
        [PublicId]         UNIQUEIDENTIFIER NOT NULL CONSTRAINT [DF_AttachmentBackfillBulkApproval_PublicId] DEFAULT NEWID(),
        [RunId]            BIGINT           NOT NULL,
        [SampleId]         BIGINT           NOT NULL,
        [ApprovedByUserId] BIGINT           NOT NULL,
        [ApprovedCount]    INT              NOT NULL CONSTRAINT [DF_AttachmentBackfillBulkApproval_ApprovedCount] DEFAULT 0,
        [PartiallyUndone]  BIT              NOT NULL CONSTRAINT [DF_AttachmentBackfillBulkApproval_PartiallyUndone] DEFAULT 0,
        [CreatedDatetime]  DATETIME2(3)     NOT NULL CONSTRAINT [DF_AttachmentBackfillBulkApproval_CreatedDatetime] DEFAULT SYSUTCDATETIME(),
        [ModifiedDatetime] DATETIME2(3)     NOT NULL CONSTRAINT [DF_AttachmentBackfillBulkApproval_ModifiedDatetime] DEFAULT SYSUTCDATETIME(),
        [UndoneByUserId]   BIGINT           NULL,
        [UndoneDatetime]   DATETIME2(3)     NULL,
        CONSTRAINT [PK_AttachmentBackfillBulkApproval] PRIMARY KEY CLUSTERED ([Id]),
        CONSTRAINT [UQ_AttachmentBackfillBulkApproval_PublicId] UNIQUE ([PublicId]),
        CONSTRAINT [FK_AttachmentBackfillBulkApproval_Run] FOREIGN KEY ([RunId]) REFERENCES dbo.[AttachmentBackfillRun]([Id]),
        CONSTRAINT [FK_AttachmentBackfillBulkApproval_Sample] FOREIGN KEY ([SampleId]) REFERENCES dbo.[AttachmentBackfillSample]([Id])
    );
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.AttachmentBackfillCandidate', 'BulkApprovalId') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_AttachmentBackfillCandidate_BulkApproval')
BEGIN
    ALTER TABLE dbo.[AttachmentBackfillCandidate] ADD CONSTRAINT [FK_AttachmentBackfillCandidate_BulkApproval]
        FOREIGN KEY ([BulkApprovalId]) REFERENCES dbo.[AttachmentBackfillBulkApproval]([Id]);
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.indexes
       WHERE name = N'IX_AttachmentBackfillCandidate_BulkApprovalId'
         AND object_id = OBJECT_ID(N'dbo.AttachmentBackfillCandidate')
   )
BEGIN
    CREATE NONCLUSTERED INDEX [IX_AttachmentBackfillCandidate_BulkApprovalId]
        ON dbo.[AttachmentBackfillCandidate] ([BulkApprovalId]) WHERE [BulkApprovalId] IS NOT NULL;
END
GO


-- Draw the run's review sample. @Seed NULL/blank = server-generated (CRYPT_GEN_RANDOM, 32 hex chars) -- the only path the CLI
-- uses; a given seed is a test-only path for reproducibility. @DryRun = 1 decides and counts only (no write).
-- Returns one row (SamplePublicId, SampleSize, Seed, EligibleCount, Drawn, RefusedReason). Refused, in order: 'invalid' (size <
-- 1, no creator on a real draw), 'not_found', 'run_not_ready', 'sample_open', 'prior_sample_rejection' (sticky flags, close
-- snapshots or current state of any closed sample), 'prior_sample_incomplete' (a sample closed with human pending / second
-- missing and no active bulk approval), 'too_few_clean'.
-- The population is read ONCE (UPDLOCK, HOLDLOCK) into a table variable: its count and the members come from the same read.
-- Validate-first / always-COMMIT.
CREATE OR ALTER PROCEDURE dbo.DrawAttachmentBackfillSample
    @RunId           BIGINT,
    @SampleSize      INT,
    @Seed            NVARCHAR(64) = NULL,
    @CreatedByUserId BIGINT = NULL,
    @DryRun          BIT = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    SET @Seed = CASE WHEN DATALENGTH(LTRIM(RTRIM(@Seed))) > 0 THEN LTRIM(RTRIM(@Seed)) END;
    IF @Seed IS NULL
        SET @Seed = CONVERT(NVARCHAR(64), CRYPT_GEN_RANDOM(16), 2);

    BEGIN TRANSACTION;

    DECLARE @Found BIT = 0, @RunReady BIT = 0, @HasOpen BIT = 0, @PriorRejection BIT = 0, @Eligible INT = 0, @Drawn INT = 0,
            @SampleId BIGINT = NULL, @SamplePublicId UNIQUEIDENTIFIER = NULL;

    SELECT @Found = 1, @RunReady = CASE WHEN r.[Status] = N'ready' THEN 1 ELSE 0 END
    FROM dbo.[AttachmentBackfillRun] AS r WITH (UPDLOCK, HOLDLOCK)
    WHERE r.[Id] = @RunId;

    SET @HasOpen = CASE WHEN EXISTS (
        SELECT 1 FROM dbo.[AttachmentBackfillSample] AS s WITH (UPDLOCK, HOLDLOCK)
        WHERE s.[RunId] = @RunId AND s.[Status] = N'open'
    ) THEN 1 ELSE 0 END;

    -- No sample shopping: any rejection recorded at a close, or visible now, on an earlier sample of the run.
    SET @PriorRejection = CASE WHEN EXISTS (
        SELECT 1 FROM dbo.[AttachmentBackfillSample] AS s
        WHERE s.[RunId] = @RunId AND s.[Status] = N'closed'
          AND (ISNULL(s.[HumanRejectedAtClose], 0) > 0 OR ISNULL(s.[SecondRejectedAtClose], 0) > 0
               OR EXISTS (
                   SELECT 1 FROM dbo.[AttachmentBackfillSampleMember] AS m
                   INNER JOIN dbo.[AttachmentBackfillCandidate] AS c ON c.[Id] = m.[CandidateId]
                   WHERE m.[SampleId] = s.[Id]
                     AND (c.[RejectCount] > m.[RejectCountAtDraw] OR m.[EverRejectedSecond] = 1
                          OR c.[ReviewStatus] = N'rejected' OR m.[SecondReviewVerdict] = N'reject')
               ))
    ) THEN 1 ELSE 0 END;

    -- A sample closed while incomplete (human pending or second verdict missing) and never bulk-approved blocks a redraw:
    -- closing early cannot be used to fish for a cleaner sample.
    DECLARE @PriorIncomplete BIT = CASE WHEN EXISTS (
        SELECT 1 FROM dbo.[AttachmentBackfillSample] AS s
        WHERE s.[RunId] = @RunId AND s.[Status] = N'closed'
          AND (ISNULL(s.[HumanPendingAtClose], 0) > 0 OR ISNULL(s.[SecondMissingAtClose], 0) > 0)
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[AttachmentBackfillBulkApproval] AS ba
              WHERE ba.[SampleId] = s.[Id] AND ba.[UndoneDatetime] IS NULL
          )
    ) THEN 1 ELSE 0 END;

    -- The population, read once under lock. Same claim rule as the b2 review proc and the bulk targets.
    DECLARE @Pop TABLE ([Id] BIGINT NOT NULL PRIMARY KEY, [SortKey] VARBINARY(32) NOT NULL);
    INSERT INTO @Pop ([Id], [SortKey])
    SELECT c.[Id], HASHBYTES('SHA2_256', CONCAT(@Seed, CONVERT(NVARCHAR(36), c.[PublicId])))
    FROM dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK)
    CROSS APPLY dbo.AttachmentBackfillEvidenceGroup(
        c.[InvoiceSeenCount], c.[AmountSeenCount], c.[OtherInvoiceLabelCount], c.[StatementLike]
    ) AS eg
    WHERE c.[RunId] = @RunId
      AND eg.[EvidenceGroup] = N'clean'
      AND c.[AttachmentId] IS NULL
      AND (c.[ApprovalMethod] IS NULL OR c.[ApprovalMethod] <> N'bulk')
      AND EXISTS (SELECT 1 FROM dbo.[Bill] AS b WHERE b.[Id] = c.[BillId])
      AND NOT EXISTS (
          SELECT 1
          FROM dbo.[AttachmentBackfillCandidate] AS o WITH (UPDLOCK, HOLDLOCK)
          INNER JOIN dbo.[AttachmentBackfillRun] AS orun ON orun.[Id] = o.[RunId]
          WHERE o.[BillId] = c.[BillId]
            AND o.[RunId] <> c.[RunId]
            AND o.[ReviewStatus] IN (N'pending', N'approved')
            AND orun.[Status] <> N'cancelled'
      );
    SET @Eligible = (SELECT COUNT(*) FROM @Pop);

    DECLARE @Refused NVARCHAR(32) = CASE
        WHEN @SampleSize IS NULL OR @SampleSize < 1 OR (ISNULL(@DryRun, 0) = 0 AND @CreatedByUserId IS NULL) THEN N'invalid'
        WHEN @Found = 0 THEN N'not_found'
        WHEN @RunReady = 0 THEN N'run_not_ready'
        WHEN @HasOpen = 1 THEN N'sample_open'
        WHEN @PriorRejection = 1 THEN N'prior_sample_rejection'
        WHEN @PriorIncomplete = 1 THEN N'prior_sample_incomplete'
        WHEN @Eligible < @SampleSize THEN N'too_few_clean'
        ELSE NULL
    END;

    IF @Refused IS NULL AND ISNULL(@DryRun, 0) = 0
    BEGIN
        DECLARE @New TABLE ([Id] BIGINT, [PublicId] UNIQUEIDENTIFIER);
        INSERT INTO dbo.[AttachmentBackfillSample] ([RunId], [SampleSize], [Seed], [CreatedByUserId])
        OUTPUT INSERTED.[Id], INSERTED.[PublicId] INTO @New
        VALUES (@RunId, @SampleSize, @Seed, @CreatedByUserId);
        SELECT @SampleId = [Id], @SamplePublicId = [PublicId] FROM @New;

        INSERT INTO dbo.[AttachmentBackfillSamplePopulation] ([SampleId], [CandidateId])
        SELECT @SampleId, p.[Id] FROM @Pop AS p;

        INSERT INTO dbo.[AttachmentBackfillSampleMember] ([SampleId], [CandidateId], [Ordinal], [RejectCountAtDraw])
        -- A member that is REJECTED at draw time (pre-migration rows included: RejectCount 0) gets an at-draw count one
        -- lower, so it counts as rejected-during-the-sample until the end: undo + approve cannot make it look clean.
        SELECT @SampleId, d.[Id], d.[Ordinal], c.[RejectCount] - CASE WHEN c.[ReviewStatus] = N'rejected' THEN 1 ELSE 0 END
        FROM (
            SELECT TOP (@SampleSize) p.[Id], CAST(ROW_NUMBER() OVER (ORDER BY p.[SortKey], p.[Id]) AS INT) AS [Ordinal]
            FROM @Pop AS p
            ORDER BY p.[SortKey], p.[Id]
        ) AS d
        INNER JOIN dbo.[AttachmentBackfillCandidate] AS c ON c.[Id] = d.[Id];
        SET @Drawn = @@ROWCOUNT;
    END

    COMMIT TRANSACTION;

    SELECT @SamplePublicId AS [SamplePublicId], @SampleSize AS [SampleSize], @Seed AS [Seed], @Eligible AS [EligibleCount],
           @Drawn AS [Drawn], @Refused AS [RefusedReason];
END
GO

-- Close the run's open sample. Records HumanRejectedAtClose / SecondRejectedAtClose (a later draw or bulk on the run is refused
-- when either is > 0) and HumanPendingAtClose / SecondMissingAtClose (a later draw is refused 'prior_sample_incomplete' when
-- either is > 0 and the sample has no active bulk approval). Returns (SamplePublicId, Closed, the four counts,
-- RefusedReason); refused 'no_open_sample'.
CREATE OR ALTER PROCEDURE dbo.CloseAttachmentBackfillSample
    @RunId BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @SampleId BIGINT = NULL, @PublicId UNIQUEIDENTIFIER = NULL, @HumanRejected INT = NULL, @SecondRejected INT = NULL,
            @HumanPending INT = NULL, @SecondMissing INT = NULL;

    SELECT @SampleId = s.[Id], @PublicId = s.[PublicId]
    FROM dbo.[AttachmentBackfillSample] AS s WITH (UPDLOCK, HOLDLOCK)
    WHERE s.[RunId] = @RunId AND s.[Status] = N'open';

    IF @SampleId IS NOT NULL
    BEGIN
        SELECT
            @HumanRejected  = ISNULL(SUM(CASE WHEN c.[ReviewStatus] = N'rejected' OR c.[RejectCount] > m.[RejectCountAtDraw]
                                              THEN 1 ELSE 0 END), 0),
            @SecondRejected = ISNULL(SUM(CASE WHEN m.[SecondReviewVerdict] = N'reject' OR m.[EverRejectedSecond] = 1
                                              THEN 1 ELSE 0 END), 0),
            -- Not decided by a human: pending, or approved by bulk (not a human approval).
            @HumanPending   = ISNULL(SUM(CASE WHEN c.[ReviewStatus] = N'pending' OR c.[ApprovalMethod] = N'bulk' THEN 1 ELSE 0 END), 0),
            @SecondMissing  = ISNULL(SUM(CASE WHEN m.[SecondReviewVerdict] IS NULL THEN 1 ELSE 0 END), 0)
        FROM dbo.[AttachmentBackfillSampleMember] AS m
        INNER JOIN dbo.[AttachmentBackfillCandidate] AS c ON c.[Id] = m.[CandidateId]
        WHERE m.[SampleId] = @SampleId;

        UPDATE dbo.[AttachmentBackfillSample]
        SET [Status] = N'closed', [ClosedDatetime] = SYSUTCDATETIME(),
            [HumanRejectedAtClose] = @HumanRejected, [SecondRejectedAtClose] = @SecondRejected,
            [HumanPendingAtClose] = @HumanPending, [SecondMissingAtClose] = @SecondMissing
        WHERE [Id] = @SampleId;
    END

    COMMIT TRANSACTION;

    SELECT @PublicId AS [SamplePublicId],
           CAST(CASE WHEN @SampleId IS NULL THEN 0 ELSE 1 END AS BIT) AS [Closed],
           @HumanRejected AS [HumanRejectedAtClose], @SecondRejected AS [SecondRejectedAtClose],
           @HumanPending AS [HumanPendingAtClose], @SecondMissing AS [SecondMissingAtClose],
           CAST(CASE WHEN @SampleId IS NULL THEN N'no_open_sample' END AS NVARCHAR(32)) AS [RefusedReason];
END
GO

-- Record a batch of blind second-review verdicts, ALL OR NOTHING. @RowsJson = [{"candidate_public_id", "verdict", "reason"}].
-- Returns (Recorded, SecondMissing (after), BadRow (1-based, the first offending row), RefusedReason). Refused, in order:
-- 'invalid' (no reviewer, JSON not an array of rows, empty, a bad verdict, a reject without a reason, a reason over 200 chars,
-- a candidate twice), 'sample_not_found', 'sample_closed', 'bulk_approved' (an active or partially undone bulk approval exists),
-- 'second_review_complete' (every member already has a verdict: the verdicts are frozen), 'not_member'.
CREATE OR ALTER PROCEDURE dbo.RecordAttachmentBackfillSampleSecondReviews
    @SamplePublicId UNIQUEIDENTIFIER,
    @RowsJson       NVARCHAR(MAX),
    @Reviewer       NVARCHAR(50)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    DECLARE @Rows TABLE (
        [Seq] INT NOT NULL, [CandidatePublicId] UNIQUEIDENTIFIER NULL, [RawId] NVARCHAR(100) NULL,
        [Verdict] NVARCHAR(16) NULL, [Reason] NVARCHAR(400) NULL, [MemberId] BIGINT NULL
    );
    DECLARE @BadJson BIT = CASE WHEN @RowsJson IS NULL OR ISJSON(@RowsJson) = 0 OR LEFT(LTRIM(@RowsJson), 1) <> N'[' THEN 1 ELSE 0 END;
    IF @BadJson = 0
        INSERT INTO @Rows ([Seq], [CandidatePublicId], [RawId], [Verdict], [Reason])
        SELECT CAST(j.[key] AS INT) + 1, TRY_CONVERT(UNIQUEIDENTIFIER, r.[candidate_public_id]), r.[candidate_public_id],
               r.[verdict],
               -- DATALENGTH, not NULLIF(..., N''): under a SQL_* collation a string of supplementary characters (emoji)
               -- compares EQUAL to N'' and would be dropped as 'no reason'.
               CASE WHEN DATALENGTH(LTRIM(RTRIM(r.[reason]))) > 0 THEN LTRIM(RTRIM(r.[reason])) END
        FROM OPENJSON(@RowsJson) AS j
        CROSS APPLY OPENJSON(j.[value]) WITH (
            [candidate_public_id] NVARCHAR(100) '$.candidate_public_id',
            [verdict]             NVARCHAR(16)  '$.verdict',
            [reason]              NVARCHAR(400) '$.reason'
        ) AS r;

    BEGIN TRANSACTION;

    DECLARE @SampleId BIGINT = NULL, @SampleOpen BIT = 0, @BulkActive BIT = 0, @Missing INT = 0, @Recorded INT = 0;

    -- The sample row is the lock BulkApproveAttachmentBackfillCandidates also takes: verdicts cannot change mid-gate.
    SELECT @SampleId = s.[Id], @SampleOpen = CASE WHEN s.[Status] = N'open' THEN 1 ELSE 0 END
    FROM dbo.[AttachmentBackfillSample] AS s WITH (UPDLOCK, HOLDLOCK)
    WHERE s.[PublicId] = @SamplePublicId;

    SET @BulkActive = CASE WHEN EXISTS (
        SELECT 1 FROM dbo.[AttachmentBackfillBulkApproval] AS ba
        WHERE ba.[SampleId] = @SampleId AND ba.[UndoneDatetime] IS NULL
    ) THEN 1 ELSE 0 END;

    SELECT @Missing = COUNT(*)
    FROM dbo.[AttachmentBackfillSampleMember] AS m WITH (UPDLOCK, HOLDLOCK)
    WHERE m.[SampleId] = @SampleId AND m.[SecondReviewVerdict] IS NULL;

    UPDATE r SET [MemberId] = m.[Id]
    FROM @Rows AS r
    INNER JOIN dbo.[AttachmentBackfillCandidate] AS c ON c.[PublicId] = r.[CandidatePublicId]
    INNER JOIN dbo.[AttachmentBackfillSampleMember] AS m ON m.[CandidateId] = c.[Id] AND m.[SampleId] = @SampleId;

    DECLARE @BadRow INT = (
        SELECT MIN(r.[Seq]) FROM @Rows AS r
        WHERE r.[CandidatePublicId] IS NULL
           OR r.[Verdict] IS NULL OR r.[Verdict] NOT IN (N'approve', N'reject')
           OR (r.[Verdict] = N'reject' AND r.[Reason] IS NULL)
           OR LEN(ISNULL(r.[Reason], N'')) > 200
           OR (SELECT COUNT(*) FROM @Rows AS d WHERE d.[CandidatePublicId] = r.[CandidatePublicId]) > 1
    );
    DECLARE @NotMemberRow INT = (SELECT MIN(r.[Seq]) FROM @Rows AS r WHERE r.[MemberId] IS NULL);

    DECLARE @Refused NVARCHAR(32) = CASE
        WHEN ISNULL(DATALENGTH(LTRIM(RTRIM(@Reviewer))), 0) = 0 OR @BadJson = 1 OR NOT EXISTS (SELECT 1 FROM @Rows)
             OR @BadRow IS NOT NULL THEN N'invalid'
        WHEN @SampleId IS NULL THEN N'sample_not_found'
        WHEN @SampleOpen = 0 THEN N'sample_closed'
        WHEN @BulkActive = 1 THEN N'bulk_approved'
        WHEN @Missing = 0 THEN N'second_review_complete'
        WHEN @NotMemberRow IS NOT NULL THEN N'not_member'
        ELSE NULL
    END;

    IF @Refused IS NULL
    BEGIN
        UPDATE m
        SET [SecondReviewVerdict]    = r.[Verdict],
            [SecondReviewReason]     = r.[Reason],
            [SecondReviewer]         = LTRIM(RTRIM(@Reviewer)),
            [EverRejectedSecond]     = CASE WHEN r.[Verdict] = N'reject' THEN 1 ELSE m.[EverRejectedSecond] END,
            [SecondReviewedDatetime] = SYSUTCDATETIME()
        FROM dbo.[AttachmentBackfillSampleMember] AS m
        INNER JOIN @Rows AS r ON r.[MemberId] = m.[Id];
        SET @Recorded = @@ROWCOUNT;

        SELECT @Missing = COUNT(*)
        FROM dbo.[AttachmentBackfillSampleMember] AS m
        WHERE m.[SampleId] = @SampleId AND m.[SecondReviewVerdict] IS NULL;
    END

    COMMIT TRANSACTION;

    SELECT @Recorded AS [Recorded], @Missing AS [SecondMissing],
           CASE WHEN @Refused = N'invalid' THEN @BadRow WHEN @Refused = N'not_member' THEN @NotMemberRow END AS [BadRow],
           @Refused AS [RefusedReason];
END
GO

-- The run's sample: the OPEN one, else the latest. One row (no row when the run never had a sample). Second-review progress is
-- always returned; the human side (HumanPending/Approved/Rejected, Disagreements, Linked) is NULL until every member has a
-- second verdict (SecondComplete = 1), so the second review stays blind. Plus the sample's current bulk approval (PublicId,
-- ApprovedCount, PartiallyUndone; NULL when none).
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillSampleStatus
    @RunId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @SampleId BIGINT = (
        SELECT TOP (1) s.[Id] FROM dbo.[AttachmentBackfillSample] AS s
        WHERE s.[RunId] = @RunId
        ORDER BY CASE WHEN s.[Status] = N'open' THEN 0 ELSE 1 END, s.[Id] DESC
    );

    SELECT
        s.[PublicId] AS [SamplePublicId], s.[Status], s.[SampleSize], s.[Seed], s.[CreatedDatetime], s.[ClosedDatetime],
        agg.[Members], agg.[SecondMissing], agg.[SecondApprove], agg.[SecondReject],
        CAST(CASE WHEN agg.[Members] > 0 AND agg.[SecondMissing] = 0 THEN 1 ELSE 0 END AS BIT) AS [SecondComplete],
        CASE WHEN agg.[Members] > 0 AND agg.[SecondMissing] = 0 THEN agg.[HumanPending] END AS [HumanPending],
        CASE WHEN agg.[Members] > 0 AND agg.[SecondMissing] = 0 THEN agg.[HumanApproved] END AS [HumanApproved],
        CASE WHEN agg.[Members] > 0 AND agg.[SecondMissing] = 0 THEN agg.[HumanRejected] END AS [HumanRejected],
        CASE WHEN agg.[Members] > 0 AND agg.[SecondMissing] = 0 THEN agg.[Disagreements] END AS [Disagreements],
        CASE WHEN agg.[Members] > 0 AND agg.[SecondMissing] = 0 THEN agg.[Linked] END AS [Linked],
        CASE WHEN agg.[Members] > 0 AND agg.[SecondMissing] = 0 THEN agg.[StickyRejections] END AS [StickyRejections],
        ba.[PublicId] AS [BulkApprovalPublicId],
        -- Live: the candidates that still carry the approval (the stored ApprovedCount is kept as history).
        (SELECT COUNT(*) FROM dbo.[AttachmentBackfillCandidate] AS bc WHERE bc.[BulkApprovalId] = ba.[Id]) AS [BulkApprovedCount],
        ba.[PartiallyUndone] AS [BulkPartiallyUndone]
    FROM dbo.[AttachmentBackfillSample] AS s
    CROSS APPLY (
        SELECT
            COUNT(*) AS [Members],
            ISNULL(SUM(CASE WHEN c.[ReviewStatus] = N'pending' OR c.[ApprovalMethod] = N'bulk' THEN 1 ELSE 0 END), 0) AS [HumanPending],
            ISNULL(SUM(CASE WHEN c.[ReviewStatus] = N'approved' AND c.[ApprovalMethod] IS NULL THEN 1 ELSE 0 END), 0) AS [HumanApproved],
            ISNULL(SUM(CASE WHEN c.[ReviewStatus] = N'rejected' THEN 1 ELSE 0 END), 0) AS [HumanRejected],
            ISNULL(SUM(CASE WHEN m.[SecondReviewVerdict] IS NULL THEN 1 ELSE 0 END), 0) AS [SecondMissing],
            ISNULL(SUM(CASE WHEN m.[SecondReviewVerdict] = N'approve' THEN 1 ELSE 0 END), 0) AS [SecondApprove],
            ISNULL(SUM(CASE WHEN m.[SecondReviewVerdict] = N'reject' THEN 1 ELSE 0 END), 0) AS [SecondReject],
            ISNULL(SUM(CASE
                WHEN c.[ReviewStatus] = N'approved' AND m.[SecondReviewVerdict] = N'reject' THEN 1
                WHEN c.[ReviewStatus] = N'rejected' AND m.[SecondReviewVerdict] = N'approve' THEN 1
                ELSE 0 END), 0) AS [Disagreements],
            ISNULL(SUM(CASE WHEN c.[AttachmentId] IS NOT NULL THEN 1 ELSE 0 END), 0) AS [Linked],
            -- The bulk gate's sticky rule: rejected during the sample (by either reviewer), even if later undone/overwritten.
            ISNULL(SUM(CASE WHEN c.[RejectCount] > m.[RejectCountAtDraw] OR m.[EverRejectedSecond] = 1 THEN 1 ELSE 0 END), 0)
                AS [StickyRejections]
        FROM dbo.[AttachmentBackfillSampleMember] AS m
        INNER JOIN dbo.[AttachmentBackfillCandidate] AS c ON c.[Id] = m.[CandidateId]
        WHERE m.[SampleId] = s.[Id]
    ) AS agg
    OUTER APPLY (
        SELECT TOP (1) b.[Id], b.[PublicId], b.[PartiallyUndone]
        FROM dbo.[AttachmentBackfillBulkApproval] AS b
        WHERE b.[SampleId] = s.[Id] AND b.[UndoneDatetime] IS NULL
        ORDER BY b.[Id] DESC
    ) AS ba
    WHERE s.[Id] = @SampleId;
END
GO

-- The open sample's members for the BLIND second review export, in draw order. Deliberately carries NO human ReviewStatus,
-- reviewer, reject reason, approval method or second-review verdict. SourceFileId / SourceSha256 / SourceFileSize are for the
-- CLI's download and hash check only. Primary project = the same inline rule as ReadAttachmentBackfillQueue (lowest Name, then Id).
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillSampleMembers
    @RunId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        s.[PublicId] AS [SamplePublicId], m.[Ordinal], c.[PublicId] AS [CandidatePublicId],
        c.[SourceFileId], c.[SourceSha256], c.[SourceFileSize],
        b.[BillNumber], b.[TotalAmount], b.[BillDate], v.[Name] AS [VendorName], pp.[ProjectName],
        (SELECT COUNT(*) FROM dbo.[BillLineItem] AS li WHERE li.[BillId] = b.[Id]) AS [LineCount],
        CAST(CASE WHEN b.[Id] IS NULL THEN 0 ELSE 1 END AS BIT) AS [BillExists],
        c.[InvoiceSeenCount], c.[AmountSeenCount], c.[VendorSeen], c.[OtherInvoiceLabelCount], c.[StatementLike], c.[UsedOcr]
    FROM dbo.[AttachmentBackfillSample] AS s
    INNER JOIN dbo.[AttachmentBackfillSampleMember] AS m ON m.[SampleId] = s.[Id]
    INNER JOIN dbo.[AttachmentBackfillCandidate] AS c ON c.[Id] = m.[CandidateId]
    LEFT JOIN dbo.[Bill] AS b ON b.[Id] = c.[BillId]
    LEFT JOIN dbo.[Vendor] AS v ON v.[Id] = b.[VendorId]
    OUTER APPLY (
        -- Primary project; sibling copies: ReadAttachmentBackfillQueue, ReadAttachmentBackfillRunFacets,
        -- ReadAttachmentBackfillCandidateDetail.
        SELECT TOP (1) p.[Name] AS [ProjectName]
        FROM dbo.[BillLineItem] AS li
        INNER JOIN dbo.[Project] AS p ON p.[Id] = li.[ProjectId]
        WHERE li.[BillId] = b.[Id]
        ORDER BY p.[Name], p.[Id]
    ) AS pp
    WHERE s.[RunId] = @RunId AND s.[Status] = N'open'
    ORDER BY m.[Ordinal];
END
GO

-- One batch of the sample-gated bulk approval (the caller loops until Remaining = 0). @DryRun = 1 checks the gate and counts
-- only. Returns one row: BulkApprovalPublicId, Approved (this batch), Remaining (targets left after it), ExcludedBillMissing,
-- ExcludedClaimedElsewhere, SampleMembers, RefusedReason. Gate refusals, in order: 'invalid' (batch < 1, no approver on a real
-- batch), 'not_found', 'run_not_ready', 'no_open_sample', 'second_review_incomplete' (generic until every member has a second
-- verdict: the human side stays hidden), 'prior_sample_rejection', 'sample_size_mismatch', 'sample_has_rejection',
-- 'sample_incomplete' (human pending, or a member approved by bulk), 'bulk_partially_undone', 'bulk_not_found'.
-- Locks: run, then sample, then candidates (the b4 apply's run -> candidate order). Validate-first / always-COMMIT.
CREATE OR ALTER PROCEDURE dbo.BulkApproveAttachmentBackfillCandidates
    @RunId                BIGINT,
    @ApprovedByUserId     BIGINT = NULL,
    @BatchSize            INT = 500,
    @BulkApprovalPublicId UNIQUEIDENTIFIER = NULL,
    @DryRun               BIT = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Found BIT = 0, @RunReady BIT = 0, @SampleId BIGINT = NULL, @SampleSize INT = NULL, @Members INT = 0,
            @HumanNotApproved INT = 0, @HumanRejected INT = 0, @SecondMissing INT = 0, @SecondReject INT = 0,
            @PriorRejection BIT = 0, @Partial BIT = 0, @BulkId BIGINT = NULL, @BulkPublicId UNIQUEIDENTIFIER = NULL,
            @BulkMismatch BIT = 0, @Approved INT = 0;

    SELECT @Found = 1, @RunReady = CASE WHEN r.[Status] = N'ready' THEN 1 ELSE 0 END
    FROM dbo.[AttachmentBackfillRun] AS r WITH (UPDLOCK, HOLDLOCK)
    WHERE r.[Id] = @RunId;

    SELECT @SampleId = s.[Id], @SampleSize = s.[SampleSize]
    FROM dbo.[AttachmentBackfillSample] AS s WITH (UPDLOCK, HOLDLOCK)
    WHERE s.[RunId] = @RunId AND s.[Status] = N'open';

    SELECT
        @Members          = COUNT(*),
        -- A bulk-approved member is NOT a human approval.
        @HumanNotApproved = ISNULL(SUM(CASE WHEN c.[ReviewStatus] = N'approved' AND c.[ApprovalMethod] IS NULL THEN 0 ELSE 1 END), 0),
        -- Sticky flags count too: a reject later undone / overwritten still blocks the bulk.
        @HumanRejected    = ISNULL(SUM(CASE WHEN c.[ReviewStatus] = N'rejected' OR c.[RejectCount] > m.[RejectCountAtDraw] THEN 1 ELSE 0 END), 0),
        @SecondMissing    = ISNULL(SUM(CASE WHEN m.[SecondReviewVerdict] IS NULL THEN 1 ELSE 0 END), 0),
        @SecondReject     = ISNULL(SUM(CASE WHEN m.[SecondReviewVerdict] = N'reject' OR m.[EverRejectedSecond] = 1 THEN 1 ELSE 0 END), 0)
    FROM dbo.[AttachmentBackfillSampleMember] AS m WITH (UPDLOCK, HOLDLOCK)
    INNER JOIN dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK) ON c.[Id] = m.[CandidateId]
    WHERE m.[SampleId] = @SampleId;

    -- Any earlier (closed) sample of the run with a rejection recorded at its close, or visible now.
    SET @PriorRejection = CASE WHEN EXISTS (
        SELECT 1 FROM dbo.[AttachmentBackfillSample] AS s
        WHERE s.[RunId] = @RunId AND s.[Status] = N'closed'
          AND (ISNULL(s.[HumanRejectedAtClose], 0) > 0 OR ISNULL(s.[SecondRejectedAtClose], 0) > 0
               OR EXISTS (
                   SELECT 1 FROM dbo.[AttachmentBackfillSampleMember] AS m
                   INNER JOIN dbo.[AttachmentBackfillCandidate] AS c ON c.[Id] = m.[CandidateId]
                   WHERE m.[SampleId] = s.[Id]
                     AND (c.[RejectCount] > m.[RejectCountAtDraw] OR m.[EverRejectedSecond] = 1
                          OR c.[ReviewStatus] = N'rejected' OR m.[SecondReviewVerdict] = N'reject')
               ))
    ) THEN 1 ELSE 0 END;

    -- The sample's current bulk approval (not undone). Partially undone = refuse until its undo completes; otherwise later
    -- batches continue it, and a given PublicId must be exactly that one.
    SELECT TOP (1) @BulkId = ba.[Id], @BulkPublicId = ba.[PublicId], @Partial = ba.[PartiallyUndone]
    FROM dbo.[AttachmentBackfillBulkApproval] AS ba WITH (UPDLOCK, HOLDLOCK)
    WHERE ba.[SampleId] = @SampleId AND ba.[UndoneDatetime] IS NULL
    ORDER BY ba.[Id] DESC;
    SET @BulkMismatch = CASE
        WHEN @BulkApprovalPublicId IS NOT NULL AND (@BulkPublicId IS NULL OR @BulkPublicId <> @BulkApprovalPublicId) THEN 1
        ELSE 0 END;

    DECLARE @Refused NVARCHAR(32) = CASE
        WHEN @BatchSize IS NULL OR @BatchSize < 1 OR (ISNULL(@DryRun, 0) = 0 AND @ApprovedByUserId IS NULL) THEN N'invalid'
        WHEN @Found = 0 THEN N'not_found'
        WHEN @RunReady = 0 THEN N'run_not_ready'
        WHEN @SampleId IS NULL OR @Members = 0 THEN N'no_open_sample'
        WHEN @SecondMissing > 0 THEN N'second_review_incomplete'
        WHEN @PriorRejection = 1 THEN N'prior_sample_rejection'
        WHEN @Members <> @SampleSize THEN N'sample_size_mismatch'
        WHEN @HumanRejected > 0 OR @SecondReject > 0 THEN N'sample_has_rejection'
        WHEN @HumanNotApproved > 0 THEN N'sample_incomplete'
        WHEN @Partial = 1 THEN N'bulk_partially_undone'
        WHEN @BulkMismatch = 1 THEN N'bulk_not_found'
        ELSE NULL
    END;

    -- The open sample's population (snapshot at draw) minus its members, still pending, unlinked and clean now.
    DECLARE @Base TABLE ([Id] BIGINT NOT NULL PRIMARY KEY, [BillMissing] BIT NOT NULL, [Claimed] BIT NOT NULL);
    IF @SampleId IS NOT NULL AND @Members > 0
    BEGIN
        INSERT INTO @Base ([Id], [BillMissing], [Claimed])
        SELECT
            c.[Id],
            CASE WHEN EXISTS (SELECT 1 FROM dbo.[Bill] AS b WHERE b.[Id] = c.[BillId]) THEN 0 ELSE 1 END,
            -- Same claim rule (hinted) as UpdateAttachmentBackfillCandidateReview: one bill, one approval.
            CASE WHEN EXISTS (
                SELECT 1
                FROM dbo.[AttachmentBackfillCandidate] AS o WITH (UPDLOCK, HOLDLOCK)
                INNER JOIN dbo.[AttachmentBackfillRun] AS orun ON orun.[Id] = o.[RunId]
                WHERE o.[BillId] = c.[BillId]
                  AND o.[RunId] <> c.[RunId]
                  AND o.[ReviewStatus] IN (N'pending', N'approved')
                  AND orun.[Status] <> N'cancelled'
            ) THEN 1 ELSE 0 END
        FROM dbo.[AttachmentBackfillSamplePopulation] AS pop
        INNER JOIN dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK) ON c.[Id] = pop.[CandidateId]
        CROSS APPLY dbo.AttachmentBackfillEvidenceGroup(
            c.[InvoiceSeenCount], c.[AmountSeenCount], c.[OtherInvoiceLabelCount], c.[StatementLike]
        ) AS eg
        WHERE pop.[SampleId] = @SampleId
          AND c.[RunId] = @RunId
          AND c.[ReviewStatus] = N'pending'
          AND c.[AttachmentId] IS NULL
          AND eg.[EvidenceGroup] = N'clean'
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[AttachmentBackfillSampleMember] AS m
              WHERE m.[SampleId] = @SampleId AND m.[CandidateId] = c.[Id]
          );
    END

    DECLARE @Targets INT = (SELECT COUNT(*) FROM @Base WHERE [BillMissing] = 0 AND [Claimed] = 0);

    IF @Refused IS NULL AND ISNULL(@DryRun, 0) = 0 AND @Targets > 0
    BEGIN
        DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
        IF @BulkId IS NULL
        BEGIN
            DECLARE @New TABLE ([Id] BIGINT, [PublicId] UNIQUEIDENTIFIER);
            INSERT INTO dbo.[AttachmentBackfillBulkApproval] ([RunId], [SampleId], [ApprovedByUserId])
            OUTPUT INSERTED.[Id], INSERTED.[PublicId] INTO @New
            VALUES (@RunId, @SampleId, @ApprovedByUserId);
            SELECT @BulkId = [Id], @BulkPublicId = [PublicId] FROM @New;
        END

        UPDATE c
        SET [ReviewStatus]     = N'approved',
            [RejectReason]     = NULL,
            [ReviewedByUserId] = @ApprovedByUserId,
            [ReviewedDatetime] = @Now,
            [ApprovalMethod]   = N'bulk',
            [BulkApprovalId]   = @BulkId,
            [ModifiedDatetime] = @Now
        FROM dbo.[AttachmentBackfillCandidate] AS c
        WHERE c.[Id] IN (
                SELECT TOP (@BatchSize) t.[Id] FROM @Base AS t
                WHERE t.[BillMissing] = 0 AND t.[Claimed] = 0
                ORDER BY t.[Id]
            )
          AND c.[ReviewStatus] = N'pending'
          AND c.[AttachmentId] IS NULL;
        SET @Approved = @@ROWCOUNT;

        UPDATE dbo.[AttachmentBackfillBulkApproval]
        SET [ApprovedCount] = [ApprovedCount] + @Approved, [ModifiedDatetime] = @Now
        WHERE [Id] = @BulkId;
    END

    COMMIT TRANSACTION;

    -- Until the second review is complete, only the generic code and the member count leave this proc.
    DECLARE @Hide BIT = CASE WHEN @Refused = N'second_review_incomplete' THEN 1 ELSE 0 END;
    SELECT CASE WHEN @Hide = 0 THEN @BulkPublicId END AS [BulkApprovalPublicId],
           @Approved AS [Approved],
           @Targets - @Approved AS [Remaining],
           (SELECT COUNT(*) FROM @Base WHERE [BillMissing] = 1) AS [ExcludedBillMissing],
           (SELECT COUNT(*) FROM @Base WHERE [BillMissing] = 0 AND [Claimed] = 1) AS [ExcludedClaimedElsewhere],
           @Members AS [SampleMembers],
           @Refused AS [RefusedReason];
END
GO

-- Undo a bulk approval: its candidates that are still bulk-approved by it and unlinked go back to pending (reviewer, method and
-- BulkApprovalId cleared; UndoneByUserId / UndoneDatetime recorded) and are subtracted from ApprovedCount. Linked ones are kept
-- (KeptLinked; undo their link with b4 first, then re-run): the approval is then marked PartiallyUndone, and the bulk refuses
-- 'bulk_partially_undone' until the undo completes -- no silent re-approval of the reverted rows. The approval is marked undone
-- only when no candidate still carries it (Completed = 1). @DryRun = 1 counts only. Returns (BulkApprovalPublicId, Reverted,
-- KeptLinked, Completed, PartiallyUndone, RefusedReason); refused, in order: 'not_found', 'already_undone', 'run_not_ready'.
-- Locks: run, then the approval, then candidates.
CREATE OR ALTER PROCEDURE dbo.UndoAttachmentBackfillBulkApproval
    @BulkApprovalPublicId UNIQUEIDENTIFIER,
    @UndoneByUserId       BIGINT = NULL,
    @DryRun               BIT = 0
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @BulkId BIGINT = NULL, @RunId BIGINT = NULL, @Undone BIT = 0, @RunReady BIT = 0,
            @Reverted INT = 0, @KeptLinked INT = 0, @Revertible INT = 0, @Completed BIT = 0, @Partial BIT = 0;

    SELECT @RunId = ba.[RunId] FROM dbo.[AttachmentBackfillBulkApproval] AS ba WHERE ba.[PublicId] = @BulkApprovalPublicId;
    SELECT @RunReady = CASE WHEN r.[Status] = N'ready' THEN 1 ELSE 0 END
    FROM dbo.[AttachmentBackfillRun] AS r WITH (UPDLOCK, HOLDLOCK)
    WHERE r.[Id] = @RunId;
    SELECT @BulkId = ba.[Id], @Undone = CASE WHEN ba.[UndoneDatetime] IS NULL THEN 0 ELSE 1 END, @Partial = ba.[PartiallyUndone]
    FROM dbo.[AttachmentBackfillBulkApproval] AS ba WITH (UPDLOCK, HOLDLOCK)
    WHERE ba.[PublicId] = @BulkApprovalPublicId;

    SELECT
        @Revertible = ISNULL(SUM(CASE WHEN c.[AttachmentId] IS NULL AND c.[ReviewStatus] = N'approved' THEN 1 ELSE 0 END), 0),
        @KeptLinked = ISNULL(SUM(CASE WHEN c.[AttachmentId] IS NOT NULL THEN 1 ELSE 0 END), 0)
    FROM dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK)
    WHERE c.[BulkApprovalId] = @BulkId AND c.[ApprovalMethod] = N'bulk';

    DECLARE @Refused NVARCHAR(32) = CASE
        WHEN @BulkId IS NULL THEN N'not_found'
        WHEN @Undone = 1 THEN N'already_undone'
        WHEN @RunReady = 0 THEN N'run_not_ready'
        ELSE NULL
    END;

    IF @Refused IS NULL AND ISNULL(@DryRun, 0) = 0
    BEGIN
        DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

        UPDATE c
        SET [ReviewStatus]     = N'pending',
            [RejectReason]     = NULL,
            [ReviewedByUserId] = NULL,
            [ReviewedDatetime] = NULL,
            [ApprovalMethod]   = NULL,
            [BulkApprovalId]   = NULL,
            [UndoneByUserId]   = @UndoneByUserId,
            [UndoneDatetime]   = @Now,
            [ModifiedDatetime] = @Now
        FROM dbo.[AttachmentBackfillCandidate] AS c
        WHERE c.[BulkApprovalId] = @BulkId
          AND c.[ApprovalMethod] = N'bulk'
          AND c.[AttachmentId] IS NULL
          AND c.[ReviewStatus] = N'approved';
        SET @Reverted = @@ROWCOUNT;

        SET @Completed = CASE WHEN EXISTS (SELECT 1 FROM dbo.[AttachmentBackfillCandidate] AS c WHERE c.[BulkApprovalId] = @BulkId)
                              THEN 0 ELSE 1 END;
        IF @Completed = 0 SET @Partial = 1;

        UPDATE dbo.[AttachmentBackfillBulkApproval]
        SET [ApprovedCount]    = CASE WHEN [ApprovedCount] - @Reverted < 0 THEN 0 ELSE [ApprovedCount] - @Reverted END,
            [PartiallyUndone]  = @Partial,
            [UndoneByUserId]   = CASE WHEN @Completed = 1 THEN @UndoneByUserId ELSE [UndoneByUserId] END,
            [UndoneDatetime]   = CASE WHEN @Completed = 1 THEN @Now ELSE [UndoneDatetime] END,
            [ModifiedDatetime] = @Now
        WHERE [Id] = @BulkId;
    END
    ELSE IF @Refused IS NULL
    BEGIN
        SET @Reverted = @Revertible;  -- dry run: what an undo would revert now
        SET @Completed = CASE WHEN @KeptLinked = 0 THEN 1 ELSE 0 END;
    END

    COMMIT TRANSACTION;

    SELECT @BulkApprovalPublicId AS [BulkApprovalPublicId], @Reverted AS [Reverted], @KeptLinked AS [KeptLinked],
           @Completed AS [Completed], @Partial AS [PartiallyUndone], @Refused AS [RefusedReason];
END
GO
