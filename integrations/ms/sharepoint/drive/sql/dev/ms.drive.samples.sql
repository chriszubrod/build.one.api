-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/ms/sharepoint/drive/sql/ms.drive.sql
-- Run manually in non-production environments.

EXEC ReadMsDrives;

DROP PROCEDURE IF EXISTS ReadMsDrivesByMsSiteId;
GO

EXEC DeleteMsDriveByPublicId

EXEC ReadMsDrives;
