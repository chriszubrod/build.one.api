GO

IF OBJECT_ID('dbo.Attachment', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Attachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Filename] NVARCHAR(MAX) NOT NULL,
    [OriginalFilename] NVARCHAR(MAX) NOT NULL,
    [FileExtension] NVARCHAR(10) NULL,
    [ContentType] NVARCHAR(255) NOT NULL,
    [FileSize] BIGINT NOT NULL,
    [FileHash] NVARCHAR(64) NULL,
    [BlobUrl] NVARCHAR(MAX) NOT NULL,
    [Description] NVARCHAR(MAX) NULL,
    [Category] NVARCHAR(50) NULL,
    [Tags] NVARCHAR(MAX) NULL,
    [IsArchived] BIT NOT NULL DEFAULT 0,
    [Status] NVARCHAR(20) NULL,
    [DownloadCount] BIGINT NOT NULL DEFAULT 0,
    [LastDownloadedDatetime] DATETIME2(3) NULL,
    [ExpirationDate] DATETIME2(3) NULL,
    [StorageTier] NVARCHAR(20) NOT NULL DEFAULT 'Hot',
    -- U-187: sync-proof vendor invoice number parsed from the attachment's own
    -- extracted text. The QBO purchase->Expense connector never writes
    -- dbo.Attachment, so this column is immune to the KI-42 ReferenceNumber
    -- clobber. NULL until the extraction sweep populates it. (On prod the column
    -- is added by scripts/migrations/attachment_vendor_invoice_number.sql — apply
    -- that BEFORE re-applying this file, same layering as the extraction columns.)
    [VendorInvoiceNumber] NVARCHAR(100) NULL
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.Attachment', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.Attachment') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[Attachment] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_Attachment_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.Attachment', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Attachment_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[Attachment] ADD CONSTRAINT [FK_Attachment_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateAttachment
(
    @Filename NVARCHAR(MAX),
    @OriginalFilename NVARCHAR(MAX),
    @FileExtension NVARCHAR(10),
    @ContentType NVARCHAR(255),
    @FileSize BIGINT,
    @FileHash NVARCHAR(64),
    @BlobUrl NVARCHAR(MAX),
    @Description NVARCHAR(MAX),
    @Category NVARCHAR(50),
    @Tags NVARCHAR(MAX),
    @IsArchived BIT = 0,
    @Status NVARCHAR(20),
    @ExpirationDate DATETIME2(3),
    @StorageTier NVARCHAR(20) = 'Hot',
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Attachment] ([CreatedDatetime], [ModifiedDatetime], [Filename], [OriginalFilename], [FileExtension], [ContentType], [FileSize], [FileHash], [BlobUrl], [Description], [Category], [Tags], [IsArchived], [Status], [ExpirationDate], [StorageTier], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Filename],
        INSERTED.[OriginalFilename],
        INSERTED.[FileExtension],
        INSERTED.[ContentType],
        INSERTED.[FileSize],
        INSERTED.[FileHash],
        INSERTED.[BlobUrl],
        INSERTED.[Description],
        INSERTED.[Category],
        INSERTED.[Tags],
        INSERTED.[IsArchived],
        INSERTED.[Status],
        INSERTED.[DownloadCount],
        CONVERT(VARCHAR(19), INSERTED.[LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ExpirationDate], 120) AS [ExpirationDate],
        INSERTED.[StorageTier]
    VALUES (@Now, @Now, @Filename, @OriginalFilename, @FileExtension, @ContentType, @FileSize, @FileHash, @BlobUrl, @Description, @Category, @Tags, @IsArchived, @Status, @ExpirationDate, @StorageTier, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachments
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber],
        [QboId],
        [RealmId]
    FROM dbo.[Attachment]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachmentByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber],
        [QboId],
        [RealmId]
    FROM dbo.[Attachment]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachmentByCategory
(
    @Category NVARCHAR(50)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    WHERE [Category] = @Category
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachmentByHash
(
    @FileHash NVARCHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    WHERE [FileHash] = @FileHash;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdateAttachmentById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Filename NVARCHAR(MAX),
    @OriginalFilename NVARCHAR(MAX),
    @FileExtension NVARCHAR(10),
    @ContentType NVARCHAR(255),
    @FileSize BIGINT,
    @FileHash NVARCHAR(64),
    @BlobUrl NVARCHAR(MAX),
    @Description NVARCHAR(MAX),
    @Category NVARCHAR(50),
    @Tags NVARCHAR(MAX),
    @IsArchived BIT,
    @Status NVARCHAR(20),
    @ExpirationDate DATETIME2(3),
    @StorageTier NVARCHAR(20),
    @AllowTerminalParent BIT = 0
)
AS
BEGIN
    -- Required: the guard below runs before the DML, so with NOCOUNT off its
    -- row-count token becomes the first result pyodbc sees (CLAUDE.md).
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    -- LOCK UNCONDITIONALLY, REFUSE CONDITIONALLY (U-446c, Codex). An exempt
    -- writer that skips the walk takes no Bill locks at all and is invisible to
    -- every other transaction, so the serialization the guards rest on vanishes
    -- for exactly the callers that mutate most.
    BEGIN
        -- U-446b (Codex round 3, P1). The Python guard asks this same question
        -- in a SEPARATE transaction: it can see zero completed parents, the
        -- completion can commit, and the write then lands on frozen evidence.
        -- UPDLOCK+HOLDLOCK here reads past the RCSI snapshot and holds to the
        -- commit, so this and FinalizeBillById serialize.
        --
        -- @AllowTerminalParent is fail-closed (= 0) since U-446c; the repo
        -- passes it explicitly regardless. See dbo.bill_line_item.sql.
        -- Serialize on the ATTACHMENT row first (Codex round 4, P1).
        -- Locking only the Bills that are linked RIGHT NOW is not enough: a
        -- second transaction can link this same file to another DRAFT Bill and
        -- complete it while we are in flight, and adding a BLIA row does not
        -- touch Attachment.RowVersion, so nothing else would notice.
        -- CreateBillLineItemAttachment takes this same lock, so a new link
        -- cannot appear between this guard and the write below.
        --
        -- It doubles as the first lock every attachment-side writer takes,
        -- which gives them a common ordering point.
        DECLARE @Locked BIT;
        SELECT @Locked = 1 FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @Id;

        -- LOCK EVERY LINKED BILL, ASCENDING, THEN VALIDATE THE SET (U-446c).
        --
        -- Ordering alone is not enough, and getting that wrong was this unit's
        -- own P0. The join this replaces locked every linked Bill in ONE
        -- statement, which gave no acquisition order (two attachment writers
        -- sharing Bills could deadlock) but DID give set stability: a single
        -- statement locks every row it scans. A plain ascending walk buys the
        -- order and loses the stability — move a line from Bill 30 to Bill 5
        -- after 10 is locked, complete Bill 5, and `> @PrevBillId` skips it.
        --
        -- So: walk ascending (a total order every writer agrees on, the same
        -- reasoning as UpdateBillLineItemById's low->high pair), then check that
        -- every currently-linked Bill is one we hold. If the set moved under us,
        -- walk again — each pass locks strictly more, and a Bill we hold can no
        -- longer be moved away from, so it converges. New LINKS cannot appear at
        -- all: CreateBillLineItemAttachment takes the Attachment lock above.
        --
        -- In practice the outer loop runs once and the inner loop once (6 of
        -- 3,605 linked attachments span more than one Bill, max 2).
        DECLARE @LockedBills TABLE ([BillId] BIGINT PRIMARY KEY);
        DECLARE @PrevBillId BIGINT, @NextBillId BIGINT, @Passes INT = 0;

        WHILE 1 = 1
        BEGIN
            SET @Passes = @Passes + 1;
            SET @PrevBillId = -1;

            WHILE 1 = 1
            BEGIN
                SET @NextBillId = NULL;

                SELECT TOP 1 @NextBillId = li.[BillId]
                FROM dbo.[BillLineItemAttachment] blia
                INNER JOIN dbo.[BillLineItem] li ON li.[Id] = blia.[BillLineItemId]
                WHERE blia.[AttachmentId] = @Id AND li.[BillId] > @PrevBillId
                ORDER BY li.[BillId];

                IF @NextBillId IS NULL BREAK;

                SELECT @Locked = 1 FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK)
                WHERE [Id] = @NextBillId;

                IF NOT EXISTS (SELECT 1 FROM @LockedBills WHERE [BillId] = @NextBillId)
                    INSERT INTO @LockedBills ([BillId]) VALUES (@NextBillId);

                SET @PrevBillId = @NextBillId;
            END

            IF NOT EXISTS (
                SELECT li.[BillId]
                FROM dbo.[BillLineItemAttachment] blia
                INNER JOIN dbo.[BillLineItem] li ON li.[Id] = blia.[BillLineItemId]
                WHERE blia.[AttachmentId] = @Id
                EXCEPT
                SELECT [BillId] FROM @LockedBills
            ) BREAK;

            IF @Passes >= 5
            BEGIN
                COMMIT TRANSACTION;
                -- Deliberately NOT the STATUS_LOCKED sentinel: this is a
                -- transient failure to stabilise, not a permanent refusal, and
                -- must not reach the client as 422 `status_locked`.
                RAISERROR('This file''s linked Bills kept changing; please retry.', 16, 1);
                RETURN;
            END
        END

        -- Decide over Bills that are STILL LINKED, intersected with the ones we
        -- actually hold (Codex P1). @LockedBills is a coverage SUPERSET: the
        -- validation above proves `linked ⊆ locked`, not equality, so a Bill
        -- whose last link was removed while we walked stays in the set. Testing
        -- the raw set would then refuse 422 `status_locked` for a Bill this
        -- attachment is no longer evidence for — a permanent answer to a
        -- transient state, which a retry would contradict.
        DECLARE @LockedCompleted INT = 0;
        SELECT @LockedCompleted = COUNT(DISTINCT b.[Id])
        FROM dbo.[Bill] b
        INNER JOIN @LockedBills l ON l.[BillId] = b.[Id]
        INNER JOIN dbo.[BillLineItem] li ON li.[BillId] = b.[Id]
        INNER JOIN dbo.[BillLineItemAttachment] blia
                ON blia.[BillLineItemId] = li.[Id] AND blia.[AttachmentId] = @Id
        WHERE b.[Status] = 'completed';

        IF @AllowTerminalParent = 0 AND @LockedCompleted > 0
        BEGIN
            COMMIT TRANSACTION;
            RAISERROR('STATUS_LOCKED: this file is evidence for a completed Bill.', 16, 1);
            RETURN;
        END

        -- U-468: the same walk for Expenses. An attachment can be a completed
        -- Expense's receipt without being linked to any Bill; the Bill walk
        -- above would then count zero and the write would land on frozen AP
        -- evidence. Bills first, then Expenses — a total order every writer
        -- that takes both agrees on. CreateExpenseLineItemAttachment takes
        -- the Attachment lock above, so new Expense links cannot appear
        -- between this guard and the write.
        DECLARE @LockedExpenses TABLE ([ExpenseId] BIGINT PRIMARY KEY);
        DECLARE @PrevExpenseId BIGINT, @NextExpenseId BIGINT, @ExpensePasses INT = 0;

        WHILE 1 = 1
        BEGIN
            SET @ExpensePasses = @ExpensePasses + 1;
            SET @PrevExpenseId = -1;

            WHILE 1 = 1
            BEGIN
                SET @NextExpenseId = NULL;

                SELECT TOP 1 @NextExpenseId = li.[ExpenseId]
                FROM dbo.[ExpenseLineItemAttachment] elia
                INNER JOIN dbo.[ExpenseLineItem] li ON li.[Id] = elia.[ExpenseLineItemId]
                WHERE elia.[AttachmentId] = @Id AND li.[ExpenseId] > @PrevExpenseId
                ORDER BY li.[ExpenseId];

                IF @NextExpenseId IS NULL BREAK;

                SELECT @Locked = 1 FROM dbo.[Expense] WITH (UPDLOCK, HOLDLOCK)
                WHERE [Id] = @NextExpenseId;

                IF NOT EXISTS (SELECT 1 FROM @LockedExpenses WHERE [ExpenseId] = @NextExpenseId)
                    INSERT INTO @LockedExpenses ([ExpenseId]) VALUES (@NextExpenseId);

                SET @PrevExpenseId = @NextExpenseId;
            END

            IF NOT EXISTS (
                SELECT li.[ExpenseId]
                FROM dbo.[ExpenseLineItemAttachment] elia
                INNER JOIN dbo.[ExpenseLineItem] li ON li.[Id] = elia.[ExpenseLineItemId]
                WHERE elia.[AttachmentId] = @Id
                EXCEPT
                SELECT [ExpenseId] FROM @LockedExpenses
            ) BREAK;

            IF @ExpensePasses >= 5
            BEGIN
                COMMIT TRANSACTION;
                -- Deliberately NOT the STATUS_LOCKED sentinel: this is a
                -- transient failure to stabilise, not a permanent refusal.
                RAISERROR('This file''s linked Expenses kept changing; please retry.', 16, 1);
                RETURN;
            END
        END

        DECLARE @LockedCompletedExpenses INT = 0;
        SELECT @LockedCompletedExpenses = COUNT(DISTINCT e.[Id])
        FROM dbo.[Expense] e
        INNER JOIN @LockedExpenses l ON l.[ExpenseId] = e.[Id]
        INNER JOIN dbo.[ExpenseLineItem] li ON li.[ExpenseId] = e.[Id]
        INNER JOIN dbo.[ExpenseLineItemAttachment] elia
                ON elia.[ExpenseLineItemId] = li.[Id] AND elia.[AttachmentId] = @Id
        WHERE e.[Status] = 'completed';

        IF @AllowTerminalParent = 0 AND @LockedCompletedExpenses > 0
        BEGIN
            COMMIT TRANSACTION;
            RAISERROR('STATUS_LOCKED: this file is evidence for a completed Expense.', 16, 1);
            RETURN;
        END
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Attachment]
    SET
        [ModifiedDatetime] = @Now,
        [Filename] = @Filename,
        [OriginalFilename] = @OriginalFilename,
        [FileExtension] = @FileExtension,
        [ContentType] = @ContentType,
        [FileSize] = @FileSize,
        [FileHash] = @FileHash,
        [BlobUrl] = @BlobUrl,
        [Description] = @Description,
        [Category] = @Category,
        [Tags] = @Tags,
        [IsArchived] = @IsArchived,
        [Status] = @Status,
        [ExpirationDate] = @ExpirationDate,
        [StorageTier] = @StorageTier
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Filename],
        INSERTED.[OriginalFilename],
        INSERTED.[FileExtension],
        INSERTED.[ContentType],
        INSERTED.[FileSize],
        INSERTED.[FileHash],
        INSERTED.[BlobUrl],
        INSERTED.[Description],
        INSERTED.[Category],
        INSERTED.[Tags],
        INSERTED.[IsArchived],
        INSERTED.[Status],
        INSERTED.[DownloadCount],
        CONVERT(VARCHAR(19), INSERTED.[LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ExpirationDate], 120) AS [ExpirationDate],
        INSERTED.[StorageTier]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeleteAttachmentById
(
    @Id BIGINT,
    @AllowTerminalParent BIT = 0
)
AS
BEGIN
    -- Required: the guard below runs before the DML, so with NOCOUNT off its
    -- row-count token becomes the first result pyodbc sees (CLAUDE.md).
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    -- LOCK UNCONDITIONALLY, REFUSE CONDITIONALLY (U-446c, Codex). An exempt
    -- writer that skips the walk takes no Bill locks at all and is invisible to
    -- every other transaction, so the serialization the guards rest on vanishes
    -- for exactly the callers that mutate most.
    BEGIN
        -- U-446b (Codex round 3, P1). The Python guard asks this same question
        -- in a SEPARATE transaction: it can see zero completed parents, the
        -- completion can commit, and the write then lands on frozen evidence.
        -- UPDLOCK+HOLDLOCK here reads past the RCSI snapshot and holds to the
        -- commit, so this and FinalizeBillById serialize.
        --
        -- @AllowTerminalParent is fail-closed (= 0) since U-446c; the repo
        -- passes it explicitly regardless. See dbo.bill_line_item.sql.
        -- Serialize on the ATTACHMENT row first (Codex round 4, P1).
        -- Locking only the Bills that are linked RIGHT NOW is not enough: a
        -- second transaction can link this same file to another DRAFT Bill and
        -- complete it while we are in flight, and adding a BLIA row does not
        -- touch Attachment.RowVersion, so nothing else would notice.
        -- CreateBillLineItemAttachment takes this same lock, so a new link
        -- cannot appear between this guard and the write below.
        --
        -- It doubles as the first lock every attachment-side writer takes,
        -- which gives them a common ordering point.
        DECLARE @Locked BIT;
        SELECT @Locked = 1 FROM dbo.[Attachment] WITH (UPDLOCK, HOLDLOCK)
        WHERE [Id] = @Id;

        -- LOCK EVERY LINKED BILL, ASCENDING, THEN VALIDATE THE SET (U-446c).
        --
        -- Ordering alone is not enough, and getting that wrong was this unit's
        -- own P0. The join this replaces locked every linked Bill in ONE
        -- statement, which gave no acquisition order (two attachment writers
        -- sharing Bills could deadlock) but DID give set stability: a single
        -- statement locks every row it scans. A plain ascending walk buys the
        -- order and loses the stability — move a line from Bill 30 to Bill 5
        -- after 10 is locked, complete Bill 5, and `> @PrevBillId` skips it.
        --
        -- So: walk ascending (a total order every writer agrees on, the same
        -- reasoning as UpdateBillLineItemById's low->high pair), then check that
        -- every currently-linked Bill is one we hold. If the set moved under us,
        -- walk again — each pass locks strictly more, and a Bill we hold can no
        -- longer be moved away from, so it converges. New LINKS cannot appear at
        -- all: CreateBillLineItemAttachment takes the Attachment lock above.
        --
        -- In practice the outer loop runs once and the inner loop once (6 of
        -- 3,605 linked attachments span more than one Bill, max 2).
        DECLARE @LockedBills TABLE ([BillId] BIGINT PRIMARY KEY);
        DECLARE @PrevBillId BIGINT, @NextBillId BIGINT, @Passes INT = 0;

        WHILE 1 = 1
        BEGIN
            SET @Passes = @Passes + 1;
            SET @PrevBillId = -1;

            WHILE 1 = 1
            BEGIN
                SET @NextBillId = NULL;

                SELECT TOP 1 @NextBillId = li.[BillId]
                FROM dbo.[BillLineItemAttachment] blia
                INNER JOIN dbo.[BillLineItem] li ON li.[Id] = blia.[BillLineItemId]
                WHERE blia.[AttachmentId] = @Id AND li.[BillId] > @PrevBillId
                ORDER BY li.[BillId];

                IF @NextBillId IS NULL BREAK;

                SELECT @Locked = 1 FROM dbo.[Bill] WITH (UPDLOCK, HOLDLOCK)
                WHERE [Id] = @NextBillId;

                IF NOT EXISTS (SELECT 1 FROM @LockedBills WHERE [BillId] = @NextBillId)
                    INSERT INTO @LockedBills ([BillId]) VALUES (@NextBillId);

                SET @PrevBillId = @NextBillId;
            END

            IF NOT EXISTS (
                SELECT li.[BillId]
                FROM dbo.[BillLineItemAttachment] blia
                INNER JOIN dbo.[BillLineItem] li ON li.[Id] = blia.[BillLineItemId]
                WHERE blia.[AttachmentId] = @Id
                EXCEPT
                SELECT [BillId] FROM @LockedBills
            ) BREAK;

            IF @Passes >= 5
            BEGIN
                COMMIT TRANSACTION;
                -- Deliberately NOT the STATUS_LOCKED sentinel: this is a
                -- transient failure to stabilise, not a permanent refusal, and
                -- must not reach the client as 422 `status_locked`.
                RAISERROR('This file''s linked Bills kept changing; please retry.', 16, 1);
                RETURN;
            END
        END

        -- Decide over Bills that are STILL LINKED, intersected with the ones we
        -- actually hold (Codex P1). @LockedBills is a coverage SUPERSET: the
        -- validation above proves `linked ⊆ locked`, not equality, so a Bill
        -- whose last link was removed while we walked stays in the set. Testing
        -- the raw set would then refuse 422 `status_locked` for a Bill this
        -- attachment is no longer evidence for — a permanent answer to a
        -- transient state, which a retry would contradict.
        DECLARE @LockedCompleted INT = 0;
        SELECT @LockedCompleted = COUNT(DISTINCT b.[Id])
        FROM dbo.[Bill] b
        INNER JOIN @LockedBills l ON l.[BillId] = b.[Id]
        INNER JOIN dbo.[BillLineItem] li ON li.[BillId] = b.[Id]
        INNER JOIN dbo.[BillLineItemAttachment] blia
                ON blia.[BillLineItemId] = li.[Id] AND blia.[AttachmentId] = @Id
        WHERE b.[Status] = 'completed';

        IF @AllowTerminalParent = 0 AND @LockedCompleted > 0
        BEGIN
            COMMIT TRANSACTION;
            RAISERROR('STATUS_LOCKED: this file is evidence for a completed Bill.', 16, 1);
            RETURN;
        END

        -- U-468: the same walk for Expenses. An attachment can be a completed
        -- Expense's receipt without being linked to any Bill; the Bill walk
        -- above would then count zero and the write would land on frozen AP
        -- evidence. Bills first, then Expenses — a total order every writer
        -- that takes both agrees on. CreateExpenseLineItemAttachment takes
        -- the Attachment lock above, so new Expense links cannot appear
        -- between this guard and the write.
        DECLARE @LockedExpenses TABLE ([ExpenseId] BIGINT PRIMARY KEY);
        DECLARE @PrevExpenseId BIGINT, @NextExpenseId BIGINT, @ExpensePasses INT = 0;

        WHILE 1 = 1
        BEGIN
            SET @ExpensePasses = @ExpensePasses + 1;
            SET @PrevExpenseId = -1;

            WHILE 1 = 1
            BEGIN
                SET @NextExpenseId = NULL;

                SELECT TOP 1 @NextExpenseId = li.[ExpenseId]
                FROM dbo.[ExpenseLineItemAttachment] elia
                INNER JOIN dbo.[ExpenseLineItem] li ON li.[Id] = elia.[ExpenseLineItemId]
                WHERE elia.[AttachmentId] = @Id AND li.[ExpenseId] > @PrevExpenseId
                ORDER BY li.[ExpenseId];

                IF @NextExpenseId IS NULL BREAK;

                SELECT @Locked = 1 FROM dbo.[Expense] WITH (UPDLOCK, HOLDLOCK)
                WHERE [Id] = @NextExpenseId;

                IF NOT EXISTS (SELECT 1 FROM @LockedExpenses WHERE [ExpenseId] = @NextExpenseId)
                    INSERT INTO @LockedExpenses ([ExpenseId]) VALUES (@NextExpenseId);

                SET @PrevExpenseId = @NextExpenseId;
            END

            IF NOT EXISTS (
                SELECT li.[ExpenseId]
                FROM dbo.[ExpenseLineItemAttachment] elia
                INNER JOIN dbo.[ExpenseLineItem] li ON li.[Id] = elia.[ExpenseLineItemId]
                WHERE elia.[AttachmentId] = @Id
                EXCEPT
                SELECT [ExpenseId] FROM @LockedExpenses
            ) BREAK;

            IF @ExpensePasses >= 5
            BEGIN
                COMMIT TRANSACTION;
                -- Deliberately NOT the STATUS_LOCKED sentinel: this is a
                -- transient failure to stabilise, not a permanent refusal.
                RAISERROR('This file''s linked Expenses kept changing; please retry.', 16, 1);
                RETURN;
            END
        END

        DECLARE @LockedCompletedExpenses INT = 0;
        SELECT @LockedCompletedExpenses = COUNT(DISTINCT e.[Id])
        FROM dbo.[Expense] e
        INNER JOIN @LockedExpenses l ON l.[ExpenseId] = e.[Id]
        INNER JOIN dbo.[ExpenseLineItem] li ON li.[ExpenseId] = e.[Id]
        INNER JOIN dbo.[ExpenseLineItemAttachment] elia
                ON elia.[ExpenseLineItemId] = li.[Id] AND elia.[AttachmentId] = @Id
        WHERE e.[Status] = 'completed';

        IF @AllowTerminalParent = 0 AND @LockedCompletedExpenses > 0
        BEGIN
            COMMIT TRANSACTION;
            RAISERROR('STATUS_LOCKED: this file is evidence for a completed Expense.', 16, 1);
            RETURN;
        END
    END

    DELETE FROM dbo.[Attachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Filename],
        DELETED.[OriginalFilename],
        DELETED.[FileExtension],
        DELETED.[ContentType],
        DELETED.[FileSize],
        DELETED.[FileHash],
        DELETED.[BlobUrl],
        DELETED.[Description],
        DELETED.[Category],
        DELETED.[Tags],
        DELETED.[IsArchived],
        DELETED.[Status],
        DELETED.[DownloadCount],
        CONVERT(VARCHAR(19), DELETED.[LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ExpirationDate], 120) AS [ExpirationDate],
        DELETED.[StorageTier]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadAttachmentsByIds
(
    @Ids NVARCHAR(MAX)   -- comma-separated BIGINT IDs
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber]
    FROM dbo.[Attachment]
    WHERE [Id] IN (
        SELECT CAST(LTRIM(RTRIM(value)) AS BIGINT)
        FROM STRING_SPLIT(@Ids, ',')
        WHERE LTRIM(RTRIM(value)) <> ''
    );

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE IncrementDownloadCount
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Attachment]
    SET
        [DownloadCount] = [DownloadCount] + 1,
        [LastDownloadedDatetime] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Filename],
        INSERTED.[OriginalFilename],
        INSERTED.[FileExtension],
        INSERTED.[ContentType],
        INSERTED.[FileSize],
        INSERTED.[FileHash],
        INSERTED.[BlobUrl],
        INSERTED.[Description],
        INSERTED.[Category],
        INSERTED.[Tags],
        INSERTED.[IsArchived],
        INSERTED.[Status],
        INSERTED.[DownloadCount],
        CONVERT(VARCHAR(19), INSERTED.[LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ExpirationDate], 120) AS [ExpirationDate],
        INSERTED.[StorageTier]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE SetAttachmentQboIdentity
(
    @Id BIGINT,
    @QboId NVARCHAR(50) = NULL,
    @RealmId NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Stolen BIT = 0;

    IF @QboId IS NOT NULL
    BEGIN
        UPDATE dbo.[Attachment]
        SET [QboId] = NULL, [RealmId] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[Attachment]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[QboId],
        INSERTED.[RealmId],
        @Stolen AS [Stolen]
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
      );
END;
GO

-- U-279 (Phase-5 enablement): read-by-QboId lookup for dbo.Attachment's native
-- QboId/RealmId (U-238c). RealmId NULL-equality mirrors SetAttachmentQboIdentity's
-- own theft-detection comparison above.
CREATE OR ALTER PROCEDURE ReadAttachmentByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Filename],
        [OriginalFilename],
        [FileExtension],
        [ContentType],
        [FileSize],
        [FileHash],
        [BlobUrl],
        [Description],
        [Category],
        [Tags],
        [IsArchived],
        [Status],
        [DownloadCount],
        CONVERT(VARCHAR(19), [LastDownloadedDatetime], 120) AS [LastDownloadedDatetime],
        CONVERT(VARCHAR(19), [ExpirationDate], 120) AS [ExpirationDate],
        [StorageTier],
        [ExtractionStatus],
        [ExtractedTextBlobUrl],
        [ExtractionError],
        CONVERT(VARCHAR(19), [ExtractedDatetime], 120) AS [ExtractedDatetime],
        [AICategory],
        [AICategoryConfidence],
        [AICategoryStatus],
        [AICategoryReasoning],
        [AIExtractedFields],
        CONVERT(VARCHAR(19), [CategorizedDatetime], 120) AS [CategorizedDatetime],
        [VendorInvoiceNumber],
        [QboId],
        [RealmId]
    FROM dbo.[Attachment]
    WHERE [QboId] = @QboId
      AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

    COMMIT TRANSACTION;
END;
GO
