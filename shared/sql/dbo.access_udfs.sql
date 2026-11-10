-- ============================================================================
-- SINGLE CANONICAL SOURCE (U-051, 2026-07-16): this file is the ONE home for
-- all five dbo.UserCanAccess* UDFs. No migration and no entity base file may
-- redefine them. Change this file and apply it. Enforced by
-- tests/test_sproc_single_source.py.
--
-- Homed here, NOT in the entity base files, on purpose. For the four
-- creator-clause UDFs (Project/Bill/BillCredit/Expense) an entity-local home is
-- structurally impossible: each schema-binds a sibling package's table whose own
-- file FKs back, so an entity-local home is a from-scratch build CYCLE.
-- UserCanAccessTimeEntry is different — it has no cycle (it lived in
-- entities/time_entry/sql/dbo.time_entry.sql until U-051) and is homed here so
-- the family has ONE canonical location under ONE guard. Do not "fix" either by
-- moving it back. Rationale + prerequisites: shared/sql/README.md.
--
-- Before running this file on a fresh DB, note the one non-obvious
-- prerequisite: SCHEMABINDING binds COLUMNS, and the four creator-clause UDFs
-- bind {Project,Bill,BillCredit,Expense}.CreatedByUserId, which those tables'
-- base CREATE TABLE blocks do NOT declare —
-- scripts/migrations/gap2_created_by_user_id.sql adds it. Without it this file
-- fails with "Invalid column name 'CreatedByUserId'".
--
-- PROVENANCE: the four UserCanAccess{Project,Bill,BillCredit,Expense} bodies
-- are byte-identical to scripts/migrations/gap3_user_can_access_remove_legacy_
-- actor_bypass.sql, which was verified against LIVE prod on 2026-07-16
-- (body-normalized match). The UserCanAccessTimeEntry body is relocated
-- byte-identical from entities/time_entry/sql/dbo.time_entry.sql (canonical
-- there since U-045). This file is net-zero to prod.
-- ============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

-- ----------------------------------------------------------------------------
-- UDF: dbo.UserCanAccessTimeEntry
--
-- Returns 1 iff the actor is system admin, OR the actor created (owns) the
-- entry, OR (actor holds CanViewTeam AND any of the entry's TimeLog rows
-- has a ProjectId in the actor's UserProject set).
--
-- Used by service-layer post-fetch checks on by-id reads + mutation gating.
-- Single-row UDF calls are cheap; the gap-1 perf concern only applies to
-- per-row list-path use.
-- ----------------------------------------------------------------------------
CREATE OR ALTER FUNCTION dbo.UserCanAccessTimeEntry
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @ActorCanViewTeam BIT,
    @TimeEntryId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[TimeEntry] te
                WHERE te.[Id] = @TimeEntryId
                  AND te.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN @ActorCanViewTeam = 1 AND EXISTS (
                SELECT 1
                FROM dbo.[TimeLog] tl
                INNER JOIN dbo.[UserProject] up
                    ON up.[ProjectId] = tl.[ProjectId]
                WHERE tl.[TimeEntryId] = @TimeEntryId
                  AND up.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO

-- 1. Project — direct ProjectId on parent.
CREATE OR ALTER FUNCTION dbo.UserCanAccessProject
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @ProjectId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[Project] p
                WHERE p.[Id] = @ProjectId
                  AND p.[CreatedByUserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1 FROM dbo.[UserProject] up
                WHERE up.[UserId] = @ActorUserId
                  AND up.[ProjectId] = @ProjectId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO

-- 2. Bill — line items carry ProjectId; parent is accessible if ANY
--    line item's project is in the user's UserProject set, OR if the
--    user created the bill (covers empty drafts).
CREATE OR ALTER FUNCTION dbo.UserCanAccessBill
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @BillId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[Bill] b
                WHERE b.[Id] = @BillId
                  AND b.[CreatedByUserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[BillLineItem] bli
                INNER JOIN dbo.[UserProject] up
                    ON up.[ProjectId] = bli.[ProjectId]
                WHERE bli.[BillId] = @BillId
                  AND up.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO

-- 3. BillCredit — same shape as Bill.
CREATE OR ALTER FUNCTION dbo.UserCanAccessBillCredit
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @BillCreditId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[BillCredit] bc
                WHERE bc.[Id] = @BillCreditId
                  AND bc.[CreatedByUserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[BillCreditLineItem] bcli
                INNER JOIN dbo.[UserProject] up
                    ON up.[ProjectId] = bcli.[ProjectId]
                WHERE bcli.[BillCreditId] = @BillCreditId
                  AND up.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO

-- 4. Expense — same shape as Bill.
CREATE OR ALTER FUNCTION dbo.UserCanAccessExpense
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @ExpenseId BIGINT
)
RETURNS BIT
WITH SCHEMABINDING
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[Expense] e
                WHERE e.[Id] = @ExpenseId
                  AND e.[CreatedByUserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[ExpenseLineItem] eli
                INNER JOIN dbo.[UserProject] up
                    ON up.[ProjectId] = eli.[ProjectId]
                WHERE eli.[ExpenseId] = @ExpenseId
                  AND up.[UserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO

-- ----------------------------------------------------------------------------
-- UDF: dbo.UserCanAccessAttachment (U-166c, 2026-11-10)
--
-- Design: docs/design/u166c-attachment-access.md (authoritative).
--
-- Deliberately NOT WITH SCHEMABINDING. It reads nine link tables owned by
-- other entity packages, and schemabinding would block every future ALTER of
-- those tables. It calls the schemabound UserCanAccess{Bill,BillCredit,Expense,
-- Project} UDFs, which is allowed: a non-schemabound function may call
-- schemabound ones.
--
-- Returns 1 when any of these holds:
--   1. the actor is a system admin;
--   2. the actor uploaded it (Attachment.CreatedByUserId);
--   3. it is linked to a record the actor can access:
--        BillLineItemAttachment       -> BillLineItem.BillId        -> UserCanAccessBill
--        BillCreditLineItemAttachment -> BillCreditLineItem.BillCreditId -> UserCanAccessBillCredit
--        ExpenseLineItemAttachment    -> ExpenseLineItem.ExpenseId  -> UserCanAccessExpense
--        InvoiceAttachment            -> Invoice.ProjectId          -> UserCanAccessProject
--        InvoiceLineItemAttachment    -> InvoiceLineItem.InvoiceId -> Invoice.ProjectId -> UserCanAccessProject
--   4. the actor holds the company role Owner or Controller AND the attachment
--      is either linked to a project-less record (Taxpayer, BusinessLicense,
--      ContractorsLicense, Asset) or linked to nothing at all.
--      The role does NOT open project-linked attachments.
--
-- Fails closed on a NULL actor: every clause compares to @ActorUserId, and a
-- NULL comparison is never true.
-- ----------------------------------------------------------------------------
CREATE OR ALTER FUNCTION dbo.UserCanAccessAttachment
(
    @ActorUserId BIGINT,
    @ActorIsSystemAdmin BIT,
    @AttachmentId BIGINT
)
RETURNS BIT
AS
BEGIN
    RETURN (
        SELECT CASE
            WHEN @ActorIsSystemAdmin = 1 THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[Attachment] a
                WHERE a.[Id] = @AttachmentId
                  AND a.[CreatedByUserId] = @ActorUserId
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[BillLineItemAttachment] blia
                INNER JOIN dbo.[BillLineItem] bli
                    ON bli.[Id] = blia.[BillLineItemId]
                WHERE blia.[AttachmentId] = @AttachmentId
                  AND dbo.UserCanAccessBill(@ActorUserId, 0, bli.[BillId]) = 1
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[BillCreditLineItemAttachment] bcla
                INNER JOIN dbo.[BillCreditLineItem] bcli
                    ON bcli.[Id] = bcla.[BillCreditLineItemId]
                WHERE bcla.[AttachmentId] = @AttachmentId
                  AND dbo.UserCanAccessBillCredit(@ActorUserId, 0, bcli.[BillCreditId]) = 1
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[ExpenseLineItemAttachment] elia
                INNER JOIN dbo.[ExpenseLineItem] eli
                    ON eli.[Id] = elia.[ExpenseLineItemId]
                WHERE elia.[AttachmentId] = @AttachmentId
                  AND dbo.UserCanAccessExpense(@ActorUserId, 0, eli.[ExpenseId]) = 1
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[InvoiceAttachment] ia
                INNER JOIN dbo.[Invoice] inv
                    ON inv.[Id] = ia.[InvoiceId]
                WHERE ia.[AttachmentId] = @AttachmentId
                  AND dbo.UserCanAccessProject(@ActorUserId, 0, inv.[ProjectId]) = 1
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[InvoiceLineItemAttachment] ilia
                INNER JOIN dbo.[InvoiceLineItem] ili
                    ON ili.[Id] = ilia.[InvoiceLineItemId]
                INNER JOIN dbo.[Invoice] inv
                    ON inv.[Id] = ili.[InvoiceId]
                WHERE ilia.[AttachmentId] = @AttachmentId
                  AND dbo.UserCanAccessProject(@ActorUserId, 0, inv.[ProjectId]) = 1
            ) THEN CONVERT(BIT, 1)
            WHEN EXISTS (
                SELECT 1
                FROM dbo.[UserRole] ur
                INNER JOIN dbo.[Role] r
                    ON r.[Id] = ur.[RoleId]
                WHERE ur.[UserId] = @ActorUserId
                  AND r.[Name] IN (N'Owner', N'Controller')
            )
            AND (
                EXISTS (SELECT 1 FROM dbo.[TaxpayerAttachment] x WHERE x.[AttachmentId] = @AttachmentId)
                OR EXISTS (SELECT 1 FROM dbo.[BusinessLicenseAttachment] x WHERE x.[AttachmentId] = @AttachmentId)
                OR EXISTS (SELECT 1 FROM dbo.[ContractorsLicenseAttachment] x WHERE x.[AttachmentId] = @AttachmentId)
                OR EXISTS (SELECT 1 FROM dbo.[AssetAttachment] x WHERE x.[AttachmentId] = @AttachmentId)
                -- X OR NOT (L OR X) == X OR NOT L: only the five project-linked tables go here.
                OR NOT EXISTS (
                    SELECT 1 FROM dbo.[BillLineItemAttachment] x WHERE x.[AttachmentId] = @AttachmentId
                    UNION ALL
                    SELECT 1 FROM dbo.[BillCreditLineItemAttachment] x WHERE x.[AttachmentId] = @AttachmentId
                    UNION ALL
                    SELECT 1 FROM dbo.[ExpenseLineItemAttachment] x WHERE x.[AttachmentId] = @AttachmentId
                    UNION ALL
                    SELECT 1 FROM dbo.[InvoiceAttachment] x WHERE x.[AttachmentId] = @AttachmentId
                    UNION ALL
                    SELECT 1 FROM dbo.[InvoiceLineItemAttachment] x WHERE x.[AttachmentId] = @AttachmentId
                )
            ) THEN CONVERT(BIT, 1)
            ELSE CONVERT(BIT, 0)
        END
    );
END;
GO
