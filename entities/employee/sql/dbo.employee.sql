GO

IF OBJECT_ID('dbo.Employee', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Employee]
(
    [Id]                BIGINT             IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]          UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
    [RowVersion]        ROWVERSION         NOT NULL,
    [CreatedDatetime]   DATETIME2(3)       NOT NULL,
    [ModifiedDatetime]  DATETIME2(3)       NULL,
    [CompanyId]         BIGINT             NOT NULL CONSTRAINT DF_Employee_CompanyId        DEFAULT (1),
    [CreatedByUserId]   BIGINT             NOT NULL CONSTRAINT DF_Employee_CreatedByUserId  DEFAULT (17),

    [Firstname]         NVARCHAR(50)       NOT NULL,
    [Lastname]          NVARCHAR(255)      NOT NULL,
    [Email]             NVARCHAR(255)      NULL,
    [HourlyRate]        DECIMAL(18,4)      NULL,
    [Markup]            DECIMAL(18,4)      NULL,
    [IsActive]          BIT                NOT NULL DEFAULT 1,
    [IsDeleted]         BIT                NOT NULL DEFAULT 0,
    [Notes]             NVARCHAR(MAX)      NULL
);
END
GO

-- FK CompanyId → Company.Id
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Employee_Company')
BEGIN
    ALTER TABLE [dbo].[Employee]
    ADD CONSTRAINT [FK_Employee_Company] FOREIGN KEY ([CompanyId]) REFERENCES [dbo].[Company]([Id]);
END
GO

-- FK CreatedByUserId → User.Id (per Gap 2 pattern)
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_Employee_CreatedByUser')
BEGIN
    ALTER TABLE [dbo].[Employee]
    ADD CONSTRAINT [FK_Employee_CreatedByUser] FOREIGN KEY ([CreatedByUserId]) REFERENCES [dbo].[User]([Id]);
END
GO

-- Unique index on (Lastname, Firstname) for active (non-deleted) employees.
-- Loose — two employees can share a name in theory; tighten if it becomes a real issue.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Employee_Lastname_Firstname' AND object_id = OBJECT_ID('dbo.Employee'))
BEGIN
    CREATE INDEX [IX_Employee_Lastname_Firstname] ON [dbo].[Employee] ([Lastname], [Firstname]) WHERE [IsDeleted] = 0;
END
GO


GO

CREATE OR ALTER PROCEDURE CreateEmployee
(
    @Firstname        NVARCHAR(50),
    @Lastname         NVARCHAR(255),
    @Email            NVARCHAR(255) = NULL,
    @HourlyRate       DECIMAL(18,4) = NULL,
    @Markup           DECIMAL(18,4) = NULL,
    @IsActive         BIT           = 1,
    @Notes            NVARCHAR(MAX) = NULL,
    @CreatedByUserId  BIGINT        = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Employee] ([CreatedDatetime], [ModifiedDatetime], [Firstname], [Lastname], [Email], [HourlyRate], [Markup], [IsActive], [IsDeleted], [Notes], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Firstname],
        INSERTED.[Lastname],
        INSERTED.[Email],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[IsActive],
        INSERTED.[IsDeleted],
        INSERTED.[Notes]
    VALUES (@Now, @Now, @Firstname, @Lastname, @Email, @HourlyRate, @Markup, @IsActive, 0, @Notes, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadEmployees
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname],
        [Email],
        [HourlyRate],
        [Markup],
        [IsActive],
        [IsDeleted],
        [Notes]
    FROM dbo.[Employee]
    WHERE [IsDeleted] = 0
    ORDER BY [Lastname] ASC, [Firstname] ASC;
END;



GO

CREATE OR ALTER PROCEDURE ReadEmployeeById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname],
        [Email],
        [HourlyRate],
        [Markup],
        [IsActive],
        [IsDeleted],
        [Notes]
    FROM dbo.[Employee]
    WHERE [Id] = @Id AND [IsDeleted] = 0;
END;



GO

CREATE OR ALTER PROCEDURE ReadEmployeeByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname],
        [Email],
        [HourlyRate],
        [Markup],
        [IsActive],
        [IsDeleted],
        [Notes]
    FROM dbo.[Employee]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;
END;



GO

CREATE OR ALTER PROCEDURE ReadEmployeeByName
(
    @Firstname NVARCHAR(50),
    @Lastname  NVARCHAR(255)
)
AS
BEGIN
    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname],
        [Email],
        [HourlyRate],
        [Markup],
        [IsActive],
        [IsDeleted],
        [Notes]
    FROM dbo.[Employee]
    WHERE [Firstname] = @Firstname AND [Lastname] = @Lastname AND [IsDeleted] = 0;
END;



GO

CREATE OR ALTER PROCEDURE UpdateEmployeeById
(
    @Id          BIGINT,
    @RowVersion  BINARY(8),
    @Firstname   NVARCHAR(50),
    @Lastname    NVARCHAR(255),
    @Email       NVARCHAR(255) = NULL,
    @HourlyRate  DECIMAL(18,4) = NULL,
    @Markup      DECIMAL(18,4) = NULL,
    @IsActive    BIT           = NULL,
    @Notes       NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[Employee] WHERE [Id] = @Id AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Employee not found.', 16, 1);
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM dbo.[Employee] WHERE [Id] = @Id AND [RowVersion] = @RowVersion AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Concurrency conflict: the employee record has been modified by another user. Please refresh and try again.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- Rate/markup CASE-WHEN preserve-on-NULL so caller can change one field
    -- without nulling the other. Per memory: stored-procedure NULL overwrite
    -- gotcha — same rule used by Vendor for IsDraft/IsContractLabor.
    UPDATE dbo.[Employee]
    SET
        [ModifiedDatetime] = @Now,
        [Firstname]   = @Firstname,
        [Lastname]    = @Lastname,
        [Email]       = @Email,
        [HourlyRate]  = CASE WHEN @HourlyRate IS NULL THEN [HourlyRate] ELSE @HourlyRate END,
        [Markup]      = CASE WHEN @Markup     IS NULL THEN [Markup]     ELSE @Markup     END,
        [IsActive]    = CASE WHEN @IsActive   IS NULL THEN [IsActive]   ELSE @IsActive   END,
        [Notes]       = @Notes
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Firstname],
        INSERTED.[Lastname],
        INSERTED.[Email],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[IsActive],
        INSERTED.[IsDeleted],
        INSERTED.[Notes]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE SoftDeleteEmployeeByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[Employee] WHERE [PublicId] = @PublicId AND [IsDeleted] = 0)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Employee not found.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[Employee]
    SET
        [ModifiedDatetime] = @Now,
        [IsDeleted] = 1
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Firstname],
        INSERTED.[Lastname],
        INSERTED.[Email],
        INSERTED.[HourlyRate],
        INSERTED.[Markup],
        INSERTED.[IsActive],
        INSERTED.[IsDeleted],
        INSERTED.[Notes]
    WHERE [PublicId] = @PublicId AND [IsDeleted] = 0;

    COMMIT TRANSACTION;
END;
GO
