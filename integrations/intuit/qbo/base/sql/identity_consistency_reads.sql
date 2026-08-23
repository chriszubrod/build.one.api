-- U-306: single-round-trip identity-check reads for base/identity_consistency.py.
--
-- Each sproc replaces a family's own 2-read verify path (mapping-by-local-id,
-- then a second round trip to fetch the mapped external row just to compare
-- one QboId string) with ONE query. It also answers the REVERSE direction the
-- old 2-read path could never see for free (U-297's booked H1): whether the
-- qbo.* mapping table already binds this entity's OWN stamped QboId to a
-- DIFFERENT local row. Because the whole thing is one query, this reverse
-- check costs nothing extra — the "a both-directions verify costs strictly
-- more than the legacy path it replaces" trade-off documented in
-- identity_consistency.py's module docstring no longer applies once the read
-- itself is redesigned.
--
-- Every mapping table here (CustomerProject/VendorVendor/BillBill/
-- CustomerCustomer) shares the same 1:1 shape: (LocalId UNIQUE, QboXId
-- UNIQUE) -> the staging table's (Id PK, QboId). The forward arm (`fwd` /
-- `fwd_ext`) is provably single-row: `fwd` is keyed by the mapping table's
-- own UNIQUE LocalId constraint, `fwd_ext` by the staging table's PK.
--
-- The reverse arm CANNOT assume the same — QboId is only unique paired WITH
-- RealmId (`UQ_Qbo*_QboId_RealmId`, filtered to non-null pairs), so a
-- `QboId`-only match against the staging table could in principle return
-- more than one candidate row (a plain `LEFT JOIN` + `TOP (1)` here would
-- pick an ARBITRARY one — Codex round-1 review, correctly caught: if one
-- candidate is a genuine conflict and another isn't, an unordered `TOP (1)`
-- could silently discard the conflict). `OUTER APPLY` with an explicit
-- ORDER BY closes that: it always prefers a row whose mapping points at a
-- DIFFERENT local id (a genuine conflict) over one that doesn't, so H1 is
-- caught whenever ANY candidate row proves it, not just whichever the
-- optimizer happens to visit first. Realm-blind by design, matching every
-- existing read in this family (identity_consistency.py never threads
-- realm_id through either) — not a new asymmetry; multi-realm activation is
-- the already-booked P3 follow-up (TODO.md, U-297).
--
-- Homed here (not in each family's own qbo.<family>.sql) because these serve
-- the shared cross-family verify engine, not any one family's CRUD surface —
-- same precedent as shared/sql/dbo.access_udfs.sql for the UserCanAccess*
-- UDF family.

GO

CREATE OR ALTER PROCEDURE ReadCustomerProjectIdentityCheckByProjectId
(
    @ProjectId BIGINT,
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [fwd].[Id]        AS [MappingId],
        [fwd_ext].[QboId] AS [ForwardExternalQboId],
        [rev].[ReverseMappedLocalId]
    FROM (SELECT 1 AS [Dummy]) AS [Anchor]
    LEFT JOIN [qbo].[CustomerProject] AS [fwd]
        ON [fwd].[ProjectId] = @ProjectId
    LEFT JOIN [qbo].[Customer] AS [fwd_ext]
        ON [fwd_ext].[Id] = [fwd].[QboCustomerId]
    OUTER APPLY (
        SELECT TOP (1) [rm].[ProjectId] AS [ReverseMappedLocalId]
        FROM [qbo].[Customer] AS [re]
        JOIN [qbo].[CustomerProject] AS [rm] ON [rm].[QboCustomerId] = [re].[Id]
        WHERE [re].[QboId] = @QboId
        ORDER BY CASE WHEN [rm].[ProjectId] <> @ProjectId THEN 0 ELSE 1 END, [rm].[Id]
    ) AS [rev];

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadVendorVendorIdentityCheckByVendorId
(
    @VendorId BIGINT,
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [fwd].[Id]        AS [MappingId],
        [fwd_ext].[QboId] AS [ForwardExternalQboId],
        [rev].[ReverseMappedLocalId]
    FROM (SELECT 1 AS [Dummy]) AS [Anchor]
    LEFT JOIN [qbo].[VendorVendor] AS [fwd]
        ON [fwd].[VendorId] = @VendorId
    LEFT JOIN [qbo].[Vendor] AS [fwd_ext]
        ON [fwd_ext].[Id] = [fwd].[QboVendorId]
    OUTER APPLY (
        SELECT TOP (1) [rm].[VendorId] AS [ReverseMappedLocalId]
        FROM [qbo].[Vendor] AS [re]
        JOIN [qbo].[VendorVendor] AS [rm] ON [rm].[QboVendorId] = [re].[Id]
        WHERE [re].[QboId] = @QboId
        ORDER BY CASE WHEN [rm].[VendorId] <> @VendorId THEN 0 ELSE 1 END, [rm].[Id]
    ) AS [rev];

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadBillBillIdentityCheckByBillId
(
    @BillId BIGINT,
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [fwd].[Id]        AS [MappingId],
        [fwd_ext].[QboId] AS [ForwardExternalQboId],
        [rev].[ReverseMappedLocalId]
    FROM (SELECT 1 AS [Dummy]) AS [Anchor]
    LEFT JOIN [qbo].[BillBill] AS [fwd]
        ON [fwd].[BillId] = @BillId
    LEFT JOIN [qbo].[Bill] AS [fwd_ext]
        ON [fwd_ext].[Id] = [fwd].[QboBillId]
    OUTER APPLY (
        SELECT TOP (1) [rm].[BillId] AS [ReverseMappedLocalId]
        FROM [qbo].[Bill] AS [re]
        JOIN [qbo].[BillBill] AS [rm] ON [rm].[QboBillId] = [re].[Id]
        WHERE [re].[QboId] = @QboId
        ORDER BY CASE WHEN [rm].[BillId] <> @BillId THEN 0 ELSE 1 END, [rm].[Id]
    ) AS [rev];

    COMMIT TRANSACTION;
END;
GO


GO

CREATE OR ALTER PROCEDURE ReadCustomerCustomerIdentityCheckByCustomerId
(
    @CustomerId BIGINT,
    @QboId NVARCHAR(50)
)
AS
BEGIN
    BEGIN TRANSACTION;

    SELECT
        [fwd].[Id]        AS [MappingId],
        [fwd_ext].[QboId] AS [ForwardExternalQboId],
        [rev].[ReverseMappedLocalId]
    FROM (SELECT 1 AS [Dummy]) AS [Anchor]
    LEFT JOIN [qbo].[CustomerCustomer] AS [fwd]
        ON [fwd].[CustomerId] = @CustomerId
    LEFT JOIN [qbo].[Customer] AS [fwd_ext]
        ON [fwd_ext].[Id] = [fwd].[QboCustomerId]
    OUTER APPLY (
        SELECT TOP (1) [rm].[CustomerId] AS [ReverseMappedLocalId]
        FROM [qbo].[Customer] AS [re]
        JOIN [qbo].[CustomerCustomer] AS [rm] ON [rm].[QboCustomerId] = [re].[Id]
        WHERE [re].[QboId] = @QboId
        ORDER BY CASE WHEN [rm].[CustomerId] <> @CustomerId THEN 0 ELSE 1 END, [rm].[Id]
    ) AS [rev];

    COMMIT TRANSACTION;
END;
GO
