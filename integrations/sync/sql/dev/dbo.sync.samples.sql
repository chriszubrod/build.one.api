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

-- Watermark reset: forces the next sync of one Sync row to re-pull from scratch.
UPDATE dbo.[Sync]
SET [LastSyncDatetime] = '2026-01-01 00:00:00.000'
WHERE [Id] = 14 AND [RowVersion] = 0x000000000004A864;
GO
