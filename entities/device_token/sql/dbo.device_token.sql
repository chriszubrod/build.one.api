IF OBJECT_ID('dbo.DeviceToken', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[DeviceToken]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [DeactivatedDatetime] DATETIME2(3) NULL,
    [UserId] BIGINT NOT NULL,
    [Token] NVARCHAR(255) NOT NULL,
    [AppBundleId] NVARCHAR(255) NOT NULL,
    [Platform] NVARCHAR(20) NOT NULL DEFAULT 'ios',
    [IsActive] BIT NOT NULL DEFAULT 1
);
END
GO

-- One row per physical device install (Apple guarantees device tokens are
-- unique per app bundle). Re-registration UPDATEs the existing row;
-- different users on the same device will overwrite UserId.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_DeviceToken_Token_AppBundle' AND object_id = OBJECT_ID('dbo.DeviceToken'))
BEGIN
    CREATE UNIQUE INDEX UX_DeviceToken_Token_AppBundle
        ON dbo.[DeviceToken] ([Token], [AppBundleId]);
END
GO

-- Used by the future push sender (and any "list my devices" admin tooling).
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_DeviceToken_UserId_Active' AND object_id = OBJECT_ID('dbo.DeviceToken'))
BEGIN
    CREATE INDEX IX_DeviceToken_UserId_Active
        ON dbo.[DeviceToken] ([UserId], [IsActive])
        INCLUDE ([Token], [AppBundleId], [Platform]);
END
GO


CREATE OR ALTER PROCEDURE RegisterDeviceToken
(
    @UserId BIGINT,
    @Token NVARCHAR(255),
    @AppBundleId NVARCHAR(255),
    @Platform NVARCHAR(20) = 'ios'
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();
    DECLARE @ExistingPublicId UNIQUEIDENTIFIER;

    SELECT @ExistingPublicId = [PublicId]
    FROM dbo.[DeviceToken]
    WHERE [Token] = @Token AND [AppBundleId] = @AppBundleId;

    IF @ExistingPublicId IS NULL
    BEGIN
        INSERT INTO dbo.[DeviceToken] (
            [CreatedDatetime], [ModifiedDatetime], [UserId],
            [Token], [AppBundleId], [Platform], [IsActive]
        )
        VALUES (@Now, @Now, @UserId, @Token, @AppBundleId, @Platform, 1);
    END
    ELSE
    BEGIN
        UPDATE dbo.[DeviceToken]
        SET
            [UserId] = @UserId,
            [Platform] = @Platform,
            [ModifiedDatetime] = @Now,
            [DeactivatedDatetime] = NULL,
            [IsActive] = 1
        WHERE [PublicId] = @ExistingPublicId;
    END

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        CONVERT(VARCHAR(19), [DeactivatedDatetime], 120) AS [DeactivatedDatetime],
        [UserId],
        [Token],
        [AppBundleId],
        [Platform],
        [IsActive]
    FROM dbo.[DeviceToken]
    WHERE [Token] = @Token AND [AppBundleId] = @AppBundleId;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeactivateDeviceToken
(
    @UserId BIGINT,
    @Token NVARCHAR(255)
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- Scoped by UserId so user A can't deactivate user B's tokens. If no
    -- row matches (e.g. token already gone, wrong user), the OUTPUT yields
    -- zero rows and the caller sees a None result.
    UPDATE dbo.[DeviceToken]
    SET
        [IsActive] = 0,
        [DeactivatedDatetime] = @Now,
        [ModifiedDatetime] = @Now
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[DeactivatedDatetime], 120) AS [DeactivatedDatetime],
        INSERTED.[UserId],
        INSERTED.[Token],
        INSERTED.[AppBundleId],
        INSERTED.[Platform],
        INSERTED.[IsActive]
    WHERE [UserId] = @UserId AND [Token] = @Token;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadActiveDeviceTokensByUserId
(
    @UserId BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        CONVERT(VARCHAR(19), [DeactivatedDatetime], 120) AS [DeactivatedDatetime],
        [UserId],
        [Token],
        [AppBundleId],
        [Platform],
        [IsActive]
    FROM dbo.[DeviceToken]
    WHERE [UserId] = @UserId AND [IsActive] = 1
    ORDER BY [Id] ASC;

    COMMIT TRANSACTION;
END;
GO
