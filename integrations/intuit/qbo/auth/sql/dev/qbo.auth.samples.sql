-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/intuit/qbo/auth/sql/qbo.auth.sql
-- Run manually in non-production environments.

EXEC CreateQboAuth
    @Code = '1234567890',
    @RealmId = '1234567890',
    @State = '1234567890',
    @TokenType = '1234567890',
    @IdToken = '1234567890',
    @AccessToken = '1234567890',
    @ExpiresIn = 1234567890,
    @RefreshToken = '1234567890',
    @XRefreshTokenExpiresIn = 1234567890;


DROP PROCEDURE IF EXISTS ReadQboAuths;
GO

EXEC ReadQboAuths;


DROP PROCEDURE IF EXISTS ReadQboAuthById;
GO

EXEC ReadQboAuthById
    @Id = 1;
GO

EXEC ReadQboAuthByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadQboAuthByRealmId @RealmId = '1234567890';


DROP PROCEDURE IF EXISTS UpdateQboAuthByRealmId;
GO

EXEC UpdateQboAuthByRealmId
    @Code = '1234567890',
    @RealmId = '1234567890',
    @State = '1234567890',
    @TokenType = '1234567890',
    @IdToken = '1234567890',
    @AccessToken = '1234567890',
    @ExpiresIn = 1234567890,
    @RefreshToken = '1234567890',
    @XRefreshTokenExpiresIn = 1234567890;


DROP PROCEDURE IF EXISTS DeleteQboAuthByRealmId;
GO

EXEC DeleteQboAuthByRealmId
