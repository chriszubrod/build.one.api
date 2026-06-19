-- One-time backfill of VendorCreditLineItemBillCreditLineItem mappings (2026-06-18).
-- The mapping sprocs were in the wrong schema so mappings were never created. Before
-- the upsert-in-place sync relies on them, seed them by POSITION (qbo line by LineNum
-- <-> local line by Id) for credits whose local line count == QBO line count, so the
-- first sync resolves via mapping and updates in place instead of hitting the
-- fingerprint fallback (which would duplicate truly-identical lines like a 50-50 split).
-- Conservative: count-matched credits only; skip any line already mapped (UNIQUE-safe).
-- Idempotent. Run with:
--   python scripts/run_sql.py scripts/migrations/qbo_vendorcredit_mapping_backfill.sql

WITH counts AS (
    SELECT m.BillCreditId, m.QboVendorCreditId,
        (SELECT COUNT(*) FROM qbo.VendorCreditLine vcl WHERE vcl.QboVendorCreditId = m.QboVendorCreditId) AS qn,
        (SELECT COUNT(*) FROM dbo.BillCreditLineItem b WHERE b.BillCreditId = m.BillCreditId) AS dn
    FROM qbo.VendorCreditBillCredit m
),
eligible AS (
    SELECT BillCreditId, QboVendorCreditId FROM counts WHERE qn = dn AND qn > 0
),
q AS (
    SELECT e.BillCreditId, vcl.Id AS QboLineId,
        ROW_NUMBER() OVER (PARTITION BY e.BillCreditId ORDER BY vcl.LineNum, vcl.Id) AS rn
    FROM eligible e
    JOIN qbo.VendorCreditLine vcl ON vcl.QboVendorCreditId = e.QboVendorCreditId
),
d AS (
    SELECT bcli.BillCreditId, bcli.Id AS BcliId,
        ROW_NUMBER() OVER (PARTITION BY bcli.BillCreditId ORDER BY bcli.Id) AS rn
    FROM dbo.BillCreditLineItem bcli
    WHERE bcli.BillCreditId IN (SELECT BillCreditId FROM eligible)
)
INSERT INTO qbo.VendorCreditLineItemBillCreditLineItem ([QboVendorCreditLineId], [BillCreditLineItemId])
SELECT q.QboLineId, d.BcliId
FROM q
JOIN d ON d.BillCreditId = q.BillCreditId AND d.rn = q.rn
WHERE NOT EXISTS (SELECT 1 FROM qbo.VendorCreditLineItemBillCreditLineItem x WHERE x.QboVendorCreditLineId = q.QboLineId)
  AND NOT EXISTS (SELECT 1 FROM qbo.VendorCreditLineItemBillCreditLineItem x WHERE x.BillCreditLineItemId = d.BcliId);
GO
