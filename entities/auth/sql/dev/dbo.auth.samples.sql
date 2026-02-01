-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/auth/sql/dbo.auth.sql
-- Run manually in non-production environments.

DELETE FROM dbo.[Auth];

SELECT * FROM dbo.[Auth];
GO

EXEC ReadAuths;
GO

EXEC ReadAuthByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadAuthByUsername
    @Username = 'zubrodcb';
GO

EXEC UpdateAuthById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = '00000000-0000-0000-0000-000000000000',
    @Username = 'sample-user',
    @PasswordHash = 'sample-password-hash';
GO

EXEC DeleteAuthById
    @Id = 3;
GO
