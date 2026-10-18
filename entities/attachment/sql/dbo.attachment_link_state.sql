-- Contract: ReadAttachmentLinkState(
--   @AttachmentId BIGINT,
--   @ActorUserId BIGINT = NULL,
--   @CompanyId BIGINT = NULL,
--   @IsSystemAdmin BIT = 0
-- ) -> one row (IsLinked BIT, IsTaxpayer BIT).
-- IsLinked: not readable by caller (ownership / company) OR AttachmentId appears in any parent link table:
--   dbo.AssetAttachment, dbo.BillCreditLineItemAttachment, dbo.BillLineItemAttachment,
--   dbo.BillTextCodingSuggestion, dbo.BusinessLicenseAttachment, dbo.CertificateOfInsurance,
--   dbo.CodingSuggestion, dbo.ContractorsLicenseAttachment, dbo.ExpenseLineItemAttachment,
--   dbo.InvoiceAttachment, dbo.InvoiceLineItemAttachment, dbo.TaxpayerAttachment,
--   ms.DriveItemAttachment, box.File.
--   (dbo.MsMessageAttachment.AttachmentId is Graph metadata NVARCHAR — not dbo.Attachment.Id.)
-- IsTaxpayer: AttachmentId appears in dbo.TaxpayerAttachment (masking hint; also counts as linked).
-- Build: entities/attachment/sql/README.md

GO

CREATE OR ALTER PROCEDURE dbo.ReadAttachmentLinkState
    @AttachmentId BIGINT,
    @ActorUserId BIGINT = NULL,
    @CompanyId BIGINT = NULL,
    @IsSystemAdmin BIT = 0
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @IsReadable BIT = 0;

    IF @IsSystemAdmin = 1
        SET @IsReadable = 1;
    ELSE IF @ActorUserId IS NOT NULL AND @CompanyId IS NOT NULL
       AND EXISTS (
            SELECT 1
            FROM dbo.[Attachment] a WITH (NOLOCK)
            WHERE a.[Id] = @AttachmentId
              AND a.[CreatedByUserId] = @ActorUserId
              AND a.[CompanyId] = @CompanyId
       )
        SET @IsReadable = 1;

    SELECT
        CAST(
            CASE
                WHEN @IsReadable = 0 THEN 1
                WHEN EXISTS (
                    SELECT 1 FROM dbo.[BillLineItemAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[ExpenseLineItemAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[BillCreditLineItemAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[InvoiceAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[InvoiceLineItemAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[BusinessLicenseAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[ContractorsLicenseAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[AssetAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM ms.[DriveItemAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[TaxpayerAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[BillTextCodingSuggestion] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[CodingSuggestion] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM dbo.[CertificateOfInsurance] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                OR EXISTS (
                    SELECT 1 FROM box.[File] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                THEN 1
                ELSE 0
            END AS BIT
        ) AS IsLinked,
        CAST(
            CASE
                WHEN EXISTS (
                    SELECT 1 FROM dbo.[TaxpayerAttachment] WITH (NOLOCK)
                    WHERE [AttachmentId] = @AttachmentId
                )
                THEN 1
                ELSE 0
            END AS BIT
        ) AS IsTaxpayer;
END;
GO
