-- Phase 4 — Access Control Rebuild — IsAgent filter on user lists.
-- Adds @IncludeAgents BIT = 0 to ReadUsers. Default behavior changes:
-- agent users (User.IsAgent = 1) are hidden from the list. Pass
-- @IncludeAgents = 1 to surface them (e.g. for an admin Agents tab).
--
-- Idempotent (CREATE OR ALTER). Safe to re-run.
--
-- Direct lookups (ReadUserById, ReadUserByPublicId, ReadUserByFirstname,
-- ReadUserByLastname) are NOT filtered — if the caller has the Id /
-- PublicId / name, they want that specific row.

SET XACT_ABORT ON;
SET NOCOUNT ON;
GO

CREATE OR ALTER PROCEDURE ReadUsers
(
    @IncludeAgents BIT = 0
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
        [Firstname],
        [Lastname],
        [IsSystemAdmin],
        [IsAgent],
        [LastCompanyId],
        [CreatedByUserId],
        [ModifiedByUserId]
    FROM dbo.[User]
    WHERE
        @IncludeAgents = 1
        OR [IsAgent] = 0
    ORDER BY [Lastname] ASC, [Firstname] ASC;

    COMMIT TRANSACTION;
END;
GO

PRINT 'ReadUsers extended with @IncludeAgents.';
