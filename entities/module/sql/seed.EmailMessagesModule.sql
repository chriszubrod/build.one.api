-- Add the Email Messages module so the email-agent role can carry
-- narrow grants on it (read+update for the agent; nothing else).
-- The Route is hypothetical for v1 — the React UI surface for emails
-- isn't built yet — but a path lets us add it later without
-- migrating data.
--
-- Idempotent: only inserts if missing.

IF NOT EXISTS (SELECT 1 FROM dbo.[Module] WHERE [Name] = 'Email Messages')
BEGIN
    INSERT INTO dbo.[Module] ([CreatedDatetime], [ModifiedDatetime], [Name], [Route])
    VALUES (SYSUTCDATETIME(), SYSUTCDATETIME(), 'Email Messages', '/email/list');
END
GO
