-- =============================================================================
-- MS Message to Vendor Connector Tables and Stored Procedures
-- =============================================================================

-- Link between MsMessage and Vendor
IF OBJECT_ID('dbo.MsMessageVendor', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.MsMessageVendor (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        RowVersion ROWVERSION NOT NULL,
        CreatedDatetime DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        ModifiedDatetime DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        
        MsMessageId INT NOT NULL,
        VendorId BIGINT NOT NULL,
        
        -- Optional notes about the relationship
        Notes NVARCHAR(1000) NULL,
        
        CONSTRAINT FK_MsMessageVendor_MsMessage 
            FOREIGN KEY (MsMessageId) REFERENCES dbo.MsMessage(Id) ON DELETE CASCADE,
        CONSTRAINT FK_MsMessageVendor_Vendor 
            FOREIGN KEY (VendorId) REFERENCES dbo.Vendor(Id) ON DELETE CASCADE,
        CONSTRAINT UQ_MsMessageVendor_PublicId UNIQUE (PublicId),
        CONSTRAINT UQ_MsMessageVendor_Unique UNIQUE (MsMessageId, VendorId)
    );
    
    IF OBJECT_ID('dbo.MsMessageVendor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessageVendor_MsMessageId' AND object_id = OBJECT_ID('dbo.MsMessageVendor'))
    BEGIN
    CREATE INDEX IX_MsMessageVendor_MsMessageId ON dbo.MsMessageVendor(MsMessageId);
    END
    IF OBJECT_ID('dbo.MsMessageVendor', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessageVendor_VendorId' AND object_id = OBJECT_ID('dbo.MsMessageVendor'))
    BEGIN
    CREATE INDEX IX_MsMessageVendor_VendorId ON dbo.MsMessageVendor(VendorId);
    END
END
GO


-- Create link between message and vendor
CREATE OR ALTER PROCEDURE dbo.CreateMsMessageVendor
    @MsMessageId INT,
    @VendorId BIGINT,
    @Notes NVARCHAR(1000) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    INSERT INTO dbo.MsMessageVendor (MsMessageId, VendorId, Notes)
    VALUES (@MsMessageId, @VendorId, @Notes);
    
    SELECT * FROM dbo.MsMessageVendor WHERE Id = SCOPE_IDENTITY();
END
GO

-- Read all links
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageVendors
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageVendor ORDER BY CreatedDatetime DESC;
END
GO

-- Read by public ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageVendorByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageVendor WHERE PublicId = @PublicId;
END
GO

-- Read by message ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageVendorsByMsMessageId
    @MsMessageId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageVendor WHERE MsMessageId = @MsMessageId;
END
GO

-- Read by vendor ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageVendorsByVendorId
    @VendorId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageVendor WHERE VendorId = @VendorId;
END
GO

-- Update notes
CREATE OR ALTER PROCEDURE dbo.UpdateMsMessageVendorByPublicId
    @PublicId UNIQUEIDENTIFIER,
    @Notes NVARCHAR(1000) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE dbo.MsMessageVendor
    SET Notes = @Notes,
        ModifiedDatetime = GETUTCDATE()
    WHERE PublicId = @PublicId;
    
    SELECT * FROM dbo.MsMessageVendor WHERE PublicId = @PublicId;
END
GO

-- Delete link
CREATE OR ALTER PROCEDURE dbo.DeleteMsMessageVendorByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @Deleted TABLE (
        Id INT, PublicId UNIQUEIDENTIFIER, RowVersion VARBINARY(8),
        CreatedDatetime DATETIME2, ModifiedDatetime DATETIME2,
        MsMessageId INT, VendorId INT, Notes NVARCHAR(1000)
    );
    
    DELETE FROM dbo.MsMessageVendor
    OUTPUT DELETED.*
    INTO @Deleted
    WHERE PublicId = @PublicId;
    
    SELECT * FROM @Deleted;
END
GO
