-- Migration 004 — FindBillForReviewerReply sproc (Wave 3 Phase 1 fuzzy
-- fallback for reviewer-reply matching).
--
-- Context: 2026-05-19 manual walk surfaced that the reviewer-reply
-- primitive (ReadBillByConversationId) is too strict — when a PM replies
-- from a non-Outlook client, the ConversationId may not survive the round
-- trip, and parsed approvals fall on the floor as `internal_reply` +
-- `flagged_needs_review`. The Bill that the PM was reviewing exists in
-- our DB; we just can't link the reply back. TODO.md line 203 specifies
-- a two-phase fix; this is Phase 1 only.
--
-- New sproc: same conv_id match path as the existing sproc, plus a
-- fuzzy fallback on (BillNumber exact match) AND (any line item on a
-- Project whose Name contains the hint substring). Returns nothing when
-- 0 or 2+ candidates match — preserves the existing
-- `flagged_needs_review` outcome for ambiguous cases.
--
-- New `MatchKind` column ('conversation' | 'fuzzy') lets the caller log
-- which path matched without re-querying. Fuzzy matches return NULL for
-- the ConversationId column because the matched Bill's conv_id is not
-- the inbound reply's conv_id (they're different by definition — that's
-- what triggered the fallback).
--
-- IsDraft = 1 filter on the fuzzy path — a completed Bill can't be
-- reviewed (apply_reviewer_decision rejects with "no longer a draft"),
-- so we don't surface it at lookup time.
--
-- Single-result-set design: resolves the matched Bill Id into a local
-- variable across the conv-match + fuzzy branches, then emits ONE final
-- SELECT (possibly zero rows). Earlier iteration emitted a SELECT in
-- the conv-match branch then another in the fuzzy branch — pyodbc's
-- fetchone() reads from the FIRST resultset, so an empty conv-match
-- emit could mask a successful fuzzy hit. The single-output design
-- avoids that trap.
--
-- Phase 2 (PendingReviewerReply table + React queue for unmatched
-- replies) is deferred.
--
-- Idempotent: CREATE OR ALTER. Safe to re-apply.
GO


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

    -- 1. Strict ConversationId match.
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

    -- 2. Fuzzy fallback — only when conv match missed AND both hints
    --    are present. Partial hints would too easily false-match
    --    (a bare BillNumber across N invoice-equivalent drafts).
    IF @MatchedBillId IS NULL
       AND @BillNumberHint IS NOT NULL AND LTRIM(RTRIM(@BillNumberHint)) <> ''
       AND @ProjectHint    IS NOT NULL AND LTRIM(RTRIM(@ProjectHint))    <> ''
    BEGIN
        DECLARE @BillNumberNorm  NVARCHAR(50)  = LTRIM(RTRIM(@BillNumberHint));
        DECLARE @ProjectHintLike NVARCHAR(257) =
            '%' + REPLACE(REPLACE(LOWER(LTRIM(RTRIM(@ProjectHint))), '%', '[%]'), '_', '[_]') + '%';

        -- TOP 2 — ambiguity guard. If exactly 1 candidate, take it;
        -- otherwise leave @MatchedBillId NULL so the caller's
        -- `flagged_needs_review` path still fires.
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

    -- 3. Single final SELECT — emits exactly one result set whether
    --    matched or not (zero rows when @MatchedBillId IS NULL).
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
