-- Contract: dbo.ContractLaborReviewHold
-- Owns: dbo.ContractLaborReviewHold (U-797b1 round 2): one marker per (ProjectId, WorkDate) whose CL review email the suggestion wait is holding. The sweep and the completion re-fire act only on these markers.
-- Invariants: unique (ProjectId, WorkDate); a marker is written only when CL_SUGGESTIONS_IN_REVIEW_ENABLED is on and the email is actually held; it is deleted when the claim succeeds, or pruned when the day already has a claim; FireAttempts counts fires that did not delete the marker, and the fifth one gives up (GaveUpDatetime) and is no longer fired; mutation sprocs SET NOCOUNT ON and take no in-proc transaction control (pyodbc autocommit-off).
-- Sprocs: UpsertContractLaborReviewHold, DeleteContractLaborReviewHold, PruneContractLaborReviewHolds, ReadContractLaborReviewHoldsDue, ReadContractLaborReviewHold, RecordContractLaborReviewHoldFire
-- ─────────────────────────────────────────────────────────────────────
-- U-797b1 round 2: the marker table. Guarded CREATE, GO-terminated, no in-proc transaction control.
-- ─────────────────────────────────────────────────────────────────────

IF OBJECT_ID('dbo.ContractLaborReviewHold', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[ContractLaborReviewHold]
(
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [ProjectId] BIGINT NOT NULL,
    [WorkDate] DATE NOT NULL,
    [HeldDatetime] DATETIME2(3) NOT NULL CONSTRAINT [DF_ContractLaborReviewHold_HeldDatetime] DEFAULT GETUTCDATE(),
    [FireAttempts] INT NOT NULL CONSTRAINT [DF_ContractLaborReviewHold_FireAttempts] DEFAULT 0,
    [LastFireDatetime] DATETIME2(3) NULL,
    [GaveUpDatetime] DATETIME2(3) NULL,
    CONSTRAINT [FK_ContractLaborReviewHold_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]),
    CONSTRAINT [UQ_ContractLaborReviewHold_ProjectWorkDate] UNIQUE ([ProjectId], [WorkDate])
);
END
GO

-- Mark a (project, date) as held. Holding it again resets the marker (new wait, 0 attempts, not given up).
CREATE OR ALTER PROCEDURE dbo.UpsertContractLaborReviewHold
(
    @ProjectId BIGINT,
    @WorkDate DATE
)
AS
BEGIN
    SET NOCOUNT ON;

    -- U-797b1 round 3: holding a day again resets its marker (a new wait, no attempts, not given up), so a day
    -- re-held after a give-up is fired again instead of being skipped forever.
    UPDATE dbo.[ContractLaborReviewHold] WITH (UPDLOCK, HOLDLOCK)
    SET [HeldDatetime] = SYSUTCDATETIME(),
        [FireAttempts] = 0,
        [LastFireDatetime] = NULL,
        [GaveUpDatetime] = NULL
    WHERE [ProjectId] = @ProjectId AND [WorkDate] = @WorkDate;

    IF @@ROWCOUNT = 0
        INSERT INTO dbo.[ContractLaborReviewHold] ([ProjectId], [WorkDate])
        VALUES (@ProjectId, @WorkDate);
END
GO

CREATE OR ALTER PROCEDURE dbo.DeleteContractLaborReviewHold
(
    @ProjectId BIGINT,
    @WorkDate DATE
)
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM dbo.[ContractLaborReviewHold]
    WHERE [ProjectId] = @ProjectId AND [WorkDate] = @WorkDate;
END
GO

-- Delete the markers whose (project, date) already has a claim (a flag-off send, or a missed delete).
CREATE OR ALTER PROCEDURE dbo.PruneContractLaborReviewHolds
AS
BEGIN
    SET NOCOUNT ON;

    DELETE h
    FROM dbo.[ContractLaborReviewHold] h
    WHERE EXISTS (
        SELECT 1 FROM dbo.[ContractLaborNotification] n
        WHERE n.[ProjectId] = h.[ProjectId] AND n.[WorkDate] = h.[WorkDate])
       OR EXISTS (
        SELECT 1 FROM dbo.[ReviewNotification] rn
        WHERE rn.[Kind] = N'contract_labor' AND rn.[ProjectId] = h.[ProjectId] AND rn.[WorkDate] = h.[WorkDate]);
END
GO

-- The markers due for the sweep: held within @Days, not given up, under @MaxAttempts fires. Each carries a
-- representative submitted crew member with a line on the project (NULL when none, which the tick skips).
CREATE OR ALTER PROCEDURE dbo.ReadContractLaborReviewHoldsDue
(
    @Days INT = 7,
    @MaxAttempts INT = 5
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT
        h.[Id],
        h.[ProjectId],
        h.[WorkDate],
        h.[HeldDatetime],
        h.[FireAttempts],
        h.[GaveUpDatetime],
        (
            SELECT MIN(cl.[Id])
            FROM dbo.[ContractLabor] cl
            INNER JOIN dbo.[ContractLaborLineItem] li ON li.[ContractLaborId] = cl.[Id]
            WHERE cl.[WorkDate] = h.[WorkDate]
              AND cl.[Status] = N'submitted'
              AND li.[ProjectId] = h.[ProjectId]
        ) AS [ContractLaborId]
    FROM dbo.[ContractLaborReviewHold] h
    WHERE h.[GaveUpDatetime] IS NULL
      AND h.[FireAttempts] < @MaxAttempts
      AND h.[HeldDatetime] >= DATEADD(DAY, -@Days, GETUTCDATE())
      -- Round 3: back off between failed fires (10 min x attempts so far), so a short outage cannot use up all
      -- five attempts in five 2-minute ticks; five failed fires span about 100 minutes before giving up.
      AND (h.[LastFireDatetime] IS NULL
           OR h.[LastFireDatetime] <= DATEADD(MINUTE, -10 * h.[FireAttempts], GETUTCDATE()));
END
GO

CREATE OR ALTER PROCEDURE dbo.ReadContractLaborReviewHold
(
    @ProjectId BIGINT,
    @WorkDate DATE
)
AS
BEGIN
    SET NOCOUNT ON;

    SELECT [Id], [ProjectId], [WorkDate], [HeldDatetime], [FireAttempts], [GaveUpDatetime]
    FROM dbo.[ContractLaborReviewHold]
    WHERE [ProjectId] = @ProjectId AND [WorkDate] = @WorkDate;
END
GO

-- Count one fire of a marker. A fire that deleted the marker updates nothing and returns no row. The fire that
-- reaches @MaxAttempts sets GaveUpDatetime, and the marker is no longer fired.
CREATE OR ALTER PROCEDURE dbo.RecordContractLaborReviewHoldFire
(
    @Id BIGINT,
    @MaxAttempts INT = 5
)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE dbo.[ContractLaborReviewHold]
    SET [FireAttempts] = [FireAttempts] + 1,
        [LastFireDatetime] = GETUTCDATE(),
        [GaveUpDatetime] = CASE WHEN [FireAttempts] + 1 >= @MaxAttempts THEN GETUTCDATE() ELSE [GaveUpDatetime] END
    WHERE [Id] = @Id;

    SELECT [Id], [ProjectId], [WorkDate], [FireAttempts], [GaveUpDatetime]
    FROM dbo.[ContractLaborReviewHold]
    WHERE [Id] = @Id;
END
GO
