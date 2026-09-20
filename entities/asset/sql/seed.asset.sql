-- TEMPLATE ONLY — real seed data for the asset register is applied by the EM at deploy time.
-- Do not run this file against production from an agent session.

DECLARE @CompanyId INT = 1;
DECLARE @ExampleQboFixedAssetAccountId NVARCHAR(50) = N'123456'; -- qbo.Account.QboId, not qbo.Account.Id

IF NOT EXISTS (
    SELECT 1 FROM dbo.[Asset]
    WHERE [CompanyId] = @CompanyId AND [Name] = N'Example Toro mower (seed template)'
)
BEGIN
    INSERT INTO dbo.[Asset]
        ([Name], [AssetType], [Make], [Model], [ModelYear], [SerialNumber], [Status],
         [QboFixedAssetAccountId], [CompanyId], [CreatedByUserId])
    VALUES
        (N'Example Toro mower (seed template)', N'machinery', N'Toro', N'GrandStand', 2022,
         N'SN-TEMPLATE-0001', N'active', @ExampleQboFixedAssetAccountId, @CompanyId, 17);
END
GO
