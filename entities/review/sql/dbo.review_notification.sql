-- Contract: dbo.ReviewNotification / dbo.ReviewNotificationTarget — one row per outbound review email, and the records it covers.
-- Owns: the record of every review-submit email (Bill, Expense, BillCredit, ContractLabor): its outbox row, its Graph conversation identity (stamped by the MS worker on draft create), and the exact documents its reply may approve.
-- Invariants: Kind is one of bill/expense/bill_credit/contract_labor. A ContractLabor claim is one row per (ProjectId, WorkDate) (CreateReviewNotification returns an empty set on a duplicate). Every target row names exactly one document. ConversationId / InternetMessageId / DraftGraphMessageId compare case-sensitively (BIN). Stamping only fills NULLs.
-- U-797b1: a Contract Labor target's snapshot columns (the suggestion the email showed and its lines fingerprint) are written only by the CL crew claim; Bill, Expense and BillCredit targets leave them NULL.
-- Sprocs: CreateReviewNotification, AddReviewNotificationTarget, SetReviewNotificationOutbox, StampReviewNotificationFromOutbox, StampReviewNotificationById, DeleteReviewNotification, FindReviewNotificationForReply, ReadReviewNotificationTargetSnapshots
-- =============================================================================
-- U-797c1: ReviewNotification is the one record a reply binds through (design
-- docs/design/u797c-review-reply-binding.md, section 1). Writers: the Bill /
-- Expense / CL notification services. Stamp: the MS outbox worker on draft
-- create. Reader: shared/authz/provenance.py (bind_reply_via_notification).
--
-- Canonical single-source home for these sprocs (entity base file). Change this
-- file and apply it; do not redefine these sprocs in migrations.
-- =============================================================================

-- Delete behaviour (deliberate exception to the no CASCADE convention, CLAUDE.md "Deletes"):
-- this is a sidecar. It must never change how an existing entity deletes. So the target rows
-- CASCADE from the notification and from each document they name (a deleted Bill, Expense,
-- BillCredit or ContractLabor takes its target rows with it), and ReviewNotification.ReviewId
-- SET NULLs when its Review is deleted. No existing delete sproc is edited to match.
IF OBJECT_ID('dbo.ReviewNotification', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ReviewNotification]
(
    [Id]                   BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [PublicId]             UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [Kind]                 NVARCHAR(20) NOT NULL,
    [ReviewId]             BIGINT NULL,
    [OutboxId]             BIGINT NULL,
    [Subject]              NVARCHAR(1024) NULL,
    [ConversationId]       NVARCHAR(512) COLLATE Latin1_General_BIN NULL,
    [InternetMessageId]    NVARCHAR(512) COLLATE Latin1_General_BIN NULL,
    [DraftGraphMessageId]  NVARCHAR(512) COLLATE Latin1_General_BIN NULL,
    [StampedDatetime]      DATETIME2(3) NULL,
    [ProjectId]            BIGINT NULL,
    [WorkDate]             DATE NULL,
    [Source]               NVARCHAR(20) NOT NULL CONSTRAINT [DF_ReviewNotification_Source] DEFAULT N'live',
    [CreatedDatetime]      DATETIME2(3) NOT NULL CONSTRAINT [DF_ReviewNotification_CreatedDatetime] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_ReviewNotification_Review] FOREIGN KEY ([ReviewId]) REFERENCES dbo.[Review]([Id]) ON DELETE SET NULL,
    CONSTRAINT [CK_ReviewNotification_Kind] CHECK ([Kind] IN (N'bill', N'expense', N'bill_credit', N'contract_labor')),
    CONSTRAINT [CK_ReviewNotification_Source] CHECK ([Source] IN (N'live', N'backfill'))
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_ReviewNotification_ReviewId' AND object_id = OBJECT_ID('dbo.ReviewNotification'))
    CREATE UNIQUE INDEX UX_ReviewNotification_ReviewId ON [dbo].[ReviewNotification] ([ReviewId]) WHERE [ReviewId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_ReviewNotification_CrewClaim' AND object_id = OBJECT_ID('dbo.ReviewNotification'))
    CREATE UNIQUE INDEX UX_ReviewNotification_CrewClaim ON [dbo].[ReviewNotification] ([ProjectId], [WorkDate]) WHERE [Kind] = N'contract_labor';
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_ReviewNotification_OutboxId' AND object_id = OBJECT_ID('dbo.ReviewNotification'))
    CREATE UNIQUE INDEX UX_ReviewNotification_OutboxId ON [dbo].[ReviewNotification] ([OutboxId]) WHERE [OutboxId] IS NOT NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ReviewNotification_ConversationId' AND object_id = OBJECT_ID('dbo.ReviewNotification'))
    CREATE INDEX IX_ReviewNotification_ConversationId ON [dbo].[ReviewNotification] ([ConversationId]) WHERE [ConversationId] IS NOT NULL;
GO

IF OBJECT_ID('dbo.ReviewNotificationTarget', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ReviewNotificationTarget]
(
    [Id]                   BIGINT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    [ReviewNotificationId] BIGINT NOT NULL,
    [BillId]               BIGINT NULL,
    [ExpenseId]            BIGINT NULL,
    [BillCreditId]         BIGINT NULL,
    [ContractLaborId]      BIGINT NULL,
    CONSTRAINT [FK_ReviewNotificationTarget_Notification] FOREIGN KEY ([ReviewNotificationId]) REFERENCES dbo.[ReviewNotification]([Id]) ON DELETE CASCADE,
    CONSTRAINT [FK_ReviewNotificationTarget_Bill]         FOREIGN KEY ([BillId])         REFERENCES dbo.[Bill]([Id]) ON DELETE CASCADE,
    CONSTRAINT [FK_ReviewNotificationTarget_Expense]      FOREIGN KEY ([ExpenseId])      REFERENCES dbo.[Expense]([Id]) ON DELETE CASCADE,
    CONSTRAINT [FK_ReviewNotificationTarget_BillCredit]   FOREIGN KEY ([BillCreditId])   REFERENCES dbo.[BillCredit]([Id]) ON DELETE CASCADE,
    CONSTRAINT [FK_ReviewNotificationTarget_ContractLabor] FOREIGN KEY ([ContractLaborId]) REFERENCES dbo.[ContractLabor]([Id]) ON DELETE CASCADE,
    CONSTRAINT [CK_ReviewNotificationTarget_OneDocument] CHECK (
        (CASE WHEN [BillId]        IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [ExpenseId]     IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [BillCreditId]  IS NOT NULL THEN 1 ELSE 0 END) +
        (CASE WHEN [ContractLaborId] IS NOT NULL THEN 1 ELSE 0 END) = 1),
    CONSTRAINT [UQ_ReviewNotificationTarget] UNIQUE ([ReviewNotificationId], [BillId], [ExpenseId], [BillCreditId], [ContractLaborId])
);
END
GO

-- Cascaded document deletes seek the target rows instead of scanning the table.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ReviewNotificationTarget_BillId' AND object_id = OBJECT_ID('dbo.ReviewNotificationTarget'))
    CREATE INDEX IX_ReviewNotificationTarget_BillId ON [dbo].[ReviewNotificationTarget] ([BillId]) WHERE [BillId] IS NOT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ReviewNotificationTarget_ExpenseId' AND object_id = OBJECT_ID('dbo.ReviewNotificationTarget'))
    CREATE INDEX IX_ReviewNotificationTarget_ExpenseId ON [dbo].[ReviewNotificationTarget] ([ExpenseId]) WHERE [ExpenseId] IS NOT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ReviewNotificationTarget_BillCreditId' AND object_id = OBJECT_ID('dbo.ReviewNotificationTarget'))
    CREATE INDEX IX_ReviewNotificationTarget_BillCreditId ON [dbo].[ReviewNotificationTarget] ([BillCreditId]) WHERE [BillCreditId] IS NOT NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_ReviewNotificationTarget_ContractLaborId' AND object_id = OBJECT_ID('dbo.ReviewNotificationTarget'))
    CREATE INDEX IX_ReviewNotificationTarget_ContractLaborId ON [dbo].[ReviewNotificationTarget] ([ContractLaborId]) WHERE [ContractLaborId] IS NOT NULL;
GO

-- U-797b1: the snapshot of what each Contract Labor target's email showed (the suggested code, its row, the
-- split note as shown) and a fingerprint of that crew member's lines. Nullable: other kinds leave them NULL.
-- One guarded ALTER, GO-terminated, so it cannot be swallowed into the next CREATE PROCEDURE.
IF COL_LENGTH('dbo.ReviewNotificationTarget', 'SuggestedSubCostCodeId') IS NULL
    ALTER TABLE [dbo].[ReviewNotificationTarget] ADD
        [SuggestedSubCostCodeId] BIGINT NULL,
        [SuggestionId]           BIGINT NULL,
        [SuggestionSource]       NVARCHAR(50) NULL,
        [SuggestionMaySplit]     BIT NULL,
        [LinesFingerprint]       NVARCHAR(64) NULL;
GO

-- -----------------------------------------------------------------------------
-- CreateReviewNotification: insert one notification and return it. For
-- contract_labor the insert is the (ProjectId, WorkDate) claim: when a row already
-- exists the INSERT is skipped and the result set is EMPTY (the caller reads that
-- as "already claimed"). UPDLOCK/HOLDLOCK makes the check-and-insert one step.
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.CreateReviewNotification
    @Kind      NVARCHAR(20),
    @ReviewId  BIGINT = NULL,
    @Subject   NVARCHAR(1024) = NULL,
    @ProjectId BIGINT = NULL,
    @WorkDate  DATE = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @NewId BIGINT = NULL;

    INSERT INTO dbo.[ReviewNotification] ([Kind], [ReviewId], [Subject], [ProjectId], [WorkDate])
    SELECT @Kind, @ReviewId, @Subject, @ProjectId, @WorkDate
    WHERE @Kind <> N'contract_labor'
       OR NOT EXISTS (
            SELECT 1 FROM dbo.[ReviewNotification] WITH (UPDLOCK, HOLDLOCK)
            WHERE [Kind] = N'contract_labor'
              AND [ProjectId] = @ProjectId
              AND [WorkDate] = @WorkDate);

    SET @NewId = SCOPE_IDENTITY();

    -- U-797c2a: the only caller reads Id (record_for_review / claim_for_crew return it as the row).
    SELECT [Id]
    FROM dbo.[ReviewNotification]
    WHERE [Id] = @NewId;
END
GO

-- -----------------------------------------------------------------------------
-- AddReviewNotificationTarget: one document covered by a notification. Exactly
-- one of the four id params is non-null (the table CHECK enforces it).
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.AddReviewNotificationTarget
    @ReviewNotificationId   BIGINT,
    @BillId                 BIGINT = NULL,
    @ExpenseId              BIGINT = NULL,
    @BillCreditId           BIGINT = NULL,
    @ContractLaborId        BIGINT = NULL,
    @SuggestedSubCostCodeId BIGINT = NULL,
    @SuggestionId           BIGINT = NULL,
    @SuggestionSource       NVARCHAR(50) = NULL,
    @SuggestionMaySplit     BIT = NULL,
    @LinesFingerprint       NVARCHAR(64) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @NewId BIGINT = NULL;

    INSERT INTO dbo.[ReviewNotificationTarget]
        ([ReviewNotificationId], [BillId], [ExpenseId], [BillCreditId], [ContractLaborId],
         [SuggestedSubCostCodeId], [SuggestionId], [SuggestionSource], [SuggestionMaySplit], [LinesFingerprint])
    VALUES
        (@ReviewNotificationId, @BillId, @ExpenseId, @BillCreditId, @ContractLaborId,
         @SuggestedSubCostCodeId, @SuggestionId, @SuggestionSource, @SuggestionMaySplit, @LinesFingerprint);

    SET @NewId = SCOPE_IDENTITY();

    SELECT [Id], [ReviewNotificationId], [BillId], [ExpenseId], [BillCreditId], [ContractLaborId]
    FROM dbo.[ReviewNotificationTarget]
    WHERE [Id] = @NewId;
END
GO

-- -----------------------------------------------------------------------------
-- SetReviewNotificationOutbox: link the notification to its ms.Outbox row, and fill the
-- identity the MS worker already persisted on that row, in ONE statement. So the
-- attach and the payload fill cannot interleave with the worker's stamp. Each id column
-- is COALESCEd (NULLs only), like the worker stamp, so whichever runs second fills the row.
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.SetReviewNotificationOutbox
    @Id       BIGINT,
    @OutboxId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE n
    SET n.[OutboxId] = @OutboxId,
        n.[ConversationId]      = COALESCE(n.[ConversationId], JSON_VALUE(o.[Payload], '$.conversation_id') COLLATE Latin1_General_BIN),
        n.[InternetMessageId]   = COALESCE(n.[InternetMessageId], JSON_VALUE(o.[Payload], '$.internet_message_id') COLLATE Latin1_General_BIN),
        n.[DraftGraphMessageId] = COALESCE(n.[DraftGraphMessageId], JSON_VALUE(o.[Payload], '$.graph_message_id') COLLATE Latin1_General_BIN),
        n.[StampedDatetime]     = CASE WHEN n.[StampedDatetime] IS NULL
                                        AND (JSON_VALUE(o.[Payload], '$.conversation_id') IS NOT NULL
                                             OR JSON_VALUE(o.[Payload], '$.internet_message_id') IS NOT NULL
                                             OR JSON_VALUE(o.[Payload], '$.graph_message_id') IS NOT NULL)
                                       THEN SYSUTCDATETIME() ELSE n.[StampedDatetime] END
    FROM dbo.[ReviewNotification] n
    LEFT JOIN [ms].[Outbox] o ON o.[Id] = @OutboxId
    WHERE n.[Id] = @Id;
END
GO

-- -----------------------------------------------------------------------------
-- StampReviewNotificationFromOutbox: called by the MS worker when it creates the
-- draft. Fills only NULL identity columns (never overwrites a recorded id). A
-- no-op when no notification carries this OutboxId.
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.StampReviewNotificationFromOutbox
    @OutboxId            BIGINT,
    @ConversationId      NVARCHAR(512) = NULL,
    @InternetMessageId   NVARCHAR(512) = NULL,
    @DraftGraphMessageId NVARCHAR(512) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.[ReviewNotification]
    SET [ConversationId]      = CASE WHEN [ConversationId] IS NULL THEN @ConversationId COLLATE Latin1_General_BIN ELSE [ConversationId] END,
        [InternetMessageId]   = CASE WHEN [InternetMessageId] IS NULL THEN @InternetMessageId COLLATE Latin1_General_BIN ELSE [InternetMessageId] END,
        [DraftGraphMessageId] = CASE WHEN [DraftGraphMessageId] IS NULL THEN @DraftGraphMessageId COLLATE Latin1_General_BIN ELSE [DraftGraphMessageId] END,
        [StampedDatetime]     = COALESCE([StampedDatetime], SYSUTCDATETIME())
    WHERE [OutboxId] = @OutboxId;
END
GO

-- -----------------------------------------------------------------------------
-- StampReviewNotificationById (U-797c2a): the worker stamps the row the enqueue
-- named in the payload (review_notification_id), not the row found by OutboxId.
-- Fills only NULL identity columns, like the by-outbox stamp. A no-op when no row
-- carries this Id.
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.StampReviewNotificationById
    @Id                  BIGINT,
    @ConversationId      NVARCHAR(512) = NULL,
    @InternetMessageId   NVARCHAR(512) = NULL,
    @DraftGraphMessageId NVARCHAR(512) = NULL,
    @OutboxId            BIGINT = NULL
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.[ReviewNotification]
    SET [ConversationId]      = CASE WHEN [ConversationId] IS NULL THEN @ConversationId COLLATE Latin1_General_BIN ELSE [ConversationId] END,
        [InternetMessageId]   = CASE WHEN [InternetMessageId] IS NULL THEN @InternetMessageId COLLATE Latin1_General_BIN ELSE [InternetMessageId] END,
        [DraftGraphMessageId] = CASE WHEN [DraftGraphMessageId] IS NULL THEN @DraftGraphMessageId COLLATE Latin1_General_BIN ELSE [DraftGraphMessageId] END,
        [StampedDatetime]     = COALESCE([StampedDatetime], SYSUTCDATETIME())
    WHERE [Id] = @Id
      AND ([OutboxId] IS NULL OR [OutboxId] = @OutboxId);  -- ownership: only this outbox row's record, or an unattached one
END
GO

-- -----------------------------------------------------------------------------
-- DeleteReviewNotification: release a notification (used when an enqueue fails
-- and the claim must be dropped). Targets go first (the target FK CASCADEs, so this explicit delete is belt and braces).
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.DeleteReviewNotification
    @Id BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM dbo.[ReviewNotificationTarget]
    WHERE [ReviewNotificationId] = @Id;

    DELETE FROM dbo.[ReviewNotification]
    WHERE [Id] = @Id;
END
GO

-- -----------------------------------------------------------------------------
-- FindReviewNotificationForReply: the reply binder's lookup. Exact, case-sensitive
-- match on ConversationId. Result set 1: the matching notifications. Result set 2:
-- their targets. The caller requires exactly one notification.
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.FindReviewNotificationForReply
    @ConversationId NVARCHAR(512)
AS
BEGIN
    SET NOCOUNT ON;

    -- U-797c2a: trimmed to what the binder reads (Kind, ProjectId, WorkDate) and the
    -- target join needs (Id). The other columns had no reader.
    -- U-797c2b: Subject is read back for the retitled-draft guard (select_notification
    -- reply_subject). Nothing else is added.
    SELECT [Id], [Kind], [ProjectId], [WorkDate], [Subject]
    FROM dbo.[ReviewNotification]
    WHERE [ConversationId] = @ConversationId COLLATE Latin1_General_BIN;

    SELECT t.[Id], t.[ReviewNotificationId], t.[BillId], t.[ExpenseId],
           t.[BillCreditId], t.[ContractLaborId]
    FROM dbo.[ReviewNotificationTarget] t
    WHERE t.[ReviewNotificationId] IN (
        SELECT n.[Id] FROM dbo.[ReviewNotification] n
        WHERE n.[ConversationId] = @ConversationId COLLATE Latin1_General_BIN);
END
GO

-- -----------------------------------------------------------------------------
-- U-797b1: the Contract Labor targets of one notification with their snapshot columns (what each crew member's
-- email showed). Read-only. The reply path and U-797b2 read it to know what was approved.
-- -----------------------------------------------------------------------------
CREATE OR ALTER PROCEDURE dbo.ReadReviewNotificationTargetSnapshots
    @ReviewNotificationId BIGINT
AS
BEGIN
    SET NOCOUNT ON;

    SELECT [ContractLaborId], [SuggestedSubCostCodeId], [SuggestionId], [SuggestionSource],
           [SuggestionMaySplit], [LinesFingerprint]
    FROM dbo.[ReviewNotificationTarget]
    WHERE [ReviewNotificationId] = @ReviewNotificationId
      AND [ContractLaborId] IS NOT NULL;
END
GO
