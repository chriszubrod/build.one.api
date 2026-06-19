-- =============================================
-- Table: [qbo].[VendorCreditLineItemBillCreditLineItem]
-- Description: Mapping between QBO VendorCreditLine and local BillCreditLineItem
-- =============================================

IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'qbo')
BEGIN
    EXEC('CREATE SCHEMA [qbo]')
END
GO

IF OBJECT_ID('qbo.VendorCreditLineItemBillCreditLineItem', 'U') IS NULL
BEGIN
    CREATE TABLE [qbo].[VendorCreditLineItemBillCreditLineItem] (
        [Id] INT IDENTITY(1,1) PRIMARY KEY,
        [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [RowVersion] ROWVERSION NOT NULL,
        [CreatedDatetime] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        [ModifiedDatetime] DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        [QboVendorCreditLineId] BIGINT NOT NULL,
        [BillCreditLineItemId] BIGINT NOT NULL,
        CONSTRAINT [UQ_VCLIBCLIMapping_QboVendorCreditLineId] UNIQUE ([QboVendorCreditLineId]),
        CONSTRAINT [UQ_VCLIBCLIMapping_BillCreditLineItemId] UNIQUE ([BillCreditLineItemId]),
        CONSTRAINT [FK_VCLIBCLIMapping_QboVendorCreditLine] FOREIGN KEY ([QboVendorCreditLineId]) 
            REFERENCES [qbo].[VendorCreditLine]([Id]) ON DELETE CASCADE,
        CONSTRAINT [FK_VCLIBCLIMapping_BillCreditLineItem] FOREIGN KEY ([BillCreditLineItemId]) 
            REFERENCES [dbo].[BillCreditLineItem]([Id]) ON DELETE CASCADE
    )
    
    IF OBJECT_ID('qbo.VendorCreditLineItemBillCreditLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VCLIBCLIMapping_QboVendorCreditLineId' AND object_id = OBJECT_ID('qbo.VendorCreditLineItemBillCreditLineItem'))
    BEGIN
    CREATE INDEX [IX_VCLIBCLIMapping_QboVendorCreditLineId] ON [qbo].[VendorCreditLineItemBillCreditLineItem]([QboVendorCreditLineId])
    END
    IF OBJECT_ID('qbo.VendorCreditLineItemBillCreditLineItem', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_VCLIBCLIMapping_BillCreditLineItemId' AND object_id = OBJECT_ID('qbo.VendorCreditLineItemBillCreditLineItem'))
    BEGIN
    CREATE INDEX [IX_VCLIBCLIMapping_BillCreditLineItemId] ON [qbo].[VendorCreditLineItemBillCreditLineItem]([BillCreditLineItemId])
    END
END
GO

-- =============================================
-- Stored Procedure: CreateVendorCreditLineItemBillCreditLineItem
-- =============================================
GO

-- NOTE: procedures live in dbo (call_procedure issues EXEC dbo.{name}); the TABLE
-- stays in the qbo schema. Mirrors Bill's mapping sprocs.
CREATE OR ALTER PROCEDURE [dbo].[CreateVendorCreditLineItemBillCreditLineItem]
    @QboVendorCreditLineId BIGINT,
    @BillCreditLineItemId BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    
    INSERT INTO [qbo].[VendorCreditLineItemBillCreditLineItem] (
        [QboVendorCreditLineId],
        [BillCreditLineItemId]
    )
    OUTPUT 
        inserted.[Id],
        inserted.[PublicId],
        inserted.[RowVersion],
        inserted.[CreatedDatetime],
        inserted.[ModifiedDatetime],
        inserted.[QboVendorCreditLineId],
        inserted.[BillCreditLineItemId]
    VALUES (
        @QboVendorCreditLineId,
        @BillCreditLineItemId
    )
END
GO

-- =============================================
-- Stored Procedure: ReadVendorCreditLineItemBillCreditLineItemByQboLineId
-- =============================================
GO

-- NOTE: procedures live in dbo (call_procedure issues EXEC dbo.{name}); the TABLE
-- stays in the qbo schema. Mirrors Bill's mapping sprocs.
CREATE OR ALTER PROCEDURE [dbo].[ReadVendorCreditLineItemBillCreditLineItemByQboLineId]
    @QboVendorCreditLineId BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT 
        [Id],
        [PublicId],
        [RowVersion],
        [CreatedDatetime],
        [ModifiedDatetime],
        [QboVendorCreditLineId],
        [BillCreditLineItemId]
    FROM [qbo].[VendorCreditLineItemBillCreditLineItem]
    WHERE [QboVendorCreditLineId] = @QboVendorCreditLineId
END
GO

-- =============================================
-- Stored Procedure: ReadVendorCreditLineItemBillCreditLineItemByBillCreditLineItemId
-- =============================================
GO

-- NOTE: procedures live in dbo (call_procedure issues EXEC dbo.{name}); the TABLE
-- stays in the qbo schema. Mirrors Bill's mapping sprocs.
CREATE OR ALTER PROCEDURE [dbo].[ReadVendorCreditLineItemBillCreditLineItemByBillCreditLineItemId]
    @BillCreditLineItemId BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT 
        [Id],
        [PublicId],
        [RowVersion],
        [CreatedDatetime],
        [ModifiedDatetime],
        [QboVendorCreditLineId],
        [BillCreditLineItemId]
    FROM [qbo].[VendorCreditLineItemBillCreditLineItem]
    WHERE [BillCreditLineItemId] = @BillCreditLineItemId
END
GO

-- =============================================
-- Stored Procedure: DeleteVendorCreditLineItemBillCreditLineItemById
-- Used to drop a stale line mapping when its qbo.VendorCreditLine is removed
-- (snapshot stale-line cleanup), and to clear a dangling mapping during upsert.
-- =============================================
GO

-- NOTE: procedures live in dbo (call_procedure issues EXEC dbo.{name}); the TABLE
-- stays in the qbo schema. Mirrors Bill's mapping sprocs.
CREATE OR ALTER PROCEDURE [dbo].[DeleteVendorCreditLineItemBillCreditLineItemById]
    @Id BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM [qbo].[VendorCreditLineItemBillCreditLineItem] WHERE [Id] = @Id;
END
GO
