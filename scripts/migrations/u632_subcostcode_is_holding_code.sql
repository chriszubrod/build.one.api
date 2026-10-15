-- U-632 bite 1: IsHoldingCode on SubCostCode; flag suspense SCC 00.02.
-- Idempotent; safe to re-run.

IF COL_LENGTH('dbo.SubCostCode', 'IsHoldingCode') IS NULL
BEGIN
    ALTER TABLE [dbo].[SubCostCode] ADD [IsHoldingCode] BIT NOT NULL
        CONSTRAINT [DF_SubCostCode_IsHoldingCode] DEFAULT 0;
END
GO

UPDATE dbo.[SubCostCode]
SET [IsHoldingCode] = 1
WHERE [Number] = N'00.02' AND [IsHoldingCode] = 0;
GO

GO

CREATE OR ALTER VIEW [dbo].[vw_SubCostCode]
AS
    SELECT
        sc.[Id],
        sc.[PublicId],
        sc.[RowVersion],
        CONVERT(VARCHAR(19), sc.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), sc.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        sc.[Number],
        sc.[Name],
        sc.[Description],
        sc.[CostCodeId],
        sc.[Aliases],
        sc.[QboActive],
        sc.[QboId],
        sc.[RealmId],
        sc.[IsHoldingCode]
    FROM dbo.[SubCostCode] sc;
GO
