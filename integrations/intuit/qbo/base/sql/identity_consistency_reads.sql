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
-- U-314 dropped the CustomerProject/VendorVendor/CustomerCustomer sibling
-- sprocs this file used to carry alongside Bill's (Wave-5 "trust dbo alone" --
-- those 3 families retired their qbo.* mapping tables entirely). Only Bill's
-- remains; the shape below (forward + reverse OUTER APPLY) is preserved as
-- the template for any future family that still needs a mapping-table cross-
-- check.
--
-- The reverse arm CANNOT assume single-row — QboId is only unique paired WITH
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
