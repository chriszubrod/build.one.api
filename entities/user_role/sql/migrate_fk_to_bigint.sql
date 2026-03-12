-- Migration: Change UserRole.UserId and UserRole.RoleId from UNIQUEIDENTIFIER to BIGINT
-- These columns should reference User.Id and Role.Id (BIGINT) instead of PublicId (UNIQUEIDENTIFIER)

-- Step 1: Add new BIGINT columns
ALTER TABLE dbo.UserRole ADD UserId_New BIGINT NULL;

GO

ALTER TABLE dbo.UserRole ADD RoleId_New BIGINT NULL;

GO

-- Step 2: Populate from JOINs on existing UUID values
UPDATE ur
SET ur.UserId_New = u.Id
FROM dbo.UserRole ur
INNER JOIN dbo.[User] u ON ur.UserId = u.PublicId;

GO

UPDATE ur
SET ur.RoleId_New = r.Id
FROM dbo.UserRole ur
INNER JOIN dbo.Role r ON ur.RoleId = r.PublicId;

GO

-- Step 3: Drop old columns
ALTER TABLE dbo.UserRole DROP COLUMN UserId;

GO

ALTER TABLE dbo.UserRole DROP COLUMN RoleId;

GO

-- Step 4: Rename new columns
EXEC sp_rename 'dbo.UserRole.UserId_New', 'UserId', 'COLUMN';

GO

EXEC sp_rename 'dbo.UserRole.RoleId_New', 'RoleId', 'COLUMN';

GO

-- Step 5: Set NOT NULL constraints
ALTER TABLE dbo.UserRole ALTER COLUMN UserId BIGINT NOT NULL;

GO

ALTER TABLE dbo.UserRole ALTER COLUMN RoleId BIGINT NOT NULL;

GO
