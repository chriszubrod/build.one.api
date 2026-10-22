-- Contract: similar-bills completed-bill corpus (U-701a)
-- Owns: ReadSimilarBillsCorpusRows — bill-grain rows for live similar-bills cache.
-- Invariants: completed coded bills only; one row per bill; attachment = earliest BLIA link with a token-hash row
--   of the CURRENT @TokenizerVersion (old-key hashes never pick a bill's attachment); a TaxpayerAttachment-linked
--   attachment is never picked, even when it was hashed before the taxpayer link existed.
-- Depends: dbo.BillDominantVoteForVendor label rule duplicated at bill grain (no vendor filter); dbo.AttachmentTextTokenHash.
-- Sprocs: ReadSimilarBillsCorpusRows
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/coding_suggestion/sql/dbo.similar_bills_corpus.sql

GO

CREATE OR ALTER PROCEDURE ReadSimilarBillsCorpusRows
    @TokenizerVersion NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH CodedBills AS (
        SELECT DISTINCT b.[Id] AS [BillId]
        FROM dbo.[Bill] b
        INNER JOIN dbo.[BillLineItem] bli ON bli.[BillId] = b.[Id]
        WHERE b.[IsDraft] = 0
          AND bli.[SubCostCodeId] IS NOT NULL
    ),
    LineAgg AS (
        SELECT
            bli.[BillId],
            bli.[SubCostCodeId],
            SUM(ABS(bli.[Amount])) AS [AbsTotal],
            MIN(bli.[Id]) AS [MinLineId]
        FROM dbo.[BillLineItem] bli
        INNER JOIN CodedBills cb ON cb.[BillId] = bli.[BillId]
        WHERE bli.[SubCostCodeId] IS NOT NULL
        GROUP BY bli.[BillId], bli.[SubCostCodeId]
    ),
    DominantScc AS (
        SELECT
            la.[BillId],
            la.[SubCostCodeId],
            la.[MinLineId],
            ROW_NUMBER() OVER (
                PARTITION BY la.[BillId]
                ORDER BY la.[AbsTotal] DESC, la.[MinLineId] ASC
            ) AS [Rn]
        FROM LineAgg la
    ),
    Dominant AS (
        SELECT
            ds.[BillId],
            ds.[SubCostCodeId] AS [DominantSubCostCodeId],
            bli.[ProjectId] AS [DominantProjectId]
        FROM DominantScc ds
        INNER JOIN dbo.[BillLineItem] bli ON bli.[Id] = ds.[MinLineId]
        WHERE ds.[Rn] = 1
    ),
    BillTotal AS (
        SELECT bli.[BillId], SUM(ABS(COALESCE(bli.[Amount], 0))) AS [TotalAbsAmount]
        FROM dbo.[BillLineItem] bli
        INNER JOIN CodedBills cb ON cb.[BillId] = bli.[BillId]
        GROUP BY bli.[BillId]
    ),
    Linked AS (
        SELECT
            bli.[BillId],
            blia.[AttachmentId],
            ROW_NUMBER() OVER (
                PARTITION BY bli.[BillId]
                ORDER BY blia.[Id] ASC
            ) AS [Rn]
        FROM dbo.[BillLineItem] bli
        INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[BillLineItemId] = bli.[Id]
        INNER JOIN dbo.[AttachmentTextTokenHash] h
            ON h.[AttachmentId] = blia.[AttachmentId]
           AND h.[TokenizerVersion] = @TokenizerVersion
        WHERE NOT EXISTS (
            SELECT 1 FROM dbo.[TaxpayerAttachment] t WHERE t.[AttachmentId] = blia.[AttachmentId]
        )
    ),
    BillAttachment AS (
        SELECT [BillId], [AttachmentId]
        FROM Linked
        WHERE [Rn] = 1
    )
    SELECT
        ba.[AttachmentId],
        b.[Id] AS [BillId],
        b.[VendorId],
        b.[CreatedDatetime],
        d.[DominantSubCostCodeId],
        d.[DominantProjectId],
        bt.[TotalAbsAmount]
    FROM CodedBills cb
    INNER JOIN dbo.[Bill] b ON b.[Id] = cb.[BillId]
    INNER JOIN Dominant d ON d.[BillId] = b.[Id]
    INNER JOIN BillTotal bt ON bt.[BillId] = b.[Id]
    INNER JOIN BillAttachment ba ON ba.[BillId] = b.[Id]
    WHERE b.[IsDraft] = 0
      AND b.[VendorId] IS NOT NULL;
END;
GO
