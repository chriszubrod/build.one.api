-- =============================================================================
-- MS Message Tables and Stored Procedures
-- For storing linked email messages (on-demand, not full sync)
-- =============================================================================

-- =============================================================================
-- Tables
-- =============================================================================

-- Linked email messages (stored when explicitly linked to a record)
IF OBJECT_ID('dbo.MsMessage', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.MsMessage (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        RowVersion ROWVERSION NOT NULL,
        CreatedDatetime DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        ModifiedDatetime DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        
        -- Graph API identifiers
        MessageId NVARCHAR(500) NOT NULL,              -- Graph message ID
        ConversationId NVARCHAR(500) NULL,             -- Thread grouping
        InternetMessageId NVARCHAR(500) NULL,          -- RFC 2822 message ID
        
        -- Message metadata
        Subject NVARCHAR(1000) NULL,
        FromEmail NVARCHAR(320) NOT NULL,              -- Max email length per RFC
        FromName NVARCHAR(255) NULL,
        ReceivedDatetime DATETIME2 NULL,
        SentDatetime DATETIME2 NULL,
        
        -- Body content
        BodyContentType NVARCHAR(10) NOT NULL DEFAULT 'HTML',  -- HTML or Text
        Body NVARCHAR(MAX) NULL,
        BodyPreview NVARCHAR(500) NULL,
        
        -- Flags
        IsRead BIT NOT NULL DEFAULT 0,
        HasAttachments BIT NOT NULL DEFAULT 0,
        Importance NVARCHAR(10) NOT NULL DEFAULT 'normal',  -- low, normal, high
        
        -- Web link for opening in Outlook
        WebLink NVARCHAR(2000) NULL,
        
        -- Constraints
        CONSTRAINT UQ_MsMessage_PublicId UNIQUE (PublicId),
        CONSTRAINT UQ_MsMessage_MessageId UNIQUE (MessageId)
    );
    
    IF OBJECT_ID('dbo.MsMessage', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessage_MessageId' AND object_id = OBJECT_ID('dbo.MsMessage'))
    BEGIN
    CREATE INDEX IX_MsMessage_MessageId ON dbo.MsMessage(MessageId);
    END
    IF OBJECT_ID('dbo.MsMessage', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessage_ConversationId' AND object_id = OBJECT_ID('dbo.MsMessage'))
    BEGIN
    CREATE INDEX IX_MsMessage_ConversationId ON dbo.MsMessage(ConversationId);
    END
    IF OBJECT_ID('dbo.MsMessage', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessage_FromEmail' AND object_id = OBJECT_ID('dbo.MsMessage'))
    BEGIN
    CREATE INDEX IX_MsMessage_FromEmail ON dbo.MsMessage(FromEmail);
    END
    IF OBJECT_ID('dbo.MsMessage', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessage_ReceivedDatetime' AND object_id = OBJECT_ID('dbo.MsMessage'))
    BEGIN
    CREATE INDEX IX_MsMessage_ReceivedDatetime ON dbo.MsMessage(ReceivedDatetime DESC);
    END
END
GO

-- Message recipients (To, CC, BCC stored separately for querying)
IF OBJECT_ID('dbo.MsMessageRecipient', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.MsMessageRecipient (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        MsMessageId INT NOT NULL,
        RecipientType NVARCHAR(3) NOT NULL,  -- TO, CC, BCC
        Email NVARCHAR(320) NOT NULL,
        Name NVARCHAR(255) NULL,
        
        CONSTRAINT FK_MsMessageRecipient_MsMessage 
            FOREIGN KEY (MsMessageId) REFERENCES dbo.MsMessage(Id) ON DELETE CASCADE,
        CONSTRAINT CK_MsMessageRecipient_Type 
            CHECK (RecipientType IN ('TO', 'CC', 'BCC'))
    );
    
    IF OBJECT_ID('dbo.MsMessageRecipient', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessageRecipient_MsMessageId' AND object_id = OBJECT_ID('dbo.MsMessageRecipient'))
    BEGIN
    CREATE INDEX IX_MsMessageRecipient_MsMessageId ON dbo.MsMessageRecipient(MsMessageId);
    END
    IF OBJECT_ID('dbo.MsMessageRecipient', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessageRecipient_Email' AND object_id = OBJECT_ID('dbo.MsMessageRecipient'))
    BEGIN
    CREATE INDEX IX_MsMessageRecipient_Email ON dbo.MsMessageRecipient(Email);
    END
END
GO

-- Message attachments (metadata only, content stored in Azure Blob)
IF OBJECT_ID('dbo.MsMessageAttachment', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.MsMessageAttachment (
        Id INT IDENTITY(1,1) PRIMARY KEY,
        PublicId UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        RowVersion ROWVERSION NOT NULL,
        CreatedDatetime DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        ModifiedDatetime DATETIME2 NOT NULL DEFAULT GETUTCDATE(),
        
        MsMessageId INT NOT NULL,
        AttachmentId NVARCHAR(500) NOT NULL,           -- Graph attachment ID
        
        -- Attachment metadata
        Name NVARCHAR(500) NOT NULL,
        ContentType NVARCHAR(255) NOT NULL,
        Size INT NULL,
        IsInline BIT NOT NULL DEFAULT 0,
        
        -- Azure Blob storage reference
        BlobUrl NVARCHAR(2000) NULL,
        BlobContainer NVARCHAR(255) NULL,
        BlobName NVARCHAR(500) NULL,
        
        CONSTRAINT FK_MsMessageAttachment_MsMessage 
            FOREIGN KEY (MsMessageId) REFERENCES dbo.MsMessage(Id) ON DELETE CASCADE,
        CONSTRAINT UQ_MsMessageAttachment_PublicId UNIQUE (PublicId)
    );
    
    IF OBJECT_ID('dbo.MsMessageAttachment', 'U') IS NOT NULL AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_MsMessageAttachment_MsMessageId' AND object_id = OBJECT_ID('dbo.MsMessageAttachment'))
    BEGIN
    CREATE INDEX IX_MsMessageAttachment_MsMessageId ON dbo.MsMessageAttachment(MsMessageId);
    END
END
GO


-- =============================================================================
-- Stored Procedures - MsMessage
-- =============================================================================

-- Create a new linked message
CREATE OR ALTER PROCEDURE dbo.CreateMsMessage
    @MessageId NVARCHAR(500),
    @ConversationId NVARCHAR(500) = NULL,
    @InternetMessageId NVARCHAR(500) = NULL,
    @Subject NVARCHAR(1000) = NULL,
    @FromEmail NVARCHAR(320),
    @FromName NVARCHAR(255) = NULL,
    @ReceivedDatetime DATETIME2 = NULL,
    @SentDatetime DATETIME2 = NULL,
    @BodyContentType NVARCHAR(10) = 'HTML',
    @Body NVARCHAR(MAX) = NULL,
    @BodyPreview NVARCHAR(500) = NULL,
    @IsRead BIT = 0,
    @HasAttachments BIT = 0,
    @Importance NVARCHAR(10) = 'normal',
    @WebLink NVARCHAR(2000) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    INSERT INTO dbo.MsMessage (
        MessageId, ConversationId, InternetMessageId,
        Subject, FromEmail, FromName,
        ReceivedDatetime, SentDatetime,
        BodyContentType, Body, BodyPreview,
        IsRead, HasAttachments, Importance, WebLink
    )
    VALUES (
        @MessageId, @ConversationId, @InternetMessageId,
        @Subject, @FromEmail, @FromName,
        @ReceivedDatetime, @SentDatetime,
        @BodyContentType, @Body, @BodyPreview,
        @IsRead, @HasAttachments, @Importance, @WebLink
    );
    
    SELECT * FROM dbo.MsMessage WHERE Id = SCOPE_IDENTITY();
END
GO

-- Read all linked messages
CREATE OR ALTER PROCEDURE dbo.ReadMsMessages
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessage
    ORDER BY ReceivedDatetime DESC;
END
GO

-- Read message by public ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessage WHERE PublicId = @PublicId;
END
GO

-- Read message by Graph message ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageByMessageId
    @MessageId NVARCHAR(500)
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessage WHERE MessageId = @MessageId;
END
GO

-- Read messages by conversation ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessagesByConversationId
    @ConversationId NVARCHAR(500)
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessage 
    WHERE ConversationId = @ConversationId
    ORDER BY ReceivedDatetime ASC;
END
GO

-- Read messages by sender email
CREATE OR ALTER PROCEDURE dbo.ReadMsMessagesByFromEmail
    @FromEmail NVARCHAR(320)
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessage 
    WHERE FromEmail = @FromEmail
    ORDER BY ReceivedDatetime DESC;
END
GO

-- Update message by public ID
CREATE OR ALTER PROCEDURE dbo.UpdateMsMessageByPublicId
    @PublicId UNIQUEIDENTIFIER,
    @Subject NVARCHAR(1000) = NULL,
    @BodyContentType NVARCHAR(10) = 'HTML',
    @Body NVARCHAR(MAX) = NULL,
    @BodyPreview NVARCHAR(500) = NULL,
    @IsRead BIT = 0,
    @HasAttachments BIT = 0,
    @Importance NVARCHAR(10) = 'normal'
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE dbo.MsMessage
    SET Subject = @Subject,
        BodyContentType = @BodyContentType,
        Body = @Body,
        BodyPreview = @BodyPreview,
        IsRead = @IsRead,
        HasAttachments = @HasAttachments,
        Importance = @Importance,
        ModifiedDatetime = GETUTCDATE()
    WHERE PublicId = @PublicId;
    
    SELECT * FROM dbo.MsMessage WHERE PublicId = @PublicId;
END
GO

-- Delete message by public ID
CREATE OR ALTER PROCEDURE dbo.DeleteMsMessageByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @DeletedMessage TABLE (
        Id INT, PublicId UNIQUEIDENTIFIER, RowVersion VARBINARY(8),
        CreatedDatetime DATETIME2, ModifiedDatetime DATETIME2,
        MessageId NVARCHAR(500), ConversationId NVARCHAR(500),
        InternetMessageId NVARCHAR(500), Subject NVARCHAR(1000),
        FromEmail NVARCHAR(320), FromName NVARCHAR(255),
        ReceivedDatetime DATETIME2, SentDatetime DATETIME2,
        BodyContentType NVARCHAR(10), Body NVARCHAR(MAX),
        BodyPreview NVARCHAR(500), IsRead BIT, HasAttachments BIT,
        Importance NVARCHAR(10), WebLink NVARCHAR(2000)
    );
    
    DELETE FROM dbo.MsMessage
    OUTPUT DELETED.*
    INTO @DeletedMessage
    WHERE PublicId = @PublicId;
    
    SELECT * FROM @DeletedMessage;
END
GO


-- =============================================================================
-- Stored Procedures - MsMessageRecipient
-- =============================================================================

-- Add recipient to message
CREATE OR ALTER PROCEDURE dbo.CreateMsMessageRecipient
    @MsMessageId INT,
    @RecipientType NVARCHAR(3),
    @Email NVARCHAR(320),
    @Name NVARCHAR(255) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    INSERT INTO dbo.MsMessageRecipient (MsMessageId, RecipientType, Email, Name)
    VALUES (@MsMessageId, @RecipientType, @Email, @Name);
    
    SELECT * FROM dbo.MsMessageRecipient WHERE Id = SCOPE_IDENTITY();
END
GO

-- Read recipients by message ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageRecipientsByMsMessageId
    @MsMessageId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageRecipient 
    WHERE MsMessageId = @MsMessageId
    ORDER BY RecipientType, Email;
END
GO

-- Delete recipients by message ID
CREATE OR ALTER PROCEDURE dbo.DeleteMsMessageRecipientsByMsMessageId
    @MsMessageId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    DELETE FROM dbo.MsMessageRecipient WHERE MsMessageId = @MsMessageId;
END
GO


-- =============================================================================
-- Stored Procedures - MsMessageAttachment
-- =============================================================================

-- Create attachment record
CREATE OR ALTER PROCEDURE dbo.CreateMsMessageAttachment
    @MsMessageId INT,
    @AttachmentId NVARCHAR(500),
    @Name NVARCHAR(500),
    @ContentType NVARCHAR(255),
    @Size INT = NULL,
    @IsInline BIT = 0,
    @BlobUrl NVARCHAR(2000) = NULL,
    @BlobContainer NVARCHAR(255) = NULL,
    @BlobName NVARCHAR(500) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    
    INSERT INTO dbo.MsMessageAttachment (
        MsMessageId, AttachmentId, Name, ContentType,
        Size, IsInline, BlobUrl, BlobContainer, BlobName
    )
    VALUES (
        @MsMessageId, @AttachmentId, @Name, @ContentType,
        @Size, @IsInline, @BlobUrl, @BlobContainer, @BlobName
    );
    
    SELECT * FROM dbo.MsMessageAttachment WHERE Id = SCOPE_IDENTITY();
END
GO

-- Read attachments by message ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageAttachmentsByMsMessageId
    @MsMessageId INT
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageAttachment 
    WHERE MsMessageId = @MsMessageId
    ORDER BY Name;
END
GO

-- Read attachment by public ID
CREATE OR ALTER PROCEDURE dbo.ReadMsMessageAttachmentByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    SELECT * FROM dbo.MsMessageAttachment WHERE PublicId = @PublicId;
END
GO

-- Update attachment blob info
CREATE OR ALTER PROCEDURE dbo.UpdateMsMessageAttachmentBlob
    @PublicId UNIQUEIDENTIFIER,
    @BlobUrl NVARCHAR(2000),
    @BlobContainer NVARCHAR(255),
    @BlobName NVARCHAR(500)
AS
BEGIN
    SET NOCOUNT ON;
    
    UPDATE dbo.MsMessageAttachment
    SET BlobUrl = @BlobUrl,
        BlobContainer = @BlobContainer,
        BlobName = @BlobName,
        ModifiedDatetime = GETUTCDATE()
    WHERE PublicId = @PublicId;
    
    SELECT * FROM dbo.MsMessageAttachment WHERE PublicId = @PublicId;
END
GO

-- Delete attachment by public ID
CREATE OR ALTER PROCEDURE dbo.DeleteMsMessageAttachmentByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;
    
    DECLARE @DeletedAttachment TABLE (
        Id INT, PublicId UNIQUEIDENTIFIER, RowVersion VARBINARY(8),
        CreatedDatetime DATETIME2, ModifiedDatetime DATETIME2,
        MsMessageId INT, AttachmentId NVARCHAR(500),
        Name NVARCHAR(500), ContentType NVARCHAR(255),
        Size INT, IsInline BIT,
        BlobUrl NVARCHAR(2000), BlobContainer NVARCHAR(255), BlobName NVARCHAR(500)
    );
    
    DELETE FROM dbo.MsMessageAttachment
    OUTPUT DELETED.*
    INTO @DeletedAttachment
    WHERE PublicId = @PublicId;
    
    SELECT * FROM @DeletedAttachment;
END
GO
