GO

IF OBJECT_ID('dbo.Review', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[Review]
(
    [Id]               BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId]         UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion]       ROWVERSION NOT NULL,
    [CreatedDatetime]  DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,
    [ReviewStatusId]   BIGINT NOT NULL,
    [UserId]           BIGINT NOT NULL,
    [Comments]         NVARCHAR(MAX) NULL,
    [BillId]           BIGINT NULL,
    [ExpenseId]        BIGINT NULL,
    [BillCreditId]     BIGINT NULL,
    [InvoiceId]        BIGINT NULL,
    CONSTRAINT [FK_Review_ReviewStatus] FOREIGN KEY ([ReviewStatusId]) REFERENCES dbo.[ReviewStatus]([Id]),
    CONSTRAINT [FK_Review_User]         FOREIGN KEY ([UserId])         REFERENCES dbo.[User]([Id]),
    CONSTRAINT [FK_Review_Bill]         FOREIGN KEY ([BillId])         REFERENCES dbo.[Bill]([Id]),
    CONSTRAINT [FK_Review_Expense]      FOREIGN KEY ([ExpenseId])      REFERENCES dbo.[Expense]([Id]),
    CONSTRAINT [FK_Review_BillCredit]   FOREIGN KEY ([BillCreditId])   REFERENCES dbo.[BillCredit]([Id]),
    CONSTRAINT [FK_Review_Invoice]      FOREIGN KEY ([InvoiceId])      REFERENCES dbo.[Invoice]([Id]),
    CONSTRAINT [CK_Review_OneParent] CHECK (
        (CASE WHEN [BillId]       IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [ExpenseId]    IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [BillCreditId] IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [InvoiceId]    IS NOT NULL THEN 1 ELSE 0 END) = 1
    )
);
END
GO


-- =========================================================================
-- Indexes (filtered, one per parent FK)
-- =========================================================================

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_BillId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_BillId] ON [dbo].[Review]([BillId]) WHERE [BillId] IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_ExpenseId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_ExpenseId] ON [dbo].[Review]([ExpenseId]) WHERE [ExpenseId] IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_BillCreditId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_BillCreditId] ON [dbo].[Review]([BillCreditId]) WHERE [BillCreditId] IS NOT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_Review_InvoiceId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_InvoiceId] ON [dbo].[Review]([InvoiceId]) WHERE [InvoiceId] IS NOT NULL;
END
GO

-- EmailMessageId: optional FK back to the EmailMessage that triggered
-- this Review state transition. Used by the Web UI's "final review"
-- surface to navigate from a state row to the source email (vendor
-- invoice / forwarded notification / PM reply).
IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.columns WHERE name='EmailMessageId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    ALTER TABLE [dbo].[Review] ADD [EmailMessageId] BIGINT NULL;
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name='FK_Review_EmailMessage')
BEGIN
    ALTER TABLE [dbo].[Review]
    ADD CONSTRAINT [FK_Review_EmailMessage] FOREIGN KEY ([EmailMessageId]) REFERENCES [dbo].[EmailMessage]([Id]);
END
GO

IF OBJECT_ID('dbo.Review', 'U') IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_Review_EmailMessageId' AND object_id = OBJECT_ID('dbo.Review'))
BEGIN
    CREATE INDEX [IX_Review_EmailMessageId] ON [dbo].[Review]([EmailMessageId]) WHERE [EmailMessageId] IS NOT NULL;
END
GO


-- =========================================================================
-- View — denormalized JOIN to ReviewStatus + User
-- Every read sproc selects from this view.
-- =========================================================================

CREATE OR ALTER VIEW [dbo].[vw_Review]
AS
    SELECT
        r.[Id],
        r.[PublicId],
        r.[RowVersion],
        CONVERT(VARCHAR(19), r.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), r.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        r.[ReviewStatusId],
        r.[UserId],
        r.[Comments],
        r.[BillId],
        r.[ExpenseId],
        r.[BillCreditId],
        r.[InvoiceId],
        r.[EmailMessageId],
        rs.[Name]       AS [StatusName],
        rs.[SortOrder]  AS [StatusSortOrder],
        rs.[IsFinal]    AS [StatusIsFinal],
        rs.[IsDeclined] AS [StatusIsDeclined],
        rs.[Color]      AS [StatusColor],
        u.[Firstname]   AS [UserFirstname],
        u.[Lastname]    AS [UserLastname]
    FROM dbo.[Review] r
    INNER JOIN dbo.[ReviewStatus] rs ON r.[ReviewStatusId] = rs.[Id]
    INNER JOIN dbo.[User] u          ON r.[UserId]         = u.[Id];
GO


-- =========================================================================
-- CreateReview
-- =========================================================================

CREATE OR ALTER PROCEDURE CreateReview
(
    @ReviewStatusId  BIGINT,
    @UserId          BIGINT,
    @Comments        NVARCHAR(MAX) = NULL,
    @BillId          BIGINT = NULL,
    @ExpenseId       BIGINT = NULL,
    @BillCreditId    BIGINT = NULL,
    @InvoiceId       BIGINT = NULL,
    @EmailMessageId  BIGINT = NULL,
    @CreatedByUserId BIGINT = NULL
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[Review] (
        [CreatedDatetime], [ModifiedDatetime],
        [ReviewStatusId], [UserId], [Comments],
        [BillId], [ExpenseId], [BillCreditId], [InvoiceId],
        [EmailMessageId],
        [CreatedByUserId]
    )
    VALUES (
        @Now, @Now,
        @ReviewStatusId, @UserId, @Comments,
        @BillId, @ExpenseId, @BillCreditId, @InvoiceId,
        @EmailMessageId,
        COALESCE(@CreatedByUserId, 17)
    );

    SELECT * FROM dbo.[vw_Review] WHERE [Id] = SCOPE_IDENTITY();

    COMMIT TRANSACTION;
END;
GO


-- =========================================================================
-- ReadReviewByPublicId
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadReviewByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review] WHERE [PublicId] = @PublicId;
END;
GO


-- =========================================================================
-- ReadReviewsByXId — full history, ascending
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadReviewsByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [BillId] = @BillId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadReviewsByExpenseId
(
    @ExpenseId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [ExpenseId] = @ExpenseId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadReviewsByBillCreditId
(
    @BillCreditId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [BillCreditId] = @BillCreditId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO

CREATE OR ALTER PROCEDURE ReadReviewsByInvoiceId
(
    @InvoiceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT * FROM dbo.[vw_Review]
    WHERE [InvoiceId] = @InvoiceId
    ORDER BY [CreatedDatetime] ASC, [Id] ASC;
END;
GO


-- =========================================================================
-- ReadCurrentReviewByXId — TOP 1 latest, descending. Tiebreak by Id DESC.
-- =========================================================================

CREATE OR ALTER PROCEDURE ReadCurrentReviewByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [BillId] = @BillId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadCurrentReviewByExpenseId
(
    @ExpenseId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [ExpenseId] = @ExpenseId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadCurrentReviewByBillCreditId
(
    @BillCreditId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [BillCreditId] = @BillCreditId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

CREATE OR ALTER PROCEDURE ReadCurrentReviewByInvoiceId
(
    @InvoiceId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    SELECT TOP 1 * FROM dbo.[vw_Review]
    WHERE [InvoiceId] = @InvoiceId
    ORDER BY [CreatedDatetime] DESC, [Id] DESC;
END;
GO

-- Batch lookup: latest Review per Bill in one call. Used by the Bill
-- list endpoint (Wave 3 Phase D) to surface ReviewStatus alongside
-- Draft state without a per-row N+1 query. Returns at most one row
-- per BillId — the most recently created Review for that bill.
CREATE OR ALTER PROCEDURE ReadCurrentReviewsByBillIds
(
    @BillIds NVARCHAR(MAX)
)
AS
BEGIN
    SET NOCOUNT ON;

    ;WITH ranked AS (
        SELECT
            r.*,
            ROW_NUMBER() OVER (
                PARTITION BY r.[BillId]
                ORDER BY r.[CreatedDatetime] DESC, r.[Id] DESC
            ) AS rn
        FROM dbo.[vw_Review] r
        INNER JOIN STRING_SPLIT(ISNULL(@BillIds, ''), ',') s
            ON s.value <> '' AND r.[BillId] = TRY_CAST(LTRIM(RTRIM(s.value)) AS BIGINT)
        WHERE r.[BillId] IS NOT NULL
    )
    SELECT
        [Id], [PublicId], [RowVersion], [CreatedDatetime], [ModifiedDatetime],
        [ReviewStatusId], [UserId], [Comments],
        [BillId], [ExpenseId], [BillCreditId], [InvoiceId],
        [StatusName], [StatusSortOrder], [StatusIsFinal], [StatusIsDeclined], [StatusColor],
        [UserFirstname], [UserLastname]
    FROM ranked
    WHERE rn = 1;
END;
GO

-- Delete all Review rows for a Bill. Called by BillService.delete_by_public_id
-- so a bill can be hard-deleted without tripping FK_Review_Bill. Reviews are
-- otherwise insert-only (audit history); this delete path exists ONLY for the
-- cascade when the parent Bill is itself being deleted. Also clears legacy
-- dbo.ReviewEntry rows (decommissioned table that still carries an FK to Bill);
-- guarded by an OBJECT_ID check so it's safe once that table is dropped.
CREATE OR ALTER PROCEDURE DeleteReviewsByBillId
(
    @BillId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRANSACTION;

    DELETE FROM dbo.[Review] WHERE [BillId] = @BillId;

    IF OBJECT_ID('dbo.ReviewEntry', 'U') IS NOT NULL
        DELETE FROM dbo.[ReviewEntry] WHERE [BillId] = @BillId;

    COMMIT TRANSACTION;
END;
GO


-- Review-notification recipient resolvers (canonical home; single-sourced U-062).
-- Human-only: excludes agent accounts (User.IsAgent=1) + persona test accounts
-- (Auth.Username LIKE 'persona_%'). Migrations 001/004/006/007/008 are superseded.

GO

-- -----------------------------------------------------------------------------
-- 1. Bill resolver — filter personas in UserProjectRoles
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ResolveReviewRecipientsByBillId
(
    @BillId BIGINT,
    @ExcludeUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    WITH BillProjects AS (
        SELECT DISTINCT bli.[ProjectId]
        FROM dbo.[BillLineItem] bli
        WHERE bli.[BillId] = @BillId
          AND bli.[ProjectId] IS NOT NULL
    ),
    UserProjectRoles AS (
        SELECT
            up.[UserId],
            up.[ProjectId],
            r.[Name] AS [RoleName],
            CASE r.[Name]
                WHEN 'Project Manager' THEN 1
                WHEN 'Owner'           THEN 2
                ELSE 99
            END AS [RolePrecedence]
        FROM dbo.[UserProject] up
        INNER JOIN BillProjects bp ON bp.[ProjectId] = up.[ProjectId]
        INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
        WHERE r.[Name] IN ('Project Manager', 'Owner')
          AND (@ExcludeUserId IS NULL OR up.[UserId] <> @ExcludeUserId)
          -- Restrict recipients to real human users: exclude LLM agent
          -- accounts (User.IsAgent = 1) and persona test accounts
          -- (Auth.Username starting with 'persona_', whitespace-tolerant).
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[User] u
              WHERE u.[Id] = up.[UserId]
                AND u.[IsAgent] = 1
          )
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[Auth] a
              WHERE a.[UserId] = up.[UserId]
                AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
          )
    ),
    DedupedRoles AS (
        SELECT
            [UserId],
            [RoleName],
            [ProjectId],
            ROW_NUMBER() OVER (
                PARTITION BY [UserId]
                ORDER BY [RolePrecedence] ASC, [ProjectId] ASC
            ) AS rn
        FROM UserProjectRoles
    ),
    UserEmails AS (
        SELECT
            c.[UserId],
            c.[Email],
            ROW_NUMBER() OVER (
                PARTITION BY c.[UserId]
                ORDER BY c.[Id] ASC
            ) AS rn
        FROM dbo.[Contact] c
        WHERE c.[UserId] IS NOT NULL
          AND c.[Email] IS NOT NULL
    )
    SELECT
        u.[Id]        AS [UserId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email],
        dr.[RoleName],
        dr.[ProjectId]
    FROM DedupedRoles dr
    INNER JOIN dbo.[User] u ON u.[Id] = dr.[UserId]
    LEFT JOIN UserEmails ue
        ON ue.[UserId] = dr.[UserId]
       AND ue.rn = 1
    WHERE dr.rn = 1
    ORDER BY dr.[RoleName], u.[Lastname], u.[Firstname];

    COMMIT TRANSACTION;
END;
GO


-- -----------------------------------------------------------------------------
-- 2. ContractLabor resolver — filter personas in UserProjectRoles
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ResolveReviewRecipientsByContractLaborId
(
    @ContractLaborId BIGINT,
    @ExcludeUserId BIGINT = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    WITH ContractLaborProjects AS (
        SELECT DISTINCT cli.[ProjectId]
        FROM dbo.[ContractLaborLineItem] cli
        WHERE cli.[ContractLaborId] = @ContractLaborId
          AND cli.[ProjectId] IS NOT NULL
    ),
    UserProjectRoles AS (
        SELECT
            up.[UserId],
            up.[ProjectId],
            r.[Name] AS [RoleName],
            CASE r.[Name]
                WHEN 'Project Manager' THEN 1
                WHEN 'Owner'           THEN 2
                ELSE 99
            END AS [RolePrecedence]
        FROM dbo.[UserProject] up
        INNER JOIN ContractLaborProjects clp ON clp.[ProjectId] = up.[ProjectId]
        INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
        WHERE r.[Name] IN ('Project Manager', 'Owner')
          AND (@ExcludeUserId IS NULL OR up.[UserId] <> @ExcludeUserId)
          -- Restrict recipients to real human users: exclude LLM agent
          -- accounts (User.IsAgent = 1) and persona test accounts
          -- (Auth.Username starting with 'persona_', whitespace-tolerant).
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[User] u
              WHERE u.[Id] = up.[UserId]
                AND u.[IsAgent] = 1
          )
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[Auth] a
              WHERE a.[UserId] = up.[UserId]
                AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
          )
    ),
    DedupedRoles AS (
        SELECT
            [UserId],
            [RoleName],
            [ProjectId],
            ROW_NUMBER() OVER (
                PARTITION BY [UserId]
                ORDER BY [RolePrecedence] ASC, [ProjectId] ASC
            ) AS rn
        FROM UserProjectRoles
    ),
    UserEmails AS (
        SELECT
            c.[UserId],
            c.[Email],
            ROW_NUMBER() OVER (
                PARTITION BY c.[UserId]
                ORDER BY c.[Id] ASC
            ) AS rn
        FROM dbo.[Contact] c
        WHERE c.[UserId] IS NOT NULL
          AND c.[Email] IS NOT NULL
    )
    SELECT
        u.[Id]        AS [UserId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email],
        dr.[RoleName],
        dr.[ProjectId]
    FROM DedupedRoles dr
    INNER JOIN dbo.[User] u ON u.[Id] = dr.[UserId]
    LEFT JOIN UserEmails ue
        ON ue.[UserId] = dr.[UserId]
       AND ue.rn = 1
    WHERE dr.rn = 1
    ORDER BY dr.[RoleName], u.[Lastname], u.[Firstname];

    COMMIT TRANSACTION;
END;
GO


-- -----------------------------------------------------------------------------
-- 3. Per-project ContractLabor resolver (v2 envelope, includes Owners)
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ResolveContractLaborReviewRecipientsPerProject
(
    @ContractLaborId BIGINT
)
AS
BEGIN
    SET NOCOUNT ON;

    WITH ContractLaborProjects AS (
        SELECT DISTINCT cli.[ProjectId]
        FROM dbo.[ContractLaborLineItem] cli
        WHERE cli.[ContractLaborId] = @ContractLaborId
          AND cli.[ProjectId] IS NOT NULL
    ),
    UserProjectRoles AS (
        SELECT
            up.[ProjectId],
            up.[UserId],
            r.[Name] AS [RoleName],
            CASE r.[Name]
                WHEN N'Project Manager' THEN 1
                WHEN N'Owner'           THEN 2
                ELSE 99
            END AS [RolePrecedence]
        FROM dbo.[UserProject] up
        INNER JOIN dbo.[Role] r ON r.[Id] = up.[RoleId]
        WHERE r.[Name] IN (N'Project Manager', N'Owner')
          -- Restrict recipients to real human users: exclude LLM agent
          -- accounts (User.IsAgent = 1) and persona test accounts
          -- (Auth.Username starting with 'persona_', whitespace-tolerant).
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[User] u
              WHERE u.[Id] = up.[UserId]
                AND u.[IsAgent] = 1
          )
          AND NOT EXISTS (
              SELECT 1 FROM dbo.[Auth] a
              WHERE a.[UserId] = up.[UserId]
                AND LEFT(LTRIM(a.[Username]), 8) = N'persona_'
          )
    ),
    -- PM wins when a user holds both roles on the same project.
    DedupedUserProjectRoles AS (
        SELECT
            [ProjectId],
            [UserId],
            [RoleName],
            ROW_NUMBER() OVER (
                PARTITION BY [ProjectId], [UserId]
                ORDER BY [RolePrecedence] ASC
            ) AS rn
        FROM UserProjectRoles
    ),
    UserEmails AS (
        SELECT
            c.[UserId],
            c.[Email],
            ROW_NUMBER() OVER (
                PARTITION BY c.[UserId]
                ORDER BY c.[Id] ASC
            ) AS rn
        FROM dbo.[Contact] c
        WHERE c.[UserId] IS NOT NULL
          AND c.[Email] IS NOT NULL
    )
    SELECT
        clp.[ProjectId],
        p.[Name]         AS [ProjectName],
        p.[Abbreviation] AS [ProjectAbbreviation],
        dpr.[UserId],
        u.[Firstname],
        u.[Lastname],
        ue.[Email],
        dpr.[RoleName]
    FROM ContractLaborProjects clp
    INNER JOIN dbo.[Project] p ON p.[Id] = clp.[ProjectId]
    LEFT JOIN DedupedUserProjectRoles dpr
        ON dpr.[ProjectId] = clp.[ProjectId]
       AND dpr.rn = 1
    LEFT JOIN dbo.[User] u      ON u.[Id] = dpr.[UserId]
    LEFT JOIN UserEmails ue     ON ue.[UserId] = dpr.[UserId] AND ue.rn = 1
    ORDER BY clp.[ProjectId], dpr.[RoleName], u.[Lastname], u.[Firstname];
END;
GO
