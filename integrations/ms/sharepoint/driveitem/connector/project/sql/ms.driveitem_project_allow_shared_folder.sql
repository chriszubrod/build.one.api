GO

-- Allow the same SharePoint folder (DriveItem) to be linked to multiple projects.
-- Previously UQ_DriveItemProject_MsDriveItemId prevented this.
IF EXISTS (
    SELECT 1 FROM sys.key_constraints
    WHERE name = 'UQ_DriveItemProject_MsDriveItemId'
      AND parent_object_id = OBJECT_ID('ms.DriveItemProject')
)
BEGIN
    ALTER TABLE [ms].[DriveItemProject]
    DROP CONSTRAINT [UQ_DriveItemProject_MsDriveItemId];
END
GO
