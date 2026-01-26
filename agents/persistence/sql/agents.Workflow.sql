-- =============================================================================
-- Agents Schema and Workflow Table with Stored Procedures
-- =============================================================================

-- Create schema if not exists
IF NOT EXISTS (SELECT * FROM sys.schemas WHERE name = 'agents')
BEGIN
    EXEC('CREATE SCHEMA agents');
END
GO

-- Workflow table
IF NOT EXISTS (SELECT * FROM sys.tables t JOIN sys.schemas s ON t.schema_id = s.schema_id WHERE t.name = 'Workflow' AND s.name = 'agents')
BEGIN
    CREATE TABLE agents.Workflow (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        RowVersion ROWVERSION NOT NULL,
        TenantId INT NOT NULL,
        
        -- Type and state
        WorkflowType VARCHAR(50) NOT NULL,
        State VARCHAR(50) NOT NULL,
        
        -- Parent/child relationship
        ParentWorkflowId INT NULL,
        
        -- Correlation keys
        ConversationId NVARCHAR(200) NULL,
        TriggerMessageId NVARCHAR(200) NULL,
        
        -- Queryable entity references
        VendorId INT NULL,
        ProjectId INT NULL,
        BillId INT NULL,
        
        -- Flexible context
        Context NVARCHAR(MAX) NULL,
        
        -- Timestamps
        CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        UpdatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        CompletedAt DATETIME2 NULL,
        
        CONSTRAINT UQ_Workflow_PublicId UNIQUE (PublicId),
        CONSTRAINT FK_Workflow_Parent FOREIGN KEY (ParentWorkflowId) REFERENCES agents.Workflow(Id)
    );
    
    CREATE INDEX IX_Workflow_TenantState ON agents.Workflow(TenantId, State);
    CREATE INDEX IX_Workflow_ConversationId ON agents.Workflow(ConversationId);
    CREATE INDEX IX_Workflow_Parent ON agents.Workflow(ParentWorkflowId);
    CREATE INDEX IX_Workflow_TriggerMessageId ON agents.Workflow(TriggerMessageId);
END
GO


-- Create workflow
CREATE OR ALTER PROCEDURE agents.CreateWorkflow
    @TenantId INT,
    @WorkflowType VARCHAR(50),
    @State VARCHAR(50),
    @ParentWorkflowId INT = NULL,
    @ConversationId NVARCHAR(200) = NULL,
    @TriggerMessageId NVARCHAR(200) = NULL,
    @VendorId INT = NULL,
    @ProjectId INT = NULL,
    @BillId INT = NULL,
    @Context NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    INSERT INTO agents.Workflow (
        TenantId, WorkflowType, State, ParentWorkflowId,
        ConversationId, TriggerMessageId,
        VendorId, ProjectId, BillId, Context
    )
    VALUES (
        @TenantId, @WorkflowType, @State, @ParentWorkflowId,
        @ConversationId, @TriggerMessageId,
        @VendorId, @ProjectId, @BillId, @Context
    );
    
    SELECT * FROM agents.Workflow WHERE Id = SCOPE_IDENTITY();
END
GO


-- Read by public ID
CREATE OR ALTER PROCEDURE agents.ReadWorkflowByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.Workflow WHERE PublicId = @PublicId;
END
GO


-- Read by ID
CREATE OR ALTER PROCEDURE agents.ReadWorkflowById
    @Id INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.Workflow WHERE Id = @Id;
END
GO


-- Read by conversation ID
CREATE OR ALTER PROCEDURE agents.ReadWorkflowsByConversationId
    @ConversationId NVARCHAR(200)
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.Workflow WHERE ConversationId = @ConversationId ORDER BY CreatedAt DESC;
END
GO


-- Read by trigger message ID
CREATE OR ALTER PROCEDURE agents.ReadWorkflowByTriggerMessageId
    @TriggerMessageId NVARCHAR(200)
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.Workflow WHERE TriggerMessageId = @TriggerMessageId;
END
GO


-- Read by tenant and state
CREATE OR ALTER PROCEDURE agents.ReadWorkflowsByTenantAndState
    @TenantId INT,
    @State VARCHAR(50) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    IF @State IS NULL
        SELECT * FROM agents.Workflow WHERE TenantId = @TenantId ORDER BY CreatedAt DESC;
    ELSE
        SELECT * FROM agents.Workflow WHERE TenantId = @TenantId AND State = @State ORDER BY CreatedAt DESC;
END
GO


-- Read active workflows (not completed, abandoned, or cancelled)
CREATE OR ALTER PROCEDURE agents.ReadActiveWorkflows
    @TenantId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.Workflow 
    WHERE TenantId = @TenantId 
      AND State NOT IN ('completed', 'abandoned', 'cancelled')
    ORDER BY CreatedAt DESC;
END
GO


-- Read child workflows
CREATE OR ALTER PROCEDURE agents.ReadChildWorkflows
    @ParentWorkflowId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.Workflow WHERE ParentWorkflowId = @ParentWorkflowId ORDER BY CreatedAt;
END
GO


-- Update workflow state
CREATE OR ALTER PROCEDURE agents.UpdateWorkflowState
    @PublicId UNIQUEIDENTIFIER,
    @State VARCHAR(50),
    @Context NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE agents.Workflow
    SET State = @State,
        Context = COALESCE(@Context, Context),
        UpdatedAt = SYSUTCDATETIME(),
        CompletedAt = CASE WHEN @State IN ('completed', 'abandoned', 'cancelled') THEN SYSUTCDATETIME() ELSE CompletedAt END
    WHERE PublicId = @PublicId;
    
    SELECT * FROM agents.Workflow WHERE PublicId = @PublicId;
END
GO


-- Update workflow entity references
CREATE OR ALTER PROCEDURE agents.UpdateWorkflowEntities
    @PublicId UNIQUEIDENTIFIER,
    @VendorId INT = NULL,
    @ProjectId INT = NULL,
    @BillId INT = NULL,
    @Context NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE agents.Workflow
    SET VendorId = COALESCE(@VendorId, VendorId),
        ProjectId = COALESCE(@ProjectId, ProjectId),
        BillId = COALESCE(@BillId, BillId),
        Context = COALESCE(@Context, Context),
        UpdatedAt = SYSUTCDATETIME()
    WHERE PublicId = @PublicId;
    
    SELECT * FROM agents.Workflow WHERE PublicId = @PublicId;
END
GO


-- Update workflow context only
CREATE OR ALTER PROCEDURE agents.UpdateWorkflowContext
    @PublicId UNIQUEIDENTIFIER,
    @Context NVARCHAR(MAX)
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE agents.Workflow
    SET Context = @Context,
        UpdatedAt = SYSUTCDATETIME()
    WHERE PublicId = @PublicId;
    
    SELECT * FROM agents.Workflow WHERE PublicId = @PublicId;
END
GO


-- Read workflows awaiting response past timeout
CREATE OR ALTER PROCEDURE agents.ReadWorkflowsPastTimeout
    @TenantId INT,
    @State VARCHAR(50),
    @TimeoutDays INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.Workflow 
    WHERE TenantId = @TenantId 
      AND State = @State
      AND UpdatedAt < DATEADD(DAY, -@TimeoutDays, SYSUTCDATETIME())
    ORDER BY UpdatedAt;
END
GO


-- Read workflows created between two dates
CREATE OR ALTER PROCEDURE agents.ReadWorkflowsCreatedBetween
    @TenantId INT,
    @StartDate DATETIME2,
    @EndDate DATETIME2
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.Workflow 
    WHERE TenantId = @TenantId 
      AND CreatedAt >= @StartDate
      AND CreatedAt < @EndDate
    ORDER BY CreatedAt DESC;
END
GO


-- Read workflows completed between two dates
CREATE OR ALTER PROCEDURE agents.ReadWorkflowsCompletedBetween
    @TenantId INT,
    @StartDate DATETIME2,
    @EndDate DATETIME2
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.Workflow 
    WHERE TenantId = @TenantId 
      AND CompletedAt >= @StartDate
      AND CompletedAt < @EndDate
    ORDER BY CompletedAt DESC;
END
GO
