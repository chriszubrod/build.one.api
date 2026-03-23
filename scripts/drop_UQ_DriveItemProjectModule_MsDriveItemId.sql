IF EXISTS (
    SELECT 1 FROM sys.key_constraints
    WHERE name = 'UQ_DriveItemProjectModule_MsDriveItemId'
      AND parent_object_id = OBJECT_ID('ms.DriveItemProjectModule')
)
    ALTER TABLE [ms].[DriveItemProjectModule]
    DROP CONSTRAINT [UQ_DriveItemProjectModule_MsDriveItemId];
GO
