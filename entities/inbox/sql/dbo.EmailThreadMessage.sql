-- =============================================================================
-- dbo.EmailThreadMessage
-- Records each individual message within an EmailThread, in order.
-- Tracks the role of the sender and whether the message was a reply
-- or a forward, giving the process engine full thread position awareness.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Table
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'EmailThreadMessage' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.EmailThreadMessage (
        Id                  BIGINT              NOT NULL IDENTITY(1,1),
        PublicId            UNIQUEIDENTIFIER    NOT NULL DEFAULT NEWID(),

        -- Parent thread
        EmailThreadId       BIGINT              NOT NULL,

        -- The InboxRecord for this specific message
        InboxRecordId       BIGINT              NOT NULL,

        -- Role of the sender in this conversation
        -- ORIGINATOR  = sent the first inbound email
        -- REVIEWER    = the party the email was forwarded to for review
        -- RESPONDER   = replied with review details or findings
        -- OWNER       = the Build.One user managing the thread
        -- SYSTEM      = automated pipeline action
        SenderRole          VARCHAR(50)         NOT NULL,

        -- Position of this message within the thread (1-based)
        MessagePosition     INT                 NOT NULL,

        -- Header-derived flags (populated before classification)
        IsReply             BIT                 NOT NULL DEFAULT 0,
        IsForward           BIT                 NOT NULL DEFAULT 0,

        -- Classification result for this specific message
        Classification      VARCHAR(100)        NULL,
        ClassificationConfidence DECIMAL(5,4)   NULL,

        -- Datetime the message was received at the mail server
        ReceivedDatetime    DATETIME2(3)        NULL,

        -- Audit
        CreatedDatetime     DATETIME2(3)        NOT NULL DEFAULT SYSUTCDATETIME(),
        UpdatedDatetime     DATETIME2(3)        NOT NULL DEFAULT SYSUTCDATETIME(),
        RowVersion          ROWVERSION          NOT NULL,

        CONSTRAINT PK_EmailThreadMessage
            PRIMARY KEY CLUSTERED (Id),

        CONSTRAINT UQ_EmailThreadMessage_PublicId
            UNIQUE (PublicId),

        CONSTRAINT UQ_EmailThreadMessage_Thread_Position
            UNIQUE (EmailThreadId, MessagePosition),

        CONSTRAINT FK_EmailThreadMessage_EmailThread
            FOREIGN KEY (EmailThreadId)
            REFERENCES dbo.EmailThread (Id),

        CONSTRAINT FK_EmailThreadMessage_InboxRecord
            FOREIGN KEY (InboxRecordId)
            REFERENCES dbo.InboxRecord (Id),

        CONSTRAINT CK_EmailThreadMessage_SenderRole CHECK (
            SenderRole IN ('ORIGINATOR', 'REVIEWER', 'RESPONDER', 'OWNER', 'SYSTEM')
        ),

        CONSTRAINT CK_EmailThreadMessage_MessagePosition
            CHECK (MessagePosition >= 1),

        CONSTRAINT CK_EmailThreadMessage_ClassificationConfidence
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
    WHERE name = 'IX_EmailThreadMessage_EmailThreadId'
    AND object_id = OBJECT_ID('dbo.EmailThreadMessage')
)
    CREATE INDEX IX_EmailThreadMessage_EmailThreadId
        ON dbo.EmailThreadMessage (EmailThreadId, MessagePosition ASC);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThreadMessage_InboxRecordId'
    AND object_id = OBJECT_ID('dbo.EmailThreadMessage')
)
    CREATE INDEX IX_EmailThreadMessage_InboxRecordId
        ON dbo.EmailThreadMessage (InboxRecordId);
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_EmailThreadMessage_SenderRole'
    AND object_id = OBJECT_ID('dbo.EmailThreadMessage')
)
    CREATE INDEX IX_EmailThreadMessage_SenderRole
        ON dbo.EmailThreadMessage (EmailThreadId, SenderRole);
GO

-- -----------------------------------------------------------------------------
-- Stored Procedures
-- -----------------------------------------------------------------------------

-- Create a new message record within an existing thread.
-- Returns the inserted row via OUTPUT INSERTED.*.
CREATE OR ALTER PROCEDURE dbo.CreateEmailThreadMessage
    @PublicId                   UNIQUEIDENTIFIER,
    @EmailThreadId              BIGINT,
    @InboxRecordId              BIGINT,
    @SenderRole                 VARCHAR(50),
    @MessagePosition            INT,
    @IsReply                    BIT             = 0,
    @IsForward                  BIT             = 0,
    @Classification             VARCHAR(100)    = NULL,
    @ClassificationConfidence   DECIMAL(5,4)    = NULL,
    @ReceivedDatetime           DATETIME2(3)    = NULL
AS
BEGIN
    SET NOCOUNT ON;

    INSERT INTO dbo.EmailThreadMessage (
        PublicId,
        EmailThreadId,
        InboxRecordId,
        SenderRole,
        MessagePosition,
        IsReply,
        IsForward,
        Classification,
        ClassificationConfidence,
        ReceivedDatetime
    )
    OUTPUT INSERTED.*
    VALUES (
        @PublicId,
        @EmailThreadId,
        @InboxRecordId,
        @SenderRole,
        @MessagePosition,
        @IsReply,
        @IsForward,
        @Classification,
        @ClassificationConfidence,
        @ReceivedDatetime
    );
END
GO

-- -----------------------------------------------------------------------------
-- Update classification result after the agent processes the message.
CREATE OR ALTER PROCEDURE dbo.UpdateEmailThreadMessageClassification
    @PublicId                   UNIQUEIDENTIFIER,
    @Classification             VARCHAR(100),
    @ClassificationConfidence   DECIMAL(5,4)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.EmailThreadMessage
    SET
        Classification              = @Classification,
        ClassificationConfidence    = @ClassificationConfidence,
        UpdatedDatetime             = SYSUTCDATETIME()
    OUTPUT INSERTED.*
    WHERE PublicId = @PublicId;
END
GO

-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ReadEmailThreadMessageByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;

    SELECT *
    FROM dbo.EmailThreadMessage
    WHERE PublicId = @PublicId;
END
GO

-- -----------------------------------------------------------------------------
-- Read all messages in a thread ordered by position ascending.
CREATE OR ALTER PROCEDURE dbo.ReadEmailThreadMessagesByThreadId
    @EmailThreadId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT *
    FROM dbo.EmailThreadMessage
    WHERE EmailThreadId = @EmailThreadId
    ORDER BY MessagePosition ASC;
END
GO

-- -----------------------------------------------------------------------------
-- Read the most recent message in a thread.
-- Used by the process engine to determine current thread position.
CREATE OR ALTER PROCEDURE dbo.ReadLatestEmailThreadMessage
    @EmailThreadId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT TOP 1 *
    FROM dbo.EmailThreadMessage
    WHERE EmailThreadId = @EmailThreadId
    ORDER BY MessagePosition DESC;
END
GO
