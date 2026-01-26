-- =============================================================================
-- MS Message to Bill Connector Tables and Stored Procedures
-- =============================================================================

-- Link between MsMessage and Bill
IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'MsMessageBill')
BEGIN
    CREATE TABLE dbo.MsMessageBill (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        RowVersion ROWVERSION NOT NULL,
        CreatedDatetime DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        ModifiedDatetime DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        
        MsMessageId INT NOT NULL,
        BillId BIGINT NOT NULL,
        
        -- Optional notes about the relationship
        Notes NVARCHAR(1000) NULL,
        
        CONSTRAINT FK_MsMessageBill_MsMessage 
            FOREIGN KEY (MsMessageId) REFERENCES dbo.MsMessage(Id) ON DELETE CASCADE,
        CONSTRAINT FK_MsMessageBill_Bill 
            FOREIGN KEY (BillId) REFERENCES dbo.Bill(Id) ON DELETE CASCADE,
        CONSTRAINT UQ_MsMessageBill_PublicId UNIQUE (PublicId),
        CONSTRAINT UQ_MsMessageBill_Unique UNIQUE (MsMessageId, BillId)
    );
    
    CREATE INDEX IX_MsMessageBill_MsMessageId ON dbo.MsMessageBill(MsMessageId);
    CREATE INDEX IX_MsMessageBill_BillId ON dbo.MsMessageBill(BillId);
END
GO


-- Create link between message and bill
CREATE OR ALTER PROCEDURE dbo.CreateMsMessageBill
    @MsMessageId INT,
    @BillId BIGINT,
    @Notes NVARCHAR(1000) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    INSERT INTO dbo.MsMessageBill (MsMessageId, BillId, Notes)
    VALUES (@MsMessageId, @BillId, @Notes);
    
    SELECT * FROM dbo.MsMessageBill WHERE Id = SCOPE_IDENTITY();
END
GO

-- Read all links
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageBills
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageBill ORDER BY CreatedDatetime DESC;
END
GO

-- Read by public ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageBillByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageBill WHERE PublicId = @PublicId;
END
GO

-- Read by message ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageBillsByMsMessageId
    @MsMessageId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageBill WHERE MsMessageId = @MsMessageId;
END
GO

-- Read by bill ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageBillsByBillId
    @BillId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageBill WHERE BillId = @BillId;
END
GO

-- Update notes
CREATE OR ALTER PROCEDURE dbo.UpdateMsMessageBillByPublicId
    @PublicId UNIQUEIDENTIFIER,
    @Notes NVARCHAR(1000) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE dbo.MsMessageBill
    SET Notes = @Notes,
        ModifiedDatetime = GETUTCDATE()
    WHERE PublicId = @PublicId;
    
    SELECT * FROM dbo.MsMessageBill WHERE PublicId = @PublicId;
END
GO

-- Delete link
CREATE OR ALTER PROCEDURE dbo.DeleteMsMessageBillByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @Deleted TABLE (
        Id INT, PublicId UNIQUEIDENTIFIER, RowVersion VARBINARY(8),
        CreatedDatetime DATETIME2, ModifiedDatetime DATETIME2,
        MsMessageId INT, BillId INT, Notes NVARCHAR(1000)
    );
    
    DELETE FROM dbo.MsMessageBill
    OUTPUT DELETED.*
    INTO @Deleted
    WHERE PublicId = @PublicId;
    
    SELECT * FROM @Deleted;
END
GO
