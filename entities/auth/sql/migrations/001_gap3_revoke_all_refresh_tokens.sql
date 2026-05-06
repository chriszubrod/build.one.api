-- Gap 3 — Credential lifecycle.
-- Adds RevokeAllAuthRefreshTokensByAuthId sproc — bulk-revokes every
-- non-revoked refresh token for a given auth_id. Used by both:
--   - User self-service password change (AuthService.change_password)
--   - Admin password reset (AuthService.set_credentials_for_user)
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

CREATE OR ALTER PROCEDURE dbo.RevokeAllAuthRefreshTokensByAuthId
(
    @AuthId BIGINT,
    @RevokedDatetime DATETIME2(3)
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    UPDATE dbo.AuthRefreshToken
    SET
        [RevokedDatetime] = @RevokedDatetime
    OUTPUT
        INSERTED.[Id]
    WHERE [AuthId] = @AuthId
      AND [RevokedDatetime] IS NULL;

    COMMIT TRANSACTION;
END;
GO

PRINT 'RevokeAllAuthRefreshTokensByAuthId installed.';
