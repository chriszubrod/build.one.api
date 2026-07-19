# VendorComplianceDocument SQL build order

## Dependencies

This entity depends on `dbo.Vendor` and `dbo.Attachment` already existing.

## From-scratch build order

1. **`entities/vendor/sql/dbo.vendor.sql`** — creates `dbo.Vendor`. Required before
   VendorComplianceDocument because of `FK_VendorComplianceDocument_Vendor`.

2. **`entities/attachment/sql/dbo.attachment.sql`** — creates `dbo.Attachment`.
   Required before VendorComplianceDocument because of
   `FK_VendorComplianceDocument_Attachment`.

3. **`entities/vendor_compliance_document/sql/dbo.vendor_compliance_document.sql`**
   — table, all sprocs, FK constraints, CHECK constraints, and indexes.

Apply the base table before FK/index batches. Each constraint/index block is
`GO`-terminated so it is not swallowed into the preceding sproc body.
