DECLARE @TradesmanId BIGINT = (SELECT [Id] FROM dbo.[VendorType] WHERE [Name] = 'Tradesman');
IF @TradesmanId IS NOT NULL
BEGIN
    IF NOT EXISTS (SELECT 1 FROM dbo.[VendorTypeRequiredCoverage] WHERE [VendorTypeId]=@TradesmanId AND [CoverageType]='GL')
        INSERT INTO dbo.[VendorTypeRequiredCoverage] ([VendorTypeId],[CoverageType]) VALUES (@TradesmanId,'GL');
    IF NOT EXISTS (SELECT 1 FROM dbo.[VendorTypeRequiredCoverage] WHERE [VendorTypeId]=@TradesmanId AND [CoverageType]='WC')
        INSERT INTO dbo.[VendorTypeRequiredCoverage] ([VendorTypeId],[CoverageType]) VALUES (@TradesmanId,'WC');
END
GO
