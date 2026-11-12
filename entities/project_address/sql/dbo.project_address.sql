GO

IF OBJECT_ID('dbo.ProjectAddress', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ProjectAddress]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ProjectId] BIGINT NULL,
    [AddressId] BIGINT NULL,
    [AddressTypeId] BIGINT NULL,
    CONSTRAINT [FK_ProjectAddress_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]),
    CONSTRAINT [FK_ProjectAddress_Address] FOREIGN KEY ([AddressId]) REFERENCES [dbo].[Address]([Id]),
    CONSTRAINT [FK_ProjectAddress_AddressType] FOREIGN KEY ([AddressTypeId]) REFERENCES [dbo].[AddressType]([Id])
);
END
GO

-- U-789: idempotent column-add so a from-scratch build of this file matches prod,
-- whose dbo.ProjectAddress.CreatedByUserId is BIGINT NOT NULL with DEFAULT (17) — matched to prod. Added out-of-band (Gap 2 / Phase 4
-- attribution) before this base file became canonical, so the CREATE TABLE above
-- never declared it while the Create sproc below writes it. No-op against prod.
IF OBJECT_ID('dbo.ProjectAddress', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.ProjectAddress') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[ProjectAddress] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_ProjectAddress_CreatedByUserId] DEFAULT (17);
END
GO




GO

-- U-789 (2026-11-12): body replaced with the LIVE prod definition, taken verbatim from
--   scripts/migrations/gap2_reference_threading.sql
-- and byte-verified against prod OBJECT_DEFINITION. The copy here was the STALE side
-- (no @CreatedByUserId), and base files are re-run routinely, so an apply would have
-- dropped the param and stopped populating the column. Same shape as the three
-- attachment Create sprocs retired from tests/sproc_drift_ledger.py before it.
CREATE OR ALTER PROCEDURE CreateProjectAddress
(
    @ProjectId BIGINT,
    @AddressId BIGINT,
    @AddressTypeId BIGINT,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[ProjectAddress] ([CreatedDatetime], [ModifiedDatetime], [ProjectId], [AddressId], [AddressTypeId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[AddressId],
        INSERTED.[AddressTypeId]
    VALUES (@Now, @Now, @ProjectId, @AddressId, @AddressTypeId, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadProjectAddresses
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    ORDER BY [ProjectId] ASC, [AddressId] ASC, [AddressTypeId] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadProjectAddressById
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
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadProjectAddressByPublicId
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
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;


GO

-- NB the ORDER BY below is an intended change that prod does NOT have (verified
-- 2026-11-12): live ReadProjectAddressByProjectId returns rows unordered. Applying
-- this file therefore makes the read deterministic — a deliberate behaviour change,
-- not a no-op. Same shape as the still-open ReadRoleModules ORDER BY item from U-048.
CREATE OR ALTER PROCEDURE ReadProjectAddressByProjectId
(
    @ProjectId BIGINT
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
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    WHERE [ProjectId] = @ProjectId
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE ReadProjectAddressByAddressId
(
    @AddressId BIGINT
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
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    WHERE [AddressId] = @AddressId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadProjectAddressByAddressTypeId
(
    @AddressTypeId BIGINT
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
        [ProjectId],
        [AddressId],
        [AddressTypeId]
    FROM dbo.[ProjectAddress]
    WHERE [AddressTypeId] = @AddressTypeId;

    COMMIT TRANSACTION;
END;







GO

CREATE OR ALTER PROCEDURE UpdateProjectAddressById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @ProjectId BIGINT,
    @AddressId BIGINT,
    @AddressTypeId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[ProjectAddress]
    SET
        [ModifiedDatetime] = @Now,
        [ProjectId] = @ProjectId,
        [AddressId] = @AddressId,
        [AddressTypeId] = @AddressTypeId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProjectId],
        INSERTED.[AddressId],
        INSERTED.[AddressTypeId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE DeleteProjectAddressById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ProjectAddress]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[ProjectId],
        DELETED.[AddressId],
        DELETED.[AddressTypeId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;


GO

CREATE OR ALTER PROCEDURE DeleteProjectAddressByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[ProjectAddress]
    WHERE [ProjectId] = @ProjectId;

    COMMIT TRANSACTION;
END;
