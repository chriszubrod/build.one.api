# Vendor SQL build order

## Single source of truth

`dbo.vendor.sql` is the **single canonical source** for the `dbo.Vendor`
table and all of its stored procedures. No migration may redefine them — change
the base file and apply it. Enforced by `tests/test_sproc_single_source.py`.
Duplicate bodies that drift from the base file break net-zero with prod.

The sprocs: `CreateVendor`, `ReadVendors`, `ReadVendorById`,
`ReadVendorByPublicId`, `ReadVendorByName`, `UpdateVendorById`,
`SoftDeleteVendorByPublicId`, `FindVendorForInvoice`,
`FindContractLaborVendorByEmail`.

The six stale bodies (`CreateVendor`, `ReadVendors`, `ReadVendorById`,
`ReadVendorByPublicId`, `ReadVendorByName`, `UpdateVendorById`) were
reconciled verbatim from `migrations/003_2026_07_19_track_compliance.sql` under
U-142 (2026-07-24) — the base was stale by two layers (002 rates, 003
compliance).

## ⚠️ Applying this file to prod

The guarded `CREATE TABLE` in the base file predates the rate, compliance, and
attribution columns; prod already has `HourlyRate` + `Markup` (migration 002),
`TrackCompliance` (migration 003), and `CreatedByUserId`
(`scripts/migrations/gap2_created_by_user_id.sql`). Re-applying only the sproc
section is the usual path once bodies are verified base==live. For a fresh
database, follow the build order below.

## From-scratch build order

1. **Prerequisites** — `dbo.VendorType` and `dbo.Taxpayer` must exist before
   the FK blocks in the base file.

2. **`entities/vendor/sql/dbo.vendor.sql` (first pass)** — the guarded
   `CREATE TABLE` and idempotent column/index batches apply; the sproc batches
   **fail** at `CREATE PROCEDURE` time: SQL Server validates columns on
   existing tables, and `HourlyRate`, `Markup`, `TrackCompliance`, and
   `CreatedByUserId` are not present yet. Continue — the second pass (step 6)
   applies them.

3. **`entities/vendor/sql/migrations/002_2026_05_27_rate_columns.sql`** —
   adds `HourlyRate` and `Markup`.

4. **`entities/vendor/sql/migrations/003_2026_07_19_track_compliance.sql`** —
   adds `TrackCompliance`.

5. **`scripts/migrations/gap2_created_by_user_id.sql`** — adds
   `CreatedByUserId` (and other Gap-2 reference-entity columns).

6. **`entities/vendor/sql/dbo.vendor.sql` (second pass)** — idempotent
   `CREATE OR ALTER`; all sprocs apply with the full column set.

`dbo.Contact` is referenced at execution time by `FindVendorForInvoice` and
`FindContractLaborVendorByEmail` — it must exist before those sprocs are called,
not necessarily before they are created.

## Superseded migration stubs

`migrations/001_find_contract_labor_vendor_by_email.sql`,
`migrations/002_2026_05_27_rate_columns.sql`, and
`migrations/003_2026_07_19_track_compliance.sql` carry SUPERSEDED banners
(U-142) and no live sproc bodies — re-running them is a no-op for those objects.
The column-add batches in 002 and 003 remain authoritative DDL for fresh-DB
builds.
