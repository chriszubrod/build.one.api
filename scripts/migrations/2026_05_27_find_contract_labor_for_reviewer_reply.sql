-- 2026-05-27 (REDO per /simplify) — ContractLaborNotification join table
-- + FindContractLaborForReviewerReply sproc.
--
-- /simplify caught the first Unit 2 design as a transport-layer bandaid:
-- subject-marker [REF:cl_id/project_id] in cl_notification_service is
-- fragile to forwarding-chain truncation, human reviewers stripping
-- the marker, line-item re-assignment desynchronizing, etc. The right
-- altitude is a join table — the same pattern Bill uses (Bill.
-- SourceEmailMessageId 1:1 FK), generalized to the CL 1:many fan-out
-- where one CL produces one notification per distinct project on its
-- line items.
--
-- This migration:
--   1. Creates dbo.ContractLaborNotification (one row per outbound
--      draft enqueued; carries (CL_id, Project_id, OutboundSubject) so
--      the lookup can JOIN without any subject parsing).
--   2. Creates FindContractLaborForReviewerReply — SUPERSEDED (U-162);
--      the body now lives only in entities/contract_labor/sql/
--      dbo.contract_labor.sql. Described here for lineage:
--        PRIMARY  - JOIN EmailMessage (by ConversationId, outbound, CL-
--                   subject prefix) → ContractLaborNotification (by
--                   exact OutboundSubject) → ContractLabor + Project.
--                   Deterministic. No parsing.
--        FUZZY    - explicit (worker / project_abbr / work_date) hints
--                   for non-Outlook clients that lose ConversationId.
--                   Mirrors the Bill pattern.
--      Status filter NOT applied (Unit 3 enforces with a specific
--      error so the agent can produce useful human-readable failure).
--   3. Backfills ContractLaborNotification rows for existing outbound
--      CL notifications using the legacy subject parse. ONE-TIME — the
--      parse logic doesn't ship to production code.
--
-- cl_notification_service.py (entities/review/business/) is updated in
-- the same commit to INSERT a join row at enqueue time AND drop the
-- [REF:] marker from the outbound subject.
--
-- Idempotent: IF NOT EXISTS table guard; the backfill uses IF NOT EXISTS
-- per-row so re-running is safe. (The CREATE OR ALTER sproc that used to
-- live here is superseded by U-162 — see the stub below.)
GO


-- ─── Table ──────────────────────────────────────────────────────────
IF OBJECT_ID('dbo.ContractLaborNotification', 'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[ContractLaborNotification]
    (
        [Id]               BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
        [PublicId]         UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
        [CreatedDatetime]  DATETIME2(3) NOT NULL DEFAULT SYSUTCDATETIME(),
        [ContractLaborId]  BIGINT NOT NULL,
        [ProjectId]        BIGINT NOT NULL,
        -- The exact subject of the outbound notification draft. Acts
        -- as the deterministic join key between this row and the BCC
        -- inbox copy that arrives after the MS outbox drains. Match
        -- is exact-equality (NOT parsing) so worker / project names
        -- containing ' - ' or NULL Abbreviation fallback all work.
        [OutboundSubject]  NVARCHAR(500) NOT NULL,

        CONSTRAINT [FK_CLN_ContractLabor] FOREIGN KEY ([ContractLaborId])
            REFERENCES [dbo].[ContractLabor] ([Id]),
        CONSTRAINT [FK_CLN_Project] FOREIGN KEY ([ProjectId])
            REFERENCES [dbo].[Project] ([Id])
    );

    -- Lookup uses subject as the JOIN key against EmailMessage.Subject
    -- so the index makes that join O(log n) instead of a full scan.
    CREATE INDEX [IX_CLN_OutboundSubject]
        ON [dbo].[ContractLaborNotification] ([OutboundSubject]);
    CREATE INDEX [IX_CLN_ContractLaborId]
        ON [dbo].[ContractLaborNotification] ([ContractLaborId]);

    PRINT '  dbo.ContractLaborNotification created';
END
ELSE
    PRINT '  dbo.ContractLaborNotification already exists';
GO


-- ─── Sproc: FindContractLaborForReviewerReply ───────────────────────
-- ---------------------------------------------------------------------------
-- SUPERSEDED (U-162, 2026-07-28) — body removed, NOT the intent.
--
-- The canonical definition of this sproc now lives in exactly ONE place:
--   entities/contract_labor/sql/dbo.contract_labor.sql
--
-- Sprocs formerly defined here (now canonical in the base file):
--   dbo.FindContractLaborForReviewerReply
--
-- Drift: comment-only — the T-SQL was identical on both sides; the base adopted
-- this copy's explanatory comment blocks so nothing was lost.
--
-- NOTE: only the sproc is superseded. The dbo.ContractLaborNotification table
-- DDL above and the one-time backfill below are still live in this file.
--
-- Re-running this file is now a no-op for this sproc. Do NOT reintroduce a
-- body here — a copy that drifts from the base file is what caused the
-- 2026-07-15 outage (SQL 8144, cross-user payroll exposure risk).
-- ---------------------------------------------------------------------------
GO


-- ─── One-time backfill of existing outbound notifications ──────────
-- Scans EmailMessage for outbound CL notifications and reconstructs
-- the (CL_id, Project_id, OutboundSubject) join rows via the legacy
-- subject parse. ONE-TIME — the parse logic doesn't ship to runtime.
--
-- Per-row IF NOT EXISTS guard so re-running the migration is safe.
DECLARE @BackfillInserted INT = 0;

WITH outbound_cl AS (
    SELECT
        em.[Id]      AS EmailMessageId,
        em.[Subject] AS [Subject]
    FROM dbo.[EmailMessage] em
    WHERE em.[ProcessingStatus] = 'outbound'
      AND em.[Subject] LIKE 'Contract Labor - %'
),
parsed AS (
    SELECT
        EmailMessageId,
        [Subject],
        -- Body after the "Contract Labor - " prefix (17 chars)
        SUBSTRING([Subject], 18, LEN([Subject])) AS Body
    FROM outbound_cl
),
split1 AS (
    SELECT
        EmailMessageId, [Subject], Body,
        CHARINDEX(' - ', Body) AS P1
    FROM parsed
),
split2 AS (
    SELECT
        EmailMessageId, [Subject],
        LTRIM(RTRIM(SUBSTRING(Body, 1, P1 - 1))) AS Worker,
        SUBSTRING(Body, P1 + 3, LEN(Body)) AS Rest1
    FROM split1
    WHERE P1 > 0
),
split3 AS (
    SELECT
        EmailMessageId, [Subject], Worker, Rest1,
        CHARINDEX(' - ', Rest1) AS P2
    FROM split2
),
split4 AS (
    SELECT
        EmailMessageId, [Subject], Worker,
        LTRIM(RTRIM(SUBSTRING(Rest1, 1, P2 - 1))) AS ProjectAbbr,
        LTRIM(RTRIM(SUBSTRING(Rest1, P2 + 3, 10))) AS DateStr
    FROM split3
    WHERE P2 > 0
),
parsed_with_date AS (
    SELECT
        EmailMessageId, [Subject], Worker, ProjectAbbr,
        TRY_CAST(DateStr AS DATE) AS WorkDate
    FROM split4
),
joined AS (
    SELECT DISTINCT
        pwd.[Subject],
        cl.[Id]  AS ContractLaborId,
        p.[Id]   AS ProjectId
    FROM parsed_with_date pwd
    INNER JOIN dbo.[ContractLabor] cl
        ON cl.[EmployeeName] = pwd.Worker
       AND cl.[WorkDate]     = pwd.WorkDate
    INNER JOIN dbo.[ContractLaborLineItem] cli ON cli.[ContractLaborId] = cl.[Id]
    INNER JOIN dbo.[Project] p ON p.[Id] = cli.[ProjectId]
    WHERE pwd.WorkDate IS NOT NULL
      AND pwd.ProjectAbbr IS NOT NULL
      AND pwd.Worker IS NOT NULL
      AND p.[Abbreviation] = pwd.ProjectAbbr
)
INSERT INTO dbo.[ContractLaborNotification] ([ContractLaborId], [ProjectId], [OutboundSubject])
SELECT j.ContractLaborId, j.ProjectId, j.[Subject]
FROM joined j
WHERE NOT EXISTS (
    SELECT 1 FROM dbo.[ContractLaborNotification] existing
    WHERE existing.[ContractLaborId] = j.ContractLaborId
      AND existing.[ProjectId]       = j.ProjectId
      AND existing.[OutboundSubject] = j.[Subject]
);

SET @BackfillInserted = @@ROWCOUNT;
PRINT CONCAT('  backfilled ', @BackfillInserted, ' ContractLaborNotification rows from existing outbound EmailMessages');
GO
