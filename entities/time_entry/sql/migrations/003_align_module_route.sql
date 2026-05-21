-- Align the Time Tracking Module.Route to the React convention
-- `/{entity}/list` so the auto-sidebar lands on TimeEntryList.tsx.
-- Was: '/time-entries' (REST-style, mismatch).
-- Idempotent — re-running is a no-op if already aligned.
UPDATE dbo.[Module]
   SET Route             = '/time-entry/list',
       ModifiedDatetime  = SYSUTCDATETIME()
 WHERE Name = 'Time Tracking'
   AND Route <> '/time-entry/list';
