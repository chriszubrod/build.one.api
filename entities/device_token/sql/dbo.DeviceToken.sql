-- =============================================================================
-- dbo.DeviceToken
-- Stores iOS device tokens registered by the mobile app for push notifications.
-- One row per device per user. Tokens are deactivated on logout rather than
-- deleted, preserving history for audit purposes.
--
-- Device tokens are unique globally (one token cannot belong to two users).
-- MERGE on DeviceToken value handles re-registration after app reinstall.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Table
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM sys.tables
    WHERE name = 'DeviceToken' AND schema_id = SCHEMA_ID('dbo')
)
BEGIN
    CREATE TABLE dbo.DeviceToken (
        Id                  BIGINT              NOT NULL IDENTITY(1,1),
        PublicId            UNIQUEIDENTIFIER    NOT NULL DEFAULT NEWID(),

        -- Owner
        UserId              BIGINT              NOT NULL,

        -- APNs device token — hex string, typically 64 chars
        DeviceToken         NVARCHAR(500)       NOT NULL,

        -- Platform — ios only for now; reserved for future android/web
        DeviceType          VARCHAR(20)         NOT NULL DEFAULT 'ios',

        -- App bundle ID — must match APNs topic
        AppBundleId         NVARCHAR(200)       NOT NULL,

        -- Lifecycle
        IsActive            BIT                 NOT NULL DEFAULT 1,
        LastSeenDatetime    DATETIME2(3)        NULL,

        -- Audit
        CreatedDatetime     DATETIME2(3)        NOT NULL DEFAULT SYSUTCDATETIME(),
        UpdatedDatetime     DATETIME2(3)        NOT NULL DEFAULT SYSUTCDATETIME(),
        RowVersion          ROWVERSION          NOT NULL,

        CONSTRAINT PK_DeviceToken
            PRIMARY KEY CLUSTERED (Id),

        CONSTRAINT UQ_DeviceToken_PublicId
            UNIQUE (PublicId),

        -- A device token is globally unique — it cannot belong to two users.
        -- If a new user logs in on the same device, MERGE updates the UserId.
        CONSTRAINT UQ_DeviceToken_Token
            UNIQUE (DeviceToken),

        CONSTRAINT FK_DeviceToken_User
            FOREIGN KEY (UserId)
            REFERENCES dbo.[User] (Id),

        CONSTRAINT CK_DeviceToken_DeviceType
            CHECK (DeviceType IN ('ios'))
    );
END
GO

-- -----------------------------------------------------------------------------
-- Indexes (all idempotent)
-- -----------------------------------------------------------------------------
IF NOT EXISTS (
    SELECT 1 FROM sys.indexes
    WHERE name = 'IX_DeviceToken_UserId_IsActive'
    AND object_id = OBJECT_ID('dbo.DeviceToken')
)
    CREATE INDEX IX_DeviceToken_UserId_IsActive
        ON dbo.DeviceToken (UserId, IsActive);
GO

-- -----------------------------------------------------------------------------
-- Stored Procedures
-- -----------------------------------------------------------------------------

-- Upsert a device token — create or update via MERGE on DeviceToken value.
-- Handles re-registration after app reinstall (same token, same user)
-- and device handoff (same token, new user after login).
CREATE OR ALTER PROCEDURE dbo.UpsertDeviceToken
    @PublicId       UNIQUEIDENTIFIER,
    @UserId         BIGINT,
    @DeviceToken    NVARCHAR(500),
    @DeviceType     VARCHAR(20)     = 'ios',
    @AppBundleId    NVARCHAR(200)
AS
BEGIN
    SET NOCOUNT ON;

    MERGE dbo.DeviceToken WITH (HOLDLOCK) AS target
    USING (SELECT @DeviceToken AS DeviceToken) AS source
        ON target.DeviceToken = source.DeviceToken

    WHEN MATCHED THEN
        UPDATE SET
            UserId              = @UserId,
            AppBundleId         = @AppBundleId,
            IsActive            = 1,
            LastSeenDatetime    = SYSUTCDATETIME(),
            UpdatedDatetime     = SYSUTCDATETIME()

    WHEN NOT MATCHED THEN
        INSERT (
            PublicId,
            UserId,
            DeviceToken,
            DeviceType,
            AppBundleId,
            IsActive,
            LastSeenDatetime
        )
        VALUES (
            @PublicId,
            @UserId,
            @DeviceToken,
            @DeviceType,
            @AppBundleId,
            1,
            SYSUTCDATETIME()
        );

    SELECT *
    FROM dbo.DeviceToken
    WHERE DeviceToken = @DeviceToken;
END
GO

-- -----------------------------------------------------------------------------
-- Read all active tokens for a user — used when sending push notifications.
CREATE OR ALTER PROCEDURE dbo.ReadActiveDeviceTokensByUserId
    @UserId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT *
    FROM dbo.DeviceToken
    WHERE UserId    = @UserId
      AND IsActive  = 1
    ORDER BY LastSeenDatetime DESC;
END
GO

-- -----------------------------------------------------------------------------
-- Deactivate a specific token on logout — preserves record for audit.
CREATE OR ALTER PROCEDURE dbo.DeactivateDeviceToken
    @DeviceToken NVARCHAR(500)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.DeviceToken
    SET
        IsActive        = 0,
        UpdatedDatetime = SYSUTCDATETIME()
    OUTPUT INSERTED.*
    WHERE DeviceToken = @DeviceToken;
END
GO

-- -----------------------------------------------------------------------------
-- Deactivate all tokens for a user — called on full account logout.
CREATE OR ALTER PROCEDURE dbo.DeactivateAllDeviceTokensByUserId
    @UserId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.DeviceToken
    SET
        IsActive        = 0,
        UpdatedDatetime = SYSUTCDATETIME()
    WHERE UserId    = @UserId
      AND IsActive  = 1;

    SELECT @@ROWCOUNT AS DeactivatedCount;
END
GO

-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ReadDeviceTokenByPublicId
    @PublicId UNIQUEIDENTIFIER
AS
BEGIN
    SET NOCOUNT ON;

    SELECT *
    FROM dbo.DeviceToken
    WHERE PublicId = @PublicId;
END
GO
