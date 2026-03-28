-- =============================================================================
-- dbo.EmailThread
-- Tracks the lifecycle of an email conversation as a business process instance.
-- One thread groups all messages (inbound, forwarded, replies) for a single
-- business event. Linked to the originating InboxRecord that started the thread.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Table
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'EmailThread' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.EmailThread (
        Id                  BIGINT              NOT NULL IDENTITY(1,1),
        PublicId            UNIQUEIDENTIFIER    NOT NULL DEFAULT NEWID(),

        -- Link to the originating InboxRecord that started this thread
        InboxRecordId       BIGINT              NOT NULL,

        -- Classification output from the ProcessEngine / Email Agent
        Category            VARCHAR(100)        NOT NULL,   -- e.g. BILL_DOCUMENT, INQUIRY
        ProcessType         VARCHAR(100)        NOT NULL,   -- maps to process registry key

        -- Current position in the process stage machine
        CurrentStage        VARCHAR(100)        NOT NULL,

        -- Thread metadata extracted from email headers before classification
        IsReply             BIT                 NOT NULL DEFAULT 0,
        IsForward           BIT                 NOT NULL DEFAULT 0,

        -- Email threading headers (preserved for dedup and chain resolution)
        InternetMessageId   NVARCHAR(500)       NULL,       -- Message-ID header
        Subject             NVARCHAR(500)       NULL,

        -- Ownership
        OwnerUserId         BIGINT              NULL,       -- NULL = unassigned

        -- Confidence score from classification agent (0.00 – 1.00)
        ClassificationConfidence DECIMAL(5,4)   NULL,

        -- Flags
        IsResolved          BIT                 NOT NULL DEFAULT 0,
        RequiresAction      BIT                 NOT NULL DEFAULT 1,

        -- Audit
        CreatedDatetime     DATETIME2(3)        NOT NULL DEFAULT SYSUTCDATETIME(),
        UpdatedDatetime     DATETIME2(3)        NOT NULL DEFAULT SYSUTCDATETIME(),
        RowVersion          ROWVERSION          NOT NULL,

        CONSTRAINT PK_EmailThread
            PRIMARY KEY CLUSTERED (Id),

        CONSTRAINT UQ_EmailThread_PublicId
            UNIQUE (PublicId),

        CONSTRAINT FK_EmailThread_InboxRecord
            FOREIGN KEY (InboxRecordId)
            REFERENCES dbo.InboxRecord (Id),

        CONSTRAINT FK_EmailThread_User
            FOREIGN KEY (OwnerUserId)
            REFERENCES dbo.[User] (Id),

        CONSTRAINT CK_EmailThread_ClassificationConfidence
            CHECK (ClassificationConfidence IS NULL
                OR (ClassificationConfidence >= 0.0
                AND ClassificationConfidence <= 1.0))
    );
END
GO

-- -----------------------------------------------------------------------------
-- Indexes (all idempotent)
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThread_InboxRecordId'
    AND object_id = OBJECT_ID('dbo.EmailThread')
)
    CREATE INDEX IX_EmailThread_InboxRecordId
        ON dbo.EmailThread (InboxRecordId);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThread_OwnerUserId'
    AND object_id = OBJECT_ID('dbo.EmailThread')
)
    CREATE INDEX IX_EmailThread_OwnerUserId
        ON dbo.EmailThread (OwnerUserId);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThread_Category_CurrentStage'
    AND object_id = OBJECT_ID('dbo.EmailThread')
)
    CREATE INDEX IX_EmailThread_Category_CurrentStage
        ON dbo.EmailThread (Category, CurrentStage);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThread_IsResolved_RequiresAction'
    AND object_id = OBJECT_ID('dbo.EmailThread')
)
    CREATE INDEX IX_EmailThread_IsResolved_RequiresAction
        ON dbo.EmailThread (IsResolved, RequiresAction);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThread_InternetMessageId'
    AND object_id = OBJECT_ID('dbo.EmailThread')
)
    CREATE INDEX IX_EmailThread_InternetMessageId
        ON dbo.EmailThread (InternetMessageId)
        WHERE InternetMessageId IS NOT NULL;
GO

-- -----------------------------------------------------------------------------
-- Stored Procedures
-- -----------------------------------------------------------------------------

-- Upsert (MERGE) — create or update an EmailThread in a single call.
-- Matches on PublicId for updates. NULL inputs on nullable fields preserve
-- existing values via COALESCE guard.
CREATE OR ALTER PROCEDURE dbo.UpsertEmailThread
    @PublicId                   UNIQUEIDENTIFIER,
    @InboxRecordId              BIGINT,
    @Category                   VARCHAR(100),
    @ProcessType                VARCHAR(100),
    @CurrentStage               VARCHAR(100),
    @IsReply                    BIT,
    @IsForward                  BIT,
    @InternetMessageId          NVARCHAR(500)   = NULL,
    @Subject                    NVARCHAR(500)   = NULL,
    @OwnerUserId                BIGINT          = NULL,
    @ClassificationConfidence   DECIMAL(5,4)    = NULL,
    @IsResolved                 BIT             = NULL,
    @RequiresAction             BIT             = NULL
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dbo.EmailThread WITH (HOLDLOCK) AS target
    USING (SELECT @PublicId AS PublicId) AS source
        ON target.PublicId = source.PublicId

    WHEN MATCHED THEN
        UPDATE SET
            CurrentStage                = @CurrentStage,
            OwnerUserId                 = COALESCE(@OwnerUserId,               target.OwnerUserId),
            ClassificationConfidence    = COALESCE(@ClassificationConfidence,  target.ClassificationConfidence),
            InternetMessageId           = COALESCE(@InternetMessageId,         target.InternetMessageId),
            Subject                     = COALESCE(@Subject,                   target.Subject),
            IsResolved                  = COALESCE(@IsResolved,                target.IsResolved),
            RequiresAction              = COALESCE(@RequiresAction,            target.RequiresAction),
            UpdatedDatetime             = SYSUTCDATETIME()

    WHEN NOT MATCHED THEN
        INSERT (
            PublicId,
            InboxRecordId,
            Category,
            ProcessType,
            CurrentStage,
            IsReply,
            IsForward,
            InternetMessageId,
            Subject,
            OwnerUserId,
            ClassificationConfidence,
            IsResolved,
            RequiresAction
        )
        VALUES (
            @PublicId,
            @InboxRecordId,
            @Category,
            @ProcessType,
            @CurrentStage,
            @IsReply,
            @IsForward,
            @InternetMessageId,
            @Subject,
            @OwnerUserId,
            @ClassificationConfidence,
            COALESCE(@IsResolved, 0),
            COALESCE(@RequiresAction, 1)
        );

    SELECT *
    FROM dbo.EmailThread
    WHERE PublicId = @PublicId;
END
GO

-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ReadEmailThreadByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;

    SELECT *
    FROM dbo.EmailThread
    WHERE PublicId = @PublicId;
END
GO

-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ReadEmailThreadByInboxRecordId
    @InboxRecordId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT *
    FROM dbo.EmailThread
    WHERE InboxRecordId = @InboxRecordId;
END
GO

-- -----------------------------------------------------------------------------
-- Resolve a thread by its Internet Message-ID header value.
-- Used during ingest to detect if an incoming email belongs to an
-- existing thread before creating a new one.
CREATE OR ALTER PROCEDURE dbo.ReadEmailThreadByInternetMessageId
    @InternetMessageId NVARCHAR(500)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT *
    FROM dbo.EmailThread
    WHERE InternetMessageId = @InternetMessageId;
END
GO

-- -----------------------------------------------------------------------------
-- Read all open threads requiring action, optionally filtered by owner.
CREATE OR ALTER PROCEDURE dbo.ReadEmailThreadsRequiringAction
    @OwnerUserId BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    SELECT *
    FROM dbo.EmailThread
    WHERE RequiresAction = 1
      AND IsResolved = 0
      AND (@OwnerUserId IS NULL OR OwnerUserId = @OwnerUserId)
    ORDER BY CreatedDatetime ASC;
END
GO
