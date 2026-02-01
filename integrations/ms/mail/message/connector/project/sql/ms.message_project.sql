-- =============================================================================
-- MS Message to Project Connector Tables and Stored Procedures
-- =============================================================================

-- Link between MsMessage and Project
IF OBJECT_ID('dbo.MsMessageProject', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.MsMessageProject (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        RowVersion ROWVERSION NOT NULL,
        CreatedDatetime DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        ModifiedDatetime DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        
        MsMessageId INT NOT NULL,
        ProjectId BIGINT NOT NULL,
        
        -- Optional notes about the relationship
        Notes NVARCHAR(1000) NULL,
        
        CONSTRAINT FK_MsMessageProject_MsMessage 
            FOREIGN KEY (MsMessageId) REFERENCES dbo.MsMessage(Id) ON DELETE CASCADE,
        CONSTRAINT FK_MsMessageProject_Project 
            FOREIGN KEY (ProjectId) REFERENCES dbo.Project(Id) ON DELETE CASCADE,
        CONSTRAINT UQ_MsMessageProject_PublicId UNIQUE (PublicId),
        CONSTRAINT UQ_MsMessageProject_Unique UNIQUE (MsMessageId, ProjectId)
    );
    
    IF OBJECT_ID('dbo.MsMessageProject', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessageProject_MsMessageId' AND object_id = OBJECT_ID('dbo.MsMessageProject'))
    BEGIN
    CREATE INDEX IX_MsMessageProject_MsMessageId ON dbo.MsMessageProject(MsMessageId);
    END
    IF OBJECT_ID('dbo.MsMessageProject', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessageProject_ProjectId' AND object_id = OBJECT_ID('dbo.MsMessageProject'))
    BEGIN
    CREATE INDEX IX_MsMessageProject_ProjectId ON dbo.MsMessageProject(ProjectId);
    END
END
GO


-- Create link between message and project
CREATE OR ALTER PROCEDURE dbo.CreateMsMessageProject
    @MsMessageId INT,
    @ProjectId BIGINT,
    @Notes NVARCHAR(1000) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    INSERT INTO dbo.MsMessageProject (MsMessageId, ProjectId, Notes)
    VALUES (@MsMessageId, @ProjectId, @Notes);
    
    SELECT * FROM dbo.MsMessageProject WHERE Id = SCOPE_IDENTITY();
END
GO

-- Read all links
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageProjects
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageProject ORDER BY CreatedDatetime DESC;
END
GO

-- Read by public ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageProjectByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageProject WHERE PublicId = @PublicId;
END
GO

-- Read by message ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageProjectsByMsMessageId
    @MsMessageId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageProject WHERE MsMessageId = @MsMessageId;
END
GO

-- Read by project ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageProjectsByProjectId
    @ProjectId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageProject WHERE ProjectId = @ProjectId;
END
GO

-- Update notes
CREATE OR ALTER PROCEDURE dbo.UpdateMsMessageProjectByPublicId
    @PublicId UNIQUEIDENTIFIER,
    @Notes NVARCHAR(1000) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE dbo.MsMessageProject
    SET Notes = @Notes,
        ModifiedDatetime = GETUTCDATE()
    WHERE PublicId = @PublicId;
    
    SELECT * FROM dbo.MsMessageProject WHERE PublicId = @PublicId;
END
GO

-- Delete link
CREATE OR ALTER PROCEDURE dbo.DeleteMsMessageProjectByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @Deleted TABLE (
        Id INT, PublicId UNIQUEIDENTIFIER, RowVersion VARBINARY(8),
        CreatedDatetime DATETIME2, ModifiedDatetime DATETIME2,
        MsMessageId INT, ProjectId INT, Notes NVARCHAR(1000)
    );
    
    DELETE FROM dbo.MsMessageProject
    OUTPUT DELETED.*
    INTO @Deleted
    WHERE PublicId = @PublicId;
    
    SELECT * FROM @Deleted;
END
GO
