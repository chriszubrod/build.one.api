-- Migration: U-326 -- project [QboId]/[RealmId] on ReadCustomerByName
-- Purpose: ReadCustomerByName's SELECT list did not project [QboId]/[RealmId],
--          unlike ReadCustomerById -- so CustomerCustomerConnector.
--          _resolve_customer_candidate's own duplicate-QboId guard was provably
--          dead against a real DB read (only _stamp_customer_identity's
--          read_by_id-based re-read ever actually carried identity in
--          production). Booked as a TODO.md follow-up by U-310 (2026-08-24).
--          CustomerRepository._from_db already getattr()s both columns
--          (proven live by the existing ReadCustomerById /
--          ReadCustomerByQboIdAndRealmId paths) -- no Python change needed.
-- Scope: SINGLE sproc, additive SELECT-list columns only. Customer is NOT on
--        the single-source-of-truth converted list (api/CLAUDE.md), so its
--        base file may carry prod drift -- do NOT blanket-reapply
--        entities/customer/sql/dbo.customer.sql, apply ONLY this file.
-- Verified live 2026-08-28 (read-only OBJECT_DEFINITION diff, normalized
-- CREATE OR ALTER -> CREATE per reference_base_vs_live_sproc_diff): all 8
-- OTHER sprocs in dbo.customer.sql are byte-identical to live prod (zero
-- drift); ReadCustomerByName is the only sproc that differs, and the sole
-- difference is exactly this SELECT-list addition -- applying this file
-- changes nothing else and clobbers no prod-only content.
-- Builders never apply prod SQL (feedback_builders_never_mutate_prod_data) --
-- this migration is STAGED ONLY, not applied, pending /em review.
-- Run with: python scripts/run_sql.py scripts/migrations/u326_customer_read_by_name_qbo_projection.sql

CREATE OR ALTER PROCEDURE ReadCustomerByName
(
    @Name NVARCHAR(50)
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
        [Name],
        [Email],
        [Phone],
        [QboId],
        [RealmId]
    FROM dbo.[Customer]
    WHERE [Name] = @Name;

    COMMIT TRANSACTION;
END;
GO
