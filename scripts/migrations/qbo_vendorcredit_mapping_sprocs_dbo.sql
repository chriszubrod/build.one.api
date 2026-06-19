-- Move VendorCreditLineItemBillCreditLineItem mapping sprocs into dbo (2026-06-18).
-- These were created in the [qbo] schema, but call_procedure() always issues
-- "EXEC dbo.{name}", so every call resolved to a non-existent dbo proc and failed.
-- Symptom: the mapping create was silently swallowed (warning) so the table was
-- effectively unpopulated (4 rows vs Bill's ~22k); the new upsert path's
-- read_by_qbo_line_id errored on every credit line. Recreate in dbo to match
-- Bill's convention + call_procedure. The TABLE stays in the qbo schema.
-- Idempotent (CREATE OR ALTER). Old qbo.* copies are harmless (unused).
--   python scripts/run_sql.py scripts/migrations/qbo_vendorcredit_mapping_sprocs_dbo.sql

CREATE OR ALTER PROCEDURE [dbo].[CreateVendorCreditLineItemBillCreditLineItem]
    @QboVendorCreditLineId BIGINT,
    @BillCreditLineItemId BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    INSERT INTO [qbo].[VendorCreditLineItemBillCreditLineItem] (
        [QboVendorCreditLineId], [BillCreditLineItemId]
    )
    OUTPUT
        inserted.[Id], inserted.[PublicId], inserted.[RowVersion],
        inserted.[CreatedDatetime], inserted.[ModifiedDatetime],
        inserted.[QboVendorCreditLineId], inserted.[BillCreditLineItemId]
    VALUES (@QboVendorCreditLineId, @BillCreditLineItemId);
END
GO

CREATE OR ALTER PROCEDURE [dbo].[ReadVendorCreditLineItemBillCreditLineItemByQboLineId]
    @QboVendorCreditLineId BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [Id], [PublicId], [RowVersion], [CreatedDatetime], [ModifiedDatetime],
        [QboVendorCreditLineId], [BillCreditLineItemId]
    FROM [qbo].[VendorCreditLineItemBillCreditLineItem]
    WHERE [QboVendorCreditLineId] = @QboVendorCreditLineId;
END
GO

CREATE OR ALTER PROCEDURE [dbo].[ReadVendorCreditLineItemBillCreditLineItemByBillCreditLineItemId]
    @BillCreditLineItemId BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    SELECT
        [Id], [PublicId], [RowVersion], [CreatedDatetime], [ModifiedDatetime],
        [QboVendorCreditLineId], [BillCreditLineItemId]
    FROM [qbo].[VendorCreditLineItemBillCreditLineItem]
    WHERE [BillCreditLineItemId] = @BillCreditLineItemId;
END
GO

CREATE OR ALTER PROCEDURE [dbo].[DeleteVendorCreditLineItemBillCreditLineItemById]
    @Id BIGINT
AS
BEGIN
    SET NOCOUNT ON;
    DELETE FROM [qbo].[VendorCreditLineItemBillCreditLineItem] WHERE [Id] = @Id;
END
GO
