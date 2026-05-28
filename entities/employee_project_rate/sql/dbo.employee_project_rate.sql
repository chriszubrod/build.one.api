GO

IF OBJECT_ID('dbo.EmployeeProjectRate', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[EmployeeProjectRate]
(
    [Id]                BIGINT             IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
    [RowVersion]        ROWVERSION         NOT NULL,
    [CreatedDatetime]   DATETIME2(3)       NOT NULL,
    [ModifiedDatetime]  DATETIME2(3)       NULL,
    [CompanyId]         BIGINT             NOT NULL CONSTRAINT DF_EmployeeProjectRate_CompanyId       DEFAULT (1),
    [CreatedByUserId]   BIGINT             NOT NULL CONSTRAINT DF_EmployeeProjectRate_CreatedByUserId DEFAULT (17),

    [EmployeeId]        BIGINT             NOT NULL,
    [ProjectId]         BIGINT             NOT NULL,
    -- Both nullable. NULL means "inherit Employee default".
    [HourlyRate]        DECIMAL(18,4)      NULL,
    [Markup]            DECIMAL(18,4)      NULL,
    [Notes]             NVARCHAR(MAX)      NULL,
    [IsDeleted]         BIT                NOT NULL DEFAULT 0
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_EmployeeProjectRate_Employee_Project_Active' AND object_id = OBJECT_ID('dbo.EmployeeProjectRate'))
BEGIN
    CREATE UNIQUE INDEX [UX_EmployeeProjectRate_Employee_Project_Active]
        ON [dbo].[EmployeeProjectRate] ([EmployeeId], [ProjectId])
        WHERE [IsDeleted] = 0;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeProjectRate_Employee')
BEGIN
    ALTER TABLE [dbo].[EmployeeProjectRate]
    ADD CONSTRAINT [FK_EmployeeProjectRate_Employee] FOREIGN KEY ([EmployeeId]) REFERENCES [dbo].[Employee]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeProjectRate_Project')
BEGIN
    ALTER TABLE [dbo].[EmployeeProjectRate]
    ADD CONSTRAINT [FK_EmployeeProjectRate_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeProjectRate_Company')
BEGIN
    ALTER TABLE [dbo].[EmployeeProjectRate]
    ADD CONSTRAINT [FK_EmployeeProjectRate_Company] FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeProjectRate_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[EmployeeProjectRate]
    ADD CONSTRAINT [FK_EmployeeProjectRate_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO


GO

-- Effective-rate lookup — mirror of ReadEffectiveRateForVendorProject.
-- Used by Phase 4 aggregation when a TimeEntry's User has an EmployeeId.
CREATE OR ALTER PROCEDURE ReadEffectiveRateForEmployeeProject
(
    @EmployeeId BIGINT,
    @ProjectId  BIGINT
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
    FROM dbo.[EmployeeProjectRate]
    WHERE [EmployeeId] = @EmployeeId
      AND [ProjectId]  = @ProjectId
      AND [IsDeleted]  = 0;

    SELECT
        @DefaultRate   = [HourlyRate],
        @DefaultMarkup = [Markup]
    FROM dbo.[Employee]
    WHERE [Id] = @EmployeeId AND [IsDeleted] = 0;

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


CREATE OR ALTER PROCEDURE CreateEmployeeProjectRate
(
    @EmployeeId      BIGINT,
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

    INSERT INTO dbo.[EmployeeProjectRate]
        ([CreatedDatetime], [ModifiedDatetime], [EmployeeId], [ProjectId],
         [HourlyRate], [Markup], [Notes], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EmployeeId],
        INSERTED.[ProjectId],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[Notes],
        INSERTED.[IsDeleted]
    VALUES (@Now, @Now, @EmployeeId, @ProjectId, @HourlyRate, @Markup, @Notes,
            COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeProjectRateById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [EmployeeId], [ProjectId], [HourlyRate], [Markup], [Notes], [IsDeleted]
    FROM dbo.[EmployeeProjectRate]
    WHERE [Id] = @Id AND [IsDeleted] = 0;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeProjectRateByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [EmployeeId], [ProjectId], [HourlyRate], [Markup], [Notes], [IsDeleted]
    FROM dbo.[EmployeeProjectRate]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeProjectRatesByEmployeeId
(
    @EmployeeId BIGINT
)
AS
BEGIN
    SELECT
        r.[Id], r.[PublicId], r.[RowVersion],
        CONVERT(VARCHAR(19), r.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), r.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        r.[EmployeeId], r.[ProjectId], r.[HourlyRate], r.[Markup], r.[Notes], r.[IsDeleted],
        p.[Name]     AS ProjectName,
        p.[PublicId] AS ProjectPublicId
    FROM dbo.[EmployeeProjectRate] r
    LEFT JOIN dbo.[Project] p ON p.[Id] = r.[ProjectId]
    WHERE r.[EmployeeId] = @EmployeeId AND r.[IsDeleted] = 0
    ORDER BY p.[Name] ASC;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeProjectRatesByProjectId
(
    @ProjectId BIGINT
)
AS
BEGIN
    SELECT
        r.[Id], r.[PublicId], r.[RowVersion],
        CONVERT(VARCHAR(19), r.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), r.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        r.[EmployeeId], r.[ProjectId], r.[HourlyRate], r.[Markup], r.[Notes], r.[IsDeleted],
        e.[Firstname] + ' ' + e.[Lastname] AS EmployeeName,
        e.[PublicId]                       AS EmployeePublicId
    FROM dbo.[EmployeeProjectRate] r
    LEFT JOIN dbo.[Employee] e ON e.[Id] = r.[EmployeeId]
    WHERE r.[ProjectId] = @ProjectId AND r.[IsDeleted] = 0
    ORDER BY e.[Lastname], e.[Firstname];
END;
GO


CREATE OR ALTER PROCEDURE UpdateEmployeeProjectRateById
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

    IF NOT EXISTS (SELECT 1 FROM dbo.[EmployeeProjectRate] WHERE [Id] = @Id AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('EmployeeProjectRate not found.', 16, 1);
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM dbo.[EmployeeProjectRate] WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Concurrency conflict: EmployeeProjectRate has been modified by another user.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[EmployeeProjectRate]
    SET
        [ModifiedDatetime] = @Now,
        [HourlyRate] = CASE WHEN @HourlyRate IS NULL THEN [HourlyRate] ELSE @HourlyRate END,
        [Markup]     = CASE WHEN @Markup     IS NULL THEN [Markup]     ELSE @Markup     END,
        [Notes]      = @Notes
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EmployeeId], INSERTED.[ProjectId], INSERTED.[HourlyRate], INSERTED.[Markup],
        INSERTED.[Notes], INSERTED.[IsDeleted]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE SoftDeleteEmployeeProjectRateByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[EmployeeProjectRate] WHERE [PublicId] = @PublicId AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('EmployeeProjectRate not found.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[EmployeeProjectRate]
    SET    [ModifiedDatetime] = @Now, [IsDeleted] = 1
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EmployeeId], INSERTED.[ProjectId], INSERTED.[HourlyRate], INSERTED.[Markup],
        INSERTED.[Notes], INSERTED.[IsDeleted]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO
