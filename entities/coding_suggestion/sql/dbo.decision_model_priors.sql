-- Contract: U-745 live inputs for the U-698 M0 bill-coding decision model (read-only).
-- Owns: ReadVendorPriorBillsForDecisionModel, ReadSubCostCodeLabelCountsForDecisionModel.
-- Training rule reproduced (scripts/u653b_backtest.py _LOAD_SQL, the same rule
--   dbo.similar_bills_corpus.sql ReadSimilarBillsCorpusRows duplicates):
--   a labelled bill = a NON-DRAFT bill with >= 1 coded line (SubCostCodeId NOT NULL);
--   label = the dominant SubCostCodeId by SUM(ABS(line Amount)), ties -> the code holding the
--   lowest line Id (MIN(Id)); project = the ProjectId of that dominant code's lowest-id line;
--   amount = SUM(ABS(COALESCE(Amount, 0))) over ALL of the bill's lines.
-- Invariants:
--   ReadVendorPriorBillsForDecisionModel - the vendor's labelled bills in @CompanyId created
--     STRICTLY before @AsOf, never @ExcludeBillId. HasPdf is the accepted live proxy for the
--     backtest's "has PDF text": the bill has a BillLineItemAttachment whose attachment has an
--     AttachmentTextTokenHash row of @TokenizerVersion and is not a TaxpayerAttachment (the join
--     ReadSimilarBillsCorpusRows uses). @TokenizerVersion NULL -> HasPdf = 0 for every row.
--   ReadSubCostCodeLabelCountsForDecisionModel - per-label bill counts over every labelled bill in
--     @CompanyId (the base-rate denominator is their sum). No as-of cut: callers cache it ~1h.
-- Depends: dbo.Bill (CompanyId, IsDraft), dbo.BillLineItem, dbo.BillLineItemAttachment,
--   dbo.AttachmentTextTokenHash, dbo.TaxpayerAttachment.
-- Sprocs: ReadVendorPriorBillsForDecisionModel, ReadSubCostCodeLabelCountsForDecisionModel
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/coding_suggestion/sql/dbo.decision_model_priors.sql

GO

CREATE OR ALTER PROCEDURE ReadVendorPriorBillsForDecisionModel
    @CompanyId BIGINT,
    @VendorId BIGINT,
    @AsOf DATETIME2(7),
    @ExcludeBillId BIGINT = NULL,
    @TokenizerVersion NVARCHAR(32) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH CodedBills AS (
        SELECT b.[Id] AS [BillId], b.[CreatedDatetime]
        FROM dbo.[Bill] b
        WHERE b.[IsDraft] = 0
          AND b.[VendorId] = @VendorId
          AND b.[CompanyId] = @CompanyId
          AND b.[CreatedDatetime] < @AsOf
          AND (@ExcludeBillId IS NULL OR b.[Id] <> @ExcludeBillId)
        -- ">= 1 coded line" is enforced by the INNER JOIN to Dominant below.
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
    )
    SELECT
        cb.[BillId],
        cb.[CreatedDatetime],
        d.[DominantSubCostCodeId],
        d.[DominantProjectId],
        bt.[TotalAbsAmount],
        CAST(
            CASE WHEN @TokenizerVersion IS NOT NULL AND EXISTS (
                SELECT 1
                FROM dbo.[BillLineItem] bli
                INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[BillLineItemId] = bli.[Id]
                INNER JOIN dbo.[AttachmentTextTokenHash] h
                    ON h.[AttachmentId] = blia.[AttachmentId]
                   AND h.[TokenizerVersion] = @TokenizerVersion
                WHERE bli.[BillId] = cb.[BillId]
                  AND NOT EXISTS (
                      SELECT 1 FROM dbo.[TaxpayerAttachment] t
                      WHERE t.[AttachmentId] = blia.[AttachmentId]
                  )
            ) THEN 1 ELSE 0 END
        AS BIT) AS [HasPdf]
    FROM CodedBills cb
    INNER JOIN Dominant d ON d.[BillId] = cb.[BillId]
    INNER JOIN BillTotal bt ON bt.[BillId] = cb.[BillId]
    ORDER BY cb.[CreatedDatetime] ASC, cb.[BillId] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadSubCostCodeLabelCountsForDecisionModel
    @CompanyId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH LineAgg AS (
        SELECT
            bli.[BillId],
            bli.[SubCostCodeId],
            SUM(ABS(bli.[Amount])) AS [AbsTotal],
            MIN(bli.[Id]) AS [MinLineId]
        FROM dbo.[BillLineItem] bli
        INNER JOIN dbo.[Bill] b ON b.[Id] = bli.[BillId]
        WHERE b.[IsDraft] = 0
          AND b.[CompanyId] = @CompanyId
          AND bli.[SubCostCodeId] IS NOT NULL
        GROUP BY bli.[BillId], bli.[SubCostCodeId]
    ),
    DominantScc AS (
        SELECT
            la.[BillId],
            la.[SubCostCodeId],
            ROW_NUMBER() OVER (
                PARTITION BY la.[BillId]
                ORDER BY la.[AbsTotal] DESC, la.[MinLineId] ASC
            ) AS [Rn]
        FROM LineAgg la
    )
    SELECT ds.[SubCostCodeId], COUNT(*) AS [BillCount]
    FROM DominantScc ds
    WHERE ds.[Rn] = 1
    GROUP BY ds.[SubCostCodeId]
    ORDER BY ds.[SubCostCodeId] ASC;
END;
GO
