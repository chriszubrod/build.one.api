-- Migration: Rebuild RoleModule table
-- Change Id from UNIQUEIDENTIFIER to BIGINT IDENTITY
-- Change RoleId and ModuleId from UNIQUEIDENTIFIER to BIGINT

-- Step 1: Create new table with correct column types
CREATE TABLE dbo.RoleModule_New
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [RoleId] BIGINT NOT NULL,
    [ModuleId] BIGINT NOT NULL
);

GO

-- Step 2: Copy existing data with FK resolution (UUID -> BIGINT)
INSERT INTO dbo.RoleModule_New (PublicId, CreatedDatetime, ModifiedDatetime, RoleId, ModuleId)
SELECT
    rm.PublicId,
    rm.CreatedDatetime,
    rm.ModifiedDatetime,
    r.Id,
    m.Id
FROM dbo.RoleModule rm
INNER JOIN dbo.Role r ON rm.RoleId = r.PublicId
INNER JOIN dbo.Module m ON rm.ModuleId = m.PublicId;

GO

-- Step 3: Drop old table
DROP TABLE dbo.RoleModule;

GO

-- Step 4: Rename new table
EXEC sp_rename 'dbo.RoleModule_New', 'RoleModule';

GO
