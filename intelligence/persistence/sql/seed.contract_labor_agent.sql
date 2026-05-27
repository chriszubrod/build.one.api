-- Provision contract_labor_agent user, auth, role, and grants. Idempotent.
--
-- Mirrors seed.bill_agent.sql structure. Grants:
--   Contract Labor — Create + Read + Update (no Delete/Submit/Approve/Complete).
--                    The agent creates a draft pending_review row; a human
--                    reviewer adds rate/markup/SCC and runs mark_as_ready.
--   Vendors        — Read (so the agent can resolve the worker's Vendor
--                    record via find_contract_labor_vendor_by_email).
--   Projects       — Read (so delegate_to_project_specialist's downstream
--                    sub-session can look up the project — and so the agent
--                    itself can inspect the resolved project if needed).
--
-- RUN:
--   .venv/bin/python scripts/run_sql.py intelligence/persistence/sql/seed.contract_labor_agent.sql

DECLARE @Now DATETIME2 = SYSUTCDATETIME();
DECLARE @ContractLaborModuleId INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Contract Labor');
DECLARE @VendorModuleId        INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Vendors');
DECLARE @ProjectModuleId       INT = (SELECT Id FROM dbo.[Module] WHERE Name = 'Projects');
DECLARE @RogersCompanyId       BIGINT = (SELECT Id FROM dbo.Company WHERE Name = 'Rogers Build, Inc.');

IF @ContractLaborModuleId IS NULL OR @VendorModuleId IS NULL OR @ProjectModuleId IS NULL OR @RogersCompanyId IS NULL
BEGIN
    RAISERROR('Required Module/Company rows (Contract Labor / Vendors / Projects / Rogers Build) missing. Aborting.', 16, 1);
    RETURN;
END;

DECLARE @Username NVARCHAR(100) = 'contract_labor_agent';
DECLARE @RoleName NVARCHAR(100) = 'Contract Labor Specialist';
DECLARE @PasswordHash NVARCHAR(255) =
    '$2b$12$tBikqd7UCLiNCmmjre7VVeA29T3sAjAS2ZcBA1/lACzKrIxJHfLxS';

DECLARE @UserId BIGINT;
SELECT @UserId = u.Id
FROM dbo.[User] u
INNER JOIN dbo.Auth a ON a.UserId = u.Id
WHERE a.Username = @Username;

IF @UserId IS NULL
BEGIN
    INSERT INTO dbo.[User] (CreatedDatetime, ModifiedDatetime, Firstname, Lastname, IsAgent)
    VALUES (@Now, @Now, 'Agent', 'Contract Labor', 1);
    SET @UserId = SCOPE_IDENTITY();
    PRINT CONCAT('  contract_labor_agent: user created (id=', @UserId, ')');
END
ELSE
    PRINT CONCAT('  contract_labor_agent: user exists (id=', @UserId, ')');

IF EXISTS (SELECT 1 FROM dbo.Auth WHERE Username = @Username)
BEGIN
    UPDATE dbo.Auth
       SET PasswordHash = @PasswordHash,
           ModifiedDatetime = @Now
     WHERE Username = @Username;
    PRINT '  contract_labor_agent: auth exists (password rotated)';
END
ELSE
BEGIN
    INSERT INTO dbo.Auth (CreatedDatetime, ModifiedDatetime, Username, PasswordHash, UserId)
    VALUES (@Now, @Now, @Username, @PasswordHash, @UserId);
    PRINT '  contract_labor_agent: auth created';
END;

DECLARE @RoleId BIGINT;
SELECT @RoleId = Id FROM dbo.Role WHERE Name = @RoleName;
IF @RoleId IS NULL
BEGIN
    INSERT INTO dbo.Role (CreatedDatetime, ModifiedDatetime, Name)
    VALUES (@Now, @Now, @RoleName);
    SET @RoleId = SCOPE_IDENTITY();
    PRINT CONCAT('  contract_labor_agent: role created (id=', @RoleId, ')');
END
ELSE
    PRINT CONCAT('  contract_labor_agent: role exists (id=', @RoleId, ')');

IF NOT EXISTS (SELECT 1 FROM dbo.UserRole WHERE UserId = @UserId AND RoleId = @RoleId AND CompanyId = @RogersCompanyId)
BEGIN
    INSERT INTO dbo.UserRole (CreatedDatetime, ModifiedDatetime, UserId, RoleId, CompanyId)
    VALUES (@Now, @Now, @UserId, @RoleId, @RogersCompanyId);
    PRINT '  contract_labor_agent: user-role link created (Rogers Build, Inc.)';
END;

IF NOT EXISTS (SELECT 1 FROM dbo.UserCompany WHERE UserId = @UserId AND CompanyId = @RogersCompanyId)
BEGIN
    INSERT INTO dbo.UserCompany (CreatedDatetime, ModifiedDatetime, UserId, CompanyId)
    VALUES (@Now, @Now, @UserId, @RogersCompanyId);
    PRINT '  contract_labor_agent: user-company link created (Rogers Build, Inc.)';
END;

-- Contract Labor — Create + Read + Update (no Delete/Submit/Approve/Complete).
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @ContractLaborModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 1, CanRead = 1, CanUpdate = 1, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @ContractLaborModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @ContractLaborModuleId, 1, 1, 1, 0, 0, 0, 0);

-- Vendors — read only (for find_contract_labor_vendor_by_email).
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @VendorModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 0, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @VendorModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @VendorModuleId, 0, 1, 0, 0, 0, 0, 0);

-- Projects — read only (for delegate_to_project_specialist sub-session).
IF EXISTS (SELECT 1 FROM dbo.RoleModule WHERE RoleId = @RoleId AND ModuleId = @ProjectModuleId)
    UPDATE dbo.RoleModule
       SET CanCreate = 0, CanRead = 1, CanUpdate = 0, CanDelete = 0,
           CanSubmit = 0, CanApprove = 0, CanComplete = 0,
           ModifiedDatetime = @Now
     WHERE RoleId = @RoleId AND ModuleId = @ProjectModuleId;
ELSE
    INSERT INTO dbo.RoleModule
        (CreatedDatetime, ModifiedDatetime, RoleId, ModuleId,
         CanCreate, CanRead, CanUpdate, CanDelete, CanSubmit, CanApprove, CanComplete)
    VALUES (@Now, @Now, @RoleId, @ProjectModuleId, 0, 1, 0, 0, 0, 0, 0);

PRINT '  contract_labor_agent: module grants set (Contract Labor CRU, Vendors read, Projects read)';

PRINT '────────────────────────────────────────────────────────────';
PRINT 'DONE — set CONTRACT_LABOR_AGENT_USERNAME / CONTRACT_LABOR_AGENT_PASSWORD on App Service.';
PRINT '────────────────────────────────────────────────────────────';
