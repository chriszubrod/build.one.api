# VendorInsurancePolicy SQL build order

## Dependencies

This entity depends on `dbo.VendorComplianceDocument` already existing.

## From-scratch build order

1. **`entities/vendor_compliance_document/sql/dbo.vendor_compliance_document.sql`** — creates
   `dbo.VendorComplianceDocument`. Required before VendorInsurancePolicy because of
   `FK_VendorInsurancePolicy_ComplianceDocument`.

2. **`entities/vendor_insurance_policy/sql/dbo.vendor_insurance_policy.sql`**
   — table, all sprocs, FK constraints, CHECK constraints, and indexes.

Apply the base table before FK/index batches. Each constraint/index block is
`GO`-terminated so it is not swallowed into the preceding sproc body.
