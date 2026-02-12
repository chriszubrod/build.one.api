-- Add BillLineItemId to ContractLaborLineItem for link-back from Contract Labor to Bill/BillLineItem
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE object_id = OBJECT_ID('dbo.ContractLaborLineItem') AND name = 'BillLineItemId'
)
BEGIN
    ALTER TABLE dbo.[ContractLaborLineItem]
    ADD [BillLineItemId] BIGINT NULL;

    ALTER TABLE dbo.[ContractLaborLineItem]
    ADD CONSTRAINT [FK_ContractLaborLineItem_BillLineItem]
    FOREIGN KEY ([BillLineItemId]) REFERENCES dbo.[BillLineItem]([Id]) ON DELETE SET NULL;
END
GO

-- Update procedures to include BillLineItemId in OUTPUT/SELECT/UPDATE
GO

CREATE OR ALTER PROCEDURE CreateContractLaborLineItem
(
    @ContractLaborId BIGINT,
    @LineDate DATE NULL,
    @ProjectId BIGINT NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Hours DECIMAL(6,2) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsBillable BIT = 1
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ContractLaborLineItem] (
        [CreatedDatetime], [ModifiedDatetime], [ContractLaborId], [LineDate], [ProjectId], [SubCostCodeId],
        [Description], [Hours], [Rate], [Markup], [Price], [IsBillable]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ContractLaborId],
        CONVERT(VARCHAR(10), INSERTED.[LineDate], 120) AS [LineDate],
        INSERTED.[ProjectId],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        INSERTED.[Hours],
        INSERTED.[Rate],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsBillable],
        INSERTED.[BillLineItemId]
    VALUES (
        @Now, @Now, @ContractLaborId, @LineDate, @ProjectId, @SubCostCodeId,
        @Description, @Hours, @Rate, @Markup, @Price, @IsBillable
    );

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractLaborLineItemsByContractLaborId
(
    @ContractLaborId BIGINT
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
        [ContractLaborId],
        CONVERT(VARCHAR(10), [LineDate], 120) AS [LineDate],
        [ProjectId],
        [SubCostCodeId],
        [Description],
        [Hours],
        [Rate],
        [Markup],
        [Price],
        [IsBillable],
        [BillLineItemId]
    FROM dbo.[ContractLaborLineItem]
    WHERE [ContractLaborId] = @ContractLaborId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractLaborLineItemById
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
        [ContractLaborId],
        CONVERT(VARCHAR(10), [LineDate], 120) AS [LineDate],
        [ProjectId],
        [SubCostCodeId],
        [Description],
        [Hours],
        [Rate],
        [Markup],
        [Price],
        [IsBillable],
        [BillLineItemId]
    FROM dbo.[ContractLaborLineItem]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadContractLaborLineItemByPublicId
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
        [ContractLaborId],
        CONVERT(VARCHAR(10), [LineDate], 120) AS [LineDate],
        [ProjectId],
        [SubCostCodeId],
        [Description],
        [Hours],
        [Rate],
        [Markup],
        [Price],
        [IsBillable],
        [BillLineItemId]
    FROM dbo.[ContractLaborLineItem]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE UpdateContractLaborLineItemById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @LineDate DATE NULL,
    @ProjectId BIGINT NULL,
    @SubCostCodeId BIGINT NULL,
    @Description NVARCHAR(MAX) NULL,
    @Hours DECIMAL(6,2) NULL,
    @Rate DECIMAL(18,4) NULL,
    @Markup DECIMAL(18,4) NULL,
    @Price DECIMAL(18,2) NULL,
    @IsBillable BIT = 1,
    @BillLineItemId BIGINT NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ContractLaborLineItem]
    SET
        [ModifiedDatetime] = @Now,
        [LineDate] = @LineDate,
        [ProjectId] = @ProjectId,
        [SubCostCodeId] = @SubCostCodeId,
        [Description] = @Description,
        [Hours] = @Hours,
        [Rate] = @Rate,
        [Markup] = @Markup,
        [Price] = @Price,
        [IsBillable] = @IsBillable,
        [BillLineItemId] = @BillLineItemId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ContractLaborId],
        CONVERT(VARCHAR(10), INSERTED.[LineDate], 120) AS [LineDate],
        INSERTED.[ProjectId],
        INSERTED.[SubCostCodeId],
        INSERTED.[Description],
        INSERTED.[Hours],
        INSERTED.[Rate],
        INSERTED.[Markup],
        INSERTED.[Price],
        INSERTED.[IsBillable],
        INSERTED.[BillLineItemId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE DeleteContractLaborLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ContractLaborLineItem]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ContractLaborId],
        CONVERT(VARCHAR(10), DELETED.[LineDate], 120) AS [LineDate],
        DELETED.[ProjectId],
        DELETED.[SubCostCodeId],
        DELETED.[Description],
        DELETED.[Hours],
        DELETED.[Rate],
        DELETED.[Markup],
        DELETED.[Price],
        DELETED.[IsBillable],
        DELETED.[BillLineItemId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
