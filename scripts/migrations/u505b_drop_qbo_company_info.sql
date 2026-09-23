-- U-505b: Phase-B guarded DROP of qbo.CompanyInfo.
--
-- STAGED, NOT APPLIED BY THE BUILD. Per feedback_builders_never_mutate_prod_data,
-- the build unit prepares this file; /em runs it only after BOTH gates below
-- pass on a FRESH run. Do not trust any row count or grep result cached in this
-- header -- they decay the moment they are written.
--
-- GATE 1 -- U-505 (7d7dfbff) is deployed and the repointed pull has actually
-- EXECUTED at least once. The pull is watermark-incremental, so simply waiting
-- proves nothing: if CompanyInfo has not changed in QBO since the watermark,
-- the pull fetches 0 records and _build_company_info is never called. Force a
-- full pull (last_updated_time=None) and confirm:
--     a. qbo.CompanyInfo.ModifiedDatetime did NOT advance, and
--     b. dbo.Company still carries the projected Name/Website/QboId/RealmId.
--   (a) without (b) is not a pass -- it would equally describe a pull that
--   silently did nothing at all.
--
-- GATE 2 -- zero readers/writers at drop time. Re-grep against whatever HEAD is
-- about to ship, not against this file's assumptions:
--     grep -rn "QboCompanyInfoRepository\|company_info.persistence" --include="*.py" .
--     grep -rn "CreateQboCompanyInfo\|ReadQboCompanyInfo\|UpdateQboCompanyInfo\|DeleteQboCompanyInfo" --include="*.py" .
--   and in the live DB, confirm no module outside this family references the
--   table (dbo.ReadCompanyByQboIdAndRealmId mentions it in COMMENTS only --
--   verified 2026-09-22 -- so a naive LIKE '%CompanyInfo%' will hit it; read
--   the match, do not just count it).
--
-- SCOPE NOTE: qbo.PhysicalAddress is deliberately NOT dropped. CompanyInfo's
-- three address ids point into it, but that table is shared with the customer
-- and vendor pulls and is still written every tick.
--
-- FK CONTEXT: sys.foreign_keys returned ZERO edges referencing qbo.CompanyInfo
-- (verified 2026-09-22), so unlike U-300c/U-307d this is a single-table drop
-- with no ordering constraint. Re-verify rather than trusting that line.
--
-- IRREVERSIBLE DATA LOSS, accepted by Chris 2026-09-22: four fields exist
-- nowhere else in dbo and die with this table --
--     CompanyName ("ROGERS BUILD INC" -- differs from LegalName, which IS
--                  projected to dbo.Company.Name)
--     Email       (invoice@rogersbuild.com -- dbo.Contact holds this address
--                  but on a USER row with CompanyId NULL, not as a company fact)
--     Country     (covered in substance by dbo.Address.Country)
--     FiscalYearStartMonth (no dbo home at all)
-- All are re-derivable from a fresh QBO pull, but stop being locally queryable.

-- ---------------------------------------------------------------------------
-- FINAL CONTENTS, captured 2026-09-23 immediately before the drop (1 row).
-- Recorded here because the four fields with no dbo home die with this table;
-- they are re-derivable from a QBO pull but stop being locally queryable.
--   Id=1  PublicId=20A52B92-4DCA-4079-980B-9C7F1184541C  QboId=1  SyncToken=149
--   RealmId=9130353016965726
--   CompanyName='ROGERS BUILD INC'          <- no dbo home (LegalName is what
--                                              projects to dbo.Company.Name)
--   LegalName='Rogers Build, Inc.'          -> dbo.Company.Name
--   WebAddr='www.rogersbuild.com'           -> dbo.Company.Website
--   Email='invoice@rogersbuild.com'         <- no dbo home as a COMPANY fact
--   Country='US'                            <- covered by dbo.Address.Country
--   FiscalYearStartMonth=1                  <- no dbo home at all
--   TaxPayerId=NULL                         (never populated)
--   CurrencyRef=NULL   CreatedDatetime=2026-01-07  ModifiedDatetime=2026-09-11
--   CompanyAddrId=1  LegalAddrId=2  CustomerCommunicationAddrId=3
--     -> qbo.PhysicalAddress rows QboId 2 / 898 / 1612 (that table SURVIVES)
--
-- GATES VERIFIED 2026-09-23 01:0x UTC, both passed:
--   Gate 1  forced full pull on deployed code (7d7dfbff): ModifiedDatetime
--           stayed 2026-09-11 02:00:06.612 (nothing wrote staging) AND
--           dbo.Company still projected ('Rogers Build, Inc.', www.rogersbuild.com,
--           QboId 1, Realm 9130353016965726). Both halves -- (a) alone would
--           equally describe a pull that silently did nothing.
--   Gate 2  zero references in the deployed tree, zero in web/mcp/ios/scheduler,
--           zero FKs, and the single live-DB mention is a COMMENT inside
--           dbo.ReadCompanyByQboIdAndRealmId (read, not counted).
-- ---------------------------------------------------------------------------

IF OBJECT_ID('qbo.CompanyInfo', 'U') IS NOT NULL
BEGIN
    DROP TABLE [qbo].[CompanyInfo];
END;
GO

-- Sprocs orphaned by the table drop. SQL Server does not require dropping these
-- first -- an unbound sproc body only errors at EXECUTE, not at DROP TABLE time
-- -- but leaving them live is a footgun: a stray caller gets a confusing
-- runtime error instead of an import-time failure.
DROP PROCEDURE IF EXISTS dbo.CreateQboCompanyInfo;
DROP PROCEDURE IF EXISTS dbo.ReadQboCompanyInfos;
DROP PROCEDURE IF EXISTS dbo.ReadQboCompanyInfoByQboId;
DROP PROCEDURE IF EXISTS dbo.ReadQboCompanyInfoById;
DROP PROCEDURE IF EXISTS dbo.ReadQboCompanyInfoByRealmId;
DROP PROCEDURE IF EXISTS dbo.UpdateQboCompanyInfoByQboId;
DROP PROCEDURE IF EXISTS dbo.DeleteQboCompanyInfoByQboId;
GO
