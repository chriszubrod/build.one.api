-- Contract: dbo.AttachmentBackfillCandidateLink, dbo.AttachmentBackfillCreatedAttachment (+ b4 columns on dbo.AttachmentBackfillCandidate)
-- Owns: U-535b b4 apply/undo of APPROVED backfill candidates: the Bill -> PDF links (BillLineItemAttachment rows), the record of
--   exactly which link rows each candidate's apply created (AttachmentBackfillCandidateLink), and whether that apply created the
--   Attachment (AttachmentBackfillCandidate.AttachmentCreatedByApply).
-- Invariants:
--   ApplyAttachmentBackfillCandidateLink is ONE transaction. It takes locks in the order every attachment-side writer uses
--   (dbo.Attachment, then dbo.Bill -- the same order as CreateBillLineItemAttachment / DeleteAttachmentById), then the run, then
--   the candidate, all UPDLOCK + HOLDLOCK, re-verifies everything, and only then inserts one BillLineItemAttachment per bill line
--   (all to the one attachment), records each new link id in AttachmentBackfillCandidateLink, and sets the candidate's
--   AttachmentId / LinkedDatetime / AttachmentCreatedByApply. It is ADD-ONLY: no Bill, BillLineItem, amount, status or coding
--   change. A completed bill is allowed (the CLI system caller's add-only exemption from the completed-bill lock).
--   Refusal order (one row, LinkCount 0, RefusedReason): not_found, run_not_ready, linked, not_approved, stale (RowVersion),
--   bill_missing, no_line_items, bill_has_attachment (any line of the bill already has a BillLineItemAttachment: the bill gained a
--   document since generation), attachment_missing, attachment_hash_mismatch (Attachment.FileHash <> the candidate's checked
--   SourceSha256). The b2 review undo refuses 'linked' once AttachmentId is set, and this proc refuses 'not_approved' / 'stale'
--   after an undo, so the two can never both win.
--   The apply also records the bill's Status at link time (AttachmentBackfillCandidate.LinkedBillStatus).
--   UndoAttachmentBackfillCandidateLink deletes ONLY the BillLineItemAttachment rows recorded in AttachmentBackfillCandidateLink for
--   that candidate that still point at the candidate's attachment (a link that existed before the apply is never recorded, so it
--   is never removed), clears AttachmentId / LinkedDatetime / AttachmentCreatedByApply / LinkedBillStatus, leaves ReviewStatus
--   'approved', and moves an 'applied' run back to 'ready' in the same transaction (so it can be re-applied). Refusals, in order:
--   not_found, not_linked, attachment_pushed (Attachment.QboId set), bill_completed_since_apply (the bill is 'completed' now but
--   was not at link time: its completion carried this PDF into QBO / Box / packets). It never deletes an Attachment.
--   AttachmentBackfillCreatedAttachment is the durable registry of every attachment the apply CREATED (written right after the
--   Attachment row). DeleteAttachmentBackfillCreatedAttachment deletes one Attachment only when it has an open registry row, its
--   FileHash equals the caller's, its QboId is NULL, and no row references it in ANY user table (any schema) that has a foreign
--   key to dbo.Attachment or an int/bigint column named AttachmentId (string AttachmentId columns, e.g. MsMessageAttachment's
--   Graph ids, are not pointers) (fail closed: the dynamic count runs inside TRY, any error refuses),
--   except the attachment's own derived text indexes (AttachmentTextTokenHash, AttachmentTextFingerprint), deleted with it.
--   It closes the registry row and returns the blob URLs for the caller to delete AFTER the commit. Undo ends with a sweep of
--   the run's open registry rows.
--   CloseAttachmentBackfillRunIfDone locks the run row first (UPDLOCK, HOLDLOCK), then moves 'ready' to 'applied' only while no
--   candidate is pending or approved-unlinked (candidates read UPDLOCK, HOLDLOCK). The DB runs READ_COMMITTED_SNAPSHOT, so the
--   b2 review / review-undo UPDATEs read the run WITH (UPDLOCK): they wait for a closing transaction and then see
--   'applied' (run_not_ready); a review undo that commits first holds its candidate row, so the closure waits and sees it pending.
--   ReadAttachmentBackfillDatabaseSize is read-only: used bytes of the data files and the database's max size.
--   No outbox, SharePoint, Box, Excel, QBO or email write anywhere in this file.
-- Depends: dbo.attachment_backfill.sql (applied first), dbo.Attachment, dbo.Bill, dbo.BillLineItem, dbo.BillLineItemAttachment.
-- Sprocs: ApplyAttachmentBackfillCandidateLink, CloseAttachmentBackfillRunIfDone, DeleteAttachmentBackfillCreatedAttachment, ReadAttachmentBackfillAttachmentsByHash, ReadAttachmentBackfillCandidatesForApply, ReadAttachmentBackfillCreatedAttachmentsToSweep, ReadAttachmentBackfillDatabaseSize, ReadAttachmentBackfillLinkedCandidates, RegisterAttachmentBackfillCreatedAttachment, UndoAttachmentBackfillCandidateLink
--
-- Run (after dbo.attachment_backfill.sql): ./.venv/bin/python scripts/run_sql.py entities/attachment_backfill/sql/b4_apply.sql

GO

IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.AttachmentBackfillCandidate', 'AttachmentCreatedByApply') IS NULL
BEGIN
    ALTER TABLE dbo.[AttachmentBackfillCandidate] ADD [AttachmentCreatedByApply] BIT NULL;
END
GO

-- The bill's Status when the apply linked it: undo refuses to strip a bill that has been completed since (R1).
IF OBJECT_ID('dbo.AttachmentBackfillCandidate', 'U') IS NOT NULL
   AND COL_LENGTH('dbo.AttachmentBackfillCandidate', 'LinkedBillStatus') IS NULL
BEGIN
    ALTER TABLE dbo.[AttachmentBackfillCandidate] ADD [LinkedBillStatus] NVARCHAR(32) NULL;
END
GO

-- Durable registry of every Attachment the apply CREATED (R3), written right after the Attachment row. The only attachments
-- DeleteAttachmentBackfillCreatedAttachment will ever delete; undo sweeps the run's rows whose DeletedDatetime is NULL.
-- No FK (to the run or to Attachment): this bookkeeping must never block a drop or a hand delete.
IF OBJECT_ID('dbo.AttachmentBackfillCreatedAttachment', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[AttachmentBackfillCreatedAttachment]
    (
        [Id]              BIGINT         IDENTITY(1,1) NOT NULL,
        [RunId]           BIGINT         NOT NULL,
        [CandidateId]     BIGINT         NULL,
        [AttachmentId]    BIGINT         NOT NULL,
        [FileHash]        NVARCHAR(64)   NOT NULL,
        [BlobUrl]         NVARCHAR(2000) NULL,
        [CreatedDatetime] DATETIME2(3)   NOT NULL
            CONSTRAINT [DF_AttachmentBackfillCreatedAttachment_CreatedDatetime] DEFAULT SYSUTCDATETIME(),
        [DeletedDatetime] DATETIME2(3)   NULL,
        CONSTRAINT [PK_AttachmentBackfillCreatedAttachment] PRIMARY KEY CLUSTERED ([Id]),
        CONSTRAINT [UQ_AttachmentBackfillCreatedAttachment_AttachmentId] UNIQUE ([AttachmentId])
    );
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillCreatedAttachment', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.indexes
       WHERE name = N'IX_AttachmentBackfillCreatedAttachment_Run'
         AND object_id = OBJECT_ID(N'dbo.AttachmentBackfillCreatedAttachment')
   )
BEGIN
    CREATE NONCLUSTERED INDEX [IX_AttachmentBackfillCreatedAttachment_Run]
        ON dbo.[AttachmentBackfillCreatedAttachment] ([RunId], [DeletedDatetime]);
END
GO

-- One row per BillLineItemAttachment an apply created. Undo deletes exactly these. No FK to BillLineItemAttachment: a link a
-- user later removes by hand must not be blocked by this bookkeeping (the undo then simply finds nothing to delete).
IF OBJECT_ID('dbo.AttachmentBackfillCandidateLink', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[AttachmentBackfillCandidateLink]
    (
        [Id]                       BIGINT        IDENTITY(1,1) NOT NULL,
        [CandidateId]              BIGINT        NOT NULL,
        [BillLineItemAttachmentId] BIGINT        NOT NULL,
        [CreatedDatetime]          DATETIME2(3)  NOT NULL
            CONSTRAINT [DF_AttachmentBackfillCandidateLink_CreatedDatetime] DEFAULT SYSUTCDATETIME(),
        CONSTRAINT [PK_AttachmentBackfillCandidateLink] PRIMARY KEY CLUSTERED ([Id]),
        CONSTRAINT [UQ_AttachmentBackfillCandidateLink_Blia] UNIQUE ([BillLineItemAttachmentId]),
        CONSTRAINT [FK_AttachmentBackfillCandidateLink_Candidate]
            FOREIGN KEY ([CandidateId]) REFERENCES dbo.[AttachmentBackfillCandidate]([Id])
    );
END
GO

IF OBJECT_ID('dbo.AttachmentBackfillCandidateLink', 'U') IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM sys.indexes
       WHERE name = N'IX_AttachmentBackfillCandidateLink_CandidateId'
         AND object_id = OBJECT_ID(N'dbo.AttachmentBackfillCandidateLink')
   )
BEGIN
    CREATE NONCLUSTERED INDEX [IX_AttachmentBackfillCandidateLink_CandidateId]
        ON dbo.[AttachmentBackfillCandidateLink] ([CandidateId]);
END
GO

-- Read-only. UsedBytes = allocated pages of the data files; MaxSizeBytes = DATABASEPROPERTYEX MaxSizeInBytes (Azure SQL), else
-- the data files' max_size when every one is capped, else NULL (unknown: the CLI refuses to apply).
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillDatabaseSize
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Used BIGINT, @Max BIGINT, @FileMax BIGINT, @Uncapped INT;

    SELECT @Used = SUM(CAST(FILEPROPERTY(f.[name], 'SpaceUsed') AS BIGINT)) * 8192,
           @FileMax = SUM(CASE WHEN f.[max_size] > 0 THEN CAST(f.[max_size] AS BIGINT) * 8192 ELSE 0 END),
           @Uncapped = SUM(CASE WHEN f.[max_size] IN (-1, 0) THEN 1 ELSE 0 END)
    FROM sys.database_files AS f
    WHERE f.[type] = 0;

    SET @Max = TRY_CAST(DATABASEPROPERTYEX(DB_NAME(), 'MaxSizeInBytes') AS BIGINT);
    IF @Max IS NULL OR @Max <= 0
        SET @Max = CASE WHEN @Uncapped = 0 AND @FileMax > 0 THEN @FileMax ELSE NULL END;

    SELECT @Used AS [UsedBytes], @Max AS [MaxSizeBytes];
END
GO

-- Approved, unlinked candidates of one run, keyset-paged by Id, with the DB facts the apply's pre-checks need.
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillCandidatesForApply
    @RunId   BIGINT,
    @AfterId BIGINT = 0,
    @Top     INT = 100
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@Top)
        c.[Id], c.[PublicId], c.[RowVersion], c.[BillId], c.[SourceFileId], c.[SourceFileName], c.[SourceFileSize],
        c.[SourceSha256],
        CAST(CASE WHEN b.[Id] IS NULL THEN 0 ELSE 1 END AS BIT) AS [BillExists],
        (SELECT COUNT(*) FROM dbo.[BillLineItem] AS li WHERE li.[BillId] = c.[BillId]) AS [LineItemCount],
        (SELECT COUNT(*)
         FROM dbo.[BillLineItemAttachment] AS blia
         INNER JOIN dbo.[BillLineItem] AS li ON li.[Id] = blia.[BillLineItemId]
         WHERE li.[BillId] = c.[BillId]) AS [BillAttachmentCount]
    FROM dbo.[AttachmentBackfillCandidate] AS c
    LEFT JOIN dbo.[Bill] AS b ON b.[Id] = c.[BillId]
    WHERE c.[RunId] = @RunId
      AND c.[ReviewStatus] = N'approved'
      AND c.[AttachmentId] IS NULL
      AND c.[Id] > @AfterId
    ORDER BY c.[Id];
END
GO

-- Every Attachment row with this FileHash (case-insensitive), lowest Id first, with the facts the reuse filter needs (R5).
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillAttachmentsByHash
    @FileHash NVARCHAR(64)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT a.[Id], a.[IsArchived], a.[Status], a.[ExpirationDate], a.[ExtractionStatus], a.[QboId]
    FROM dbo.[Attachment] AS a
    WHERE a.[FileHash] = LOWER(LTRIM(RTRIM(@FileHash)))
       OR a.[FileHash] = UPPER(LTRIM(RTRIM(@FileHash)))
    ORDER BY a.[Id];
END
GO

-- Record an attachment the apply just created (R3). Idempotent on AttachmentId (the existing row is returned).
CREATE OR ALTER PROCEDURE dbo.RegisterAttachmentBackfillCreatedAttachment
    @RunId        BIGINT,
    @CandidateId  BIGINT = NULL,
    @AttachmentId BIGINT,
    @FileHash     NVARCHAR(64),
    @BlobUrl      NVARCHAR(2000) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    IF NOT EXISTS (
        SELECT 1 FROM dbo.[AttachmentBackfillCreatedAttachment] WITH (UPDLOCK, HOLDLOCK) WHERE [AttachmentId] = @AttachmentId
    )
        INSERT INTO dbo.[AttachmentBackfillCreatedAttachment] ([RunId], [CandidateId], [AttachmentId], [FileHash], [BlobUrl])
        VALUES (@RunId, @CandidateId, @AttachmentId, LOWER(LTRIM(RTRIM(@FileHash))), @BlobUrl);

    COMMIT TRANSACTION;

    SELECT [Id], [RunId], [CandidateId], [AttachmentId], [DeletedDatetime]
    FROM dbo.[AttachmentBackfillCreatedAttachment]
    WHERE [AttachmentId] = @AttachmentId;
END
GO

-- The run's registered attachments not yet deleted (the undo sweep), lowest AttachmentId first.
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillCreatedAttachmentsToSweep
    @RunId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT ca.[AttachmentId], ca.[FileHash]
    FROM dbo.[AttachmentBackfillCreatedAttachment] AS ca
    WHERE ca.[RunId] = @RunId AND ca.[DeletedDatetime] IS NULL
    ORDER BY ca.[AttachmentId];
END
GO

-- See the header for the lock order and the refusal order. Returns one row:
-- (CandidateId, AttachmentId, LinkCount, AttachmentCreatedByApply, RefusedReason); RefusedReason NULL = linked.
-- Validate-first / always-COMMIT (CLAUDE.md): nothing is written unless every check passes.
CREATE OR ALTER PROCEDURE dbo.ApplyAttachmentBackfillCandidateLink
    @CandidateId       BIGINT,
    @RowVersion        BINARY(8),
    @AttachmentId      BIGINT,
    @AttachmentCreated BIT,
    @CreatedByUserId   BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;
    -- Any runtime error aborts and rolls back the whole transaction: never a half-applied link set.
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Locked BIT, @BillId BIGINT, @RunId BIGINT;

    -- 1. Attachment, then Bill: the order CreateBillLineItemAttachment and DeleteAttachmentById use.
    SELECT @Locked = 1 FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = @AttachmentId;

    SELECT @BillId = c.[BillId], @RunId = c.[RunId]
    FROM dbo.[AttachmentBackfillCandidate] AS c
    WHERE c.[Id] = @CandidateId;

    SELECT @Locked = 1 FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = @BillId;
    SELECT @Locked = 1 FROM dbo.[AttachmentBackfillRun] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = @RunId;

    -- 2. Re-read everything under the locks.
    DECLARE @Found BIT = 0, @RunReady BIT = 0, @Linked BIT = 0, @Approved BIT = 0, @Fresh BIT = 0,
            @BillExists BIT = 0, @LineCount INT = 0, @BillHasAttachment BIT = 0,
            @AttachmentExists BIT = 0, @HashMatches BIT = 0, @SourceSha CHAR(64) = NULL, @BillStatus NVARCHAR(32) = NULL;

    SELECT
        @Found     = 1,
        @RunReady  = CASE WHEN r.[Status] = N'ready' THEN 1 ELSE 0 END,
        @Linked    = CASE WHEN c.[AttachmentId] IS NOT NULL THEN 1 ELSE 0 END,
        @Approved  = CASE WHEN c.[ReviewStatus] = N'approved' THEN 1 ELSE 0 END,
        @Fresh     = CASE WHEN c.[RowVersion] = @RowVersion THEN 1 ELSE 0 END,
        @SourceSha = c.[SourceSha256]
    FROM dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK)
    INNER JOIN dbo.[AttachmentBackfillRun] AS r ON r.[Id] = c.[RunId]
    WHERE c.[Id] = @CandidateId;

    SELECT @BillExists = 1, @BillStatus = b.[Status] FROM dbo.[Bill] AS b WHERE b.[Id] = @BillId;
    SELECT @LineCount = COUNT(*) FROM dbo.[BillLineItem] WITH (UPDLOCK, HOLDLOCK) WHERE [BillId] = @BillId;
    SET @BillHasAttachment = CASE WHEN EXISTS (
        SELECT 1
        FROM dbo.[BillLineItemAttachment] AS blia WITH (UPDLOCK, HOLDLOCK)
        INNER JOIN dbo.[BillLineItem] AS li ON li.[Id] = blia.[BillLineItemId]
        WHERE li.[BillId] = @BillId
    ) THEN 1 ELSE 0 END;
    SELECT @AttachmentExists = 1,
           @HashMatches = CASE WHEN LOWER(LTRIM(RTRIM(a.[FileHash]))) = LOWER(LTRIM(RTRIM(@SourceSha))) THEN 1 ELSE 0 END
    FROM dbo.[Attachment] AS a
    WHERE a.[Id] = @AttachmentId;

    DECLARE @Refused NVARCHAR(32) = CASE
        WHEN @Found = 0 THEN N'not_found'
        WHEN @RunReady = 0 THEN N'run_not_ready'
        WHEN @Linked = 1 THEN N'linked'
        WHEN @Approved = 0 THEN N'not_approved'
        WHEN @Fresh = 0 THEN N'stale'
        WHEN @BillExists = 0 THEN N'bill_missing'
        WHEN @LineCount = 0 THEN N'no_line_items'
        WHEN @BillHasAttachment = 1 THEN N'bill_has_attachment'
        WHEN @AttachmentExists = 0 THEN N'attachment_missing'
        WHEN @HashMatches = 0 THEN N'attachment_hash_mismatch'
        ELSE NULL
    END;

    DECLARE @Links TABLE ([BlliaId] BIGINT NOT NULL);

    IF @Refused IS NULL
    BEGIN
        DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

        INSERT INTO dbo.[BillLineItemAttachment]
            ([CreatedDatetime], [ModifiedDatetime], [BillLineItemId], [AttachmentId], [CreatedByUserId])
        OUTPUT INSERTED.[Id] INTO @Links ([BlliaId])
        SELECT @Now, @Now, li.[Id], @AttachmentId, COALESCE(@CreatedByUserId, 17)
        FROM dbo.[BillLineItem] AS li
        WHERE li.[BillId] = @BillId;

        INSERT INTO dbo.[AttachmentBackfillCandidateLink] ([CandidateId], [BillLineItemAttachmentId], [CreatedDatetime])
        SELECT @CandidateId, l.[BlliaId], @Now
        FROM @Links AS l;

        UPDATE dbo.[AttachmentBackfillCandidate]
        SET [AttachmentId]             = @AttachmentId,
            [LinkedDatetime]           = @Now,
            [AttachmentCreatedByApply] = CASE WHEN @AttachmentCreated = 1 THEN 1 ELSE 0 END,
            [LinkedBillStatus]         = @BillStatus,
            [ModifiedDatetime]         = @Now
        WHERE [Id] = @CandidateId;
    END

    COMMIT TRANSACTION;

    SELECT @CandidateId AS [CandidateId],
           CASE WHEN @Refused IS NULL THEN @AttachmentId END AS [AttachmentId],
           (SELECT COUNT(*) FROM @Links) AS [LinkCount],
           CAST(CASE WHEN @Refused IS NULL THEN @AttachmentCreated END AS BIT) AS [AttachmentCreatedByApply],
           @Refused AS [RefusedReason];
END
GO

-- Linked candidates of one run (for undo), keyset-paged by Id, with how many link rows each apply recorded.
CREATE OR ALTER PROCEDURE dbo.ReadAttachmentBackfillLinkedCandidates
    @RunId   BIGINT,
    @AfterId BIGINT = 0,
    @Top     INT = 100
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP (@Top)
        c.[Id], c.[AttachmentId], c.[AttachmentCreatedByApply], c.[SourceSha256],
        (SELECT COUNT(*) FROM dbo.[AttachmentBackfillCandidateLink] AS l WHERE l.[CandidateId] = c.[Id]) AS [RecordedLinkCount]
    FROM dbo.[AttachmentBackfillCandidate] AS c
    WHERE c.[RunId] = @RunId
      AND c.[AttachmentId] IS NOT NULL
      AND c.[Id] > @AfterId
    ORDER BY c.[Id];
END
GO

-- Undo one candidate's apply (see the header). Returns one row:
-- (CandidateId, AttachmentId, AttachmentCreatedByApply, LinksDeleted, RefusedReason); refusals, in order: not_found,
-- not_linked, attachment_pushed (Attachment.QboId set), bill_completed_since_apply (completed now, not at link time).
CREATE OR ALTER PROCEDURE dbo.UndoAttachmentBackfillCandidateLink
    @CandidateId BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    -- Any runtime error aborts and rolls back the whole transaction: never a half-applied link set.
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

    DECLARE @Locked BIT, @AttachmentId BIGINT, @BillId BIGINT, @RunId BIGINT;

    SELECT @AttachmentId = c.[AttachmentId], @BillId = c.[BillId], @RunId = c.[RunId]
    FROM dbo.[AttachmentBackfillCandidate] AS c
    WHERE c.[Id] = @CandidateId;

    -- Same order as the apply: Attachment, Bill, run, candidate.
    SELECT @Locked = 1 FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = @AttachmentId;
    SELECT @Locked = 1 FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = @BillId;
    SELECT @Locked = 1 FROM dbo.[AttachmentBackfillRun] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = @RunId;

    DECLARE @Found BIT = 0, @CurrentAttachmentId BIGINT = NULL, @CreatedByApply BIT = NULL,
            @LinkedBillStatus NVARCHAR(32) = NULL, @BillStatus NVARCHAR(32) = NULL, @Pushed BIT = 0;
    SELECT @Found = 1, @CurrentAttachmentId = c.[AttachmentId], @CreatedByApply = c.[AttachmentCreatedByApply],
           @LinkedBillStatus = c.[LinkedBillStatus]
    FROM dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK)
    WHERE c.[Id] = @CandidateId;
    SELECT @BillStatus = b.[Status] FROM dbo.[Bill] AS b WHERE b.[Id] = @BillId;
    SELECT @Pushed = CASE WHEN a.[QboId] IS NOT NULL THEN 1 ELSE 0 END FROM dbo.[Attachment] AS a WHERE a.[Id] = @AttachmentId;

    -- R1: never strip evidence from a bill completed (and so pushed / packeted) after the link, nor an attachment that
    -- has been uploaded to QBO. Order: not_found, not_linked, attachment_pushed, bill_completed_since_apply.
    DECLARE @Refused NVARCHAR(32) = CASE
        WHEN @Found = 0 THEN N'not_found'
        WHEN @CurrentAttachmentId IS NULL OR @CurrentAttachmentId <> @AttachmentId THEN N'not_linked'
        WHEN @Pushed = 1 THEN N'attachment_pushed'
        WHEN @BillStatus = N'completed' AND ISNULL(@LinkedBillStatus, N'') <> N'completed' THEN N'bill_completed_since_apply'
        ELSE NULL
    END;

    DECLARE @Deleted TABLE ([Id] BIGINT NOT NULL);

    IF @Refused IS NULL
    BEGIN
        DELETE blia
        OUTPUT DELETED.[Id] INTO @Deleted ([Id])
        FROM dbo.[BillLineItemAttachment] AS blia
        INNER JOIN dbo.[AttachmentBackfillCandidateLink] AS l ON l.[BillLineItemAttachmentId] = blia.[Id]
        WHERE l.[CandidateId] = @CandidateId
          AND blia.[AttachmentId] = @AttachmentId;

        DELETE FROM dbo.[AttachmentBackfillCandidateLink] WHERE [CandidateId] = @CandidateId;

        UPDATE dbo.[AttachmentBackfillCandidate]
        SET [AttachmentId]             = NULL,
            [LinkedDatetime]           = NULL,
            [AttachmentCreatedByApply] = NULL,
            [LinkedBillStatus]         = NULL,
            [ModifiedDatetime]         = SYSUTCDATETIME()
        WHERE [Id] = @CandidateId;

        -- R7: an 'applied' run with an unlinked approved candidate is no longer done: reopen it so it can be re-applied
        -- (and reviewed). 'ready' and 'cancelled' runs are left as they are.
        UPDATE dbo.[AttachmentBackfillRun]
        SET [Status] = N'ready', [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] = @RunId AND [Status] = N'applied';
    END

    COMMIT TRANSACTION;

    SELECT @CandidateId AS [CandidateId],
           CASE WHEN @Refused IS NULL THEN @AttachmentId END AS [AttachmentId],
           CASE WHEN @Refused IS NULL THEN @CreatedByApply END AS [AttachmentCreatedByApply],
           (SELECT COUNT(*) FROM @Deleted) AS [LinksDeleted],
           @Refused AS [RefusedReason];
END
GO

-- Delete one attachment this tool created, when nothing references it (see the header). Returns one row:
-- (Deleted BIT, BlobUrl, ExtractedTextBlobUrl, RefusedReason). Refusals: not_registered (no open registry row -- this proc
-- never deletes an attachment the apply did not create), already_gone (the row no longer exists; the registry row is
-- closed), not_found (FileHash differs), attachment_pushed (QboId set), referenced, error (anything unexpected: fail closed).
CREATE OR ALTER PROCEDURE dbo.DeleteAttachmentBackfillCreatedAttachment
    @AttachmentId BIGINT,
    @FileHash     NVARCHAR(64)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT OFF;

    BEGIN TRANSACTION;

    DECLARE @Refused NVARCHAR(32) = NULL, @Registered BIT = 0, @Exists BIT = 0, @HashOk BIT = 0, @Pushed BIT = 0,
            @BlobUrl NVARCHAR(MAX) = NULL, @TextBlobUrl NVARCHAR(MAX) = NULL, @Saved BIT = 0, @Refs BIGINT = 0;

    SELECT @Registered = 1
    FROM dbo.[AttachmentBackfillCreatedAttachment] WITH (UPDLOCK, HOLDLOCK)
    WHERE [AttachmentId] = @AttachmentId AND [DeletedDatetime] IS NULL;

    SELECT @Exists = 1,
           @HashOk = CASE WHEN LOWER(LTRIM(RTRIM(a.[FileHash]))) = LOWER(LTRIM(RTRIM(@FileHash))) THEN 1 ELSE 0 END,
           @Pushed = CASE WHEN a.[QboId] IS NOT NULL THEN 1 ELSE 0 END,
           @BlobUrl = a.[BlobUrl], @TextBlobUrl = a.[ExtractedTextBlobUrl]
    FROM dbo.[Attachment] AS a WITH (UPDLOCK, HOLDLOCK)
    WHERE a.[Id] = @AttachmentId;

    SET @Refused = CASE
        WHEN @Registered = 0 THEN N'not_registered'
        WHEN @Exists = 0 THEN N'already_gone'
        WHEN @HashOk = 0 THEN N'not_found'
        WHEN @Pushed = 1 THEN N'attachment_pushed'
        ELSE NULL
    END;

    IF @Refused IS NULL
    BEGIN
        BEGIN TRY
            -- R2, fail closed: every user table in any schema that has a foreign key to dbo.Attachment OR an int/bigint
            -- column named AttachmentId (loose pointers: box.File, BillIntakePrediction, CodingSuggestion*, ExpenseReceiptMatch, ...),
            -- except the attachment's own derived text indexes (deleted with it) and this tool's registry.
            DECLARE @Sql NVARCHAR(MAX), @FoundRefs BIGINT = 0;
            SELECT @Sql = N'SELECT @n = CAST(0 AS BIGINT)' + ISNULL(STRING_AGG(CAST(
                       N' + (SELECT COUNT_BIG(*) FROM ' + QUOTENAME(x.[SchemaName]) + N'.' + QUOTENAME(x.[TableName])
                       + N' WHERE ' + QUOTENAME(x.[ColumnName]) + N' = @id)' AS NVARCHAR(MAX)), N''), N'')
            FROM (
                SELECT DISTINCT SCHEMA_NAME(t.[schema_id]) AS [SchemaName], t.[name] AS [TableName], col.[name] AS [ColumnName]
                FROM sys.tables AS t
                INNER JOIN sys.columns AS col ON col.[object_id] = t.[object_id]
                LEFT JOIN sys.foreign_key_columns AS fkc
                    ON fkc.[parent_object_id] = col.[object_id] AND fkc.[parent_column_id] = col.[column_id]
                   AND fkc.[referenced_object_id] = OBJECT_ID(N'dbo.Attachment')
                WHERE t.[is_ms_shipped] = 0
                  -- A name match counts only for an integer column: dbo.MsMessageAttachment.AttachmentId is NVARCHAR(500)
                  -- holding Graph ids, and comparing it to a BIGINT would convert the column and fail (error 245) on
                  -- every delete. FK-discovered columns count whatever their type.
                  AND ((col.[name] = N'AttachmentId' AND TYPE_NAME(col.[system_type_id]) IN (N'int', N'bigint'))
                       OR fkc.[parent_object_id] IS NOT NULL)
                  AND t.[object_id] NOT IN (
                        ISNULL(OBJECT_ID(N'dbo.AttachmentTextTokenHash'), 0),
                        ISNULL(OBJECT_ID(N'dbo.AttachmentTextFingerprint'), 0),
                        ISNULL(OBJECT_ID(N'dbo.AttachmentBackfillCreatedAttachment'), 0)
                  )
            ) AS x;
            EXEC sp_executesql @Sql, N'@id BIGINT, @n BIGINT OUTPUT', @id = @AttachmentId, @n = @FoundRefs OUTPUT;
            SET @Refs = ISNULL(@FoundRefs, 1);  -- an unreadable count is a reference

            IF @Refs > 0
                SET @Refused = N'referenced';
            ELSE
            BEGIN
                SAVE TRANSACTION [DeleteBackfillAttachment];
                SET @Saved = 1;
                IF OBJECT_ID(N'dbo.AttachmentTextTokenHash', 'U') IS NOT NULL
                    EXEC sp_executesql N'DELETE FROM dbo.[AttachmentTextTokenHash] WHERE [AttachmentId] = @id',
                                       N'@id BIGINT', @id = @AttachmentId;
                IF OBJECT_ID(N'dbo.AttachmentTextFingerprint', 'U') IS NOT NULL
                    EXEC sp_executesql N'DELETE FROM dbo.[AttachmentTextFingerprint] WHERE [AttachmentId] = @id',
                                       N'@id BIGINT', @id = @AttachmentId;
                DELETE FROM dbo.[Attachment] WHERE [Id] = @AttachmentId;
            END
        END TRY
        BEGIN CATCH
            IF @Saved = 1 AND XACT_STATE() = 1
                ROLLBACK TRANSACTION [DeleteBackfillAttachment];
            SET @Refused = CASE WHEN ERROR_NUMBER() = 547 THEN N'referenced' ELSE N'error' END;
        END CATCH
    END

    -- The registry row closes when the attachment is gone (deleted now, or already gone).
    IF (@Refused IS NULL OR @Refused = N'already_gone') AND XACT_STATE() = 1
        UPDATE dbo.[AttachmentBackfillCreatedAttachment]
        SET [DeletedDatetime] = SYSUTCDATETIME()
        WHERE [AttachmentId] = @AttachmentId AND [DeletedDatetime] IS NULL;

    IF XACT_STATE() = -1
    BEGIN
        -- Doomed: nothing can be committed. Rolling back raises 266 to the caller, which counts it as kept.
        ROLLBACK TRANSACTION;
        SET @Refused = N'error';
    END
    ELSE IF XACT_STATE() = 1
        COMMIT TRANSACTION;

    SELECT CAST(CASE WHEN @Refused IS NULL THEN 1 ELSE 0 END AS BIT) AS [Deleted],
           CASE WHEN @Refused IS NULL THEN @BlobUrl END AS [BlobUrl],
           CASE WHEN @Refused IS NULL THEN @TextBlobUrl END AS [ExtractedTextBlobUrl],
           @Refused AS [RefusedReason];
END
GO

-- 'ready' -> 'applied' when nothing in the run is left to review or apply. Returns the run row when moved, else an empty set.
CREATE OR ALTER PROCEDURE dbo.CloseAttachmentBackfillRunIfDone
    @RunId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    -- R9: run first, then candidates (the order the apply and undo use).
    DECLARE @Locked BIT;
    SELECT @Locked = 1 FROM dbo.[AttachmentBackfillRun] WITH (UPDLOCK, HOLDLOCK) WHERE [Id] = @RunId;

    DECLARE @Moved TABLE ([Id] BIGINT, [PublicId] UNIQUEIDENTIFIER, [Status] NVARCHAR(16));

    UPDATE r
    SET [Status] = N'applied',
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT INSERTED.[Id], INSERTED.[PublicId], INSERTED.[Status] INTO @Moved
    FROM dbo.[AttachmentBackfillRun] AS r
    WHERE r.[Id] = @RunId
      AND r.[Status] = N'ready'
      AND NOT EXISTS (
            SELECT 1
            FROM dbo.[AttachmentBackfillCandidate] AS c WITH (UPDLOCK, HOLDLOCK)
            WHERE c.[RunId] = @RunId
              AND (c.[ReviewStatus] = N'pending' OR (c.[ReviewStatus] = N'approved' AND c.[AttachmentId] IS NULL))
      );

    COMMIT TRANSACTION;

    SELECT [Id], [PublicId], [Status] FROM @Moved;
END
GO
