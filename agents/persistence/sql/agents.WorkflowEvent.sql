-- =============================================================================
-- WorkflowEvent Table and Stored Procedures
-- =============================================================================

-- WorkflowEvent table (audit trail)
IF NOT EXISTS (SELECT * FROM sys.tables t JOIN sys.schemas s ON t.schema_id = s.schema_id WHERE t.name = 'WorkflowEvent' AND s.name = 'agents')
BEGIN
    CREATE TABLE agents.WorkflowEvent (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        WorkflowId INT NOT NULL,
        
        -- Event details
        EventType VARCHAR(50) NOT NULL,
        FromState VARCHAR(50) NULL,
        ToState VARCHAR(50) NULL,
        StepName VARCHAR(100) NULL,
        
        -- Event data
        Data NVARCHAR(MAX) NULL,
        
        -- Metadata
        CreatedAt DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME(),
        CreatedBy VARCHAR(200) NULL,
        
        CONSTRAINT FK_WorkflowEvent_Workflow FOREIGN KEY (WorkflowId) REFERENCES agents.Workflow(Id) ON DELETE CASCADE
    );
    
    CREATE INDEX IX_WorkflowEvent_Workflow ON agents.WorkflowEvent(WorkflowId, CreatedAt);
    CREATE INDEX IX_WorkflowEvent_EventType ON agents.WorkflowEvent(EventType, CreatedAt);
END
GO


-- Create workflow event
CREATE OR ALTER PROCEDURE agents.CreateWorkflowEvent
    @WorkflowId INT,
    @EventType VARCHAR(50),
    @FromState VARCHAR(50) = NULL,
    @ToState VARCHAR(50) = NULL,
    @StepName VARCHAR(100) = NULL,
    @Data NVARCHAR(MAX) = NULL,
    @CreatedBy VARCHAR(200) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    INSERT INTO agents.WorkflowEvent (
        WorkflowId, EventType, FromState, ToState, StepName, Data, CreatedBy
    )
    VALUES (
        @WorkflowId, @EventType, @FromState, @ToState, @StepName, @Data, @CreatedBy
    );
    
    SELECT * FROM agents.WorkflowEvent WHERE Id = SCOPE_IDENTITY();
END
GO


-- Read events by workflow ID
CREATE OR ALTER PROCEDURE agents.ReadWorkflowEventsByWorkflowId
    @WorkflowId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.WorkflowEvent WHERE WorkflowId = @WorkflowId ORDER BY CreatedAt;
END
GO


-- Read events by event type
CREATE OR ALTER PROCEDURE agents.ReadWorkflowEventsByType
    @WorkflowId INT,
    @EventType VARCHAR(50)
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM agents.WorkflowEvent 
    WHERE WorkflowId = @WorkflowId AND EventType = @EventType 
    ORDER BY CreatedAt;
END
GO


-- Read latest event for workflow
CREATE OR ALTER PROCEDURE agents.ReadLatestWorkflowEvent
    @WorkflowId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT TOP 1 * FROM agents.WorkflowEvent 
    WHERE WorkflowId = @WorkflowId 
    ORDER BY CreatedAt DESC;
END
GO


-- Read events in date range (for reporting)
CREATE OR ALTER PROCEDURE agents.ReadWorkflowEventsInRange
    @StartDate DATETIME2,
    @EndDate DATETIME2,
    @EventType VARCHAR(50) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    IF @EventType IS NULL
        SELECT * FROM agents.WorkflowEvent 
        WHERE CreatedAt >= @StartDate AND CreatedAt < @EndDate
        ORDER BY CreatedAt;
    ELSE
        SELECT * FROM agents.WorkflowEvent 
        WHERE CreatedAt >= @StartDate AND CreatedAt < @EndDate AND EventType = @EventType
        ORDER BY CreatedAt;
END
GO
