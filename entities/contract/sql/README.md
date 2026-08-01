# Contract SQL build order

## Minimal by design

`dbo.Contract` currently carries exactly one business field, `BuildersFeeRate`
(the DECIMAL(9,6) fraction the U6 cover page reads). The full contract model —
contract value, change orders, retainage, dates, and the relationship to the
existing **Budget** entity — is **deferred to a formal design conversation**. Do
not add business columns without that decision.

## Single source of truth

`dbo.contract.sql` is the **sole canonical source** for the `dbo.Contract` table
bootstrap and all of its stored procedures. No migration may redefine the sprocs —
change the base file and re-apply it (it uses `CREATE OR ALTER` and is re-run
routinely). `scripts/migrations/contract_entity.sql` carries ONLY the idempotent
table + FK + index DDL, never a sproc body, so the single-source sproc guards
(`tests/test_sproc_single_source.py`) stay clean.

The sprocs (lean CRUD, enough to set + read the fee rate): `CreateContract`,
`ReadContractByPublicId`, `ReadContractsByProjectId`, `UpdateContractByPublicId`.
List-all, read-by-id, and delete are intentionally omitted until the model is
designed.

## From-scratch build order

1. **Prerequisites** — `dbo.User` and `dbo.Project` must exist first (the FK
   batches at the bottom of the base file reference `dbo.User(Id)` and
   `dbo.Project(Id)`).

2. **`entities/contract/sql/dbo.contract.sql`** — creates the table (guarded),
   all four sprocs (`CREATE OR ALTER`), the two FKs, and the `IX_Contract_ProjectId`
   / `IX_Contract_PublicId` indexes. Idempotent end to end.

Applying `dbo.contract.sql` alone against a database that already has `dbo.User`
and `dbo.Project` is sufficient; `scripts/migrations/contract_entity.sql` is the
narrower table-only path for the standard migration runner
(`python scripts/run_sql.py scripts/migrations/contract_entity.sql`).

## Notes

- `BuildersFeeRate` is a `DECIMAL(9,6)` **fraction** (0.100000 = 10%).
- `CreatedByUserId` is `BIGINT NOT NULL DEFAULT 17` (FK `dbo.User`); the Create
  sproc uses `COALESCE(@CreatedByUserId, 17)` for scheduler / system context.
- One Project may carry several Contracts, so `ProjectId` is **not** unique —
  `ReadContractsByProjectId` returns a list.
