# Review SQL build order

## Single source of truth

`dbo.review.sql` is the **canonical home** for all Review stored procedures,
including the three review-notification recipient resolvers:

- `dbo.ResolveReviewRecipientsByBillId`
- `dbo.ResolveReviewRecipientsByContractLaborId`
- `dbo.ResolveContractLaborReviewRecipientsPerProject`

The resolver bodies were single-sourced from migration 008 in U-062. Change the
base file and apply it — do not redefine these sprocs in migrations. Enforced by
`tests/test_sproc_single_source.py` (`SINGLE_SOURCE_SPROCS` rows + the
`test_review_resolvers_keep_persona_agent_filter` human-only guard).

## Superseded migration stubs

These files retain header intent and SUPERSEDED banners but no longer carry live
bodies for the three recipient resolvers. Re-running them is a no-op for those
sprocs:

- `migrations/001_resolve_review_recipients.sql`
- `migrations/004_resolve_review_recipients_contract_labor.sql`
- `migrations/006_resolve_review_recipients_contract_labor_per_project.sql`
- `migrations/007_resolve_review_recipients_contract_labor_per_project_v2.sql`
- `migrations/008_filter_personas_from_review_recipients.sql`

## From-scratch build order

1. **`entities/review/sql/dbo.review.sql`** — Review table, indexes, view, CRUD
   read sprocs, and the three recipient resolvers (human-only filter: excludes
   `User.IsAgent = 1` and `Auth.Username` prefixed with `persona_`).

2. **Other review migrations** — schema-only or entity-specific additions not
   yet folded into the base file (apply after the base file on fresh builds).
