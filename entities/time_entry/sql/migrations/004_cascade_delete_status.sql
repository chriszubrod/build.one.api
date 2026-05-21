-- Drop + recreate FK_TimeEntryStatus_TimeEntry with ON DELETE CASCADE so
-- deleting a TimeEntry also wipes its status history.
--
-- Without this, every TimeEntry delete fails because the row carries at
-- least its initial 'draft' status row (created in TimeEntryService.create).
-- The SQL error reads "DELETE statement conflicted with the REFERENCE
-- constraint", which shared/database.py::map_database_error misclassifies
-- as a "Concurrency violation" because of its keyword match on 'conflict'.
--
-- Mirrors the existing FK_TimeLog_TimeEntry CASCADE behavior, so deleting
-- a TimeEntry cleans up TimeLog + TimeEntryStatus in one operation.
--
-- Idempotent — only acts if the existing constraint lacks CASCADE.

IF EXISTS (
    SELECT 1
      FROM sys.foreign_keys
     WHERE name = 'FK_TimeEntryStatus_TimeEntry'
       AND delete_referential_action <> 1  -- 1 = CASCADE
)
BEGIN
    ALTER TABLE dbo.[TimeEntryStatus]
        DROP CONSTRAINT FK_TimeEntryStatus_TimeEntry;

    ALTER TABLE dbo.[TimeEntryStatus]
        ADD CONSTRAINT FK_TimeEntryStatus_TimeEntry
            FOREIGN KEY ([TimeEntryId])
            REFERENCES dbo.[TimeEntry]([Id])
            ON DELETE CASCADE;
END;
GO
