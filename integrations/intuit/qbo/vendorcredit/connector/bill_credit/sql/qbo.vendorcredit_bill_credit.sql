-- =============================================
-- Table: [qbo].[VendorCreditBillCredit]
-- Description: Mapping between QBO VendorCredit and local BillCredit
-- =============================================

IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'qbo')
BEGIN
    EXEC('CREATE SCHEMA [qbo]')
END
GO

IF OBJECT_ID('qbo.VendorCreditBillCredit', 'U') IS NULL
BEGIN
    CREATE TABLE [qbo].[VendorCreditBillCredit] (
        [Id] INT IDENTITY(1,1) PRIMARY KEY,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        [ModifiedDatetime] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        [QboVendorCreditId] INT NOT NULL,
        [BillCreditId] INT NOT NULL,
        CONSTRAINT [UQ_VendorCreditBillCredit_QboVendorCreditId] UNIQUE ([QboVendorCreditId]),
        CONSTRAINT [UQ_VendorCreditBillCredit_BillCreditId] UNIQUE ([BillCreditId]),
        CONSTRAINT [FK_VendorCreditBillCredit_QboVendorCredit] FOREIGN KEY ([QboVendorCreditId]) 
            REFERENCES [qbo].[VendorCredit]([Id]) ON DELETE CASCADE,
        CONSTRAINT [FK_VendorCreditBillCredit_BillCredit] FOREIGN KEY ([BillCreditId]) 
            REFERENCES [dbo].[BillCredit]([Id]) ON DELETE CASCADE
    )
    
    IF OBJECT_ID('qbo.VendorCreditBillCredit', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VendorCreditBillCredit_QboVendorCreditId' AND object_id = OBJECT_ID('qbo.VendorCreditBillCredit'))
    BEGIN
    CREATE INDEX [IX_VendorCreditBillCredit_QboVendorCreditId] ON [qbo].[VendorCreditBillCredit]([QboVendorCreditId])
    END
    IF OBJECT_ID('qbo.VendorCreditBillCredit', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VendorCreditBillCredit_BillCreditId' AND object_id = OBJECT_ID('qbo.VendorCreditBillCredit'))
    BEGIN
    CREATE INDEX [IX_VendorCreditBillCredit_BillCreditId] ON [qbo].[VendorCreditBillCredit]([BillCreditId])
    END
END
GO

-- =============================================
-- Stored Procedure: CreateVendorCreditBillCredit
-- =============================================
GO

CREATE OR ALTER PROCEDURE [qbo].[CreateVendorCreditBillCredit]
    @QboVendorCreditId INT,
    @BillCreditId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    INSERT INTO [qbo].[VendorCreditBillCredit] (
        [QboVendorCreditId],
        [BillCreditId]
    )
    OUTPUT 
        inserted.[Id],
        inserted.[PublicId],
        inserted.[RowVersion],
        inserted.[CreatedDatetime],
        inserted.[ModifiedDatetime],
        inserted.[QboVendorCreditId],
        inserted.[BillCreditId]
    VALUES (
        @QboVendorCreditId,
        @BillCreditId
    )
END
GO

-- =============================================
-- Stored Procedure: ReadVendorCreditBillCreditByQboVendorCreditId
-- =============================================
GO

CREATE OR ALTER PROCEDURE [qbo].[ReadVendorCreditBillCreditByQboVendorCreditId]
    @QboVendorCreditId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT 
        [Id],
        [PublicId],
        [RowVersion],
        [CreatedDatetime],
        [ModifiedDatetime],
        [QboVendorCreditId],
        [BillCreditId]
    FROM [qbo].[VendorCreditBillCredit]
    WHERE [QboVendorCreditId] = @QboVendorCreditId
END
GO

-- =============================================
-- Stored Procedure: ReadVendorCreditBillCreditByBillCreditId
-- =============================================
GO

CREATE OR ALTER PROCEDURE [qbo].[ReadVendorCreditBillCreditByBillCreditId]
    @BillCreditId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT 
        [Id],
        [PublicId],
        [RowVersion],
        [CreatedDatetime],
        [ModifiedDatetime],
        [QboVendorCreditId],
        [BillCreditId]
    FROM [qbo].[VendorCreditBillCredit]
    WHERE [BillCreditId] = @BillCreditId
END
GO

-- =============================================
-- Stored Procedure: DeleteVendorCreditBillCreditByQboVendorCreditId
-- =============================================
GO

CREATE OR ALTER PROCEDURE [qbo].[DeleteVendorCreditBillCreditByQboVendorCreditId]
    @QboVendorCreditId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    DELETE FROM [qbo].[VendorCreditBillCredit]
    WHERE [QboVendorCreditId] = @QboVendorCreditId
END
GO
