GO

IF OBJECT_ID('dbo.VendorProjectRate', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[VendorProjectRate]
(
    [Id]                BIGINT             IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
    [RowVersion]        ROWVERSION         NOT NULL,
    [CreatedDatetime]   DATETIME2(3)       NOT NULL,
    [ModifiedDatetime]  DATETIME2(3)       NULL,
    [CompanyId]         BIGINT             NOT NULL CONSTRAINT DF_VendorProjectRate_CompanyId       DEFAULT (1),
    [CreatedByUserId]   BIGINT             NOT NULL CONSTRAINT DF_VendorProjectRate_CreatedByUserId DEFAULT (17),

    [VendorId]          BIGINT             NOT NULL,
    [ProjectId]         BIGINT             NOT NULL,
    -- Both nullable. NULL means "inherit Vendor default" (so an override row
    -- can carry just a project-specific markup while leaving the rate at the
    -- Vendor's default, or vice versa).
    [HourlyRate]        DECIMAL(18,4)      NULL,
    [Markup]            DECIMAL(18,4)      NULL,
    [Notes]             NVARCHAR(MAX)      NULL,
    [IsDeleted]         BIT                NOT NULL DEFAULT 0
);
END
GO

-- One row per (Vendor, Project). Filtered for soft-deletes so the same pair
-- can be re-created after a delete without colliding.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_VendorProjectRate_Vendor_Project_Active' AND object_id = OBJECT_ID('dbo.VendorProjectRate'))
BEGIN
    CREATE UNIQUE INDEX [UX_VendorProjectRate_Vendor_Project_Active]
        ON [dbo].[VendorProjectRate] ([VendorId], [ProjectId])
        WHERE [IsDeleted] = 0;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorProjectRate_Vendor')
BEGIN
    ALTER TABLE [dbo].[VendorProjectRate]
    ADD CONSTRAINT [FK_VendorProjectRate_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorProjectRate_Project')
BEGIN
    ALTER TABLE [dbo].[VendorProjectRate]
    ADD CONSTRAINT [FK_VendorProjectRate_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorProjectRate_Company')
BEGIN
    ALTER TABLE [dbo].[VendorProjectRate]
    ADD CONSTRAINT [FK_VendorProjectRate_Company] FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_VendorProjectRate_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[VendorProjectRate]
    ADD CONSTRAINT [FK_VendorProjectRate_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO


GO

-- ─────────────────────────────────────────────────────────────────────
-- Effective-rate lookup — used by Phase 4 aggregation.
-- Resolution order:
--   1. (Vendor, Project) override row's HourlyRate / Markup (non-NULL)
--   2. Vendor.HourlyRate / Vendor.Markup default
--   3. NULL — caller treats as "rate not configured", flags the row
--      pending_review with a Description annotation rather than billing $0.
--
-- Returns a single row: HourlyRate, Markup, RateSource (NVARCHAR(20)).
-- RateSource ∈ {'override', 'default', 'none'} — caller uses this to
-- decide whether to proceed or flag.
-- ─────────────────────────────────────────────────────────────────────

CREATE OR ALTER PROCEDURE ReadEffectiveRateForVendorProject
(
    @VendorId  BIGINT,
    @ProjectId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @OverrideRate   DECIMAL(18,4);
    DECLARE @OverrideMarkup DECIMAL(18,4);
    DECLARE @DefaultRate    DECIMAL(18,4);
    DECLARE @DefaultMarkup  DECIMAL(18,4);

    SELECT TOP 1
        @OverrideRate   = [HourlyRate],
        @OverrideMarkup = [Markup]
    FROM dbo.[VendorProjectRate]
    WHERE [VendorId] = @VendorId
      AND [ProjectId] = @ProjectId
      AND [IsDeleted] = 0;

    SELECT
        @DefaultRate   = [HourlyRate],
        @DefaultMarkup = [Markup]
    FROM dbo.[Vendor]
    WHERE [Id] = @VendorId AND [IsDeleted] = 0;

    DECLARE @ResolvedRate   DECIMAL(18,4) = COALESCE(@OverrideRate,   @DefaultRate);
    DECLARE @ResolvedMarkup DECIMAL(18,4) = COALESCE(@OverrideMarkup, @DefaultMarkup);

    DECLARE @RateSource NVARCHAR(20) =
        CASE
            WHEN @ResolvedRate IS NULL THEN 'none'
            WHEN @OverrideRate IS NOT NULL THEN 'override'
            ELSE 'default'
        END;

    SELECT
        @ResolvedRate    AS HourlyRate,
        @ResolvedMarkup  AS Markup,
        @RateSource      AS RateSource;
END;
GO


CREATE OR ALTER PROCEDURE CreateVendorProjectRate
(
    @VendorId        BIGINT,
    @ProjectId       BIGINT,
    @HourlyRate      DECIMAL(18,4) = NULL,
    @Markup          DECIMAL(18,4) = NULL,
    @Notes           NVARCHAR(MAX) = NULL,
    @CreatedByUserId BIGINT        = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[VendorProjectRate]
        ([CreatedDatetime], [ModifiedDatetime], [VendorId], [ProjectId],
         [HourlyRate], [Markup], [Notes], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[ProjectId],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[Notes],
        INSERTED.[IsDeleted]
    VALUES (@Now, @Now, @VendorId, @ProjectId, @HourlyRate, @Markup, @Notes,
            COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorProjectRateById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        [ProjectId],
        [HourlyRate],
        [Markup],
        [Notes],
        [IsDeleted]
    FROM dbo.[VendorProjectRate]
    WHERE [Id] = @Id AND [IsDeleted] = 0;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorProjectRateByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [VendorId],
        [ProjectId],
        [HourlyRate],
        [Markup],
        [Notes],
        [IsDeleted]
    FROM dbo.[VendorProjectRate]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorProjectRatesByVendorId
(
    @VendorId BIGINT
)
AS
BEGIN
    SELECT
        r.[Id],
        r.[PublicId],
        r.[RowVersion],
        CONVERT(VARCHAR(19), r.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), r.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        r.[VendorId],
        r.[ProjectId],
        r.[HourlyRate],
        r.[Markup],
        r.[Notes],
        r.[IsDeleted],
        p.[Name]   AS ProjectName,
        p.[PublicId] AS ProjectPublicId
    FROM dbo.[VendorProjectRate] r
    LEFT JOIN dbo.[Project] p ON p.[Id] = r.[ProjectId]
    WHERE r.[VendorId] = @VendorId AND r.[IsDeleted] = 0
    ORDER BY p.[Name] ASC;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorProjectRatesByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    SELECT
        r.[Id],
        r.[PublicId],
        r.[RowVersion],
        CONVERT(VARCHAR(19), r.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), r.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        r.[VendorId],
        r.[ProjectId],
        r.[HourlyRate],
        r.[Markup],
        r.[Notes],
        r.[IsDeleted],
        v.[Name]   AS VendorName,
        v.[PublicId] AS VendorPublicId
    FROM dbo.[VendorProjectRate] r
    LEFT JOIN dbo.[Vendor] v ON v.[Id] = r.[VendorId]
    WHERE r.[ProjectId] = @ProjectId AND r.[IsDeleted] = 0
    ORDER BY v.[Name] ASC;
END;
GO


CREATE OR ALTER PROCEDURE UpdateVendorProjectRateById
(
    @Id         BIGINT,
    @RowVersion BINARY(8),
    @HourlyRate DECIMAL(18,4) = NULL,
    @Markup     DECIMAL(18,4) = NULL,
    @Notes      NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[VendorProjectRate] WHERE [Id] = @Id AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('VendorProjectRate not found.', 16, 1);
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM dbo.[VendorProjectRate] WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Concurrency conflict: VendorProjectRate has been modified by another user.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- Rate / Markup use CASE WHEN preserve-on-NULL: pass an explicit value
    -- to overwrite, pass NULL to keep the existing value. There's no path
    -- through this sproc to set them back to NULL once populated — if you
    -- want to revert to "inherit Vendor default", soft-delete + recreate.
    UPDATE dbo.[VendorProjectRate]
    SET
        [ModifiedDatetime] = @Now,
        [HourlyRate] = CASE WHEN @HourlyRate IS NULL THEN [HourlyRate] ELSE @HourlyRate END,
        [Markup]     = CASE WHEN @Markup     IS NULL THEN [Markup]     ELSE @Markup     END,
        [Notes]      = @Notes
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[ProjectId],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[Notes],
        INSERTED.[IsDeleted]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE SoftDeleteVendorProjectRateByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[VendorProjectRate] WHERE [PublicId] = @PublicId AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('VendorProjectRate not found.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[VendorProjectRate]
    SET    [ModifiedDatetime] = @Now,
           [IsDeleted] = 1
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[VendorId],
        INSERTED.[ProjectId],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[Notes],
        INSERTED.[IsDeleted]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO
