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
--   2. Creates FindContractLaborForReviewerReply that:
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
-- Idempotent: IF NOT EXISTS table guard; CREATE OR ALTER sproc; the
-- backfill uses IF NOT EXISTS per-row so re-running is safe.
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
CREATE OR ALTER PROCEDURE FindContractLaborForReviewerReply
(
    @ConversationId NVARCHAR(255),
    @WorkerHint NVARCHAR(255) = NULL,
    @ProjectHint NVARCHAR(255) = NULL,
    @WorkDateHint NVARCHAR(20) = NULL
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @MatchKind NVARCHAR(20) = NULL;
    DECLARE @CLId BIGINT = NULL;
    DECLARE @ProjectId BIGINT = NULL;

    -- ── PRIMARY: JOIN outbound EmailMessage → ContractLaborNotification ─
    -- Single-result-or-null: COUNT(*)=1 gate via a table variable, same
    -- shape as BillRepository's mirror at entities/bill/sql/dbo.
    -- bill_create_source_email.sql (FindBillForReviewerReply).
    DECLARE @PrimaryHits TABLE (CLId BIGINT, ProjectId BIGINT);

    INSERT INTO @PrimaryHits (CLId, ProjectId)
    SELECT DISTINCT TOP 2
        cln.[ContractLaborId],
        cln.[ProjectId]
    FROM dbo.[EmailMessage] em
    INNER JOIN dbo.[ContractLaborNotification] cln
        ON cln.[OutboundSubject] = em.[Subject]
    WHERE em.[ConversationId]   = @ConversationId
      AND em.[ProcessingStatus] = 'outbound'
      AND em.[Subject] LIKE 'Contract Labor - %';

    IF (SELECT COUNT(*) FROM @PrimaryHits) = 1
    BEGIN
        SELECT TOP 1
            @CLId      = CLId,
            @ProjectId = ProjectId
        FROM @PrimaryHits;
        SET @MatchKind = 'conversation';
    END

    -- ── FUZZY: caller-supplied hints (non-Outlook conv-id loss) ────
    -- Triggered only when PRIMARY missed. Requires all three hints.
    -- Same JOIN-through-line-items shape as the Bill fuzzy fallback.
    -- TRY_CAST guards @WorkDateHint regardless of session DATEFORMAT.
    IF @MatchKind IS NULL
       AND @WorkerHint IS NOT NULL
       AND @ProjectHint IS NOT NULL
       AND @WorkDateHint IS NOT NULL
    BEGIN
        DECLARE @WorkDate DATE = TRY_CAST(@WorkDateHint AS DATE);
        IF @WorkDate IS NOT NULL
        BEGIN
            DECLARE @FuzzyHits TABLE (CLId BIGINT, ProjectId BIGINT);

            INSERT INTO @FuzzyHits (CLId, ProjectId)
            SELECT DISTINCT TOP 2 cl.[Id], p.[Id]
            FROM dbo.[ContractLabor] cl
            INNER JOIN dbo.[ContractLaborLineItem] cli ON cli.[ContractLaborId] = cl.[Id]
            INNER JOIN dbo.[Project] p ON p.[Id] = cli.[ProjectId]
            WHERE cl.[EmployeeName] = @WorkerHint
              AND cl.[WorkDate]     = @WorkDate
              AND p.[Abbreviation]  = @ProjectHint;

            IF (SELECT COUNT(*) FROM @FuzzyHits) = 1
            BEGIN
                SELECT TOP 1
                    @CLId      = CLId,
                    @ProjectId = ProjectId
                FROM @FuzzyHits;
                SET @MatchKind = 'fuzzy';
            END
        END
    END

    -- ── Hydrate + return ──────────────────────────────────────────
    -- Single SELECT joining CL + Project for the final shape. NO Status
    -- filter (Unit 3's apply path enforces).
    SELECT
        cl.[Id]                                         AS ContractLaborId,
        CAST(cl.[PublicId] AS NVARCHAR(36))             AS ContractLaborPublicId,
        p.[Id]                                          AS ProjectId,
        CAST(p.[PublicId] AS NVARCHAR(36))              AS ProjectPublicId,
        p.[Abbreviation]                                AS ProjectAbbreviation,
        p.[Name]                                        AS ProjectName,
        cl.[EmployeeName]                               AS ParsedWorker,
        CONVERT(VARCHAR(10), cl.[WorkDate], 120)        AS ParsedWorkDate,
        cl.[Status]                                     AS ContractLaborStatus,
        @MatchKind                                      AS MatchKind
    FROM dbo.[ContractLabor] cl
    INNER JOIN dbo.[Project] p ON p.[Id] = @ProjectId
    WHERE cl.[Id] = @CLId AND @CLId IS NOT NULL;
END;
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
