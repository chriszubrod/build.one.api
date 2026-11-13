-- Contract: dbo.ProjectDrawPacketSetting   (U-792; mirror of entities/project_draw_packet_setting/business/service.py docstring)
-- Owns: one row per Project switching the optional Application for Payment blocks on or off.
-- Invariants: UNIQUE ProjectId; each switch is a nullable BIT where NULL = the house default; UpsertProjectDrawPacketSetting is ROWVERSION-guarded once a row exists and always returns a result set (empty = conflict).
-- Sprocs: ReadProjectDrawPacketSettingByProjectId, UpsertProjectDrawPacketSetting
-- =============================================================================
-- Which optional blocks print on a project's Application for Payment (draw packet).
-- A NULL switch means "use the house default" (application code owns the defaults:
-- contractor statement ON, owner approval ON, architect certificate OFF, notary ON),
-- so a project with no row — or a row with NULLs — follows the default even if the
-- default later changes. Re-runnable: table/constraints are guarded, sprocs are
-- CREATE OR ALTER. This file is the SOLE home of both sprocs.
-- Build order: dbo.User and dbo.Project must exist first (FK targets).
-- =============================================================================

IF OBJECT_ID('dbo.ProjectDrawPacketSetting', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ProjectDrawPacketSetting]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [CreatedByUserId] BIGINT NOT NULL DEFAULT 17,
    [ProjectId] BIGINT NOT NULL,
    [ShowContractorStatement] BIT NULL,
    [ShowOwnerApproval] BIT NULL,
    [ShowArchitectCertificate] BIT NULL,
    [ShowNotary] BIT NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ProjectDrawPacketSetting_Project')
BEGIN
    ALTER TABLE [dbo].[ProjectDrawPacketSetting] ADD CONSTRAINT [FK_ProjectDrawPacketSetting_Project]
        FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_ProjectDrawPacketSetting_User')
BEGIN
    ALTER TABLE [dbo].[ProjectDrawPacketSetting] ADD CONSTRAINT [FK_ProjectDrawPacketSetting_User]
        FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_ProjectDrawPacketSetting_ProjectId'
               AND object_id = OBJECT_ID('dbo.ProjectDrawPacketSetting'))
BEGIN
    CREATE UNIQUE INDEX [UX_ProjectDrawPacketSetting_ProjectId] ON [dbo].[ProjectDrawPacketSetting] ([ProjectId]);
END
GO


CREATE OR ALTER PROCEDURE ReadProjectDrawPacketSettingByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [CreatedByUserId],
        [ProjectId],
        [ShowContractorStatement],
        [ShowOwnerApproval],
        [ShowArchitectCertificate],
        [ShowNotary]
    FROM dbo.[ProjectDrawPacketSetting]
    WHERE [ProjectId] = @ProjectId;
END;
GO


-- Full replace: every switch is SET to the value passed, and NULL is meaningful (it
-- resets that switch to the house default) — so, deliberately, no CASE WHEN
-- NULL-preserving guards here. First write (no row yet) inserts and must carry NO
-- @RowVersion; every later write must carry the current @RowVersion. A stale or
-- missing token, or a token sent when no row exists, writes nothing and returns an
-- EMPTY result set (the caller maps it to a 409). Never ROLLBACK in-sproc (pyodbc
-- autocommit-off); the trailing SELECT always yields a result set.
CREATE OR ALTER PROCEDURE UpsertProjectDrawPacketSetting
(
    @ProjectId BIGINT,
    @RowVersion BINARY(8) = NULL,
    @ShowContractorStatement BIT = NULL,
    @ShowOwnerApproval BIT = NULL,
    @ShowArchitectCertificate BIT = NULL,
    @ShowNotary BIT = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @Written BIT = 0;

    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[ProjectDrawPacketSetting] WITH (UPDLOCK, HOLDLOCK)
                   WHERE [ProjectId] = @ProjectId)
    BEGIN
        IF @RowVersion IS NULL
        BEGIN
            INSERT INTO dbo.[ProjectDrawPacketSetting]
                ([CreatedDatetime], [ModifiedDatetime], [CreatedByUserId], [ProjectId],
                 [ShowContractorStatement], [ShowOwnerApproval], [ShowArchitectCertificate], [ShowNotary])
            VALUES
                (@Now, @Now, COALESCE(@CreatedByUserId, 17), @ProjectId,
                 @ShowContractorStatement, @ShowOwnerApproval, @ShowArchitectCertificate, @ShowNotary);
            SET @Written = 1;
        END
    END
    ELSE IF @RowVersion IS NOT NULL
    BEGIN
        UPDATE dbo.[ProjectDrawPacketSetting]
        SET
            [ModifiedDatetime]         = @Now,
            [ShowContractorStatement]  = @ShowContractorStatement,
            [ShowOwnerApproval]        = @ShowOwnerApproval,
            [ShowArchitectCertificate] = @ShowArchitectCertificate,
            [ShowNotary]               = @ShowNotary
        WHERE [ProjectId] = @ProjectId AND [RowVersion] = @RowVersion;
        IF @@ROWCOUNT = 1 SET @Written = 1;
    END

    COMMIT TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [CreatedByUserId],
        [ProjectId],
        [ShowContractorStatement],
        [ShowOwnerApproval],
        [ShowArchitectCertificate],
        [ShowNotary]
    FROM dbo.[ProjectDrawPacketSetting]
    WHERE [ProjectId] = @ProjectId AND @Written = 1;
END;
GO
