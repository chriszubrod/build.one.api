# CompletionJob SQL build order

## Single source of truth

`dbo.completion_job.sql` is the **single canonical source** for the
`dbo.CompletionJob` table and all four CompletionJob stored procedures.
No migration may redefine them — change the base file and apply it.
Enforced by `tests/test_sproc_single_source.py` (the `completion_job` row).

## From-scratch build order

1. **`entities/completion_job/sql/dbo.completion_job.sql`** — table, indexes,
   and sprocs (`CreateCompletionJob`, `ClaimNextStuckCompletionJob`,
   `MarkCompletionJobSuccess`, `MarkCompletionJobFailure`).

Apply with:

```bash
python scripts/run_sql.py entities/completion_job/sql/dbo.completion_job.sql
```
