# Runbook: Expense Pull Fan-Out (Excel + SharePoint for QBO-pulled expenses)

A QBO-pulled expense landed in `dbo.Expense` but its DETAILS row or its receipt
is missing from the project's SharePoint tracker / module folder. Since
2026-10-03 the purchase tick does **not** run that work inline: it enqueues
exactly one `[ms].[Outbox]` row of Kind `expense_pull_fanout` **per project
touched in the tick** (deliberately unchunked — a second row for the same
project would read the sheet before the first row's inserts apply), and
`MsOutboxWorker._handle_expense_pull_fanout` runs
`ExpenseService.sync_expenses_batch_to_excel` once per project (one worksheet
snapshot for all expenses on that project) plus
`ExpenseService._upload_attachments_to_module_folder` per expense at drain. Those
methods then enqueue the `insert_excel_row` / `append_excel_row` /
`upload_sharepoint_file` rows that actually write to Graph.

Why: the inline shape (folder resolution, workbook session, used-range read,
blob probes — all Graph HTTP) stretched a tick past the next 15-minute timer,
which the admin dispatcher answers with a silent `lock_busy` skip (now a
WARNING with `consecutive_skips`), and past the 240 s gateway cap. Failures
were swallowed to a WARN with no retry.

## Outbox row shape

| Field | Value |
|---|---|
| `Kind` | `expense_pull_fanout` |
| `EntityType` | `Project` |
| `EntityPublicId` | The project's `PublicId` |
| `Payload` | `{"project_id": <int>, "expense_public_ids": ["<expense-uuid>", ...]}` |

The handler re-reads each expense and its line items at drain time. Expenses
deleted after enqueue are skipped. If every id is missing or has no lines on
that project, the row completes with no Excel/SharePoint work.

## Symptom

- `qbo_to_local.ms_fanout_enqueued` in the purchase sync result is `0` for a tick
  that projected expenses, or `ms_fanout_refused > 0`.
- An `expense_pull_fanout` row sits in `failed` / `dead_letter` in `[ms].[Outbox]`.
- `[ms].[ReconciliationIssue]` carries `expense_pull_fanout_dead_letter` (severity critical).
- The expense exists locally with lines on a mapped project, but the tracker
  DETAILS row (col-Z = line public_id) or the receipt file is absent.

## Severity

| Condition | Severity | Response |
|---|---|---|
| `ms_fanout_refused > 0` | High | `ALLOW_MS_WRITES` is not `true` on the API, or `_resolve_tenant_id()` found no MsAuth row |
| Rows dead-lettered | High | Read `LastError`; it is step labels from the handler (no raw driver text) |
| Rows pending for > 10 min | Warning | MS drain is a bounded loop (20 rows / 20 s per 30 s tick); check `drain_ms_outbox` is firing |

## Diagnosis

```sql
SELECT TOP 50 PublicId, EntityType, EntityPublicId, Status, Attempts, LastError, ReadyAfter, Payload
FROM [ms].[Outbox]
WHERE Kind = 'expense_pull_fanout'
ORDER BY CreatedDatetime DESC;
```

What the handler treats as **done, not failure** (logged at INFO, row marked done):

- `Excel not linked for project N` — no `ProjectExcel` mapping; map it, then re-run (below).
- `Module folder not linked for project N` — no `[ms].[DriveItemProjectModule]` row for Expenses.
- Expense deleted between enqueue and drain.
- No line items on that project (lines re-coded after the pull).

Everything else in `errors` (Drive not found, Vendor not found, Graph failures,
enqueue refused) raises a **retryable `MsServerError`** → `_handle_ms_error` backoff →
dead-letter after 5 attempts. Transient DB read failures at drain also raise
`MsServerError` with a step label (`read_expense`, `read_expense_lines`).

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
    project_public_id="<project-uuid>",
    project_id=<N>,
    expense_public_ids=["<expense-uuid>", ...],
)
```

Both downstream writers are idempotent (Excel on column Z, SharePoint on the
done-row target match), so a replay never duplicates a row or a file.

## Receipts and the attachable snapshot (how the bound stays safe)

The purchase tick bounds its QBO Attachable snapshot to its own watermark. Three rules
keep that from losing a receipt (the Pass-1 P0 of this unit was exactly that loss):

1. **The late-attach pass runs on EVERY incremental tick, including one with no
   changed purchases** (`_link_attachables_for_unchanged_purchases`, before the
   empty-tick return). The
   watermark commits on empty ticks too, so a receipt matched during a quiet window
   is linked by the tick that first sees it, never by a later one that cannot.
2. **A purchase that is NEW locally looks back to its own transaction date.** A receipt
   cannot predate the purchase it is attached to, and a purchase is not entered in QBO
   before its transaction date, so a window starting `CREATE_WINDOW_MARGIN_DAYS` (3) before
   `txn_date` holds every receipt it can have (`window_since=` → the per-run create-window
   cache, `qbo.attachable.create_window_loaded since=…`). This is exact whether the purchase
   was deferred yesterday or skipped a month ago — the first fix round's "older than 7
   days → full list" heuristic missed a purchase deferred and then recoded within the week.
   Past `CREATE_WINDOW_CAP_DAYS` (60), or with no transaction date, the authoritative full
   list is used (`qbo.attachable.full_list_loaded reason=authoritative_lookup`).
3. **A full or historical pull (no watermark) always takes the full list.**

A future-dated purchase (window newer than the watermark) falls back to the per-tick
snapshot, which reaches further back. Residual: the window keys on the editable
`TxnDate`; a purchase deferred, then given a receipt, then re-dated forward by more than
the margin before it first lands would miss that receipt — booked (stage `CreateTime`).

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
  `sharepoint_uploads` counts for the project.
- Column Z of the tracker DETAILS tab carries each line's public_id.
- The module folder holds `EXP - … .pdf` for the expense.
