-- Extend CreateBill to accept @SourceEmailMessageId so the email-agent
-- pipeline can stamp the source email FK on a draft bill at creation
-- time. NULL is the default — manual UI / API callers don't need to
-- pass it. Idempotent (CREATE OR ALTER).

GO

CREATE OR ALTER PROCEDURE CreateBill
(
    @VendorId BIGINT = NULL,
    @PaymentTermId BIGINT = NULL,
    @BillDate DATETIME2(3),
    @DueDate DATETIME2(3),
    @BillNumber NVARCHAR(50) = NULL,
    @TotalAmount DECIMAL(18,2) = NULL,
    @Memo NVARCHAR(MAX) = NULL,
    @IsDraft BIT = 1,
    @IntakeSource NVARCHAR(20) = NULL,
    @IntakeSourceDetail NVARCHAR(100) = NULL,
    @SourceEmailMessageId BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Bill]
        ([CreatedDatetime], [ModifiedDatetime], [VendorId], [PaymentTermId],
         [BillDate], [DueDate], [BillNumber], [TotalAmount], [Memo],
         [IsDraft], [IntakeSource], [IntakeSourceDetail], [SourceEmailMessageId],
         [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[PaymentTermId],
        CONVERT(VARCHAR(19), INSERTED.[BillDate], 120) AS [BillDate],
        CONVERT(VARCHAR(19), INSERTED.[DueDate], 120) AS [DueDate],
        INSERTED.[BillNumber],
        INSERTED.[TotalAmount],
        INSERTED.[Memo],
        INSERTED.[IsDraft],
        INSERTED.[IntakeSource],
        INSERTED.[IntakeSourceDetail],
        INSERTED.[SourceEmailMessageId]
    VALUES (@Now, @Now, @VendorId, @PaymentTermId, @BillDate, @DueDate,
            @BillNumber, @TotalAmount, @Memo, @IsDraft, @IntakeSource,
            @IntakeSourceDetail, @SourceEmailMessageId,
            COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

-- Filtered index for the email → bill reverse lookup. Tiny: only rows
-- that actually carry a source-email FK are indexed.
IF OBJECT_ID('dbo.Bill', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'IX_Bill_SourceEmailMessageId'
      AND object_id = OBJECT_ID('dbo.Bill')
)
BEGIN
    CREATE INDEX [IX_Bill_SourceEmailMessageId]
        ON [dbo].[Bill] ([SourceEmailMessageId])
        WHERE [SourceEmailMessageId] IS NOT NULL;
END
GO

-- Slim lookup returning just the fields the React Email-message detail
-- view needs to render a "Linked Bill" panel. Joins Vendor for the
-- denormalized vendor_name. Returns 0 or 1 row — if a duplicate-source
-- ever occurs, takes the most-recently-created Bill.
CREATE OR ALTER PROCEDURE ReadBillSlimBySourceEmailMessageId
(
    @SourceEmailMessageId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 1
        b.[Id]                                                    AS Id,
        b.[PublicId]                                              AS PublicId,
        b.[BillNumber]                                            AS BillNumber,
        b.[TotalAmount]                                           AS TotalAmount,
        b.[IsDraft]                                               AS IsDraft,
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120)            AS CreatedDatetime,
        v.[Name]                                                  AS VendorName
    FROM dbo.[Bill] b
    LEFT JOIN dbo.[Vendor] v ON v.[Id] = b.[VendorId]
    WHERE b.[SourceEmailMessageId] = @SourceEmailMessageId
    ORDER BY b.[CreatedDatetime] DESC;
END;
GO

-- Find the Bill linked to an email conversation. The PM's reply lands
-- in the same MS Graph conversation as the original vendor email. The
-- agent calls this sproc with the reply's ConversationId to identify
-- which Bill the reply is reviewing.
--
-- Path: ConversationId → EmailMessage → Bill.SourceEmailMessageId.
-- Filtered to draft bills (IsDeleted is implicit on Bill via the
-- standard ReadBill convention). Returns at most one row — most
-- recently created Bill if multiple share a conversation.
CREATE OR ALTER PROCEDURE ReadBillByConversationId
(
    @ConversationId NVARCHAR(255)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 1
        b.[Id]                                                    AS Id,
        b.[PublicId]                                              AS PublicId,
        b.[BillNumber]                                            AS BillNumber,
        b.[TotalAmount]                                           AS TotalAmount,
        b.[IsDraft]                                               AS IsDraft,
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120)            AS CreatedDatetime,
        v.[Name]                                                  AS VendorName,
        em.[Id]                                                   AS SourceEmailMessageId,
        em.[ConversationId]                                       AS ConversationId
    FROM dbo.[Bill] b
    INNER JOIN dbo.[EmailMessage] em ON em.[Id] = b.[SourceEmailMessageId]
    LEFT JOIN dbo.[Vendor] v ON v.[Id] = b.[VendorId]
    WHERE em.[ConversationId] = @ConversationId
    ORDER BY b.[CreatedDatetime] DESC;
END;
GO


-- FindBillForReviewerReply — superset of ReadBillByConversationId with a
-- fuzzy fallback for the Wave 3 Phase 1 reviewer-reply gap (TODO.md line
-- 203). Strict conv_id match path returns first; if conv_id missed and
-- both hint params are supplied, run a deterministic fuzzy lookup on
-- (IsDraft=1 AND BillNumber exact match AND any BillLineItem on a Project
-- whose Name contains the hint). Returns nothing on 0 or 2+ fuzzy hits —
-- ambiguous cases stay in the `flagged_needs_review` queue rather than
-- risk applying a PM decision to the wrong Bill.
--
-- Single-result-set design — resolves @MatchedBillId across the two
-- branches, then emits ONE final SELECT (possibly zero rows). Avoids
-- pyodbc's fetchone() consuming an empty conv-match result set and
-- masking a later fuzzy hit.
CREATE OR ALTER PROCEDURE FindBillForReviewerReply
(
    @ConversationId NVARCHAR(255) = NULL,
    @BillNumberHint NVARCHAR(50) = NULL,
    @ProjectHint    NVARCHAR(255) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @MatchedBillId BIGINT       = NULL;
    DECLARE @MatchKind     NVARCHAR(20) = NULL;

    IF @ConversationId IS NOT NULL AND LTRIM(RTRIM(@ConversationId)) <> ''
    BEGIN
        SELECT TOP 1
               @MatchedBillId = b.[Id],
               @MatchKind     = 'conversation'
        FROM dbo.[Bill] b
        INNER JOIN dbo.[EmailMessage] em ON em.[Id] = b.[SourceEmailMessageId]
        WHERE em.[ConversationId] = @ConversationId
        ORDER BY b.[CreatedDatetime] DESC;
    END

    IF @MatchedBillId IS NULL
       AND @BillNumberHint IS NOT NULL AND LTRIM(RTRIM(@BillNumberHint)) <> ''
       AND @ProjectHint    IS NOT NULL AND LTRIM(RTRIM(@ProjectHint))    <> ''
    BEGIN
        DECLARE @BillNumberNorm  NVARCHAR(50)  = LTRIM(RTRIM(@BillNumberHint));
        DECLARE @ProjectHintLike NVARCHAR(257) =
            '%' + REPLACE(REPLACE(LOWER(LTRIM(RTRIM(@ProjectHint))), '%', '[%]'), '_', '[_]') + '%';

        DECLARE @CandidateIds TABLE (Id BIGINT);

        INSERT INTO @CandidateIds
        SELECT TOP 2 b.[Id]
        FROM dbo.[Bill] b
        WHERE b.[IsDraft] = 1
          AND b.[BillNumber] = @BillNumberNorm
          AND EXISTS (
              SELECT 1
              FROM dbo.[BillLineItem] bli
              INNER JOIN dbo.[Project] p ON p.[Id] = bli.[ProjectId]
              WHERE bli.[BillId] = b.[Id]
                AND LOWER(p.[Name]) LIKE @ProjectHintLike
          )
        ORDER BY b.[CreatedDatetime] DESC;

        IF (SELECT COUNT(*) FROM @CandidateIds) = 1
        BEGIN
            SELECT @MatchedBillId = Id FROM @CandidateIds;
            SET @MatchKind = 'fuzzy';
        END
    END

    SELECT
        b.[Id]                                          AS Id,
        b.[PublicId]                                    AS PublicId,
        b.[BillNumber]                                  AS BillNumber,
        b.[TotalAmount]                                 AS TotalAmount,
        b.[IsDraft]                                     AS IsDraft,
        CONVERT(VARCHAR(19), b.[CreatedDatetime], 120)  AS CreatedDatetime,
        v.[Name]                                        AS VendorName,
        b.[SourceEmailMessageId]                        AS SourceEmailMessageId,
        CASE WHEN @MatchKind = 'conversation' THEN em.[ConversationId] ELSE NULL END
                                                        AS ConversationId,
        @MatchKind                                      AS MatchKind
    FROM dbo.[Bill] b
    LEFT JOIN dbo.[Vendor] v        ON v.[Id]  = b.[VendorId]
    LEFT JOIN dbo.[EmailMessage] em ON em.[Id] = b.[SourceEmailMessageId]
    WHERE b.[Id] = @MatchedBillId;
END;
GO
