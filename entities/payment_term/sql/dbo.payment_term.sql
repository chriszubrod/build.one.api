GO

IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[PaymentTerm]
(
    [Id] BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Name] NVARCHAR(50) NOT NULL,
    [Description] NVARCHAR(255) NULL,
    [DiscountPercent] DECIMAL(5,2) NULL,
    [DiscountDays] INT NULL,
    [DueDays] INT NULL
);
END
GO

-- U-345: idempotent column-add so a from-scratch build of this file doesn't fail on the
-- CreatedByUserId param/INSERT-list references below — live since
-- scripts/migrations/gap2_created_by_user_id.sql / gap2_created_by_user_id_finalize.sql.
-- No-op against the live schema (column/FK already exist there).
IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns
                   WHERE object_id = OBJECT_ID('dbo.PaymentTerm') AND name = 'CreatedByUserId')
BEGIN
    ALTER TABLE [dbo].[PaymentTerm] ADD [CreatedByUserId] BIGINT NOT NULL
        CONSTRAINT [DF_PaymentTerm_CreatedByUserId] DEFAULT (17);
END
GO
IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_PaymentTerm_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[PaymentTerm] ADD CONSTRAINT [FK_PaymentTerm_CreatedByUser]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

-- Idempotent column add for existing environments (U-282). Live since migration
-- 238c_qbo_identity_reference.sql (2026-06) but never declared in this base file —
-- a fresh environment built from just the base file would fail at CREATE PROCEDURE
-- time the moment a sproc references [QboId]/[RealmId] (SQL error 207), the same trap
-- U-277 found and fixed for dbo.company.sql/dbo.address.sql. Declared here so a
-- from-scratch build matches prod. No-op-safe against live prod (columns AND the
-- unique index already exist there from 238c).
IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.PaymentTerm') AND name = 'QboId')
BEGIN
    ALTER TABLE [dbo].[PaymentTerm] ADD [QboId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.PaymentTerm') AND name = 'RealmId')
BEGIN
    ALTER TABLE [dbo].[PaymentTerm] ADD [RealmId] NVARCHAR(50) NULL;
END
GO

IF OBJECT_ID('dbo.PaymentTerm', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.indexes WHERE name = 'UQ_PaymentTerm_QboId_RealmId' AND object_id = OBJECT_ID('dbo.PaymentTerm')
)
BEGIN
    CREATE UNIQUE INDEX UQ_PaymentTerm_QboId_RealmId ON [dbo].[PaymentTerm] ([QboId], [RealmId]) WHERE [QboId] IS NOT NULL;
END
GO

-- Add QboActive column if it does not exist (U-275: dbo-native mirror of
-- qbo.Term.Active, replacing the read-side LEFT JOIN). NULL = no QBO
-- identity yet or not yet backfilled; populated at pull time via
-- SetPaymentTermQboIdentity, backfilled for existing rows by
-- scripts/backfill_qbo_active_mirror.py.
IF COL_LENGTH('dbo.PaymentTerm', 'QboActive') IS NULL
BEGIN
    ALTER TABLE [dbo].[PaymentTerm] ADD [QboActive] BIT NULL;
END
GO

-- ===== 5. CreatePaymentTerm =====
CREATE OR ALTER PROCEDURE CreatePaymentTerm
(
    @Name NVARCHAR(50),
    @Description NVARCHAR(255),
    @DiscountPercent DECIMAL(5,2) NULL,
    @DiscountDays INT NULL,
    @DueDays INT NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[PaymentTerm] ([CreatedDatetime], [ModifiedDatetime], [Name], [Description], [DiscountPercent], [DiscountDays], [DueDays], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[DiscountPercent],
        INSERTED.[DiscountDays],
        INSERTED.[DueDays]
    VALUES (@Now, @Now, @Name, @Description, @DiscountPercent, @DiscountDays, @DueDays, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE ReadPaymentTerms
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        pt.[Id],
        pt.[PublicId],
        pt.[RowVersion],
        CONVERT(VARCHAR(19), pt.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pt.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pt.[Name],
        pt.[Description],
        pt.[DiscountPercent],
        pt.[DiscountDays],
        pt.[DueDays],
        pt.[QboActive]
    FROM dbo.[PaymentTerm] pt
    ORDER BY pt.[Name] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadPaymentTermById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        pt.[Id],
        pt.[PublicId],
        pt.[RowVersion],
        CONVERT(VARCHAR(19), pt.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pt.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pt.[Name],
        pt.[Description],
        pt.[DiscountPercent],
        pt.[DiscountDays],
        pt.[DueDays],
        pt.[QboActive],
        pt.[QboId],
        pt.[RealmId]
    FROM dbo.[PaymentTerm] pt
    WHERE pt.[Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadPaymentTermByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        pt.[Id],
        pt.[PublicId],
        pt.[RowVersion],
        CONVERT(VARCHAR(19), pt.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pt.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pt.[Name],
        pt.[Description],
        pt.[DiscountPercent],
        pt.[DiscountDays],
        pt.[DueDays],
        pt.[QboActive]
    FROM dbo.[PaymentTerm] pt
    WHERE pt.[PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadPaymentTermByName
(
    @Name NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        pt.[Id],
        pt.[PublicId],
        pt.[RowVersion],
        CONVERT(VARCHAR(19), pt.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pt.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pt.[Name],
        pt.[Description],
        pt.[DiscountPercent],
        pt.[DiscountDays],
        pt.[DueDays],
        pt.[QboActive]
    FROM dbo.[PaymentTerm] pt
    WHERE pt.[Name] = @Name;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdatePaymentTermById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Name NVARCHAR(50),
    @Description NVARCHAR(255),
    @DiscountPercent DECIMAL(5,2) NULL,
    @DiscountDays INT NULL,
    @DueDays INT NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[PaymentTerm]
    SET
        [ModifiedDatetime] = @Now,
        [Name] = @Name,
        [Description] = @Description,
        [DiscountPercent] = @DiscountPercent,
        [DiscountDays] = @DiscountDays,
        [DueDays] = @DueDays
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Name],
        INSERTED.[Description],
        INSERTED.[DiscountPercent],
        INSERTED.[DiscountDays],
        INSERTED.[DueDays]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeletePaymentTermById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[PaymentTerm]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Name],
        DELETED.[Description],
        DELETED.[DiscountPercent],
        DELETED.[DiscountDays],
        DELETED.[DueDays]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- U-282 (Phase-4, term repoint): direct dbo-native identity lookup, mirroring
-- ReadBillCreditByQboIdAndRealmId (U-278) / ReadCustomerByQboIdAndRealmId (U-276). Lets
-- TermPaymentTermConnector resolve "does a dbo.PaymentTerm already exist for this
-- external QBO id" WITHOUT hopping through the qbo.Term / qbo.TermPaymentTerm
-- staging/mapping tables — every PaymentTerm synced at least once already carries
-- QboId/RealmId via SetPaymentTermQboIdentity, so this is the steady-state fast path;
-- the mapping-table lookup remains as a fallback for rows that predate identity
-- stamping. RealmId NULL-equality mirrors SetPaymentTermQboIdentity's own
-- stolen-identity comparison. No RBAC threading — PaymentTerm has no row-level RBAC on
-- direct-id reads (ReadPaymentTermById/ReadPaymentTermByPublicId are unscoped too).
CREATE OR ALTER PROCEDURE ReadPaymentTermByQboIdAndRealmId
(
    @QboId NVARCHAR(50),
    @RealmId NVARCHAR(50) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        pt.[Id],
        pt.[PublicId],
        pt.[RowVersion],
        CONVERT(VARCHAR(19), pt.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), pt.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        pt.[Name],
        pt.[Description],
        pt.[DiscountPercent],
        pt.[DiscountDays],
        pt.[DueDays],
        pt.[QboActive],
        pt.[QboId],
        pt.[RealmId]
    FROM dbo.[PaymentTerm] pt
    WHERE pt.[QboId] = @QboId
      AND ((pt.[RealmId] = @RealmId) OR (pt.[RealmId] IS NULL AND @RealmId IS NULL));

    COMMIT TRANSACTION;
END;
GO

CREATE OR ALTER PROCEDURE SetPaymentTermQboIdentity
(
    @Id BIGINT,
    @QboId NVARCHAR(50) = NULL,
    @RealmId NVARCHAR(50) = NULL,
    @Active BIT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    DECLARE @Stolen BIT = 0;

    IF @QboId IS NOT NULL
    BEGIN
        UPDATE dbo.[PaymentTerm]
        SET [QboId] = NULL, [RealmId] = NULL, [QboActive] = NULL, [ModifiedDatetime] = SYSUTCDATETIME()
        WHERE [Id] <> @Id
          AND [QboId] = @QboId
          AND (([RealmId] = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL));

        IF @@ROWCOUNT > 0
            SET @Stolen = 1;
    END

    UPDATE dbo.[PaymentTerm]
    SET
        [QboId] = CASE WHEN @QboId IS NOT NULL THEN @QboId ELSE [QboId] END,
        [RealmId] = CASE WHEN @RealmId IS NOT NULL THEN @RealmId ELSE [RealmId] END,
        [QboActive] = CASE WHEN @Active IS NOT NULL THEN @Active ELSE [QboActive] END,
        [ModifiedDatetime] = SYSUTCDATETIME()
    OUTPUT
        INSERTED.[Id],
        INSERTED.[QboId],
        INSERTED.[RealmId],
        INSERTED.[QboActive],
        @Stolen AS [Stolen]
    WHERE [Id] = @Id
      AND (
            (@QboId IS NOT NULL AND ([QboId] IS NULL OR [QboId] <> @QboId))
         OR (@RealmId IS NOT NULL AND ([RealmId] IS NULL OR [RealmId] <> @RealmId))
         OR (@Active IS NOT NULL AND ([QboActive] IS NULL OR [QboActive] <> @Active))
      );
END;
GO
