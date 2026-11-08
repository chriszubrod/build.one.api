# Runbook: QBO Staging Freshness (billable status + deletes)

The `qbo.*` staging tables are refreshed by watermark-based incremental pulls. Two kinds of QBO change
never reach them that way (U-718, measured 2026-10-22):

1. **Billable-status flips.** When a QBO invoice consumes a billable expense, QBO flips the source
   Bill / Purchase / VendorCredit line from `Billable` to `HasBeenBilled`. It bumps the document's
   displayed `MetaData.LastUpdatedTime`, but the document is returned by **neither** the
   `WHERE MetaData.LastUpdatedTime > X` query **nor** the CDC endpoint, and its `SyncToken` does not
   change. Before U-718, 1,004 of 1,617 staged `Billable` lines were stale ($5.16M), which inflated
   every staging-based "what's billable" figure and produced 1,661 false `billable_status_drift`
   issues (U-335).
2. **Deletes.** Delete-reconcile ran only on full unfiltered pulls, which never run in prod; the
   invoice and reimburse-charge pulls had none. Ghosts found: 63 Bills, 39 Purchases, 18 Invoices,
   90 ReimburseCharges.

Two jobs now cover them.

| Job | Endpoint | Timer | QBO cost |
|---|---|---|---|
| Billable-status refresh | `POST /api/v1/admin/reconcile/qbo/billable-status[?dry_run=true]` | `qbo_billable_status_refresh`, hourly :28 | ~15 calls (by-id batches of 100) |
| Staging deletes (CDC) | `POST /api/v1/admin/reconcile/qbo/staging-deletes?mode=cdc[&lookback_hours=N][&dry_run=true]` | `qbo_staging_deletes_cdc`, hourly :43 | 1 call + 1 GET per candidate |
| Staging deletes (full) | `POST /api/v1/admin/reconcile/qbo/staging-deletes?mode=full[&max_candidates=N][&dry_run=true]` | `qbo_staging_deletes_full`, Sunday 06:00 UTC | ~65 id pages + 1 GET per candidate |

All three require `X-Drain-Secret`. The full mode outlives the scheduler's 60 s `_post` timeout and
finishes server-side (same as `reconcile_qbo`); its timer logging a request error is expected.

## What each job may write

- **Billable refresh** — staging `BillableStatus` only, by compare-and-set from `Billable` to
  `HasBeenBilled` / `NotBillable` (any other live value is skipped and counted). For a Bill/Purchase line
  that flipped to `HasBeenBilled`, the dbo line's `IsBilled` is set 0→1 **first** (never 1→0); the
  staging write follows only if that succeeded, so a failed heal retries next run. Headers missing in
  QBO are counted, never touched. Note: the expense-line update round-trips `BillableDecidedAt`, which
  the read sproc truncates to whole seconds (pre-existing, shared by every caller; booked in TODO.md).
- **Staging deletes** — deletes a staging header (lines cascade) **only** after an individual GET raised
  `QboNotFoundError`. Realm-scoped sprocs. **dbo is never written.** An Invoice still in dbo gets one
  `qbo_deleted` ReconciliationIssue (high) and its staging row is kept until that issue exists. Bills /
  Purchases still in dbo are only counted (`dbo_still_bound`); the daily `qbo_voided` detector flags them.
  More candidates than `QBO_PULL_DELETE_MAX_CANDIDATES` (default 50) aborts that entity and, outside a
  dry run, records a `pull_delete_reconcile` issue.
- **Drift auto-resolve** — the daily `reconcile/qbo` run resolves open `billable_status_drift` issues
  whose parent no longer drifts (cap `QBO_DRIFT_AUTORESOLVE_MAX`, default 5000; over the cap it resolves
  none and logs an ERROR). Failure-isolated from flagging.

## Symptom

- Staging `BillableStatus='Billable'` totals far above live QBO un-invoiced ReimburseCharges.
- `billable_status_drift` issue count growing; or open `qbo_deleted` / `pull_delete_reconcile` issues.
- A refresh or deletes result with `aborted` set, non-zero `batch_errors`, `dbo_heal_errors`,
  `binding_read_errors`, `staging_delete_errors` or `flag_errors`.

## Severity

Medium. Nothing here moves money, but stale staging misstates what is billable and can mislead an
invoice run. Treat open `qbo_deleted` Invoice issues as high: a dbo invoice whose QBO invoice is gone
shows as open A/R until someone decides what to do with it.

## Diagnosis

1. Dry-run both jobs (read-only, safe in prod):
   `POST …/billable-status?dry_run=true` and `POST …/staging-deletes?mode=full&dry_run=true`.
   Counts to read: `flipped_to_has_been_billed`, `dbo_would_heal`, `headers_missing_live`;
   per entity `candidates`, `confirmed_deleted`, `dbo_still_bound`, `invoices_flagged`, `aborted`.
2. `aborted` naming a systemic error (auth, budget, rate limit): see `qbo-token-expiration.md` /
   `qbo-api-budget-breaker.md`. The next run retries on its own.
3. `reconcile_aborted` on one entity in full mode: more absentees than the ceiling. Confirm in QBO that
   they really are deleted before raising the ceiling.
4. Compare staging against live QBO for one document: `SELECT * FROM Bill WHERE Id = '<id>'`.

## Recovery

- Backlog or a ceiling abort that you have confirmed in QBO: one manual
  `POST …/staging-deletes?mode=full&max_candidates=<n>` (1..1000; dry-run it first).
- An open `qbo_deleted` Invoice issue: decide with the operator whether the dbo invoice should be
  deleted; nothing here deletes it.
- Re-running either job is idempotent.

## Verification

- Billable refresh: a second run reports `flipped_to_* = 0` and staging `Billable` totals line up with
  live un-invoiced ReimburseCharges.
- Deletes: a second full dry run reports `confirmed_deleted = 0` for every entity.

## Prevention

Keep both timers deployed (the API before the scheduler). The CDC window is clamped to 696 h (29 days;
QBO's CDC limit is 30), so a scheduler outage longer than that needs a manual full run.

## The ReimburseCharge link on staged source lines (U-778a)

`qbo.BillLine`, `qbo.PurchaseLine` and `qbo.VendorCreditLine` carry `ReimburseChargeQboId`: the id of the QBO
ReimburseCharge (the "billable expense" charge) for that line, read from the line's own
`LinkedTxn [{TxnType: "ReimburseCharge"}]`. It is the deterministic link source line → charge, used by the reverse
billable flip (U-778b) and the invoice push (U-778d). Rules:

- **Every pull writes it straight** from the payload (`@SetReimburseChargeQboId = 1`): a line whose charge was removed
  or recreated is cleared or re-pointed. Any other caller of `UpdateQbo*LineById` preserves it (flag defaults to 0).
- **Only a valid id is stored:** an ASCII digit string of 1-50 characters (or a non-negative int). Anything malformed,
  and a line carrying two distinct charge ids, stores nothing — a missing link is acceptable, a wrong one is not.
- It is the CURRENT link, not history: editing the source in QBO recreates the charge under a new id.

**Backfill** (lines staged before U-778a have no link; new pulls fill new and changed lines):

```bash
PYTHONPATH=. ./.venv/bin/python scripts/backfill_u778a_charge_links.py --entity bill          # dry-run (default)
PYTHONPATH=. ./.venv/bin/python scripts/backfill_u778a_charge_links.py --entity bill --apply
```

Run from the repo root with `PYTHONPATH=.` (the script has no `sys.path` bootstrap). Run per entity (`bill`,
`purchase`, `vendor_credit`). Dry-run writes no links (the QBO transport still meters its
reads). It re-fetches headers 100 per QBO call; any client or DB error aborts the whole run and prints `resume_from` —
re-run with `--entity <that entity> --resume-from <id>`. The writer is fill-only: it never overwrites a link a fresher
pull already wrote. Counters: `written`, `not_written` (already linked, or the row vanished), `no_link_live`,
`ambiguous`, `line_gone`, `header_gone`.

**Verify:** `SELECT COUNT(*) FROM qbo.BillLine WHERE BillableStatus IN ('Billable','HasBeenBilled') AND
ReimburseChargeQboId IS NULL` (and the Purchase / VendorCredit twins) falls to roughly the `no_link_live` +
`header_gone` counts.


## Reverse billable flip: a deleted invoice un-bills its source lines (U-778b)

QBO flips a source line (Bill / Purchase / VendorCredit line) back to `Billable` when the invoice that billed it is
deleted, but does NOT bump the source document's `LastUpdatedTime`, so neither the pull nor the forward pass above
revisits it. What does move is the line's ReimburseCharge: QBO sets `HasBeenInvoiced=false` and bumps the charge, so
the hourly RC pull re-stages it. The hourly billable-status job therefore also runs a **reverse pass** per entity,
reported under `result[<entity>]["reverse"]`.

**What it does.** Candidates = staged line `HasBeenBilled` whose staged ReimburseCharge (same realm, via
`ReimburseChargeQboId`) has `HasBeenInvoiced=0`. Each candidate's header is re-fetched live; it acts ONLY when the live
line is `Billable` AND its ReimburseCharge id equals the staged one. Then, in this order: (1) dbo `IsBilled` 1→0 via the
line service (BillCreditLineItem without `_via_internal_pipeline`), unless a `dbo.InvoiceLineItem` still uses the line;
(2) staging CAS `HasBeenBilled`→`Billable` (skipped for a flagged or cap-deferred line). Any error in step 1 skips step 2, so the line stays a candidate and is
retried next hour. `?dry_run=true` writes nothing and reports `dbo_would_unbill` / `dbo_would_flag`.

### Symptom
A line that is `Billable` in QBO (invoice deleted) still shows `IsBilled = 1` in the app and is missing from the
billable list; or a `[qbo].[ReconciliationIssue]` with `DriftType = 'charge_uninvoiced_locally_billed'` is open.

### Diagnosis
```sql
-- staged candidates the reverse pass will pick up (swap the table for Purchase / VendorCredit)
EXEC dbo.ReadQboBillLinesWithUninvoicedChargeByRealmId @RealmId = N'<realm>';
-- open local-invoice conflicts
SELECT * FROM [qbo].[ReconciliationIssue]
WHERE DriftType = 'charge_uninvoiced_locally_billed' AND [Status] <> 'resolved';
```
An issue means QBO un-invoiced the charge but a local `dbo.InvoiceLineItem` still points at the source line; its
`QboId` is `<header qbo id>:<line qbo id>` and `Details` lists the charge id and the local invoice line / invoice ids.
Counters worth watching: `reverse_charge_changed` (live line points at a different charge: left alone),
`reverse_still_billed` (charge un-invoiced but the line is still HasBeenBilled live: left alone),
`dbo_unbill_errors` (retried next run), `dbo_flag_deduped`.

### Recovery
For a flagged issue (QBO un-invoiced the charge, but a local invoice line still uses the source line): the flagged line
is deliberately left `HasBeenBilled` in staging, so it stays a candidate and is re-checked every hour (one open issue,
deduped). Remove the local invoice line, or the QBO-deleted dbo invoice itself (U-778c), as appropriate. The next
hourly run then finds no `dbo.InvoiceLineItem` using the line, un-bills dbo `IsBilled` and flips staging to `Billable`
automatically. Resolve the issue by hand afterwards.

⚠️ **Caveat — a re-pull cancels the self-heal.** If the source Bill/Purchase/VendorCredit is edited in QBO while the
line is flagged, the pull re-stages the line as `Billable` and it stops being a reverse candidate. A Purchase re-pull
keeps `IsBilled = 1` while a local invoice line links it; a Bill re-pull writes `IsBilled` from the live status with no
local-invoice guard (pre-existing, booked in TODO.md under U-778). So after removing the local invoice line, CHECK
`IsBilled`: if it is still 1 and the line is `Billable` in QBO, reset it through the line service as a system caller,
then resolve the issue.

If the local invoice is actually right, re-invoice the charge in
QBO instead. Never edit `IsBilled` by hand while a local invoice line uses the source. Deleting an invoice does not by
itself un-bill its source lines (draft deletes leave sources unchanged, U-684a).

A line counted `deferred_by_cap` exceeded `max_unbill_per_run` (50 per entity per run) and is retried next run. A
`billable_refresh.<entity>.reverse_unmapped` WARNING means no local row matches that QBO header/line identity (staging
is still flipped; investigate a mis-stamped local row).

### Verification
Re-run `POST /api/v1/admin/reconcile/qbo/billable-status?dry_run=true`: the candidate no longer appears
(`candidate_lines` drops) or shows `dbo_would_flag` / `dbo_flag_deduped` for a still-flagged conflict (it keeps
appearing until the local invoice line is gone); `dbo.*LineItem.IsBilled = 0` for an un-billed line and the staged line
reads `Billable`.
