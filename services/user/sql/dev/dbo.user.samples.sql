-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: services/user/sql/dbo.user.sql
-- Run manually in non-production environments.

DELETE FROM dbo.[User];

SELECT * FROM dbo.[User];
GO

EXEC CreateUser
    @Firstname = 'John',
    @Lastname = 'Doe';
GO

EXEC ReadUsers;
GO

EXEC ReadUserById
    @Id = 1;
GO

EXEC ReadUserByPublicId
    @PublicId = '00000000-0000-0000-0000-000000000000';
GO

EXEC ReadUserByFirstname
    @Firstname = 'John';
GO

EXEC ReadUserByLastname
    @Lastname = 'Doe';
GO

EXEC UpdateUserById
    @Id = '00000000-0000-0000-0000-000000000000',
    @RowVersion = 0x0000000000000000,
    @Firstname = 'John',
    @Lastname = 'Doe';
GO

EXEC DeleteUserById
    @Id = '056f4b7a-5bac-f011-8e61-7c1e52165f92';
GO
