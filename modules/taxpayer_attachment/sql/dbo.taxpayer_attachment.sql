DROP TABLE IF EXISTS [dbo].[TaxpayerAttachment];
GO

CREATE TABLE [dbo].[TaxpayerAttachment]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [TaxpayerId] BIGINT NULL,
    [AttachmentId] BIGINT NULL
);
GO


DROP PROCEDURE IF EXISTS CreateTaxpayerAttachment;
GO

CREATE PROCEDURE CreateTaxpayerAttachment
(
    @TaxpayerId BIGINT,
    @AttachmentId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[TaxpayerAttachment] ([CreatedDatetime], [ModifiedDatetime], [TaxpayerId], [AttachmentId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TaxpayerId],
        INSERTED.[AttachmentId]
    VALUES (@Now, @Now, @TaxpayerId, @AttachmentId);

    COMMIT TRANSACTION;
END;

EXEC CreateTaxpayerAttachment
    @TaxpayerId = 1,
    @AttachmentId = 1;
GO


DROP PROCEDURE IF EXISTS ReadTaxpayerAttachments;
GO

CREATE PROCEDURE ReadTaxpayerAttachments
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [TaxpayerId],
        [AttachmentId]
    FROM dbo.[TaxpayerAttachment]
    ORDER BY [TaxpayerId] ASC, [AttachmentId] ASC;

    COMMIT TRANSACTION;
END;

EXEC ReadTaxpayerAttachments;
GO


DROP PROCEDURE IF EXISTS ReadTaxpayerAttachmentById;
GO

CREATE PROCEDURE ReadTaxpayerAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [TaxpayerId],
        [AttachmentId]
    FROM dbo.[TaxpayerAttachment]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC ReadTaxpayerAttachmentById
    @Id = 1;
GO


DROP PROCEDURE IF EXISTS ReadTaxpayerAttachmentByPublicId;
GO

CREATE PROCEDURE ReadTaxpayerAttachmentByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [TaxpayerId],
        [AttachmentId]
    FROM dbo.[TaxpayerAttachment]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;

EXEC ReadTaxpayerAttachmentByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO


DROP PROCEDURE IF EXISTS ReadTaxpayerAttachmentsByTaxpayerId;
GO

CREATE PROCEDURE ReadTaxpayerAttachmentsByTaxpayerId
(
    @TaxpayerId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [TaxpayerId],
        [AttachmentId]
    FROM dbo.[TaxpayerAttachment]
    WHERE [TaxpayerId] = @TaxpayerId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;

EXEC ReadTaxpayerAttachmentsByTaxpayerId
    @TaxpayerId = 1;
GO


DROP PROCEDURE IF EXISTS DeleteTaxpayerAttachmentById;
GO

CREATE PROCEDURE DeleteTaxpayerAttachmentById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[TaxpayerAttachment]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[TaxpayerId],
        DELETED.[AttachmentId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;

EXEC DeleteTaxpayerAttachmentById
    @Id = 1;
GO

