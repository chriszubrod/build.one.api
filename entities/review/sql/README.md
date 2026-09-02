# Review SQL build order

## Single source of truth

`dbo.review.sql` is the **canonical home** for all Review stored procedures,
including the three review-notification recipient resolvers:

- `dbo.ResolveReviewRecipientsByBillId`
- `dbo.ResolveReviewRecipientsByContractLaborId`
- `dbo.ResolveContractLaborReviewRecipientsPerProject`

The resolver bodies were single-sourced from migration 008 in U-062. U-126 also
homes the three ContractLabor parent review sprocs in the same base file:

- `dbo.ReadReviewsByContractLaborId`
- `dbo.ReadCurrentReviewByContractLaborId`
- `dbo.DeleteReviewsByContractLaborId`

`dbo.inbox_tasks.sql` is the **canonical home** (whole-file guarded) for the
cross-entity task inbox reads:

- `dbo.ReadInboxTasks`
- `dbo.ReadInboxTaskCounts`

Change the base files and apply them — do not redefine these sprocs in
migrations. Enforced by `tests/test_sproc_single_source.py` (`SINGLE_SOURCE_SPROCS`
rows + `ENTITY_BASE_FILES` rows for `review` and `inbox_tasks` + the
`test_review_resolvers_keep_persona_agent_filter` human-only guard).

## Superseded migration stubs

These files retain header intent and SUPERSEDED banners but no longer carry live
bodies for the sprocs noted below. Re-running them is a no-op for those sprocs:

- `migrations/001_resolve_review_recipients.sql`
- `migrations/004_resolve_review_recipients_contract_labor.sql`
- `migrations/006_resolve_review_recipients_contract_labor_per_project.sql`
- `migrations/007_resolve_review_recipients_contract_labor_per_project_v2.sql`
- `migrations/008_filter_personas_from_review_recipients.sql`
- `migrations/002_inbox_tasks.sql` (ReadInboxTasks / ReadInboxTaskCounts →
  `dbo.inbox_tasks.sql`)
- `migrations/003_add_contract_labor_parent.sql` (ContractLabor parent schema —
  column / FK / filtered index / 5-way `CK_Review_OneParent` → the base file's
  guarded ALTER block + CREATE TABLE; U-357b)
- `migrations/005_review_sprocs_contract_labor.sql` (ContractLabor review
  read/delete trio → `dbo.review.sql` in U-126; `vw_Review` / `CreateReview`
  → `dbo.review.sql` in U-357b — fully superseded)
- `scripts/migrations/gap2_adjacent_threading.sql` §7 (a stale `CreateReview`
  copy lacking `@ContractLaborId` / `@EmailMessageId` → pointer stub; U-357b)

## From-scratch build order

1. **`entities/review/sql/dbo.review.sql`** — Review table, indexes, view, CRUD
   read sprocs, ContractLabor review sprocs, and the three recipient resolvers.
   **Prerequisite tables:** `dbo.ReviewStatus`, `dbo.User`, `dbo.Bill`,
   `dbo.Expense`, `dbo.BillCredit`, `dbo.Invoice` **and `dbo.ContractLabor`** —
   the from-scratch `CREATE TABLE` declares all six parent FKs inline (U-357b
   added `FK_Review_ContractLabor`), so build the parent entities first; the
   guarded ALTER block only heals an already-existing 4-parent `dbo.Review`
   (human-only filter: excludes `User.IsAgent = 1` and `Auth.Username` prefixed
   with `persona_`).

2. **`entities/review/sql/dbo.inbox_tasks.sql`** — Task inbox list + count sprocs
   (apply after `dbo.review.sql` — depends on `vw_Review`).

3. **Nothing else.** As of U-357b the base file is self-sufficient for all five
   parents (Bill / Expense / BillCredit / Invoice / ContractLabor); every review
   migration is a pointer stub. `dbo.review.sql` is whole-file guarded
   (`ENTITY_BASE_FILES` row `review`) in addition to the per-sproc pins.
