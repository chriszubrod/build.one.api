-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/sync/sql/dbo.sync.sql
-- Run manually in non-production environments.

SELECT * FROM dbo.[Sync];

EXEC CreateSync
    @Provider = 'qbo',
    @Env = 'production',
    @Entity = 'vendor',
    @LastSyncDatetime = NULL;
GO

EXEC ReadSyncs;
GO

EXEC ReadSyncById
    @Id = 1;
GO

EXEC ReadSyncByPublicId
    @PublicId = 'c86edd93-a99c-424b-afa3-8df26f7de144';
GO

EXEC ReadSyncByProvider
    @Provider = 'qbo';
GO

EXEC UpdateSyncById
    @Id = 5,
    @RowVersion = 0x0000000000020B85,
    @Provider = 'qbo',
    @Env = 'production',
    @Entity = 'vendor',
    @LastSyncDatetime = NULL;
GO

EXEC DeleteSyncById
    @Id = 8;
GO
