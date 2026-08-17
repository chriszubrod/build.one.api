IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_SubCostCode_CostCodeId' AND object_id = OBJECT_ID('dbo.SubCostCode'))
BEGIN
    CREATE INDEX [IX_SubCostCode_CostCodeId] ON [dbo].[SubCostCode] ([CostCodeId]) INCLUDE ([Number]);
END
GO
