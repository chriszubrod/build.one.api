-- =============================================================================
-- Task Inbox sprocs — reads the cross-entity reviewer worklist.
--
-- Canonical single-source home (U-126) — read by
-- entities/review/persistence/inbox_repo.py. Change this file and apply it;
-- do not redefine these sprocs in migrations. Bodies are the LIVE prod
-- definitions captured via sys.sql_modules (2026-07-23).
--
-- A "task" today is a Review row whose latest state (per parent) is NOT final
-- and NOT declined — i.e. an item still awaiting reviewer action. The four
-- parent types (Bill, Expense, BillCredit, Invoice) are merged via UNION ALL
-- into one uniform row shape so the caller can render a single inbox without
-- branching on entity type.
--
-- Scoping:
--   * scope='mine'           => current user has a UserProject row with Role
--                               'Project Manager' or 'Owner' on a project
--                               this entity's line items touch (Invoice uses
--                               its direct ProjectId).
--   * scope='all'            => current user has ANY UserProject access to a
--                               project this entity touches. The existing Gap 1
--                               access-control layer already enforces this for
--                               list endpoints, so we mirror that shape here —
--                               nobody sees rows they can't already access.
--   * scope='mine_submitted' => current user SUBMITTED this document -- i.e. is
--                               the actor on its most recent review row at the
--                               INITIAL status (U-453). It used to mean "wrote
--                               the latest review row of any kind", which read
--                               as the submitter only while the system's
--                               auto-advance row carried the submitter's id.
--                               For "sent box" surfaces.
--
-- System admin (@IsSystemAdmin=1) bypasses all scope filtering — they see
-- everything pending regardless of UserProject membership.
--
-- assigned_to_me is always computed independently of scope so the client can
-- badge rows when paging through the wider 'all' queue.
--
-- Latest-review-per-parent picked via ROW_NUMBER() OVER (PARTITION BY parent
-- ORDER BY CreatedDatetime DESC, Id DESC) — Id DESC closes the same-tick
-- nondeterminism gap.
-- =============================================================================


-- =============================================================================
-- ReadInboxTasks — paged list of pending review tasks for one user
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.ReadInboxTasks
(
    @CurrentUserId    BIGINT,
    @IsSystemAdmin    BIT              = 0,
    @Scope            NVARCHAR(32)     = N'mine',     -- 'mine' | 'all' | 'mine_submitted'
    @EntityType       NVARCHAR(32)     = NULL,        -- NULL=all; 'Bill'|'Expense'|'BillCredit'|'Invoice'
    @StatusPublicId   UNIQUEIDENTIFIER = NULL,
    @Page             INT              = 0,
    @PageSize         INT              = 50
)
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @StatusId BIGINT = NULL;
    IF @StatusPublicId IS NOT NULL
        SELECT @StatusId = [Id] FROM dbo.[ReviewStatus] WHERE [PublicId] = @StatusPublicId;

    ;WITH Keyed AS (
    -- U-453. ONE partition key, defined ONCE, and the submitter resolved from
    -- the latest INITIAL review row rather than from whatever row happens to be
    -- newest.
    --
    -- Why: `Pending` is `LatestReview WHERE rn = 1`, and it used to alias that
    -- row's [UserId] as [SubmitterId] -- so "who submitted this" was really
    -- "who touched it last". That was accidentally correct only while the
    -- system's auto-advance row carried the submitter's id. U-453 re-points
    -- that row at the system actor (a bill moved into review by the pipeline
    -- was NOT moved by its submitter), which makes the old shape wrong: all 33
    -- in_review bills would have dropped out of their submitter's
    -- `mine_submitted` scope and rendered "Claude Agent" as the submitter.
    --
    -- The ContractLabor branch is new. Without it every CL review row keyed to
    -- NULL, so all 652 of them shared ONE partition and exactly one row
    -- survived `rn = 1`. Harmless today -- the Rows/Tagged arms below have no
    -- ContractLabor arm, so CL never surfaces either way -- but it is a trap
    -- armed for whoever adds one, and this is the only place it can be fixed.
        SELECT
            r.[Id], r.[PublicId],
            r.[BillId], r.[ExpenseId], r.[BillCreditId], r.[InvoiceId],
            r.[ReviewStatusId], r.[StatusName], r.[StatusColor],
            r.[StatusSortOrder], r.[StatusIsFinal], r.[StatusIsDeclined],
            r.[StatusIsInitial], r.[ReviewKind],
            r.[UserId], r.[UserFirstname], r.[UserLastname],
            r.[CreatedDatetime],
            CASE
                WHEN r.[BillId]          IS NOT NULL THEN CONCAT(N'B', r.[BillId])
                WHEN r.[ExpenseId]       IS NOT NULL THEN CONCAT(N'E', r.[ExpenseId])
                WHEN r.[BillCreditId]    IS NOT NULL THEN CONCAT(N'C', r.[BillCreditId])
                WHEN r.[InvoiceId]       IS NOT NULL THEN CONCAT(N'I', r.[InvoiceId])
                WHEN r.[ContractLaborId] IS NOT NULL THEN CONCAT(N'L', r.[ContractLaborId])
            END AS [ParentKey]
        FROM dbo.[vw_Review] r
    ),
    LatestReview AS (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY [ParentKey]
                ORDER BY [CreatedDatetime] DESC, [Id] DESC
            ) AS rn
        FROM Keyed
    ),
    Submitter AS (
        -- The most recent row at the INITIAL status. "Most recent" matters
        -- because a declined document is edited in place and resubmitted: the
        -- live submission is the last one, and its author is who is waiting on
        -- an answer.
        --
        -- Keys on [ReviewKind], FROZEN at insert (U-455) -- not on the
        -- status's live [IsInitial] flag.
        --
        -- That flag is current configuration: `UpdateReviewStatus` clears it
        -- from every other status when the initial role is transferred, so
        -- moving it silently orphaned every historical submission here. Sent-box
        -- rows vanished and [MineSubmitted] decremented, with nothing to notice.
        -- U-453 shipped that exposure knowingly and booked it; this is the fix.
        --
        -- The frozen value cannot be un-made by reconfiguring statuses, which is
        -- the whole point: a submission that happened, happened.
        SELECT
            [ParentKey], [UserId], [UserFirstname], [UserLastname],
            ROW_NUMBER() OVER (
                PARTITION BY [ParentKey]
                ORDER BY [CreatedDatetime] DESC, [Id] DESC
            ) AS rn
        FROM Keyed
        WHERE [ReviewKind] = N'submitted'
    ),
    Pending AS (
        -- Columns are projected EXPLICITLY, and the latest row's own actor
        -- ([UserId]/[UserFirstname]/[UserLastname]) is deliberately NOT among
        -- them. Carrying it alongside [SubmitterId] would leave the wrong
        -- column one autocomplete away from re-introducing the exact bug this
        -- unit fixes -- and it is now usually the system actor, so the mistake
        -- would look like "submitted by Claude Agent" rather than like an
        -- error. Nothing downstream needs it; add it back deliberately if
        -- something ever does.
        --
        -- LEFT JOIN, not INNER. Since U-455 the submitter comes from the
        -- FROZEN [ReviewKind], so re-flagging statuses can no longer orphan a
        -- submission -- that was the whole point. What remains reachable is a
        -- document whose review rows were never submissions at all (rows written
        -- directly at an intermediate status). Such a document keeps its place
        -- in the `mine` and `all` scopes with a NULL submitter, instead of
        -- disappearing from the inbox entirely. NOTE it does drop out of
        -- `mine_submitted` for everyone,
        -- since NULL never equals @CurrentUserId; that is the honest answer
        -- (nobody's submission is on file) and it is strictly better than the
        -- row vanishing from every scope.
        SELECT
            L.[Id], L.[PublicId],
            L.[BillId], L.[ExpenseId], L.[BillCreditId], L.[InvoiceId],
            L.[ReviewStatusId], L.[StatusName], L.[StatusColor],
            L.[StatusSortOrder], L.[StatusIsFinal], L.[StatusIsDeclined],
            L.[CreatedDatetime],
            S.[UserId]        AS [SubmitterId],
            S.[UserFirstname] AS [SubmitterFirstname],
            S.[UserLastname]  AS [SubmitterLastname]
        FROM LatestReview L
        LEFT JOIN Submitter S
            ON S.[ParentKey] = L.[ParentKey] AND S.rn = 1
        WHERE L.rn = 1
          -- ⚠ These two are the status's LIVE flags, deliberately NOT the
          -- frozen [ReviewKind] (U-455, Codex P1). "Is this review still
          -- pending" is a question about the workflow as configured NOW: if an
          -- admin makes a status non-final, reviews resting there arguably
          -- SHOULD requeue. Freezing them would mean a reconfigured workflow
          -- never reaches its own documents.
          --
          -- That is a product decision, not an oversight, and it is BOOKED
          -- rather than settled here -- so the "history must not be current
          -- config" rule this unit establishes applies to a row's KIND, and
          -- not (yet) to whether it is pending.
          AND L.[StatusIsFinal]    = 0
          AND L.[StatusIsDeclined] = 0
          AND (@StatusId IS NULL OR L.[ReviewStatusId] = @StatusId)
    ),
    Rows AS (
        -- =================================================================
        -- Bill
        -- =================================================================
        SELECT
            N'Bill'                   AS [EntityType],
            CAST(0 AS BIT)            AS [IsCredit],
            B.[PublicId]              AS [ParentPublicId],
            B.[Id]                    AS [ParentId],
            B.[BillNumber]            AS [ParentNumber],
            V.[Name]                  AS [CounterpartyName],
            B.[TotalAmount]           AS [Amount],
            P.[Id]                    AS [ReviewId],
            P.[PublicId]              AS [ReviewPublicId],
            P.[ReviewStatusId]        AS [ReviewStatusId],
            P.[StatusName]            AS [StatusName],
            P.[StatusColor]           AS [StatusColor],
            P.[StatusSortOrder]       AS [StatusSortOrder],
            P.[StatusIsFinal]         AS [StatusIsFinal],
            P.[StatusIsDeclined]      AS [StatusIsDeclined],
            P.[SubmitterId],
            P.[SubmitterFirstname],
            P.[SubmitterLastname],
            P.[CreatedDatetime]       AS [LastActivityAt],
            CASE WHEN EXISTS (
                SELECT 1
                FROM dbo.[BillLineItem] BLI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = BLI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                INNER JOIN dbo.[Role] R         ON R.[Id]         = UP.[RoleId]     AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE BLI.[BillId] = B.[Id]
            ) THEN 1 ELSE 0 END        AS [AssignedToMe]
        FROM Pending P
        INNER JOIN dbo.[Bill]   B ON B.[Id]      = P.[BillId]
        LEFT  JOIN dbo.[Vendor] V ON V.[Id]      = B.[VendorId]
        WHERE (@EntityType IS NULL OR @EntityType = N'Bill')
          -- U-454: the parent must still be OPEN. A finished document's review
          -- is moot -- its money has already reached QBO/SharePoint/Excel/Box --
          -- and leaving those tasks in the queue made the inbox majority noise:
          -- 69 of 111 live tasks (64 Bills + all 5 Invoices) sat on documents
          -- that were already completed.
          --
          -- `IsDraft = 1`, not `Status <> 'completed'`: on Bill the two are the
          -- same predicate by construction (IsDraft is PERSISTED COMPUTED over
          -- Status since U-446), and IsDraft is the only one that exists on
          -- Expense/BillCredit/Invoice, whose Status columns are LS-03b/c/d and
          -- NOT BUILT.
          AND B.[IsDraft] = 1
          AND (
            @IsSystemAdmin = 1
            OR (@Scope = N'mine_submitted' AND P.[SubmitterId] = @CurrentUserId)
            OR (@Scope = N'mine' AND EXISTS (
                SELECT 1
                FROM dbo.[BillLineItem] BLI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = BLI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                INNER JOIN dbo.[Role] R         ON R.[Id]         = UP.[RoleId]     AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE BLI.[BillId] = B.[Id]
            ))
            OR (@Scope = N'all' AND EXISTS (
                SELECT 1
                FROM dbo.[BillLineItem] BLI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = BLI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                WHERE BLI.[BillId] = B.[Id]
            ))
          )

        UNION ALL
        -- =================================================================
        -- Expense (IsCredit=true rolled in; client distinguishes by is_credit)
        -- =================================================================
        SELECT
            N'Expense',
            E.[IsCredit],
            E.[PublicId],
            E.[Id],
            E.[ReferenceNumber],
            V.[Name],
            E.[TotalAmount],
            P.[Id], P.[PublicId], P.[ReviewStatusId], P.[StatusName], P.[StatusColor],
            P.[StatusSortOrder], P.[StatusIsFinal], P.[StatusIsDeclined],
            P.[SubmitterId], P.[SubmitterFirstname], P.[SubmitterLastname],
            P.[CreatedDatetime],
            CASE WHEN EXISTS (
                SELECT 1
                FROM dbo.[ExpenseLineItem] ELI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = ELI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                INNER JOIN dbo.[Role] R         ON R.[Id]         = UP.[RoleId]     AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE ELI.[ExpenseId] = E.[Id]
            ) THEN 1 ELSE 0 END
        FROM Pending P
        INNER JOIN dbo.[Expense] E ON E.[Id] = P.[ExpenseId]
        LEFT  JOIN dbo.[Vendor]  V ON V.[Id] = E.[VendorId]
        WHERE (@EntityType IS NULL OR @EntityType = N'Expense')
          -- U-454: the parent must still be OPEN. A finished document's review
          -- is moot -- its money has already reached QBO/SharePoint/Excel/Box --
          -- and leaving those tasks in the queue made the inbox majority noise:
          -- 69 of 111 live tasks (64 Bills + all 5 Invoices) sat on documents
          -- that were already completed.
          --
          -- `IsDraft = 1`, not `Status <> 'completed'`: on Bill the two are the
          -- same predicate by construction (IsDraft is PERSISTED COMPUTED over
          -- Status since U-446), and IsDraft is the only one that exists on
          -- Expense/BillCredit/Invoice, whose Status columns are LS-03b/c/d and
          -- NOT BUILT.
          AND E.[IsDraft] = 1
          AND (
            @IsSystemAdmin = 1
            OR (@Scope = N'mine_submitted' AND P.[SubmitterId] = @CurrentUserId)
            OR (@Scope = N'mine' AND EXISTS (
                SELECT 1
                FROM dbo.[ExpenseLineItem] ELI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = ELI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                INNER JOIN dbo.[Role] R         ON R.[Id]         = UP.[RoleId]     AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE ELI.[ExpenseId] = E.[Id]
            ))
            OR (@Scope = N'all' AND EXISTS (
                SELECT 1
                FROM dbo.[ExpenseLineItem] ELI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = ELI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                WHERE ELI.[ExpenseId] = E.[Id]
            ))
          )

        UNION ALL
        -- =================================================================
        -- BillCredit
        -- =================================================================
        SELECT
            N'BillCredit',
            CAST(0 AS BIT),
            BC.[PublicId],
            BC.[Id],
            BC.[CreditNumber],
            V.[Name],
            BC.[TotalAmount],
            P.[Id], P.[PublicId], P.[ReviewStatusId], P.[StatusName], P.[StatusColor],
            P.[StatusSortOrder], P.[StatusIsFinal], P.[StatusIsDeclined],
            P.[SubmitterId], P.[SubmitterFirstname], P.[SubmitterLastname],
            P.[CreatedDatetime],
            CASE WHEN EXISTS (
                SELECT 1
                FROM dbo.[BillCreditLineItem] BCLI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = BCLI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                INNER JOIN dbo.[Role] R         ON R.[Id]         = UP.[RoleId]      AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE BCLI.[BillCreditId] = BC.[Id]
            ) THEN 1 ELSE 0 END
        FROM Pending P
        INNER JOIN dbo.[BillCredit] BC ON BC.[Id] = P.[BillCreditId]
        LEFT  JOIN dbo.[Vendor]     V  ON V.[Id]  = BC.[VendorId]
        WHERE (@EntityType IS NULL OR @EntityType = N'BillCredit')
          -- U-454: the parent must still be OPEN. A finished document's review
          -- is moot -- its money has already reached QBO/SharePoint/Excel/Box --
          -- and leaving those tasks in the queue made the inbox majority noise:
          -- 69 of 111 live tasks (64 Bills + all 5 Invoices) sat on documents
          -- that were already completed.
          --
          -- `IsDraft = 1`, not `Status <> 'completed'`: on Bill the two are the
          -- same predicate by construction (IsDraft is PERSISTED COMPUTED over
          -- Status since U-446), and IsDraft is the only one that exists on
          -- Expense/BillCredit/Invoice, whose Status columns are LS-03b/c/d and
          -- NOT BUILT.
          AND BC.[IsDraft] = 1
          AND (
            @IsSystemAdmin = 1
            OR (@Scope = N'mine_submitted' AND P.[SubmitterId] = @CurrentUserId)
            OR (@Scope = N'mine' AND EXISTS (
                SELECT 1
                FROM dbo.[BillCreditLineItem] BCLI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = BCLI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                INNER JOIN dbo.[Role] R         ON R.[Id]         = UP.[RoleId]      AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE BCLI.[BillCreditId] = BC.[Id]
            ))
            OR (@Scope = N'all' AND EXISTS (
                SELECT 1
                FROM dbo.[BillCreditLineItem] BCLI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = BCLI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                WHERE BCLI.[BillCreditId] = BC.[Id]
            ))
          )

        UNION ALL
        -- =================================================================
        -- Invoice (direct ProjectId; counterparty resolved via Project.Customer)
        -- =================================================================
        SELECT
            N'Invoice',
            CAST(0 AS BIT),
            I.[PublicId],
            I.[Id],
            I.[InvoiceNumber],
            C.[Name],
            I.[TotalAmount],
            P.[Id], P.[PublicId], P.[ReviewStatusId], P.[StatusName], P.[StatusColor],
            P.[StatusSortOrder], P.[StatusIsFinal], P.[StatusIsDeclined],
            P.[SubmitterId], P.[SubmitterFirstname], P.[SubmitterLastname],
            P.[CreatedDatetime],
            CASE WHEN EXISTS (
                SELECT 1
                FROM dbo.[UserProject] UP
                INNER JOIN dbo.[Role] R ON R.[Id] = UP.[RoleId] AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE UP.[ProjectId] = I.[ProjectId] AND UP.[UserId] = @CurrentUserId
            ) THEN 1 ELSE 0 END
        FROM Pending P
        INNER JOIN dbo.[Invoice]  I  ON I.[Id]  = P.[InvoiceId]
        INNER JOIN dbo.[Project]  Pr ON Pr.[Id] = I.[ProjectId]
        LEFT  JOIN dbo.[Customer] C  ON C.[Id]  = Pr.[CustomerId]
        WHERE (@EntityType IS NULL OR @EntityType = N'Invoice')
          -- U-454: the parent must still be OPEN. A finished document's review
          -- is moot -- its money has already reached QBO/SharePoint/Excel/Box --
          -- and leaving those tasks in the queue made the inbox majority noise:
          -- 69 of 111 live tasks (64 Bills + all 5 Invoices) sat on documents
          -- that were already completed.
          --
          -- `IsDraft = 1`, not `Status <> 'completed'`: on Bill the two are the
          -- same predicate by construction (IsDraft is PERSISTED COMPUTED over
          -- Status since U-446), and IsDraft is the only one that exists on
          -- Expense/BillCredit/Invoice, whose Status columns are LS-03b/c/d and
          -- NOT BUILT.
          AND I.[IsDraft] = 1
          AND (
            @IsSystemAdmin = 1
            OR (@Scope = N'mine_submitted' AND P.[SubmitterId] = @CurrentUserId)
            OR (@Scope = N'mine' AND EXISTS (
                SELECT 1
                FROM dbo.[UserProject] UP
                INNER JOIN dbo.[Role] R ON R.[Id] = UP.[RoleId] AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE UP.[ProjectId] = I.[ProjectId] AND UP.[UserId] = @CurrentUserId
            ))
            OR (@Scope = N'all' AND EXISTS (
                SELECT 1
                FROM dbo.[UserProject] UP
                WHERE UP.[ProjectId] = I.[ProjectId] AND UP.[UserId] = @CurrentUserId
            ))
          )
    )
    SELECT *
    FROM Rows
    ORDER BY [LastActivityAt] DESC, [ParentId] DESC
    OFFSET (@Page * @PageSize) ROWS FETCH NEXT @PageSize ROWS ONLY;
END;
GO


-- =============================================================================
-- ReadInboxTaskCounts — sidebar badge / tab counts for one user
-- Returns one row per (EntityType, IsCredit) with Mine / All / MineSubmitted
-- columns. No parent JOINs for display fields — light enough to call on every
-- sidebar render.
-- =============================================================================
CREATE OR ALTER PROCEDURE dbo.ReadInboxTaskCounts
(
    @CurrentUserId BIGINT,
    @IsSystemAdmin BIT = 0
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH Keyed AS (
        -- Mirror of ReadInboxTasks' Keyed/Submitter/Pending shape (U-453) --
        -- the list and the badge counts must agree on who submitted a document
        -- or they diverge, which is the class of bug this sproc exists to make
        -- impossible.
        SELECT
            r.[Id],
            r.[BillId], r.[ExpenseId], r.[BillCreditId], r.[InvoiceId],
            r.[StatusIsFinal], r.[StatusIsDeclined], r.[ReviewKind],
            r.[UserId], r.[CreatedDatetime],
            CASE
                WHEN r.[BillId]          IS NOT NULL THEN CONCAT(N'B', r.[BillId])
                WHEN r.[ExpenseId]       IS NOT NULL THEN CONCAT(N'E', r.[ExpenseId])
                WHEN r.[BillCreditId]    IS NOT NULL THEN CONCAT(N'C', r.[BillCreditId])
                WHEN r.[InvoiceId]       IS NOT NULL THEN CONCAT(N'I', r.[InvoiceId])
                WHEN r.[ContractLaborId] IS NOT NULL THEN CONCAT(N'L', r.[ContractLaborId])
            END AS [ParentKey]
        FROM dbo.[vw_Review] r
    ),
    LatestReview AS (
        SELECT *,
            ROW_NUMBER() OVER (
                PARTITION BY [ParentKey]
                ORDER BY [CreatedDatetime] DESC, [Id] DESC
            ) AS rn
        FROM Keyed
    ),
    Submitter AS (
        SELECT
            [ParentKey], [UserId],
            ROW_NUMBER() OVER (
                PARTITION BY [ParentKey]
                ORDER BY [CreatedDatetime] DESC, [Id] DESC
            ) AS rn
        FROM Keyed
        WHERE [ReviewKind] = N'submitted'
    ),
    Pending AS (
        -- Explicit columns, latest-row actor omitted -- see ReadInboxTasks.
        SELECT
            L.[BillId], L.[ExpenseId], L.[BillCreditId], L.[InvoiceId],
            S.[UserId] AS [SubmitterId]
        FROM LatestReview L
        LEFT JOIN Submitter S
            ON S.[ParentKey] = L.[ParentKey] AND S.rn = 1
        WHERE L.rn = 1 AND L.[StatusIsFinal] = 0 AND L.[StatusIsDeclined] = 0
    ),
    Tagged AS (
        SELECT
            N'Bill'        AS [EntityType],
            CAST(0 AS BIT) AS [IsCredit],
            CASE WHEN EXISTS (
                SELECT 1 FROM dbo.[BillLineItem] BLI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = BLI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                INNER JOIN dbo.[Role] R         ON R.[Id]         = UP.[RoleId]     AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE BLI.[BillId] = P.[BillId]
            ) THEN 1 ELSE 0 END AS [Mine],
            CASE WHEN @IsSystemAdmin = 1 OR EXISTS (
                SELECT 1 FROM dbo.[BillLineItem] BLI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = BLI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                WHERE BLI.[BillId] = P.[BillId]
            ) THEN 1 ELSE 0 END AS [Total],
            CASE WHEN P.[SubmitterId] = @CurrentUserId THEN 1 ELSE 0 END AS [MineSubmitted]
        FROM Pending P
        INNER JOIN dbo.[Bill] B ON B.[Id] = P.[BillId]
        -- U-454: same open-parent predicate as ReadInboxTasks. The Bill and
        -- BillCredit arms had NO parent join at all -- they read straight off
        -- Pending -- so without adding one here the badge would keep counting
        -- the 64 finished Bills the list no longer shows, and the two surfaces
        -- would disagree. That divergence is precisely what this sproc exists
        -- to prevent.
        WHERE P.[BillId] IS NOT NULL AND B.[IsDraft] = 1

        UNION ALL
        SELECT
            N'Expense',
            E.[IsCredit],
            CASE WHEN EXISTS (
                SELECT 1 FROM dbo.[ExpenseLineItem] ELI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = ELI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                INNER JOIN dbo.[Role] R         ON R.[Id]         = UP.[RoleId]     AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE ELI.[ExpenseId] = P.[ExpenseId]
            ) THEN 1 ELSE 0 END,
            CASE WHEN @IsSystemAdmin = 1 OR EXISTS (
                SELECT 1 FROM dbo.[ExpenseLineItem] ELI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = ELI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                WHERE ELI.[ExpenseId] = P.[ExpenseId]
            ) THEN 1 ELSE 0 END,
            CASE WHEN P.[SubmitterId] = @CurrentUserId THEN 1 ELSE 0 END
        FROM Pending P
        INNER JOIN dbo.[Expense] E ON E.[Id] = P.[ExpenseId]
        WHERE P.[ExpenseId] IS NOT NULL AND E.[IsDraft] = 1

        UNION ALL
        SELECT
            N'BillCredit',
            CAST(0 AS BIT),
            CASE WHEN EXISTS (
                SELECT 1 FROM dbo.[BillCreditLineItem] BCLI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = BCLI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                INNER JOIN dbo.[Role] R         ON R.[Id]         = UP.[RoleId]      AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE BCLI.[BillCreditId] = P.[BillCreditId]
            ) THEN 1 ELSE 0 END,
            CASE WHEN @IsSystemAdmin = 1 OR EXISTS (
                SELECT 1 FROM dbo.[BillCreditLineItem] BCLI
                INNER JOIN dbo.[UserProject] UP ON UP.[ProjectId] = BCLI.[ProjectId] AND UP.[UserId] = @CurrentUserId
                WHERE BCLI.[BillCreditId] = P.[BillCreditId]
            ) THEN 1 ELSE 0 END,
            CASE WHEN P.[SubmitterId] = @CurrentUserId THEN 1 ELSE 0 END
        FROM Pending P
        INNER JOIN dbo.[BillCredit] BC ON BC.[Id] = P.[BillCreditId]
        -- U-454: same open-parent predicate as ReadInboxTasks. The Bill and
        -- BillCredit arms had NO parent join at all -- they read straight off
        -- Pending -- so without adding one here the badge would keep counting
        -- the 64 finished Bills the list no longer shows, and the two surfaces
        -- would disagree. That divergence is precisely what this sproc exists
        -- to prevent.
        WHERE P.[BillCreditId] IS NOT NULL AND BC.[IsDraft] = 1

        UNION ALL
        SELECT
            N'Invoice',
            CAST(0 AS BIT),
            CASE WHEN EXISTS (
                SELECT 1 FROM dbo.[UserProject] UP
                INNER JOIN dbo.[Role] R ON R.[Id] = UP.[RoleId] AND R.[Name] IN (N'Project Manager', N'Owner')
                WHERE UP.[ProjectId] = I.[ProjectId] AND UP.[UserId] = @CurrentUserId
            ) THEN 1 ELSE 0 END,
            CASE WHEN @IsSystemAdmin = 1 OR EXISTS (
                SELECT 1 FROM dbo.[UserProject] UP
                WHERE UP.[ProjectId] = I.[ProjectId] AND UP.[UserId] = @CurrentUserId
            ) THEN 1 ELSE 0 END,
            CASE WHEN P.[SubmitterId] = @CurrentUserId THEN 1 ELSE 0 END
        FROM Pending P
        INNER JOIN dbo.[Invoice] I ON I.[Id] = P.[InvoiceId]
        WHERE P.[InvoiceId] IS NOT NULL AND I.[IsDraft] = 1
    )
    SELECT
        [EntityType],
        [IsCredit],
        SUM([Mine])          AS [Mine],
        SUM([Total])         AS [Total],
        SUM([MineSubmitted]) AS [MineSubmitted]
    FROM Tagged
    GROUP BY [EntityType], [IsCredit]
    ORDER BY [EntityType], [IsCredit];
END;
GO
