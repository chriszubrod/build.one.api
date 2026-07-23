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


-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-126, 2026-07-23) — sproc body removed, NOT the intent.
--
-- Original intent of this section (preserved for lineage):
--   Dedicated set/clear User EmployeeId/VendorId worker linkage with XOR guard.
--
-- The canonical definition of this sproc now lives in exactly ONE place:
--   entities/user/sql/dbo.user.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.UpdateUserWorkerLink
--
-- Re-running this file is now a no-op for this sproc. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------

PRINT 'User worker-link migration applied: EmployeeId/VendorId columns + 5 Read sprocs.';
