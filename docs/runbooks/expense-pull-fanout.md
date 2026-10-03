# Runbook: Expense Pull Fan-Out (Excel + SharePoint for QBO-pulled expenses)

A QBO-pulled expense landed in `dbo.Expense` but its DETAILS row or its receipt
is missing from the project's SharePoint tracker / module folder. Since
2026-10-03 the purchase tick does **not** run that work inline: it enqueues one
`[ms].[Outbox]` row of Kind `expense_pull_fanout` per (expense, project), and
`MsOutboxWorker._handle_expense_pull_fanout` runs `ExpenseService.sync_to_excel_workbook`
and `ExpenseService._upload_attachments_to_module_folder` at drain. Those two
methods then enqueue the `insert_excel_row` / `append_excel_row` /
`upload_sharepoint_file` rows that actually write to Graph.

Why: the inline shape (folder resolution, workbook session, used-range read,
blob probes — all Graph HTTP) stretched a tick past the next 15-minute timer,
which the admin dispatcher answers with a silent `lock_busy` skip (now a
WARNING with `consecutive_skips`), and past the 240 s gateway cap. Failures
were swallowed to a WARN with no retry.

## Symptom

- `qbo_to_local.ms_fanout_enqueued` in the purchase sync result is `0` for a tick
  that projected expenses, or `ms_fanout_refused > 0`.
- An `expense_pull_fanout` row sits in `failed` / `dead_letter` in `[ms].[Outbox]`.
- `[ms].[ReconciliationIssue]` carries `expense_pull_fanout_dead_letter` (severity high).
- The expense exists locally with lines on a mapped project, but the tracker
  DETAILS row (col-Z = line public_id) or the receipt file is absent.

## Severity

| Condition | Severity | Response |
|---|---|---|
| `ms_fanout_refused > 0` | High | `ALLOW_MS_WRITES` is not `true` on the API, or `_resolve_tenant_id()` found no MsAuth row |
| Rows dead-lettered | High | Read `LastError`; it is the joined `errors` list from the two service methods |
| Rows pending for > 10 min | Warning | MS drain is a bounded loop (20 rows / 20 s per 30 s tick); check `drain_ms_outbox` is firing |

## Diagnosis

```sql
SELECT TOP 50 PublicId, EntityPublicId, Status, Attempts, LastError, ReadyAfter, Payload
FROM [ms].[Outbox]
WHERE Kind = 'expense_pull_fanout'
ORDER BY CreatedDatetime DESC;
```

`Payload` is `{"project_id": N, "expense_line_items_count": N}`. The handler
re-reads the expense + lines at drain time, so a payload is never stale.

What the handler treats as **done, not failure** (logged at INFO, row marked done):

- `Excel not linked for project N` — no `ProjectExcel` mapping; map it, then re-run (below).
- `Module folder not linked for project N` — no `[ms].[DriveItemProjectModule]` row for Expenses.
- Expense deleted between enqueue and drain.
- No line items on that project (lines re-coded after the pull).

Everything else in `errors` (Drive not found, Vendor not found, Graph failures,
enqueue refused) raises → retry with backoff → dead-letter after 5 attempts.

## Recovery

1. Fix the cause (map the project, restore `ALLOW_MS_WRITES`, re-auth MS).
2. Replay dead letters: `scripts/retry_ms_outbox_dead_letters.py --kind expense_pull_fanout --apply`.
3. For a row that was marked done against an unmapped project, re-run the fan-out
   by re-pulling the purchase (edit-and-save it in QBO, or call
   `POST /api/v1/admin/sync/qbo/purchase` after setting the purchase watermark back),
   or enqueue directly from a shell:

```python
from integrations.ms.outbox.business.service import MsOutboxService
MsOutboxService().enqueue_expense_pull_fanout(
    expense_public_id="<uuid>", project_id=<N>, expense_line_items_count=<N>,
)
```

Both downstream writers are idempotent (Excel on column Z, SharePoint on the
done-row target match), so a replay never duplicates a row or a file.

## Known hazards this does NOT fix (booked in TODO.md, 2026-10-03 review)

- **Multi-attachment expenses overwrite themselves in SharePoint.** With more than
  one line the filename is `EXP - proj - vendor - ref - "Multiple See Image" - total - date`
  with no per-attachment component; the second receipt's bare `:/content` PUT replaces
  the first, in both the module folder and `520 - Current Receipts`. Box has the same
  class through same-name 409 re-versioning.
- **Receipts-folder early returns report `errors: []`**, so a missing
  `520 - Current Receipts/yyyy/mm` path completes "successfully" with no upload.
- `complete_expense` (the human completion path, not the pull) still runs the
  Excel session and the folder CREATE inline.

## Verification

- `ms.outbox.expense_pull_fanout.completed` log line with `excel_rows` /
  `sharepoint_uploads` counts for the entity.
- Column Z of the tracker DETAILS tab carries each line's public_id.
- The module folder holds `EXP - … .pdf` for the expense.
