-- =============================================================================
-- 2026-05-27 — TimeTracking → ContractLabor → Bill integration, Phase 1.
-- Adds User.EmployeeId / User.VendorId worker linkage. A User is at most one of
-- the two (XOR enforced in the Python service layer + defense-in-depth check in
-- UpdateUserWorkerLink).
--
-- Re-issues the 5 Read sprocs with the live shape (per migrations 002 + 004) +
-- the 2 new worker-link columns so /auth/me, React UserProfile, and any other
-- consumer sees the current linkage state.
--
-- CreateUser / UpdateUserById / DeleteUserById are NOT re-issued — they don't
-- write or care about worker links, and the User repo's `_from_db` tolerates
-- missing columns via getattr() defaults. The next Read populates the fields.
--
-- Idempotent (IF NOT EXISTS guards + CREATE OR ALTER). Safe to re-run.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO


-- Column additions ------------------------------------------------------------
IF COL_LENGTH('dbo.[User]', 'EmployeeId') IS NULL
    ALTER TABLE [dbo].[User] ADD [EmployeeId] BIGINT NULL;
GO

IF COL_LENGTH('dbo.[User]', 'VendorId') IS NULL
    ALTER TABLE [dbo].[User] ADD [VendorId] BIGINT NULL;
GO


-- Foreign keys ----------------------------------------------------------------
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


-- Filtered unique indexes — prevent two Users claiming the same worker row.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_User_EmployeeId' AND object_id = OBJECT_ID('dbo.[User]'))
    CREATE UNIQUE INDEX [UX_User_EmployeeId] ON [dbo].[User] ([EmployeeId]) WHERE [EmployeeId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_User_VendorId' AND object_id = OBJECT_ID('dbo.[User]'))
    CREATE UNIQUE INDEX [UX_User_VendorId] ON [dbo].[User] ([VendorId]) WHERE [VendorId] IS NOT NULL;
GO


-- Read sprocs — re-issued with the live shape (Phase 0 + Phase 4) + the 2 new
-- worker-link columns. EmployeeId / VendorId surface NULL on rows that haven't
-- been linked.
CREATE OR ALTER PROCEDURE ReadUsers
(
    @IncludeAgents BIT = 0
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
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId],
        [EmployeeId],
        [VendorId]
    FROM dbo.[User]
    WHERE
        @IncludeAgents = 1
        OR [IsAgent] = 0
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
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId],
        [EmployeeId],
        [VendorId]
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
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId],
        [EmployeeId],
        [VendorId]
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
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId],
        [EmployeeId],
        [VendorId]
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
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId],
        [EmployeeId],
        [VendorId]
    FROM dbo.[User]
    WHERE [Lastname] = @Lastname;

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

PRINT 'User worker-link migration applied: EmployeeId/VendorId columns + 5 Read sprocs + UpdateUserWorkerLink.';
