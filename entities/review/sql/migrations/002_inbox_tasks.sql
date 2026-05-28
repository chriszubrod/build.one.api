-- =============================================================================
-- Task Inbox sprocs — reads the cross-entity reviewer worklist.
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
--   * scope='mine_submitted' => current user submitted the latest review
--                               (Review.UserId match). For "sent box" surfaces.
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

    ;WITH LatestReview AS (
        SELECT
            r.[Id], r.[PublicId],
            r.[BillId], r.[ExpenseId], r.[BillCreditId], r.[InvoiceId],
            r.[ReviewStatusId], r.[StatusName], r.[StatusColor],
            r.[StatusSortOrder], r.[StatusIsFinal], r.[StatusIsDeclined],
            r.[UserId], r.[UserFirstname], r.[UserLastname],
            r.[CreatedDatetime],
            ROW_NUMBER() OVER (
                PARTITION BY
                    CASE
                        WHEN r.[BillId]       IS NOT NULL THEN CONCAT(N'B', r.[BillId])
                        WHEN r.[ExpenseId]    IS NOT NULL THEN CONCAT(N'E', r.[ExpenseId])
                        WHEN r.[BillCreditId] IS NOT NULL THEN CONCAT(N'C', r.[BillCreditId])
                        WHEN r.[InvoiceId]    IS NOT NULL THEN CONCAT(N'I', r.[InvoiceId])
                    END
                ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC
            ) AS rn
        FROM dbo.[vw_Review] r
    ),
    Pending AS (
        SELECT *
        FROM LatestReview
        WHERE rn = 1
          AND [StatusIsFinal]    = 0
          AND [StatusIsDeclined] = 0
          AND (@StatusId IS NULL OR [ReviewStatusId] = @StatusId)
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
            P.[UserId]                AS [SubmitterId],
            P.[UserFirstname]         AS [SubmitterFirstname],
            P.[UserLastname]          AS [SubmitterLastname],
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
          AND (
            @IsSystemAdmin = 1
            OR (@Scope = N'mine_submitted' AND P.[UserId] = @CurrentUserId)
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
            P.[UserId], P.[UserFirstname], P.[UserLastname],
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
          AND (
            @IsSystemAdmin = 1
            OR (@Scope = N'mine_submitted' AND P.[UserId] = @CurrentUserId)
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
            P.[UserId], P.[UserFirstname], P.[UserLastname],
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
          AND (
            @IsSystemAdmin = 1
            OR (@Scope = N'mine_submitted' AND P.[UserId] = @CurrentUserId)
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
            P.[UserId], P.[UserFirstname], P.[UserLastname],
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
          AND (
            @IsSystemAdmin = 1
            OR (@Scope = N'mine_submitted' AND P.[UserId] = @CurrentUserId)
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

    ;WITH LatestReview AS (
        SELECT
            r.[Id],
            r.[BillId], r.[ExpenseId], r.[BillCreditId], r.[InvoiceId],
            r.[StatusIsFinal], r.[StatusIsDeclined],
            r.[UserId], r.[CreatedDatetime],
            ROW_NUMBER() OVER (
                PARTITION BY
                    CASE
                        WHEN r.[BillId]       IS NOT NULL THEN CONCAT(N'B', r.[BillId])
                        WHEN r.[ExpenseId]    IS NOT NULL THEN CONCAT(N'E', r.[ExpenseId])
                        WHEN r.[BillCreditId] IS NOT NULL THEN CONCAT(N'C', r.[BillCreditId])
                        WHEN r.[InvoiceId]    IS NOT NULL THEN CONCAT(N'I', r.[InvoiceId])
                    END
                ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC
            ) AS rn
        FROM dbo.[vw_Review] r
    ),
    Pending AS (
        SELECT *
        FROM LatestReview
        WHERE rn = 1 AND [StatusIsFinal] = 0 AND [StatusIsDeclined] = 0
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
            CASE WHEN P.[UserId] = @CurrentUserId THEN 1 ELSE 0 END AS [MineSubmitted]
        FROM Pending P WHERE P.[BillId] IS NOT NULL

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
            CASE WHEN P.[UserId] = @CurrentUserId THEN 1 ELSE 0 END
        FROM Pending P
        INNER JOIN dbo.[Expense] E ON E.[Id] = P.[ExpenseId]
        WHERE P.[ExpenseId] IS NOT NULL

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
            CASE WHEN P.[UserId] = @CurrentUserId THEN 1 ELSE 0 END
        FROM Pending P WHERE P.[BillCreditId] IS NOT NULL

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
            CASE WHEN P.[UserId] = @CurrentUserId THEN 1 ELSE 0 END
        FROM Pending P
        INNER JOIN dbo.[Invoice] I ON I.[Id] = P.[InvoiceId]
        WHERE P.[InvoiceId] IS NOT NULL
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
