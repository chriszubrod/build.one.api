-- VendorCredit line sync: upsert-in-place parity with Bill (2026-06-18).
-- Adds the sprocs needed to match qbo.VendorCreditLine rows by stable QBO Line.Id
-- across re-pulls (instead of delete-then-recreate), keeping row PKs + the
-- VendorCreditLineItemBillCreditLineItem mapping stable so the connector can
-- update BillCreditLineItems in place (preserving attachments + invoice FK links).
-- Idempotent (CREATE OR ALTER). Run with:
--   python scripts/run_sql.py scripts/migrations/qbo_vendorcredit_upsert_inplace.sql

CREATE OR ALTER PROCEDURE ReadQboVendorCreditLineByVendorCreditIdAndQboLineId
(
    @QboVendorCreditId BIGINT,
    @QboLineId NVARCHAR(50)
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [QboVendorCreditId], [QboLineId], [LineNum],
        [Description], [Amount], [DetailType],
        [ItemRefValue], [ItemRefName], [ClassRefValue], [ClassRefName],
        [UnitPrice], [Qty], [BillableStatus],
        [CustomerRefValue], [CustomerRefName],
        [AccountRefValue], [AccountRefName]
    FROM [qbo].[VendorCreditLine]
    WHERE [QboVendorCreditId] = @QboVendorCreditId AND [QboLineId] = @QboLineId;
END;
GO


CREATE OR ALTER PROCEDURE UpdateQboVendorCreditLineById
(
    @Id BIGINT,
    @QboLineId NVARCHAR(50) NULL,
    @LineNum INT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Amount DECIMAL(18,2) NULL,
    @DetailType NVARCHAR(50) NULL,
    @ItemRefValue NVARCHAR(50) NULL,
    @ItemRefName NVARCHAR(255) NULL,
    @ClassRefValue NVARCHAR(50) NULL,
    @ClassRefName NVARCHAR(255) NULL,
    @UnitPrice DECIMAL(18,4) NULL,
    @Qty DECIMAL(18,4) NULL,
    @BillableStatus NVARCHAR(50) NULL,
    @CustomerRefValue NVARCHAR(50) NULL,
    @CustomerRefName NVARCHAR(255) NULL,
    @AccountRefValue NVARCHAR(50) NULL,
    @AccountRefName NVARCHAR(255) NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE [qbo].[VendorCreditLine]
    SET
        [ModifiedDatetime] = @Now,
        [QboLineId] = @QboLineId,
        [LineNum] = @LineNum,
        [Description] = @Description,
        [Amount] = @Amount,
        [DetailType] = @DetailType,
        [ItemRefValue] = @ItemRefValue,
        [ItemRefName] = @ItemRefName,
        [ClassRefValue] = @ClassRefValue,
        [ClassRefName] = @ClassRefName,
        [UnitPrice] = @UnitPrice,
        [Qty] = @Qty,
        [BillableStatus] = @BillableStatus,
        [CustomerRefValue] = @CustomerRefValue,
        [CustomerRefName] = @CustomerRefName,
        [AccountRefValue] = @AccountRefValue,
        [AccountRefName] = @AccountRefName
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[QboVendorCreditId], INSERTED.[QboLineId], INSERTED.[LineNum],
        INSERTED.[Description], INSERTED.[Amount], INSERTED.[DetailType],
        INSERTED.[ItemRefValue], INSERTED.[ItemRefName], INSERTED.[ClassRefValue], INSERTED.[ClassRefName],
        INSERTED.[UnitPrice], INSERTED.[Qty], INSERTED.[BillableStatus],
        INSERTED.[CustomerRefValue], INSERTED.[CustomerRefName],
        INSERTED.[AccountRefValue], INSERTED.[AccountRefName]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteQboVendorCreditLineById
(
    @Id BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM [qbo].[VendorCreditLine] WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE [qbo].[DeleteVendorCreditLineItemBillCreditLineItemById]
    @Id BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM [qbo].[VendorCreditLineItemBillCreditLineItem] WHERE [Id] = @Id;
END
GO
