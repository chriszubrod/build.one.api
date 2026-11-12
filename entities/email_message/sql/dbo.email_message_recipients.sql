-- Additive migration: add ToRecipients + CcRecipients (JSON arrays) to
-- dbo.EmailMessage and extend UpsertEmailMessage to accept them.
--
-- The columns store JSON like:
--   [{"email": "...", "name": "..."}, ...]
-- Storing as NVARCHAR(MAX) (not the JSON type) for cross-version
-- portability; SQL Server's JSON_QUERY / OPENJSON still work over it
-- when we want to query.
--
-- Idempotent: the ADD COLUMN guards on sys.columns; the sproc is
-- CREATE OR ALTER.

GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'ToRecipients' AND object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage] ADD [ToRecipients] NVARCHAR(MAX) NULL;
END
GO

IF OBJECT_ID('dbo.EmailMessage', 'U') IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sys.columns
    WHERE Name = N'CcRecipients' AND object_id = OBJECT_ID('dbo.EmailMessage')
)
BEGIN
    ALTER TABLE dbo.[EmailMessage] ADD [CcRecipients] NVARCHAR(MAX) NULL;
END
GO

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-789, 2026-11-12) — body removed, NOT the recipient-column intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/email_message/sql/dbo.email_message.sql
--
-- This file's copy had fallen far behind prod: it selected ToRecipients/CcRecipients,
-- omitted Folder and the four AgentClassification* columns, and UpsertEmailMessage
-- matched on GraphMessageId instead of prod's (InternetMessageId, Folder) and lacked
-- @CreatedByUserId/@DefaultProcessingStatus. Applying this file would have reverted
-- four live email-intake sprocs. The ToRecipients/CcRecipients COLUMNS this migration
-- adds are already present in prod and its ADD guards above are kept; only the sproc
-- bodies are neutralised. The read shape is owned by the base file.
-- Re-running this file is now a no-op for UpsertEmailMessage. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- Update the read sprocs to include the new columns so existing API
-- consumers see them. The remaining sprocs (Update*Status,
-- ClaimNextPending, paginated read, count, delete) don't need touching —
-- they either don't return body data or already SELECT *.
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-789, 2026-11-12) — body removed, NOT the recipient-column intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/email_message/sql/dbo.email_message.sql
--
-- This file's copy had fallen far behind prod: it selected ToRecipients/CcRecipients,
-- omitted Folder and the four AgentClassification* columns, and UpsertEmailMessage
-- matched on GraphMessageId instead of prod's (InternetMessageId, Folder) and lacked
-- @CreatedByUserId/@DefaultProcessingStatus. Applying this file would have reverted
-- four live email-intake sprocs. The ToRecipients/CcRecipients COLUMNS this migration
-- adds are already present in prod and its ADD guards above are kept; only the sproc
-- bodies are neutralised. The read shape is owned by the base file.
-- Re-running this file is now a no-op for ReadEmailMessageById. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-789, 2026-11-12) — body removed, NOT the recipient-column intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/email_message/sql/dbo.email_message.sql
--
-- This file's copy had fallen far behind prod: it selected ToRecipients/CcRecipients,
-- omitted Folder and the four AgentClassification* columns, and UpsertEmailMessage
-- matched on GraphMessageId instead of prod's (InternetMessageId, Folder) and lacked
-- @CreatedByUserId/@DefaultProcessingStatus. Applying this file would have reverted
-- four live email-intake sprocs. The ToRecipients/CcRecipients COLUMNS this migration
-- adds are already present in prod and its ADD guards above are kept; only the sproc
-- bodies are neutralised. The read shape is owned by the base file.
-- Re-running this file is now a no-op for ReadEmailMessageByPublicId. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO

-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-789, 2026-11-12) — body removed, NOT the recipient-column intent.
--
-- Canonical definition now lives in exactly ONE place:
--   entities/email_message/sql/dbo.email_message.sql
--
-- This file's copy had fallen far behind prod: it selected ToRecipients/CcRecipients,
-- omitted Folder and the four AgentClassification* columns, and UpsertEmailMessage
-- matched on GraphMessageId instead of prod's (InternetMessageId, Folder) and lacked
-- @CreatedByUserId/@DefaultProcessingStatus. Applying this file would have reverted
-- four live email-intake sprocs. The ToRecipients/CcRecipients COLUMNS this migration
-- adds are already present in prod and its ADD guards above are kept; only the sproc
-- bodies are neutralised. The read shape is owned by the base file.
-- Re-running this file is now a no-op for ReadEmailMessageByGraphMessageId. Do NOT reintroduce a body here.
-- ---------------------------------------------------------------------------
GO
