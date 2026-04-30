-- Phase 1 — Access Control Rebuild
-- Re-issue User Create/Update/Delete sprocs to thread CreatedByUserId
-- and ModifiedByUserId. Read sprocs already SELECT them (Phase 0 002).
-- New params default to NULL.
-- Idempotent (CREATE OR ALTER).

CREATE OR ALTER PROCEDURE CreateUser
(
    @Firstname NVARCHAR(50),
    @Lastname NVARCHAR(255),
    @CreatedByUserId BIGINT = NULL,
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[User]
        ([CreatedDatetime], [ModifiedDatetime], [Firstname], [Lastname],
         [CreatedByUserId], [ModifiedByUserId])
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Firstname],
        INSERTED.[Lastname],
        INSERTED.[IsSystemAdmin],
        INSERTED.[IsAgent],
        INSERTED.[LastCompanyId],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    VALUES
        (@Now, @Now, @Firstname, @Lastname,
         @CreatedByUserId, COALESCE(@ModifiedByUserId, @CreatedByUserId));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE UpdateUserById
(
    @Id BIGINT,
    @RowVersion BINARY(8),
    @Firstname NVARCHAR(50),
    @Lastname NVARCHAR(255),
    @ModifiedByUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    UPDATE dbo.[User]
    SET
        [ModifiedDatetime] = @Now,
        [Firstname] = @Firstname,
        [Lastname] = @Lastname,
        [ModifiedByUserId] = @ModifiedByUserId
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[Firstname],
        INSERTED.[Lastname],
        INSERTED.[IsSystemAdmin],
        INSERTED.[IsAgent],
        INSERTED.[LastCompanyId],
        INSERTED.[CreatedByUserId],
        INSERTED.[ModifiedByUserId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteUserById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    DELETE FROM dbo.[User]
    OUTPUT
        DELETED.[Id],
        DELETED.[PublicId],
        DELETED.[RowVersion],
        CONVERT(VARCHAR(19), DELETED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), DELETED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        DELETED.[Firstname],
        DELETED.[Lastname],
        DELETED.[IsSystemAdmin],
        DELETED.[IsAgent],
        DELETED.[LastCompanyId],
        DELETED.[CreatedByUserId],
        DELETED.[ModifiedByUserId]
    WHERE [Id] = @Id;

    COMMIT TRANSACTION;
END;
GO
