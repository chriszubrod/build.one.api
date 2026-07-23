IF OBJECT_ID('dbo.User', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[User]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [Firstname] NVARCHAR(50) NOT NULL,
    [Lastname] NVARCHAR(255) NOT NULL,
    -- Worker linkage — at most one of EmployeeId / VendorId is non-NULL per User.
    -- XOR enforced in the Python service layer (UserService.set_worker_link); we
    -- skip a CHECK constraint so admin tooling can flip a row through a neutral
    -- (both-NULL) state without needing an atomic transaction.
    [EmployeeId] BIGINT NULL,
    [VendorId]   BIGINT NULL
);
END
GO

-- Idempotent column adds for existing environments.
IF COL_LENGTH('dbo.[User]', 'EmployeeId') IS NULL
    ALTER TABLE [dbo].[User] ADD [EmployeeId] BIGINT NULL;
GO

IF COL_LENGTH('dbo.[User]', 'VendorId') IS NULL
    ALTER TABLE [dbo].[User] ADD [VendorId] BIGINT NULL;
GO

-- FK constraints — Employee must exist before User can be backfilled.
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_User_Employee')
   AND OBJECT_ID('dbo.[Employee]', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[User]
    ADD CONSTRAINT [FK_User_Employee] FOREIGN KEY ([EmployeeId]) REFERENCES [dbo].[Employee]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_User_Vendor')
   AND OBJECT_ID('dbo.[Vendor]', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[User]
    ADD CONSTRAINT [FK_User_Vendor] FOREIGN KEY ([VendorId]) REFERENCES [dbo].[Vendor]([Id]);
END
GO

-- Filtered unique indexes prevent two Users from claiming the same worker row.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_User_EmployeeId' AND object_id = OBJECT_ID('dbo.[User]'))
BEGIN
    CREATE UNIQUE INDEX [UX_User_EmployeeId] ON [dbo].[User] ([EmployeeId]) WHERE [EmployeeId] IS NOT NULL;
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_User_VendorId' AND object_id = OBJECT_ID('dbo.[User]'))
BEGIN
    CREATE UNIQUE INDEX [UX_User_VendorId] ON [dbo].[User] ([VendorId]) WHERE [VendorId] IS NOT NULL;
END
GO



GO

CREATE OR ALTER PROCEDURE CreateUser
(
    @Firstname NVARCHAR(50),
    @Lastname NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[User] ([CreatedDatetime], [ModifiedDatetime], [Firstname], [Lastname])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Firstname],
        INSERTED.[Lastname]
    VALUES (@Now, @Now, @Firstname, @Lastname);

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadUsers
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    ORDER BY [Lastname] ASC, [Firstname] ASC;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserById
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
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserByPublicId
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
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [PublicId] = @PublicId;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserByFirstname
(
    @Firstname NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [Firstname] = @Firstname;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE ReadUserByLastname
(
    @Lastname NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT TOP 1
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [Firstname],
        [Lastname]
    FROM dbo.[User]
    WHERE [Lastname] = @Lastname;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE UpdateUserById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Firstname NVARCHAR(50),
    @Lastname NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[User]
    SET
        [ModifiedDatetime] = @Now,
        [Firstname] = @Firstname,
        [Lastname] = @Lastname
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Firstname],
        INSERTED.[Lastname]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;



GO

CREATE OR ALTER PROCEDURE DeleteUserById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[User]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Firstname],
        DELETED.[Lastname]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO

-- U-126 (2026-07-23): sprocs below (ReadWorkers, SetUserLastCompanyId, UpdateUserWorkerLink) homed from migrations 2026_06_10/002/005; bodies are the LIVE prod definitions captured via sys.sql_modules.

-- =============================================================================
-- 2026-06-10 — dbo.ReadWorkers — curated list for the time-entry worker picker.
--
-- "Workers" are users who can have hours billed to them. The picker on the
-- web (and eventually iOS) populates from this list. The set is:
--   - excludes LLM agents          (User.IsAgent = 1)
--   - excludes persona test users  (Auth.Username starts with 'persona_')
--   - includes anyone with an Employee or Vendor FK linkage
--   - includes anyone holding a 'Field Crew' or 'Intern' role (covers
--     interns and non-W2 crew whose User row isn't linked to an Employee
--     or Vendor record)
--
-- Excludes pure-admin / PM / Owner / Controller / Reviewer / Auditor /
-- Time Clerk / AP-Spec / AR-Spec / Tenant Admin roles — those users
-- don't log their own time and shouldn't clutter the picker.
--
-- Same column shape as ReadUsers so the existing UserRepository._from_db
-- hydrator handles the row without changes.
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
-- =============================================================================

CREATE OR ALTER PROCEDURE dbo.ReadWorkers
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        u.[Id],
        u.[PublicId],
        u.[RowVersion],
        CONVERT(VARCHAR(19), u.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), u.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        u.[Firstname],
        u.[Lastname],
        u.[IsSystemAdmin],
        u.[IsAgent],
        u.[LastCompanyId],
        u.[CreatedByUserId],
        u.[ModifiedByUserId],
        u.[EmployeeId],
        u.[VendorId]
    FROM dbo.[User] u
    WHERE ISNULL(u.[IsAgent], 0) = 0
      AND NOT EXISTS (
          SELECT 1
          FROM dbo.[Auth] a
          WHERE a.[UserId] = u.[Id]
            AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
      )
      AND (
          u.[EmployeeId] IS NOT NULL
          OR u.[VendorId] IS NOT NULL
          OR EXISTS (
              SELECT 1
              FROM dbo.[UserRole] ur
              INNER JOIN dbo.[Role] r ON r.[Id] = ur.[RoleId]
              WHERE ur.[UserId] = u.[Id]
                AND r.[Name] IN (N'Field Crew', N'Intern')
          )
      )
    ORDER BY u.[Lastname] ASC, u.[Firstname] ASC;
END;
GO


-- Dedicated mutation sproc for User.LastCompanyId. Used by the
-- switch-company endpoint to remember the user's choice.
CREATE OR ALTER PROCEDURE SetUserLastCompanyId
(
    @UserId BIGINT,
    @LastCompanyId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    UPDATE dbo.[User]
       SET [LastCompanyId] = @LastCompanyId,
           [ModifiedDatetime] = SYSUTCDATETIME()
     WHERE [Id] = @UserId;

    COMMIT TRANSACTION;
END;
GO


-- New mutation sproc — dedicated set-worker-link path. Defense-in-depth XOR
-- check (also enforced in UserService.set_worker_link). Pass both NULL to
-- clear the link.
CREATE OR ALTER PROCEDURE UpdateUserWorkerLink
(
    @Id          BIGINT,
    @RowVersion  BINARY(8),
    @EmployeeId  BIGINT = NULL,
    @VendorId    BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF @EmployeeId IS NOT NULL AND @VendorId IS NOT NULL
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('User.EmployeeId and User.VendorId are mutually exclusive — at most one may be set.', 16, 1);
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM dbo.[User] WHERE [Id] = @Id)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('User not found.', 16, 1);
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM dbo.[User] WHERE [Id] = @Id AND [RowVersion] = @RowVersion)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Concurrency conflict: the user record has been modified by another user. Please refresh and try again.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[User]
    SET
        [ModifiedDatetime] = @Now,
        [EmployeeId] = @EmployeeId,
        [VendorId]   = @VendorId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Firstname],
        INSERTED.[Lastname],
        INSERTED.[IsSystemAdmin],
        INSERTED.[IsAgent],
        INSERTED.[LastCompanyId],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId],
        INSERTED.[EmployeeId],
        INSERTED.[VendorId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO

-- PublicId index
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_User_PublicId' AND object_id = OBJECT_ID('dbo.User'))
BEGIN
    CREATE INDEX [IX_User_PublicId] ON [dbo].[User] ([PublicId]);
END
GO

