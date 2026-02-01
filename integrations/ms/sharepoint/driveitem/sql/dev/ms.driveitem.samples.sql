-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/ms/sharepoint/driveitem/sql/ms.driveitem.sql
-- Run manually in non-production environments.

EXEC DeleteMsDriveItemByPublicId

EXEC ReadMsDriveItems;
