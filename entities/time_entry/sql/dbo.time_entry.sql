-- Module registration for RBAC
IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Time Tracking')
BEGIN
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    INSERT INTO dbo.[Module] ([Name], [Route], [CreatedDatetime], [ModifiedDatetime])
    VALUES ('Time Tracking', '/time-entries', @Now, @Now);
END
GO


-- TimeEntry Table
-- Stores time tracking entries for workers (vendors and employees) on projects

GO

IF OBJECT_ID('dbo.TimeEntry', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[TimeEntry]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Worker and assignment
    [UserId] BIGINT NOT NULL,                      -- FK to User (the worker)
    [ProjectId] BIGINT NOT NULL,                   -- FK to Project
    [WorkDate] DATE NOT NULL,
    [Note] NVARCHAR(MAX) NULL,                     -- Worker's note, important for reviewer

    CONSTRAINT [FK_TimeEntry_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id]),
    CONSTRAINT [FK_TimeEntry_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id])
);
END
GO


-- TimeLog Table
-- Stores raw clock in/out timestamps for time entries (many per TimeEntry)
GO

IF OBJECT_ID('dbo.TimeLog', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[TimeLog]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Parent reference
    [TimeEntryId] BIGINT NOT NULL,

    -- Timestamp data
    [ClockIn] DATETIME2(3) NOT NULL,
    [ClockOut] DATETIME2(3) NULL,                  -- NULL = still clocked in
    [LogType] NVARCHAR(10) NOT NULL DEFAULT 'work', -- 'work' or 'break'
    [Duration] DECIMAL(6,2) NULL,                   -- Calculated from timestamps
    [Latitude] DECIMAL(9,6) NULL,                   -- GPS latitude at clock in/out
    [Longitude] DECIMAL(9,6) NULL,                  -- GPS longitude at clock in/out

    CONSTRAINT [FK_TimeLog_TimeEntry] FOREIGN KEY ([TimeEntryId]) REFERENCES [dbo].[TimeEntry]([Id]) ON DELETE CASCADE
);
END
GO

-- Add Latitude/Longitude columns (idempotent migration)
IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.TimeLog') AND name = 'Latitude')
BEGIN
    ALTER TABLE [dbo].[TimeLog] ADD [Latitude] DECIMAL(9,6) NULL;
END
GO

IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.TimeLog') AND name = 'Longitude')
BEGIN
    ALTER TABLE [dbo].[TimeLog] ADD [Longitude] DECIMAL(9,6) NULL;
END
GO


-- TimeEntryStatus Table
-- Stores full history of status transitions with audit trail
GO

IF OBJECT_ID('dbo.TimeEntryStatus', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[TimeEntryStatus]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,

    -- Status transition
    [TimeEntryId] BIGINT NOT NULL,
    [Status] NVARCHAR(20) NOT NULL,                -- draft, submitted, approved, rejected, billed
    [UserId] BIGINT NOT NULL,                      -- FK to User (who made the change)
    [Note] NVARCHAR(MAX) NULL,                     -- Rejection reason, approval notes, etc.

    CONSTRAINT [FK_TimeEntryStatus_TimeEntry] FOREIGN KEY ([TimeEntryId]) REFERENCES [dbo].[TimeEntry]([Id]),
    CONSTRAINT [FK_TimeEntryStatus_User] FOREIGN KEY ([UserId]) REFERENCES [dbo].[User]([Id])
);
END
GO


-- Indexes
IF OBJECT_ID('dbo.TimeEntry', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntry_PublicId' AND object_id = OBJECT_ID('dbo.TimeEntry'))
BEGIN
CREATE INDEX IX_TimeEntry_PublicId ON [dbo].[TimeEntry] ([PublicId]);
END
GO

IF OBJECT_ID('dbo.TimeEntry', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntry_UserId' AND object_id = OBJECT_ID('dbo.TimeEntry'))
BEGIN
CREATE INDEX IX_TimeEntry_UserId ON [dbo].[TimeEntry] ([UserId]);
END
GO

IF OBJECT_ID('dbo.TimeEntry', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntry_ProjectId' AND object_id = OBJECT_ID('dbo.TimeEntry'))
BEGIN
CREATE INDEX IX_TimeEntry_ProjectId ON [dbo].[TimeEntry] ([ProjectId]);
END
GO

IF OBJECT_ID('dbo.TimeEntry', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntry_WorkDate' AND object_id = OBJECT_ID('dbo.TimeEntry'))
BEGIN
CREATE INDEX IX_TimeEntry_WorkDate ON [dbo].[TimeEntry] ([WorkDate]);
END
GO

IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeLog_TimeEntryId' AND object_id = OBJECT_ID('dbo.TimeLog'))
BEGIN
CREATE INDEX IX_TimeLog_TimeEntryId ON [dbo].[TimeLog] ([TimeEntryId]);
END
GO

IF OBJECT_ID('dbo.TimeLog', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeLog_PublicId' AND object_id = OBJECT_ID('dbo.TimeLog'))
BEGIN
CREATE INDEX IX_TimeLog_PublicId ON [dbo].[TimeLog] ([PublicId]);
END
GO

IF OBJECT_ID('dbo.TimeEntryStatus', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntryStatus_TimeEntryId' AND object_id = OBJECT_ID('dbo.TimeEntryStatus'))
BEGIN
CREATE INDEX IX_TimeEntryStatus_TimeEntryId ON [dbo].[TimeEntryStatus] ([TimeEntryId]);
END
GO

IF OBJECT_ID('dbo.TimeEntryStatus', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_TimeEntryStatus_PublicId' AND object_id = OBJECT_ID('dbo.TimeEntryStatus'))
BEGIN
CREATE INDEX IX_TimeEntryStatus_PublicId ON [dbo].[TimeEntryStatus] ([PublicId]);
END
GO


-- ============================================
-- TimeEntry Stored Procedures
-- ============================================

GO

CREATE OR ALTER PROCEDURE CreateTimeEntry
(
    @UserId BIGINT,
    @ProjectId BIGINT,
    @WorkDate DATE,
    @Note NVARCHAR(MAX) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[TimeEntry] (
        [CreatedDatetime], [ModifiedDatetime], [UserId], [ProjectId], [WorkDate], [Note]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[ProjectId],
        CONVERT(VARCHAR(10), INSERTED.[WorkDate], 120) AS [WorkDate],
        INSERTED.[Note]
    VALUES (
        @Now, @Now, @UserId, @ProjectId, @WorkDate, @Note
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTimeEntries
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        [ProjectId],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [Note]
    FROM dbo.[TimeEntry]
    ORDER BY [WorkDate] DESC, [UserId] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTimeEntryById
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
        [UserId],
        [ProjectId],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [Note]
    FROM dbo.[TimeEntry]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTimeEntryByPublicId
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
        [UserId],
        [ProjectId],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [Note]
    FROM dbo.[TimeEntry]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTimeEntriesByUserId
(
    @UserId BIGINT
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
        [UserId],
        [ProjectId],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [Note]
    FROM dbo.[TimeEntry]
    WHERE [UserId] = @UserId
    ORDER BY [WorkDate] DESC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTimeEntriesByProjectId
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
        [UserId],
        [ProjectId],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [Note]
    FROM dbo.[TimeEntry]
    WHERE [ProjectId] = @ProjectId
    ORDER BY [WorkDate] DESC, [UserId] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTimeEntriesPaginated
(
    @PageNumber INT = 1,
    @PageSize INT = 50,
    @SearchTerm NVARCHAR(255) = NULL,
    @UserId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @Status NVARCHAR(20) = NULL,
    @StartDate DATE = NULL,
    @EndDate DATE = NULL,
    @SortBy NVARCHAR(50) = 'WorkDate',
    @SortDirection NVARCHAR(4) = 'DESC'
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Offset INT = (@PageNumber - 1) * @PageSize;

    SELECT
        te.[Id],
        te.[PublicId],
        te.[RowVersion],
        CONVERT(VARCHAR(19), te.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), te.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        te.[UserId],
        te.[ProjectId],
        CONVERT(VARCHAR(10), te.[WorkDate], 120) AS [WorkDate],
        te.[Note]
    FROM dbo.[TimeEntry] te
    LEFT JOIN dbo.[User] u ON te.[UserId] = u.[Id]
    LEFT JOIN dbo.[Project] p ON te.[ProjectId] = p.[Id]
    -- Get current status from most recent TimeEntryStatus row
    OUTER APPLY (
        SELECT TOP 1 s.[Status]
        FROM dbo.[TimeEntryStatus] s
        WHERE s.[TimeEntryId] = te.[Id]
        ORDER BY s.[CreatedDatetime] DESC
    ) cs
    WHERE
        (@SearchTerm IS NULL OR
         te.[Note] LIKE '%' + @SearchTerm + '%' OR
         u.[Firstname] LIKE '%' + @SearchTerm + '%' OR
         u.[Lastname] LIKE '%' + @SearchTerm + '%' OR
         p.[Name] LIKE '%' + @SearchTerm + '%')
        AND (@UserId IS NULL OR te.[UserId] = @UserId)
        AND (@ProjectId IS NULL OR te.[ProjectId] = @ProjectId)
        AND (@Status IS NULL OR cs.[Status] = @Status)
        AND (@StartDate IS NULL OR te.[WorkDate] >= @StartDate)
        AND (@EndDate IS NULL OR te.[WorkDate] <= @EndDate)
    ORDER BY
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'WorkDate' THEN te.[WorkDate] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'WorkDate' THEN te.[WorkDate] END DESC,
        CASE WHEN @SortDirection = 'ASC' AND @SortBy = 'CreatedDatetime' THEN te.[CreatedDatetime] END ASC,
        CASE WHEN @SortDirection = 'DESC' AND @SortBy = 'CreatedDatetime' THEN te.[CreatedDatetime] END DESC,
        u.[Lastname] ASC,
        u.[Firstname] ASC
    OFFSET @Offset ROWS
    FETCH NEXT @PageSize ROWS ONLY;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE CountTimeEntries
(
    @SearchTerm NVARCHAR(255) = NULL,
    @UserId BIGINT = NULL,
    @ProjectId BIGINT = NULL,
    @Status NVARCHAR(20) = NULL,
    @StartDate DATE = NULL,
    @EndDate DATE = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT COUNT(*) AS [TotalCount]
    FROM dbo.[TimeEntry] te
    LEFT JOIN dbo.[User] u ON te.[UserId] = u.[Id]
    LEFT JOIN dbo.[Project] p ON te.[ProjectId] = p.[Id]
    OUTER APPLY (
        SELECT TOP 1 s.[Status]
        FROM dbo.[TimeEntryStatus] s
        WHERE s.[TimeEntryId] = te.[Id]
        ORDER BY s.[CreatedDatetime] DESC
    ) cs
    WHERE
        (@SearchTerm IS NULL OR
         te.[Note] LIKE '%' + @SearchTerm + '%' OR
         u.[Firstname] LIKE '%' + @SearchTerm + '%' OR
         u.[Lastname] LIKE '%' + @SearchTerm + '%' OR
         p.[Name] LIKE '%' + @SearchTerm + '%')
        AND (@UserId IS NULL OR te.[UserId] = @UserId)
        AND (@ProjectId IS NULL OR te.[ProjectId] = @ProjectId)
        AND (@Status IS NULL OR cs.[Status] = @Status)
        AND (@StartDate IS NULL OR te.[WorkDate] >= @StartDate)
        AND (@EndDate IS NULL OR te.[WorkDate] <= @EndDate);

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateTimeEntryById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @UserId BIGINT,
    @ProjectId BIGINT,
    @WorkDate DATE,
    @Note NVARCHAR(MAX) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[TimeEntry]
    SET
        [ModifiedDatetime] = @Now,
        [UserId] = @UserId,
        [ProjectId] = @ProjectId,
        [WorkDate] = @WorkDate,
        [Note] = @Note
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[UserId],
        INSERTED.[ProjectId],
        CONVERT(VARCHAR(10), INSERTED.[WorkDate], 120) AS [WorkDate],
        INSERTED.[Note]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteTimeEntryById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[TimeEntry]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[UserId],
        DELETED.[ProjectId],
        CONVERT(VARCHAR(10), DELETED.[WorkDate], 120) AS [WorkDate],
        DELETED.[Note]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- ============================================
-- TimeLog Stored Procedures
-- ============================================

GO

CREATE OR ALTER PROCEDURE CreateTimeLog
(
    @TimeEntryId BIGINT,
    @ClockIn DATETIME2(3),
    @ClockOut DATETIME2(3) NULL,
    @LogType NVARCHAR(10) = 'work',
    @Duration DECIMAL(6,2) NULL,
    @Latitude DECIMAL(9,6) NULL,
    @Longitude DECIMAL(9,6) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[TimeLog] (
        [CreatedDatetime], [ModifiedDatetime], [TimeEntryId], [ClockIn], [ClockOut], [LogType], [Duration], [Latitude], [Longitude]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TimeEntryId],
        CONVERT(VARCHAR(23), INSERTED.[ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), INSERTED.[ClockOut], 121) AS [ClockOut],
        INSERTED.[LogType],
        INSERTED.[Duration],
        INSERTED.[Latitude],
        INSERTED.[Longitude]
    VALUES (
        @Now, @Now, @TimeEntryId, @ClockIn, @ClockOut, @LogType, @Duration, @Latitude, @Longitude
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTimeLogsByTimeEntryId
(
    @TimeEntryId BIGINT
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
        [TimeEntryId],
        CONVERT(VARCHAR(23), [ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), [ClockOut], 121) AS [ClockOut],
        [LogType],
        [Duration],
        [Latitude],
        [Longitude]
    FROM dbo.[TimeLog]
    WHERE [TimeEntryId] = @TimeEntryId
    ORDER BY [ClockIn] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTimeLogById
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
        [TimeEntryId],
        CONVERT(VARCHAR(23), [ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), [ClockOut], 121) AS [ClockOut],
        [LogType],
        [Duration],
        [Latitude],
        [Longitude]
    FROM dbo.[TimeLog]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTimeLogByPublicId
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
        [TimeEntryId],
        CONVERT(VARCHAR(23), [ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), [ClockOut], 121) AS [ClockOut],
        [LogType],
        [Duration],
        [Latitude],
        [Longitude]
    FROM dbo.[TimeLog]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE UpdateTimeLogById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @ClockIn DATETIME2(3),
    @ClockOut DATETIME2(3) NULL,
    @LogType NVARCHAR(10),
    @Duration DECIMAL(6,2) NULL,
    @Latitude DECIMAL(9,6) NULL,
    @Longitude DECIMAL(9,6) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[TimeLog]
    SET
        [ModifiedDatetime] = @Now,
        [ClockIn] = @ClockIn,
        [ClockOut] = @ClockOut,
        [LogType] = @LogType,
        [Duration] = @Duration,
        [Latitude] = @Latitude,
        [Longitude] = @Longitude
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[TimeEntryId],
        CONVERT(VARCHAR(23), INSERTED.[ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), INSERTED.[ClockOut], 121) AS [ClockOut],
        INSERTED.[LogType],
        INSERTED.[Duration],
        INSERTED.[Latitude],
        INSERTED.[Longitude]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE DeleteTimeLogById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[TimeLog]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[TimeEntryId],
        CONVERT(VARCHAR(23), DELETED.[ClockIn], 121) AS [ClockIn],
        CONVERT(VARCHAR(23), DELETED.[ClockOut], 121) AS [ClockOut],
        DELETED.[LogType],
        DELETED.[Duration],
        DELETED.[Latitude],
        DELETED.[Longitude]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO


-- ============================================
-- TimeEntryStatus Stored Procedures
-- ============================================

GO

CREATE OR ALTER PROCEDURE CreateTimeEntryStatus
(
    @TimeEntryId BIGINT,
    @Status NVARCHAR(20),
    @UserId BIGINT,
    @Note NVARCHAR(MAX) NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[TimeEntryStatus] (
        [CreatedDatetime], [TimeEntryId], [Status], [UserId], [Note]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        INSERTED.[TimeEntryId],
        INSERTED.[Status],
        INSERTED.[UserId],
        INSERTED.[Note]
    VALUES (
        @Now, @TimeEntryId, @Status, @UserId, @Note
    );

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadTimeEntryStatusesByTimeEntryId
(
    @TimeEntryId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        [TimeEntryId],
        [Status],
        [UserId],
        [Note]
    FROM dbo.[TimeEntryStatus]
    WHERE [TimeEntryId] = @TimeEntryId
    ORDER BY [CreatedDatetime] ASC;

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadCurrentTimeEntryStatus
(
    @TimeEntryId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        [TimeEntryId],
        [Status],
        [UserId],
        [Note]
    FROM dbo.[TimeEntryStatus]
    WHERE [TimeEntryId] = @TimeEntryId
    ORDER BY [CreatedDatetime] DESC;

    COMMIT TRANSACTION;
END;
GO
