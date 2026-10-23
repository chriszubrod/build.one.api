-- Contract: dbo.AttachmentTextFingerprint
-- Owns: keyed document-text fingerprint for one attachment (no plaintext).
-- Invariants: one row per AttachmentId; Version must match writer fingerprint version.
-- Sprocs: UpsertAttachmentTextFingerprint, ReadAttachmentTextFingerprintByAttachmentId
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/attachment/sql/dbo.attachment_text_fingerprint.sql

GO

IF OBJECT_ID('dbo.AttachmentTextFingerprint', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[AttachmentTextFingerprint]
    (
        [AttachmentId]       BIGINT           NOT NULL,
        [Version]            NVARCHAR(16)     NOT NULL,
        [Fingerprint]        CHAR(64)         NOT NULL,
        [CreatedDatetime]    DATETIME2(3)     NOT NULL CONSTRAINT [DF_AttachmentTextFingerprint_CreatedDatetime] DEFAULT (SYSUTCDATETIME()),
        CONSTRAINT [PK_AttachmentTextFingerprint] PRIMARY KEY ([AttachmentId])
    );
END
GO

IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_AttachmentTextFingerprint_Fingerprint_Version'
      AND object_id = OBJECT_ID('dbo.AttachmentTextFingerprint')
)
BEGIN
    CREATE INDEX [IX_AttachmentTextFingerprint_Fingerprint_Version]
        ON dbo.[AttachmentTextFingerprint] ([Fingerprint], [Version]);
END
GO

IF OBJECT_ID('dbo.AttachmentTextFingerprint', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.Attachment', 'U') IS NOT NULL
BEGIN
    DELETE h
    FROM dbo.[AttachmentTextFingerprint] h
    WHERE NOT EXISTS (SELECT 1 FROM dbo.[Attachment] a WHERE a.[Id] = h.[AttachmentId]);

    IF NOT EXISTS (
        SELECT 1 FROM sys.foreign_keys
        WHERE name = 'FK_AttachmentTextFingerprint_Attachment'
          AND parent_object_id = OBJECT_ID('dbo.AttachmentTextFingerprint')
    )
    BEGIN
        ALTER TABLE dbo.[AttachmentTextFingerprint]
            ADD CONSTRAINT [FK_AttachmentTextFingerprint_Attachment]
            FOREIGN KEY ([AttachmentId]) REFERENCES dbo.[Attachment] ([Id])
            ON DELETE CASCADE;
    END
END
GO

CREATE OR ALTER PROCEDURE UpsertAttachmentTextFingerprint
(
    @AttachmentId BIGINT,
    @Version NVARCHAR(16),
    @Fingerprint CHAR(64)
)
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM dbo.[Attachment] WHERE [Id] = @AttachmentId)
    BEGIN
        SELECT TOP (0)
            CAST(NULL AS BIGINT) AS [AttachmentId],
            CAST(NULL AS NVARCHAR(16)) AS [Version],
            CAST(NULL AS CHAR(64)) AS [Fingerprint],
            CAST(NULL AS DATETIME2(3)) AS [CreatedDatetime];
        RETURN;
    END

    MERGE dbo.[AttachmentTextFingerprint] WITH (HOLDLOCK) AS target
    USING (SELECT @AttachmentId AS AttachmentId) AS source
    ON target.[AttachmentId] = source.AttachmentId
    WHEN MATCHED THEN
        UPDATE SET
            [Version] = @Version,
            [Fingerprint] = @Fingerprint
    WHEN NOT MATCHED THEN
        INSERT ([AttachmentId], [Version], [Fingerprint])
        VALUES (@AttachmentId, @Version, @Fingerprint)
    OUTPUT
        INSERTED.[AttachmentId],
        INSERTED.[Version],
        INSERTED.[Fingerprint],
        INSERTED.[CreatedDatetime];
END;
GO

CREATE OR ALTER PROCEDURE ReadAttachmentTextFingerprintByAttachmentId
(
    @AttachmentId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        [AttachmentId],
        [Version],
        [Fingerprint],
        [CreatedDatetime]
    FROM dbo.[AttachmentTextFingerprint]
    WHERE [AttachmentId] = @AttachmentId;
END;
GO
