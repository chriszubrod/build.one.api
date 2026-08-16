-- U-238c: Phase-4 reverse-lookup contract — given a QBO Customer/Item id, which dbo
-- row and which table. Read-only views; purely additive.

CREATE OR ALTER VIEW [dbo].[vw_QboCustomerIdentity]
AS
    SELECT 'Customer' AS [EntityType], [Id] AS [DboId], [QboId], [RealmId]
    FROM [dbo].[Customer]
    WHERE [QboId] IS NOT NULL
    UNION ALL
    SELECT 'Project', [Id], [QboId], [RealmId]
    FROM [dbo].[Project]
    WHERE [QboId] IS NOT NULL;
GO

CREATE OR ALTER VIEW [dbo].[vw_QboItemIdentity]
AS
    SELECT 'CostCode' AS [EntityType], [Id] AS [DboId], [QboId], [RealmId]
    FROM [dbo].[CostCode]
    WHERE [QboId] IS NOT NULL
    UNION ALL
    SELECT 'SubCostCode', [Id], [QboId], [RealmId]
    FROM [dbo].[SubCostCode]
    WHERE [QboId] IS NOT NULL;
GO
