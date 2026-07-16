-- =============================================================================
-- 2026-07-01 — allow @SortBy = 'Worker' in ReadTimeEntries (ORIGINAL INTENT).
--
-- ORIGINAL PROBLEM: This migration MISTARGETED the dead `ReadTimeEntries` singular
-- sproc with a 10-param PAGINATED, UNSCOPED body (page/search/sort params) when
-- the list-page Worker sort actually belongs in `ReadTimeEntriesPaginated`.
--
-- `ReadTimeEntries`'s only caller is TimeEntryService.read_all() — a dead path with
-- no HTTP or agent caller — and it takes the scoped 3-param actor signature
-- (@ActorUserId, @ActorIsSystemAdmin, @ActorCanViewTeam).
--
-- Reconciled (U-039, 2026-07-16) to the canonical scoped 3-param `ReadTimeEntries`
-- from entities/time_entry/sql/dbo.time_entry.sql so a re-run is a safe no-op.
-- Previously, re-running this file reverted RBAC scoping and re-drifted prod.
--
-- The list-page Worker sort belongs in `ReadTimeEntriesPaginated` (the scoped
-- paginated sproc the /time-entries endpoint actually calls) and is DEFERRED to
-- a separate follow-up unit — NOT added here.
-- =============================================================================

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

GO

CREATE OR ALTER PROCEDURE dbo.ReadTimeEntries
(
    @ActorUserId BIGINT = NULL,
    @ActorIsSystemAdmin BIT = NULL,
    @ActorCanViewTeam BIT = 0
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [UserId],
        CONVERT(VARCHAR(10), [WorkDate], 120) AS [WorkDate],
        [Note]
    FROM dbo.[TimeEntry] te
    WHERE
        @ActorIsSystemAdmin = 1
        OR te.[UserId] = @ActorUserId
        OR (
            @ActorCanViewTeam = 1
            AND EXISTS (
                SELECT 1
                FROM dbo.[TimeLog] tl
                WHERE tl.[TimeEntryId] = te.[Id]
                  AND tl.[ProjectId] IN (
                    SELECT up.[ProjectId] FROM dbo.[UserProject] up WHERE up.[UserId] = @ActorUserId
                  )
            )
        )
    ORDER BY te.[WorkDate] DESC, te.[UserId] ASC;

    COMMIT TRANSACTION;
END;
GO


PRINT 'ReadTimeEntries reconciled to scoped 3-param canonical (U-039).';