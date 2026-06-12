GO

-- ─────────────────────────────────────────────────────────────────────
-- EmployeeLabor — internal-employee labor aggregation table.
-- Mirrors ContractLabor in shape minus the Bill-generation columns.
-- Status workflow: pending_review → ready → invoiced.
-- ─────────────────────────────────────────────────────────────────────

IF OBJECT_ID('dbo.EmployeeLabor', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[EmployeeLabor]
(
    [Id]                 BIGINT             IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]           UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
    [RowVersion]         ROWVERSION         NOT NULL,
    [CreatedDatetime]    DATETIME2(3)       NOT NULL,
    [ModifiedDatetime]   DATETIME2(3)       NULL,
    [CompanyId]          BIGINT             NOT NULL CONSTRAINT DF_EmployeeLabor_CompanyId       DEFAULT (1),
    [CreatedByUserId]    BIGINT             NOT NULL CONSTRAINT DF_EmployeeLabor_CreatedByUserId DEFAULT (17),

    [EmployeeId]         BIGINT             NOT NULL,
    [ProjectId]          BIGINT             NULL,
    [WorkDate]           DATE               NOT NULL,
    [BillingPeriodStart] DATE               NOT NULL,
    [BillingPeriodEnd]   DATE               NOT NULL,

    [TotalHours]         DECIMAL(6,2)       NOT NULL DEFAULT 0,
    [HourlyRate]         DECIMAL(18,4)      NULL,
    [Markup]             DECIMAL(18,4)      NULL,
    [TotalAmount]        DECIMAL(18,2)      NULL,
    [SubCostCodeId]      BIGINT             NULL,
    [Description]        NVARCHAR(MAX)      NULL,

    -- pending_review → ready → invoiced.
    [Status]             NVARCHAR(20)       NOT NULL DEFAULT 'pending_review',

    -- Lineage back to the TimeEntry that produced this row (when TT-sourced).
    [SourceTimeEntryId]  BIGINT             NULL,

    -- InvoiceLineItemId back-ref — set when an Invoice's line points at our
    -- aggregated EmployeeLabor row. Mirrors ContractLabor.BillLineItemId.
    [InvoiceLineItemId]  BIGINT             NULL
);
END
GO

-- Natural-key uniqueness — one row per (Employee × Project × Day × Period).
-- Phase 4 aggregation upserts on this key.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'UX_EmployeeLabor_NaturalKey' AND object_id = OBJECT_ID('dbo.EmployeeLabor'))
BEGIN
    CREATE UNIQUE INDEX [UX_EmployeeLabor_NaturalKey]
        ON [dbo].[EmployeeLabor] ([EmployeeId], [ProjectId], [WorkDate], [BillingPeriodStart]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmployeeLabor_BillingPeriod' AND object_id = OBJECT_ID('dbo.EmployeeLabor'))
BEGIN
    CREATE INDEX [IX_EmployeeLabor_BillingPeriod]
        ON [dbo].[EmployeeLabor] ([BillingPeriodStart], [Status]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeLabor_Employee')
BEGIN
    ALTER TABLE [dbo].[EmployeeLabor]
    ADD CONSTRAINT [FK_EmployeeLabor_Employee] FOREIGN KEY ([EmployeeId]) REFERENCES [dbo].[Employee]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeLabor_Project')
BEGIN
    ALTER TABLE [dbo].[EmployeeLabor]
    ADD CONSTRAINT [FK_EmployeeLabor_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeLabor_SubCostCode')
BEGIN
    ALTER TABLE [dbo].[EmployeeLabor]
    ADD CONSTRAINT [FK_EmployeeLabor_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeLabor_SourceTimeEntry')
   AND OBJECT_ID('dbo.[TimeEntry]', 'U') IS NOT NULL
BEGIN
    ALTER TABLE [dbo].[EmployeeLabor]
    ADD CONSTRAINT [FK_EmployeeLabor_SourceTimeEntry] FOREIGN KEY ([SourceTimeEntryId]) REFERENCES [dbo].[TimeEntry]([Id]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateEmployeeLabor
(
    @EmployeeId         BIGINT,
    @ProjectId          BIGINT        = NULL,
    @WorkDate           DATE,
    @BillingPeriodStart DATE,
    @BillingPeriodEnd   DATE,
    @TotalHours         DECIMAL(6,2)  = 0,
    @HourlyRate         DECIMAL(18,4) = NULL,
    @Markup             DECIMAL(18,4) = NULL,
    @TotalAmount        DECIMAL(18,2) = NULL,
    @SubCostCodeId      BIGINT        = NULL,
    @Description        NVARCHAR(MAX) = NULL,
    @Status             NVARCHAR(20)  = 'pending_review',
    @SourceTimeEntryId  BIGINT        = NULL,
    @CreatedByUserId    BIGINT        = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[EmployeeLabor]
        ([CreatedDatetime], [ModifiedDatetime], [EmployeeId], [ProjectId], [WorkDate],
         [BillingPeriodStart], [BillingPeriodEnd], [TotalHours], [HourlyRate], [Markup],
         [TotalAmount], [SubCostCodeId], [Description], [Status], [SourceTimeEntryId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EmployeeId], INSERTED.[ProjectId],
        CONVERT(VARCHAR(10), INSERTED.[WorkDate], 120)            AS [WorkDate],
        CONVERT(VARCHAR(10), INSERTED.[BillingPeriodStart], 120)  AS [BillingPeriodStart],
        CONVERT(VARCHAR(10), INSERTED.[BillingPeriodEnd], 120)    AS [BillingPeriodEnd],
        INSERTED.[TotalHours], INSERTED.[HourlyRate], INSERTED.[Markup], INSERTED.[TotalAmount],
        INSERTED.[SubCostCodeId], INSERTED.[Description], INSERTED.[Status],
        INSERTED.[SourceTimeEntryId], INSERTED.[InvoiceLineItemId]
    VALUES (@Now, @Now, @EmployeeId, @ProjectId, @WorkDate,
            @BillingPeriodStart, @BillingPeriodEnd, @TotalHours, @HourlyRate, @Markup,
            @TotalAmount, @SubCostCodeId, @Description, @Status, @SourceTimeEntryId,
            COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeLaborById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [EmployeeId], [ProjectId],
        CONVERT(VARCHAR(10), [WorkDate], 120)            AS [WorkDate],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120)  AS [BillingPeriodStart],
        CONVERT(VARCHAR(10), [BillingPeriodEnd], 120)    AS [BillingPeriodEnd],
        [TotalHours], [HourlyRate], [Markup], [TotalAmount],
        [SubCostCodeId], [Description], [Status],
        [SourceTimeEntryId], [InvoiceLineItemId]
    FROM dbo.[EmployeeLabor]
    WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeLaborByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [EmployeeId], [ProjectId],
        CONVERT(VARCHAR(10), [WorkDate], 120)            AS [WorkDate],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120)  AS [BillingPeriodStart],
        CONVERT(VARCHAR(10), [BillingPeriodEnd], 120)    AS [BillingPeriodEnd],
        [TotalHours], [HourlyRate], [Markup], [TotalAmount],
        [SubCostCodeId], [Description], [Status],
        [SourceTimeEntryId], [InvoiceLineItemId]
    FROM dbo.[EmployeeLabor]
    WHERE [PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeLaborByNaturalKey
(
    @EmployeeId         BIGINT,
    @ProjectId          BIGINT = NULL,
    @WorkDate           DATE,
    @BillingPeriodStart DATE
)
AS
BEGIN
    SELECT TOP 1
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [EmployeeId], [ProjectId],
        CONVERT(VARCHAR(10), [WorkDate], 120)            AS [WorkDate],
        CONVERT(VARCHAR(10), [BillingPeriodStart], 120)  AS [BillingPeriodStart],
        CONVERT(VARCHAR(10), [BillingPeriodEnd], 120)    AS [BillingPeriodEnd],
        [TotalHours], [HourlyRate], [Markup], [TotalAmount],
        [SubCostCodeId], [Description], [Status],
        [SourceTimeEntryId], [InvoiceLineItemId]
    FROM dbo.[EmployeeLabor]
    WHERE [EmployeeId] = @EmployeeId
      AND (
            (@ProjectId IS NULL AND [ProjectId] IS NULL)
         OR (@ProjectId IS NOT NULL AND [ProjectId] = @ProjectId)
          )
      AND [WorkDate] = @WorkDate
      AND [BillingPeriodStart] = @BillingPeriodStart;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeLaborsByBillingPeriod
(
    @BillingPeriodStart DATE
)
AS
BEGIN
    SELECT
        el.[Id], el.[PublicId], el.[RowVersion],
        CONVERT(VARCHAR(19), el.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), el.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        el.[EmployeeId], el.[ProjectId],
        CONVERT(VARCHAR(10), el.[WorkDate], 120)            AS [WorkDate],
        CONVERT(VARCHAR(10), el.[BillingPeriodStart], 120)  AS [BillingPeriodStart],
        CONVERT(VARCHAR(10), el.[BillingPeriodEnd], 120)    AS [BillingPeriodEnd],
        el.[TotalHours], el.[HourlyRate], el.[Markup], el.[TotalAmount],
        el.[SubCostCodeId], el.[Description], el.[Status],
        el.[SourceTimeEntryId], el.[InvoiceLineItemId],
        e.[Firstname] + ' ' + e.[Lastname] AS EmployeeName,
        p.[Name]                            AS ProjectName
    FROM dbo.[EmployeeLabor] el
    LEFT JOIN dbo.[Employee] e ON e.[Id] = el.[EmployeeId]
    LEFT JOIN dbo.[Project]  p ON p.[Id] = el.[ProjectId]
    WHERE el.[BillingPeriodStart] = @BillingPeriodStart
    ORDER BY e.[Lastname], e.[Firstname], el.[WorkDate];
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeLaborsByStatus
(
    @Status             NVARCHAR(20),
    @BillingPeriodStart DATE = NULL
)
AS
BEGIN
    SELECT
        el.[Id], el.[PublicId], el.[RowVersion],
        CONVERT(VARCHAR(19), el.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), el.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        el.[EmployeeId], el.[ProjectId],
        CONVERT(VARCHAR(10), el.[WorkDate], 120)            AS [WorkDate],
        CONVERT(VARCHAR(10), el.[BillingPeriodStart], 120)  AS [BillingPeriodStart],
        CONVERT(VARCHAR(10), el.[BillingPeriodEnd], 120)    AS [BillingPeriodEnd],
        el.[TotalHours], el.[HourlyRate], el.[Markup], el.[TotalAmount],
        el.[SubCostCodeId], el.[Description], el.[Status],
        el.[SourceTimeEntryId], el.[InvoiceLineItemId]
    FROM dbo.[EmployeeLabor] el
    WHERE el.[Status] = @Status
      AND (@BillingPeriodStart IS NULL OR el.[BillingPeriodStart] = @BillingPeriodStart)
    ORDER BY el.[BillingPeriodStart], el.[WorkDate];
END;
GO


CREATE OR ALTER PROCEDURE UpdateEmployeeLaborById
(
    @Id                 BIGINT,
    @RowVersion         BINARY(8),
    @ProjectId          BIGINT        = NULL,
    @TotalHours         DECIMAL(6,2)  = NULL,
    @HourlyRate         DECIMAL(18,4) = NULL,
    @Markup             DECIMAL(18,4) = NULL,
    @TotalAmount        DECIMAL(18,2) = NULL,
    @SubCostCodeId      BIGINT        = NULL,
    @Description        NVARCHAR(MAX) = NULL,
    @Status             NVARCHAR(20)  = NULL,
    @InvoiceLineItemId  BIGINT        = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[EmployeeLabor] WHERE [Id] = @Id)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('EmployeeLabor not found.', 16, 1);
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM dbo.[EmployeeLabor] WHERE [Id] = @Id AND [RowVersion] = @RowVersion)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Concurrency conflict: EmployeeLabor has been modified by another user.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- CASE WHEN preserve-on-NULL for every column. InvoiceLineItemId in
    -- particular MUST preserve — never null it out from an Update path or
    -- we'd orphan the Invoice line. To clear it, soft-delete + recreate.
    UPDATE dbo.[EmployeeLabor]
    SET
        [ModifiedDatetime] = @Now,
        [ProjectId]        = CASE WHEN @ProjectId         IS NULL THEN [ProjectId]         ELSE @ProjectId         END,
        [TotalHours]       = CASE WHEN @TotalHours        IS NULL THEN [TotalHours]        ELSE @TotalHours        END,
        [HourlyRate]       = CASE WHEN @HourlyRate        IS NULL THEN [HourlyRate]        ELSE @HourlyRate        END,
        [Markup]           = CASE WHEN @Markup            IS NULL THEN [Markup]            ELSE @Markup            END,
        [TotalAmount]      = CASE WHEN @TotalAmount       IS NULL THEN [TotalAmount]       ELSE @TotalAmount       END,
        [SubCostCodeId]    = CASE WHEN @SubCostCodeId     IS NULL THEN [SubCostCodeId]     ELSE @SubCostCodeId     END,
        [Description]      = CASE WHEN @Description       IS NULL THEN [Description]       ELSE @Description       END,
        [Status]           = CASE WHEN @Status            IS NULL THEN [Status]            ELSE @Status            END,
        [InvoiceLineItemId] = CASE WHEN @InvoiceLineItemId IS NULL THEN [InvoiceLineItemId] ELSE @InvoiceLineItemId END
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EmployeeId], INSERTED.[ProjectId],
        CONVERT(VARCHAR(10), INSERTED.[WorkDate], 120)            AS [WorkDate],
        CONVERT(VARCHAR(10), INSERTED.[BillingPeriodStart], 120)  AS [BillingPeriodStart],
        CONVERT(VARCHAR(10), INSERTED.[BillingPeriodEnd], 120)    AS [BillingPeriodEnd],
        INSERTED.[TotalHours], INSERTED.[HourlyRate], INSERTED.[Markup], INSERTED.[TotalAmount],
        INSERTED.[SubCostCodeId], INSERTED.[Description], INSERTED.[Status],
        INSERTED.[SourceTimeEntryId], INSERTED.[InvoiceLineItemId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteEmployeeLaborById
(
    @Id BIGINT
)
AS
BEGIN
    BEGIN TRANSACTION;
    -- Cascade child line items first (no FK CASCADE per project convention).
    DELETE FROM dbo.[EmployeeLaborLineItem] WHERE [EmployeeLaborId] = @Id;
    DELETE FROM dbo.[EmployeeLabor]         WHERE [Id] = @Id;
    COMMIT TRANSACTION;
END;
GO


-- ─────────────────────────────────────────────────────────────────────
-- EmployeeLaborLineItem — child line items of EmployeeLabor.
-- ─────────────────────────────────────────────────────────────────────

IF OBJECT_ID('dbo.EmployeeLaborLineItem', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[EmployeeLaborLineItem]
(
    [Id]                 BIGINT             IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId]           UNIQUEIDENTIFIER   NOT NULL DEFAULT NEWID(),
    [RowVersion]         ROWVERSION         NOT NULL,
    [CreatedDatetime]    DATETIME2(3)       NOT NULL,
    [ModifiedDatetime]   DATETIME2(3)       NULL,
    [CreatedByUserId]    BIGINT             NOT NULL CONSTRAINT DF_EmployeeLaborLineItem_CreatedByUserId DEFAULT (17),

    [EmployeeLaborId]    BIGINT             NOT NULL,
    [LineDate]           DATE               NULL,
    [ProjectId]          BIGINT             NULL,
    [SubCostCodeId]      BIGINT             NULL,
    [Description]        NVARCHAR(MAX)      NULL,

    [Hours]              DECIMAL(6,2)       NULL,
    [Rate]               DECIMAL(18,4)      NULL,
    [Markup]             DECIMAL(18,4)      NULL,
    [Price]              DECIMAL(18,2)      NULL,
    [IsBillable]         BIT                NOT NULL DEFAULT 1,
    [IsOverhead]         BIT                NOT NULL DEFAULT 0,

    [InvoiceLineItemId]  BIGINT             NULL
);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeLaborLineItem_EmployeeLabor')
BEGIN
    ALTER TABLE [dbo].[EmployeeLaborLineItem]
    ADD CONSTRAINT [FK_EmployeeLaborLineItem_EmployeeLabor]
        FOREIGN KEY ([EmployeeLaborId]) REFERENCES [dbo].[EmployeeLabor]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeLaborLineItem_Project')
BEGIN
    ALTER TABLE [dbo].[EmployeeLaborLineItem]
    ADD CONSTRAINT [FK_EmployeeLaborLineItem_Project] FOREIGN KEY ([ProjectId]) REFERENCES [dbo].[Project]([Id]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = 'FK_EmployeeLaborLineItem_SubCostCode')
BEGIN
    ALTER TABLE [dbo].[EmployeeLaborLineItem]
    ADD CONSTRAINT [FK_EmployeeLaborLineItem_SubCostCode] FOREIGN KEY ([SubCostCodeId]) REFERENCES [dbo].[SubCostCode]([Id]);
END
GO

-- Baseline + budget-variance indexes (2026-06-12): the line table shipped
-- with zero nonclustered indexes. ProjectId feeds ReadBudgetVarianceByProjectId;
-- EmployeeLaborId/PublicId are the standard line-table pair every sibling has.
-- Mirrored in scripts/migrations/budget_variance_support_indexes.sql.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmployeeLaborLineItem_ProjectId' AND object_id = OBJECT_ID('dbo.EmployeeLaborLineItem'))
BEGIN
    CREATE INDEX [IX_EmployeeLaborLineItem_ProjectId] ON [dbo].[EmployeeLaborLineItem] ([ProjectId])
        INCLUDE ([SubCostCodeId], [Hours], [Rate], [IsOverhead]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmployeeLaborLineItem_EmployeeLaborId' AND object_id = OBJECT_ID('dbo.EmployeeLaborLineItem'))
BEGIN
    CREATE INDEX [IX_EmployeeLaborLineItem_EmployeeLaborId] ON [dbo].[EmployeeLaborLineItem] ([EmployeeLaborId]);
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'IX_EmployeeLaborLineItem_PublicId' AND object_id = OBJECT_ID('dbo.EmployeeLaborLineItem'))
BEGIN
    CREATE INDEX [IX_EmployeeLaborLineItem_PublicId] ON [dbo].[EmployeeLaborLineItem] ([PublicId]);
END
GO


GO

CREATE OR ALTER PROCEDURE CreateEmployeeLaborLineItem
(
    @EmployeeLaborId   BIGINT,
    @LineDate          DATE          = NULL,
    @ProjectId         BIGINT        = NULL,
    @SubCostCodeId     BIGINT        = NULL,
    @Description       NVARCHAR(MAX) = NULL,
    @Hours             DECIMAL(6,2)  = NULL,
    @Rate              DECIMAL(18,4) = NULL,
    @Markup            DECIMAL(18,4) = NULL,
    @Price             DECIMAL(18,2) = NULL,
    @IsBillable        BIT           = 1,
    @IsOverhead        BIT           = 0,
    @InvoiceLineItemId BIGINT        = NULL,
    @CreatedByUserId   BIGINT        = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;
    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[EmployeeLaborLineItem]
        ([CreatedDatetime], [ModifiedDatetime], [EmployeeLaborId], [LineDate], [ProjectId],
         [SubCostCodeId], [Description], [Hours], [Rate], [Markup], [Price],
         [IsBillable], [IsOverhead], [InvoiceLineItemId], [CreatedByUserId])
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EmployeeLaborId],
        CONVERT(VARCHAR(10), INSERTED.[LineDate], 120) AS [LineDate],
        INSERTED.[ProjectId], INSERTED.[SubCostCodeId], INSERTED.[Description],
        INSERTED.[Hours], INSERTED.[Rate], INSERTED.[Markup], INSERTED.[Price],
        INSERTED.[IsBillable], INSERTED.[IsOverhead], INSERTED.[InvoiceLineItemId]
    VALUES (@Now, @Now, @EmployeeLaborId, @LineDate, @ProjectId,
            @SubCostCodeId, @Description, @Hours, @Rate, @Markup, @Price,
            @IsBillable, @IsOverhead, @InvoiceLineItemId, COALESCE(@CreatedByUserId, 17));

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeLaborLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [EmployeeLaborId],
        CONVERT(VARCHAR(10), [LineDate], 120) AS [LineDate],
        [ProjectId], [SubCostCodeId], [Description], [Hours], [Rate], [Markup], [Price],
        [IsBillable], [IsOverhead], [InvoiceLineItemId]
    FROM dbo.[EmployeeLaborLineItem]
    WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeLaborLineItemByPublicId
(
    @PublicId UNIQUEIDENTIFIER
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [EmployeeLaborId],
        CONVERT(VARCHAR(10), [LineDate], 120) AS [LineDate],
        [ProjectId], [SubCostCodeId], [Description], [Hours], [Rate], [Markup], [Price],
        [IsBillable], [IsOverhead], [InvoiceLineItemId]
    FROM dbo.[EmployeeLaborLineItem]
    WHERE [PublicId] = @PublicId;
END;
GO


CREATE OR ALTER PROCEDURE ReadEmployeeLaborLineItemsByEmployeeLaborId
(
    @EmployeeLaborId BIGINT
)
AS
BEGIN
    SELECT
        [Id], [PublicId], [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [EmployeeLaborId],
        CONVERT(VARCHAR(10), [LineDate], 120) AS [LineDate],
        [ProjectId], [SubCostCodeId], [Description], [Hours], [Rate], [Markup], [Price],
        [IsBillable], [IsOverhead], [InvoiceLineItemId]
    FROM dbo.[EmployeeLaborLineItem]
    WHERE [EmployeeLaborId] = @EmployeeLaborId
    ORDER BY [LineDate], [Id];
END;
GO


CREATE OR ALTER PROCEDURE UpdateEmployeeLaborLineItemById
(
    @Id                BIGINT,
    @RowVersion        BINARY(8),
    @LineDate          DATE          = NULL,
    @ProjectId         BIGINT        = NULL,
    @SubCostCodeId     BIGINT        = NULL,
    @Description       NVARCHAR(MAX) = NULL,
    @Hours             DECIMAL(6,2)  = NULL,
    @Rate              DECIMAL(18,4) = NULL,
    @Markup            DECIMAL(18,4) = NULL,
    @Price             DECIMAL(18,2) = NULL,
    @IsBillable        BIT           = NULL,
    @IsOverhead        BIT           = NULL,
    @InvoiceLineItemId BIGINT        = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    IF NOT EXISTS (SELECT 1 FROM dbo.[EmployeeLaborLineItem] WHERE [Id] = @Id)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('EmployeeLaborLineItem not found.', 16, 1);
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM dbo.[EmployeeLaborLineItem] WHERE [Id] = @Id AND [RowVersion] = @RowVersion)
    BEGIN
        ROLLBACK TRANSACTION;
        RAISERROR('Concurrency conflict: EmployeeLaborLineItem has been modified by another user.', 16, 1);
        RETURN;
    END

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    -- ALL columns CASE WHEN preserve-on-NULL — InvoiceLineItemId especially
    -- (same gotcha as ContractLaborLineItem.BillLineItemId).
    UPDATE dbo.[EmployeeLaborLineItem]
    SET
        [ModifiedDatetime]  = @Now,
        [LineDate]          = CASE WHEN @LineDate          IS NULL THEN [LineDate]          ELSE @LineDate          END,
        [ProjectId]         = CASE WHEN @ProjectId         IS NULL THEN [ProjectId]         ELSE @ProjectId         END,
        [SubCostCodeId]     = CASE WHEN @SubCostCodeId     IS NULL THEN [SubCostCodeId]     ELSE @SubCostCodeId     END,
        [Description]       = CASE WHEN @Description       IS NULL THEN [Description]       ELSE @Description       END,
        [Hours]             = CASE WHEN @Hours             IS NULL THEN [Hours]             ELSE @Hours             END,
        [Rate]              = CASE WHEN @Rate              IS NULL THEN [Rate]              ELSE @Rate              END,
        [Markup]            = CASE WHEN @Markup            IS NULL THEN [Markup]            ELSE @Markup            END,
        [Price]             = CASE WHEN @Price             IS NULL THEN [Price]             ELSE @Price             END,
        [IsBillable]        = CASE WHEN @IsBillable        IS NULL THEN [IsBillable]        ELSE @IsBillable        END,
        [IsOverhead]        = CASE WHEN @IsOverhead        IS NULL THEN [IsOverhead]        ELSE @IsOverhead        END,
        [InvoiceLineItemId] = CASE WHEN @InvoiceLineItemId IS NULL THEN [InvoiceLineItemId] ELSE @InvoiceLineItemId END
    OUTPUT
        INSERTED.[Id], INSERTED.[PublicId], INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120)  AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[EmployeeLaborId],
        CONVERT(VARCHAR(10), INSERTED.[LineDate], 120) AS [LineDate],
        INSERTED.[ProjectId], INSERTED.[SubCostCodeId], INSERTED.[Description],
        INSERTED.[Hours], INSERTED.[Rate], INSERTED.[Markup], INSERTED.[Price],
        INSERTED.[IsBillable], INSERTED.[IsOverhead], INSERTED.[InvoiceLineItemId]
    WHERE [Id] = @Id AND [RowVersion] = @RowVersion;

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE DeleteEmployeeLaborLineItemById
(
    @Id BIGINT
)
AS
BEGIN
    DELETE FROM dbo.[EmployeeLaborLineItem] WHERE [Id] = @Id;
END;
GO


CREATE OR ALTER PROCEDURE DeleteEmployeeLaborLineItemsByEmployeeLaborId
(
    @EmployeeLaborId BIGINT
)
AS
BEGIN
    DELETE FROM dbo.[EmployeeLaborLineItem] WHERE [EmployeeLaborId] = @EmployeeLaborId;
END;
GO
