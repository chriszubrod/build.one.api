-- =============================================================================
-- dbo.EmailThreadStageHistory
-- Immutable audit trail of every stage transition for an EmailThread.
-- One row is written for each stage advance — records are never updated
-- or deleted. Provides full process history for reporting and dispute
-- resolution. TriggeredBy holds the EventType value that caused the move.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Table
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'EmailThreadStageHistory' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.EmailThreadStageHistory (
        Id                  BIGINT              NOT NULL IDENTITY(1,1),
        PublicId            UNIQUEIDENTIFIER    NOT NULL DEFAULT NEWID(),

        -- Parent thread
        EmailThreadId       BIGINT              NOT NULL,

        -- Stage transition
        FromStage           VARCHAR(100)        NOT NULL,
        ToStage             VARCHAR(100)        NOT NULL,

        -- What caused this transition (EventType value from the process engine)
        TriggeredBy         VARCHAR(100)        NOT NULL,

        -- Who or what triggered it
        -- NULL = system/agent triggered; populated = user-initiated
        UserId              BIGINT              NULL,

        -- Link to the message that caused this transition, if applicable
        EmailThreadMessageId BIGINT             NULL,

        -- Optional context — agent reasoning, override notes, SLA details
        Notes               NVARCHAR(1000)      NULL,

        -- Transition datetime (explicit, not derived from CreatedDatetime)
        TransitionDatetime  DATETIME2(3)        NOT NULL DEFAULT SYSUTCDATETIME(),

        -- Audit (CreatedDatetime = write time; no UpdatedDatetime — immutable)
        CreatedDatetime     DATETIME2(3)        NOT NULL DEFAULT SYSUTCDATETIME(),
        RowVersion          ROWVERSION          NOT NULL,

        CONSTRAINT PK_EmailThreadStageHistory
            PRIMARY KEY CLUSTERED (Id),

        CONSTRAINT UQ_EmailThreadStageHistory_PublicId
            UNIQUE (PublicId),

        CONSTRAINT FK_EmailThreadStageHistory_EmailThread
            FOREIGN KEY (EmailThreadId)
            REFERENCES dbo.EmailThread (Id),

        CONSTRAINT FK_EmailThreadStageHistory_User
            FOREIGN KEY (UserId)
            REFERENCES dbo.[User] (Id),

        CONSTRAINT FK_EmailThreadStageHistory_EmailThreadMessage
            FOREIGN KEY (EmailThreadMessageId)
            REFERENCES dbo.EmailThreadMessage (Id)
    );
END
GO

-- -----------------------------------------------------------------------------
-- Indexes (all idempotent)
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThreadStageHistory_EmailThreadId'
    AND object_id = OBJECT_ID('dbo.EmailThreadStageHistory')
)
    CREATE INDEX IX_EmailThreadStageHistory_EmailThreadId
        ON dbo.EmailThreadStageHistory (EmailThreadId, TransitionDatetime ASC);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThreadStageHistory_UserId'
    AND object_id = OBJECT_ID('dbo.EmailThreadStageHistory')
)
    CREATE INDEX IX_EmailThreadStageHistory_UserId
        ON dbo.EmailThreadStageHistory (UserId)
        WHERE UserId IS NOT NULL;
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThreadStageHistory_TriggeredBy'
    AND object_id = OBJECT_ID('dbo.EmailThreadStageHistory')
)
    CREATE INDEX IX_EmailThreadStageHistory_TriggeredBy
        ON dbo.EmailThreadStageHistory (TriggeredBy, TransitionDatetime ASC);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThreadStageHistory_ToStage'
    AND object_id = OBJECT_ID('dbo.EmailThreadStageHistory')
)
    CREATE INDEX IX_EmailThreadStageHistory_ToStage
        ON dbo.EmailThreadStageHistory (EmailThreadId, ToStage);
GO

-- -----------------------------------------------------------------------------
-- Stored Procedures
-- -----------------------------------------------------------------------------

-- Write a new stage transition record. This table is append-only —
-- no UPDATE or DELETE procedures are provided by design.
-- Returns the inserted row via OUTPUT INSERTED.*.
CREATE OR ALTER PROCEDURE dbo.CreateEmailThreadStageHistory
    @PublicId               UNIQUEIDENTIFIER,
    @EmailThreadId          BIGINT,
    @FromStage              VARCHAR(100),
    @ToStage                VARCHAR(100),
    @TriggeredBy            VARCHAR(100),
    @UserId                 BIGINT          = NULL,
    @EmailThreadMessageId   BIGINT          = NULL,
    @Notes                  NVARCHAR(1000)  = NULL,
    @TransitionDatetime     DATETIME2(3)    = NULL
AS
BEGIN
    SET NOCOUNT ON;

    INSERT INTO dbo.EmailThreadStageHistory (
        PublicId,
        EmailThreadId,
        FromStage,
        ToStage,
        TriggeredBy,
        UserId,
        EmailThreadMessageId,
        Notes,
        TransitionDatetime
    )
    OUTPUT INSERTED.*
    VALUES (
        @PublicId,
        @EmailThreadId,
        @FromStage,
        @ToStage,
        @TriggeredBy,
        @UserId,
        @EmailThreadMessageId,
        @Notes,
        COALESCE(@TransitionDatetime, SYSUTCDATETIME())
    );
END
GO

-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ReadEmailThreadStageHistoryByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;

    SELECT *
    FROM dbo.EmailThreadStageHistory
    WHERE PublicId = @PublicId;
END
GO

-- -----------------------------------------------------------------------------
-- Read the full stage history for a thread in chronological order.
-- Primary read for process audit trail and timeline display.
CREATE OR ALTER PROCEDURE dbo.ReadEmailThreadStageHistoryByThreadId
    @EmailThreadId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT *
    FROM dbo.EmailThreadStageHistory
    WHERE EmailThreadId = @EmailThreadId
    ORDER BY TransitionDatetime ASC;
END
GO

-- -----------------------------------------------------------------------------
-- Read the most recent stage transition for a thread.
-- Used by the process engine to confirm the current stage after an advance.
CREATE OR ALTER PROCEDURE dbo.ReadLatestEmailThreadStageHistory
    @EmailThreadId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 1 *
    FROM dbo.EmailThreadStageHistory
    WHERE EmailThreadId = @EmailThreadId
    ORDER BY TransitionDatetime DESC;
END
GO

-- -----------------------------------------------------------------------------
-- SLA reporting: find threads where the current stage has been held
-- longer than the specified number of hours. Used by the scheduler
-- to fire SLA_BREACH events.
CREATE OR ALTER PROCEDURE dbo.ReadEmailThreadsExceedingStageDuration
    @MaxHours INT
AS
BEGIN
    SET NOCOUNT ON;

    -- Latest transition per thread
    WITH LatestTransition AS (
        SELECT
            EmailThreadId,
            ToStage,
            TransitionDatetime,
            ROW_NUMBER() OVER (
                PARTITION BY EmailThreadId
                ORDER BY TransitionDatetime DESC
            ) AS RowNum
        FROM dbo.EmailThreadStageHistory
    )
    SELECT
        t.*,
        lt.ToStage          AS StalledAtStage,
        lt.TransitionDatetime AS StageEnteredDatetime,
        DATEDIFF(HOUR, lt.TransitionDatetime, SYSUTCDATETIME()) AS HoursInStage
    FROM dbo.EmailThread t
    INNER JOIN LatestTransition lt
        ON lt.EmailThreadId = t.Id
        AND lt.RowNum = 1
    WHERE t.IsResolved = 0
      AND DATEDIFF(HOUR, lt.TransitionDatetime, SYSUTCDATETIME()) > @MaxHours
    ORDER BY lt.TransitionDatetime ASC;
END
GO
