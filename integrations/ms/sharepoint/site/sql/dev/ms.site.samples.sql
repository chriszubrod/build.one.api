-- DEV/TEST ONLY: sample calls removed from production migration.
-- Source: integrations/ms/sharepoint/site/sql/ms.site.sql
-- Run manually in non-production environments.

EXEC ReadMsSites;


DROP PROCEDURE IF EXISTS ReadMsSiteById;
GO

EXEC DeleteMsSiteByPublicId

EXEC ReadMsSites;
