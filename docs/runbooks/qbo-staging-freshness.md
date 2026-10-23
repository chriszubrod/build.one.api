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
