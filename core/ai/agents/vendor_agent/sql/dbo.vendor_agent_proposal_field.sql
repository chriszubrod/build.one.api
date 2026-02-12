-- =============================================================================
-- VendorAgent Tables with Stored Procedures (dbo schema)
-- =============================================================================
--
-- This schema supports the VendorAgent system:
-- - VendorAgentProposalField: Individual field changes within a proposal
--
-- =============================================================================



-- =============================================================================
-- VendorAgentProposalField: Individual field changes within a proposal
-- =============================================================================

IF OBJECT_ID('dbo.VendorAgentProposalField', 'U') IS NULL
BEGIN
CREATE TABLE [dbo].[VendorAgentProposalField]
(
    -- Standard columns
    [Id] BIGINT IDENTITY(1,1) PRIMARY KEY NOT NULL,
    [PublicId] UNIQUEIDENTIFIER NOT NULL DEFAULT NEWID(),
    [RowVersion] ROWVERSION NOT NULL,
    [CreatedDatetime] DATETIME2(3) NOT NULL,
    [ModifiedDatetime] DATETIME2(3) NULL,

    -- Relationship
    [ProposalId] BIGINT NOT NULL,                 -- FK to VendorAgentProposal

    -- Field change details
    [FieldName] VARCHAR(100) NOT NULL,            -- e.g., 'vendor_type_id', 'name', 'abbreviation'
    [OldValue] NVARCHAR(MAX) NULL,                -- Current value (NULL if not set)
    [NewValue] NVARCHAR(MAX) NULL,                -- Proposed value
    [OldDisplayValue] NVARCHAR(500) NULL,         -- Human-readable current (e.g., vendor type name)
    [NewDisplayValue] NVARCHAR(500) NULL,         -- Human-readable proposed

    -- Field-level reasoning (optional, for complex changes)
    [FieldReasoning] NVARCHAR(MAX) NULL,

    CONSTRAINT [UQ_VendorAgentProposalField_PublicId] UNIQUE ([PublicId]),
    CONSTRAINT [FK_VendorAgentProposalField_Proposal] FOREIGN KEY ([ProposalId])
        REFERENCES [dbo].[VendorAgentProposal]([Id]) ON DELETE CASCADE
);
END
GO

CREATE INDEX IX_VendorAgentProposalField_ProposalId ON [dbo].[VendorAgentProposalField]([ProposalId]);
GO




-- =============================================================================
-- Stored Procedures: VendorAgentProposalField
-- =============================================================================

CREATE OR ALTER PROCEDURE CreateVendorAgentProposalField
(
    @ProposalId BIGINT,
    @FieldName VARCHAR(100),
    @OldValue NVARCHAR(MAX) = NULL,
    @NewValue NVARCHAR(MAX) = NULL,
    @OldDisplayValue NVARCHAR(500) = NULL,
    @NewDisplayValue NVARCHAR(500) = NULL,
    @FieldReasoning NVARCHAR(MAX) = NULL
)
AS
BEGIN
    BEGIN TRANSACTION;

    DECLARE @Now DATETIME2(3) = SYSUTCDATETIME();

    INSERT INTO dbo.[VendorAgentProposalField] (
        [CreatedDatetime], [ModifiedDatetime], [ProposalId], [FieldName],
        [OldValue], [NewValue], [OldDisplayValue], [NewDisplayValue], [FieldReasoning]
    )
    OUTPUT
        INSERTED.[Id],
        INSERTED.[PublicId],
        INSERTED.[RowVersion],
        CONVERT(VARCHAR(19), INSERTED.[CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), INSERTED.[ModifiedDatetime], 120) AS [ModifiedDatetime],
        INSERTED.[ProposalId],
        INSERTED.[FieldName],
        INSERTED.[OldValue],
        INSERTED.[NewValue],
        INSERTED.[OldDisplayValue],
        INSERTED.[NewDisplayValue],
        INSERTED.[FieldReasoning]
    VALUES (
        @Now, @Now, @ProposalId, @FieldName,
        @OldValue, @NewValue, @OldDisplayValue, @NewDisplayValue, @FieldReasoning
    );

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorAgentProposalFields
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [Id],
        [PublicId],
        [RowVersion],
        CONVERT(VARCHAR(19), [CreatedDatetime], 120) AS [CreatedDatetime],
        CONVERT(VARCHAR(19), [ModifiedDatetime], 120) AS [ModifiedDatetime],
        [ProposalId],
        [FieldName],
        [OldValue],
        [NewValue],
        [OldDisplayValue],
        [NewDisplayValue],
        [FieldReasoning]
    FROM dbo.[VendorAgentProposalField]
    ORDER BY [Id];

    COMMIT TRANSACTION;
END;
GO


CREATE OR ALTER PROCEDURE ReadVendorAgentProposalFieldById
(
    @ProposalId BIGINT
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
        [ProposalId],
        [FieldName],
        [OldValue],
        [NewValue],
        [OldDisplayValue],
        [NewDisplayValue],
        [FieldReasoning]
    FROM dbo.[VendorAgentProposalField]
    WHERE [ProposalId] = @ProposalId
    ORDER BY [Id];

    COMMIT TRANSACTION;
END;
GO

-- By project name
SELECT li.Id, li.ContractLaborId, li.ProjectId, li.Markup, p.Name AS ProjectName
FROM dbo.ContractLaborLineItem li
INNER JOIN dbo.Project p ON li.ProjectId = p.Id
WHERE p.Name = 'MR2-MAIN' OR p.Abbreviation = 'MR2-MAIN';

-- Or if the project display is something like "MR2 - 1577 Moran Rd", match on abbreviation:
SELECT li.Id, li.ContractLaborId, li.ProjectId, li.Markup, p.Abbreviation, p.Name
FROM dbo.ContractLaborLineItem li
INNER JOIN dbo.Project p ON li.ProjectId = p.Id
WHERE p.Abbreviation = 'MR2';  -- adjust if MR2-MAIN is the full name/abbrev


SELECT *
FROM dbo.ContractLaborLineItem li
WHERE li.IsBillable = 1;


UPDATE dbo.ContractLaborLineItem
SET Price = ([Hours] * [Rate])
WHERE Id IN (4,5,6,7,62,63,64,65);


SELECT 
    li.Id,
    li.ContractLaborId,
    li.Hours,
    li.Rate,
    li.Markup,
    li.IsBillable,
    li.Price AS CurrentPrice,
    ROUND((li.Hours / 8.0 * li.Rate) * (1 + ISNULL(li.Markup, 0)), 2) AS CalculatedPrice,
    ROUND(li.Price - (li.Hours / 8.0 * li.Rate) * (1 + ISNULL(li.Markup, 0)), 2) AS Difference
FROM dbo.ContractLaborLineItem li
WHERE li.Hours IS NOT NULL 
  AND li.Rate IS NOT NULL;


UPDATE li
SET li.Price = ROUND((li.Hours / 8.0 * li.Rate) * (1 + ISNULL(li.Markup, 0)), 2)
FROM dbo.ContractLaborLineItem li
WHERE li.Hours IS NOT NULL
  AND li.Rate IS NOT NULL
  AND (
      li.Price IS NULL
      OR li.Price <> ROUND((li.Hours / 8.0 * li.Rate) * (1 + ISNULL(li.Markup, 0)), 2)
  );

SELECT * FROM dbo.BillLineItem;