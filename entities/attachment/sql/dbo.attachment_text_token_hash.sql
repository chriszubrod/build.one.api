-- Contract: dbo.AttachmentTextTokenHash
-- Owns: keyed hashed tokens for one attachment extraction (no plaintext).
-- Invariants: one row per AttachmentId; TokenizerVersion must match writer fingerprint.
-- Sprocs: UpsertAttachmentTextTokenHash, ReadAttachmentTextTokenHashesSince,
--   ReadAttachmentTextTokenHashesForAttachmentIds
--
-- Run: ./.venv/bin/python scripts/run_sql.py entities/attachment/sql/dbo.attachment_text_token_hash.sql

GO

IF OBJECT_ID('dbo.AttachmentTextTokenHash', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.[AttachmentTextTokenHash]
    (
        [AttachmentId]       BIGINT           NOT NULL,
        [TokenizerVersion]   NVARCHAR(32)     NOT NULL,
        [TokenHashes]        NVARCHAR(MAX)    NOT NULL,
        [TokenCount]         INT              NOT NULL,
        [CreatedDatetime]    DATETIME2(3)     NOT NULL,
        [ModifiedDatetime]   DATETIME2(3)     NOT NULL,
        CONSTRAINT [PK_AttachmentTextTokenHash] PRIMARY KEY ([AttachmentId])
    );
END
GO

IF OBJECT_ID('dbo.AttachmentTextTokenHash', 'U') IS NOT NULL
   AND OBJECT_ID('dbo.Attachment', 'U') IS NOT NULL
BEGIN
    DELETE h
    FROM dbo.[AttachmentTextTokenHash] h
    WHERE NOT EXISTS (SELECT 1 FROM dbo.[Attachment] a WHERE a.[Id] = h.[AttachmentId]);

    IF NOT EXISTS (
        SELECT 1 FROM sys.foreign_keys
        WHERE name = 'FK_AttachmentTextTokenHash_Attachment'
          AND parent_object_id = OBJECT_ID('dbo.AttachmentTextTokenHash')
    )
    BEGIN
        ALTER TABLE dbo.[AttachmentTextTokenHash]
            ADD CONSTRAINT [FK_AttachmentTextTokenHash_Attachment]
            FOREIGN KEY ([AttachmentId]) REFERENCES dbo.[Attachment] ([Id])
            ON DELETE CASCADE;
    END
END
GO

CREATE OR ALTER PROCEDURE UpsertAttachmentTextTokenHash
(
    @AttachmentId BIGINT,
    @TokenizerVersion NVARCHAR(32),
    @TokenHashes NVARCHAR(MAX),
    @TokenCount INT
)
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM dbo.[Attachment] WHERE [Id] = @AttachmentId)
    BEGIN
        SELECT TOP (0)
            CAST(NULL AS BIGINT) AS [AttachmentId],
            CAST(NULL AS NVARCHAR(32)) AS [TokenizerVersion],
            CAST(NULL AS NVARCHAR(MAX)) AS [TokenHashes],
            CAST(NULL AS INT) AS [TokenCount],
            CAST(NULL AS DATETIME2(3)) AS [CreatedDatetime],
            CAST(NULL AS DATETIME2(3)) AS [ModifiedDatetime];
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    MERGE dbo.[AttachmentTextTokenHash] WITH (HOLDLOCK) AS target
    USING (SELECT @AttachmentId AS AttachmentId) AS source
    ON target.[AttachmentId] = source.AttachmentId
    WHEN MATCHED THEN
        UPDATE SET
            [TokenizerVersion] = @TokenizerVersion,
            [TokenHashes] = @TokenHashes,
            [TokenCount] = @TokenCount,
            [ModifiedDatetime] = @Now
    WHEN NOT MATCHED THEN
        INSERT ([AttachmentId], [TokenizerVersion], [TokenHashes], [TokenCount], [CreatedDatetime], [ModifiedDatetime])
        VALUES (@AttachmentId, @TokenizerVersion, @TokenHashes, @TokenCount, @Now, @Now)
    OUTPUT
        INSERTED.[AttachmentId],
        INSERTED.[TokenizerVersion],
        INSERTED.[TokenHashes],
        INSERTED.[TokenCount],
        INSERTED.[CreatedDatetime],
        INSERTED.[ModifiedDatetime];
END;
GO

CREATE OR ALTER PROCEDURE ReadAttachmentTextTokenHashesSince
(
    @Since DATETIME2(3)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        h.[AttachmentId],
        h.[TokenizerVersion],
        h.[TokenHashes],
        h.[TokenCount],
        h.[ModifiedDatetime]
    FROM dbo.[AttachmentTextTokenHash] h
    WHERE h.[ModifiedDatetime] >= @Since
    ORDER BY h.[ModifiedDatetime], h.[AttachmentId];
END;
GO

CREATE OR ALTER PROCEDURE ReadAttachmentTextTokenHashesForAttachmentIds
(
    @AttachmentIds NVARCHAR(MAX),
    @TokenizerVersion NVARCHAR(32)
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        h.[AttachmentId],
        h.[TokenizerVersion],
        h.[TokenHashes],
        h.[TokenCount],
        h.[ModifiedDatetime]
    FROM dbo.[AttachmentTextTokenHash] h
    INNER JOIN (
        SELECT DISTINCT TRY_CAST(LTRIM(RTRIM(value)) AS BIGINT) AS [AttachmentId]
        FROM STRING_SPLIT(ISNULL(@AttachmentIds, ''), ',')
        WHERE LTRIM(RTRIM(value)) <> ''
    ) ids ON ids.[AttachmentId] = h.[AttachmentId]
    WHERE h.[TokenizerVersion] = @TokenizerVersion
    ORDER BY h.[AttachmentId];
END;
GO
