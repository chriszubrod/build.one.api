-- =========================================================================
-- u218d_qbo_staging_dedupe.sql
--
-- One-shot dedupe of duplicate (QboId, RealmId) rows in qbo.Purchase and
-- qbo.Vendor staging tables before U-218d unique indexes land.
--
-- INCIDENT: a single QBO purchase pull double-ran, inserting 17 consecutive
-- Id pairs in block 11265-11305 (QboIds 69248-69339) with identical content.
-- One Vendor duplicate (Id 1140 vs keeper 1139) shares the same root cause.
-- Bill, Customer, Item, Account, Term, and ReimburseCharge were unaffected.
--
-- CASCADE HAZARD — do NOT "simplify" survivor selection to keep-MIN or keep-MAX:
--   FK_PurchaseExpense_QboPurchase and FK_QboPurchaseLine_QboPurchase are both
--   ON DELETE CASCADE; PurchaseLineExpenseLineItem cascades one level deeper.
--   qbo.PurchaseExpense sits on the MIN Id in 11 duplicate groups and the MAX
--   Id in 5 others. Measured cascade damage: keep-MIN silently destroys 5
--   PurchaseExpense mappings; keep-MAX silently destroys 11 — neither raises.
--   The correct rule is positional-by-mapping: keep whichever member holds the
--   qbo.PurchaseExpense row (or SyncToken then MIN Id when neither has one).
--
-- Orphans without FK: dbo.ExpenseCodingItem.QboPurchaseId and
-- qbo.VendorVendor.QboVendorId — delete coding items explicitly before parent
-- delete (prod already carries dangling ExpenseCodingItem rows from past pulls).
--
-- Run: python scripts/run_sql.py scripts/migrations/u218d_qbo_staging_dedupe.sql
-- Apply BEFORE u218d_qbo_staging_unique_indexes.sql
-- =========================================================================

SET XACT_ABORT ON;
-- NB: nested BEGIN/COMMIT only adjusts @@TRANCOUNT; run_sql.py get_connection
-- commits the outer connection on successful exit — that is the durable commit.
BEGIN TRANSACTION;

DECLARE @DoomedPurchaseIds TABLE (Id BIGINT PRIMARY KEY);
INSERT INTO @DoomedPurchaseIds (Id) VALUES
    (11265), (11268), (11269), (11272), (11274), (11276), (11277), (11280),
    (11282), (11284), (11285), (11288), (11290), (11292), (11294), (11303), (11304);

DECLARE @DoomedVendorId BIGINT = 1140;

-- -------------------------------------------------------------------------
-- Pre-flight guards (RAISERROR + ROLLBACK — scripts/run_sql.py ignores PRINT)
-- -------------------------------------------------------------------------

-- 1) No doomed Purchase may hold a qbo.PurchaseExpense row (survivor carries mapping).
IF EXISTS (
    SELECT 1
    FROM qbo.PurchaseExpense pe
    JOIN @DoomedPurchaseIds d ON d.Id = pe.QboPurchaseId
)
BEGIN
    RAISERROR('u218d dedupe: doomed Purchase Id holds qbo.PurchaseExpense — aborting.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

-- 2) No PurchaseLineExpenseLineItem reachable through a doomed Purchase's lines.
IF EXISTS (
    SELECT 1
    FROM qbo.PurchaseLine pl
    JOIN @DoomedPurchaseIds d ON d.Id = pl.QboPurchaseId
    JOIN qbo.PurchaseLineExpenseLineItem pleli ON pleli.QboPurchaseLineId = pl.Id
)
BEGIN
    RAISERROR('u218d dedupe: doomed Purchase has PurchaseLineExpenseLineItem — aborting.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

-- 3) No doomed ExpenseCodingItem may carry human/agent work (Status beyond
-- pending, or any confirm/write/claim/suggest/flag column set).
-- Prod allowlist Ids 101,104,105,110,111,113,114,119: survivor parity verified —
-- six have a byte-equivalent ECI on the survivor; for QboIds 69265 and 69266 the
-- survivor legitimately has none because its line was already recoded off 58999 and
-- the doomed row holds the stale pre-recode snapshot.
IF EXISTS (
    SELECT 1
    FROM dbo.ExpenseCodingItem eci
    JOIN @DoomedPurchaseIds d ON d.Id = eci.QboPurchaseId
    WHERE eci.Id NOT IN (101, 104, 105, 110, 111, 113, 114, 119)
      AND (
           eci.Status <> N'pending'
        OR eci.ConfirmedProjectId IS NOT NULL
        OR eci.ConfirmedSubCostCodeId IS NOT NULL
        OR eci.ConfirmedDescription IS NOT NULL
        OR eci.ConfirmedAt IS NOT NULL
        OR eci.ConfirmedByUserId IS NOT NULL
        OR eci.WasOverridden IS NOT NULL
        OR eci.WrittenAt IS NOT NULL
        OR eci.WriteError IS NOT NULL
        OR eci.ClaimedByUserId IS NOT NULL
        OR eci.ClaimedAt IS NOT NULL
        OR eci.FlagReason IS NOT NULL
        OR eci.FlaggedAt IS NOT NULL
        OR eci.SuggestedProjectId IS NOT NULL
        OR eci.SuggestedSubCostCodeId IS NOT NULL
        OR eci.SuggestedDescription IS NOT NULL
        OR eci.SuggestionSource IS NOT NULL
        OR eci.SuggestionConfidence IS NOT NULL
        OR eci.SuggestedAt IS NOT NULL
      )
)
BEGIN
    RAISERROR('u218d dedupe: doomed ExpenseCodingItem carries human/agent work — aborting.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

-- 4) Every duplicate Purchase group touched by @DoomedPurchaseIds must leave exactly one survivor.
-- NB: the doomed-membership test is a LEFT JOIN, not a correlated EXISTS inside
-- SUM(). SQL Server rejects a subquery within an aggregate ("Cannot perform an
-- aggregate function on an expression containing an aggregate or a subquery"),
-- and PARSEONLY does NOT catch it — only NOEXEC/compile does. Keep the join form.
IF EXISTS (
    SELECT p.QboId, p.RealmId
    FROM qbo.Purchase p
    LEFT JOIN @DoomedPurchaseIds dm ON dm.Id = p.Id
    WHERE EXISTS (
        SELECT 1
        FROM qbo.Purchase p2
        JOIN @DoomedPurchaseIds d ON d.Id = p2.Id
        WHERE p2.QboId = p.QboId AND p2.RealmId = p.RealmId
    )
    GROUP BY p.QboId, p.RealmId
    HAVING SUM(CASE WHEN dm.Id IS NULL THEN 1 ELSE 0 END) <> 1
)
BEGIN
    RAISERROR('u218d dedupe: Purchase duplicate group would not leave exactly one survivor — aborting.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

-- 5) Doomed Vendor 1140 must not hold qbo.VendorVendor (keeper 1139 does).
IF EXISTS (
    SELECT 1
    FROM qbo.VendorVendor vv
    WHERE vv.QboVendorId = @DoomedVendorId
)
BEGIN
    RAISERROR('u218d dedupe: doomed Vendor Id 1140 holds VendorVendor mapping — aborting.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

-- Vendor duplicate group: exactly one non-doomed survivor when doomed row still present.
IF EXISTS (SELECT 1 FROM qbo.Vendor WHERE Id = @DoomedVendorId)
BEGIN
    DECLARE @VendorDupQboId NVARCHAR(50);
    DECLARE @VendorDupRealmId NVARCHAR(50);
    SELECT @VendorDupQboId = QboId, @VendorDupRealmId = RealmId
    FROM qbo.Vendor WHERE Id = @DoomedVendorId;

    IF (
        SELECT COUNT(*)
        FROM qbo.Vendor v
        WHERE v.QboId = @VendorDupQboId AND v.RealmId = @VendorDupRealmId
          AND v.Id <> @DoomedVendorId
    ) <> 1
    BEGIN
        RAISERROR('u218d dedupe: Vendor duplicate group would not leave exactly one survivor — aborting.', 16, 1);
        ROLLBACK TRANSACTION;
        RETURN;
    END;
END;

-- -------------------------------------------------------------------------
-- Mutations (no-ops when already applied — idempotent re-run)
-- -------------------------------------------------------------------------

-- ExpenseCodingItem has no FK to qbo.Purchase; delete explicitly before CASCADE parent delete.
DELETE eci
FROM dbo.ExpenseCodingItem eci
JOIN @DoomedPurchaseIds d ON d.Id = eci.QboPurchaseId;

DELETE p
FROM qbo.Purchase p
JOIN @DoomedPurchaseIds d ON d.Id = p.Id;

DELETE FROM qbo.Vendor WHERE Id = @DoomedVendorId;

-- -------------------------------------------------------------------------
-- Post-state guard (human-visible duplicate inventory is runbook Step 2)
-- -------------------------------------------------------------------------

IF EXISTS (
    SELECT 1 FROM qbo.Purchase GROUP BY QboId, RealmId HAVING COUNT(*) > 1
    UNION ALL
    SELECT 1 FROM qbo.Bill GROUP BY QboId, RealmId HAVING COUNT(*) > 1
    UNION ALL
    SELECT 1 FROM qbo.Vendor GROUP BY QboId, RealmId HAVING COUNT(*) > 1
    UNION ALL
    SELECT 1 FROM qbo.Customer GROUP BY QboId, RealmId HAVING COUNT(*) > 1
    UNION ALL
    SELECT 1 FROM qbo.Item GROUP BY QboId, RealmId HAVING COUNT(*) > 1
    UNION ALL
    SELECT 1 FROM qbo.Account GROUP BY QboId, RealmId HAVING COUNT(*) > 1
    UNION ALL
    SELECT 1 FROM qbo.Term GROUP BY QboId, RealmId HAVING COUNT(*) > 1
    UNION ALL
    SELECT 1 FROM qbo.ReimburseCharge GROUP BY QboId, RealmId HAVING COUNT(*) > 1
)
BEGIN
    RAISERROR('u218d dedupe: duplicate (QboId, RealmId) groups remain — aborting.', 16, 1);
    ROLLBACK TRANSACTION;
    RETURN;
END;

COMMIT TRANSACTION;
