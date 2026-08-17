# U-249 Reconciler Triage Findings — 2026-08-17

> Read-only investigation + regression tests for the Bill `qbo_missing_locally` reconciler backlog.
> Method: live DB SELECT-only analysis (`scripts/analyze_qbo_reconciliation_backlog.py`), three pure-logic regression tests locking in fixes already shipped in production code.

**EM review correction (2026-08-17):** The U-246 board entry's "1 unique invoice" claim for `invoice_draw_mismatch` was inaccurate — each row's `Details` is a rotating multi-invoice daily digest (EntityPublicId/QboId are NULL on every row), not a single repeatedly-flagged invoice. Section 4(d) and the net-effect totals below reflect the corrected per-run-full-rewrite pattern (same pathology as (b)).

**Sprocs from U-246 are NOT YET APPLIED to prod.** None of the bulk-resolve commands documented below can be run yet, and none were run by this unit.

---

## Bug 1 — `vendor_ref_value` AttributeError (DEAD)

### Root cause

The `_reconcile_bill_qbo_missing_locally` autofix path (when `QBO_RECONCILE_BILL_AUTOFIX=true`) passed the **raw external QBO Bill object** directly to `BillBillConnector.sync_from_qbo_bill`. The connector expects a **local staging dataclass** (with fields like `vendor_ref_value`), not the external shape — causing `AttributeError: 'SimpleNamespace' object has no attribute 'vendor_ref_value'` on every autofix attempt. Each failure wrote a high-severity `qbo_missing_locally` / `flagged` issue, flooding the table (~600×/day before the gate).

### Fix commits (already shipped — no further code change needed)

| Commit | Date | What it did |
|--------|------|-------------|
| `c5fc21fe` | 2026-04-23 | Route raw external bill through `QboBillService.upsert_from_external(qbo_bill, realm_id)` **before** handing the returned local object to `connector.sync_from_qbo_bill(qbo_bill=local_bill, …)` |
| `55994162` | 2026-06-20 | Gate the autofix path behind `QBO_RECONCILE_BILL_AUTOFIX` env var (default `"false"`) — when off, the loop only counts `missing` and emits one low-severity summary |

### Dead-row evidence

Section 3 canary: max `CreatedDatetime` for open high-severity Bill `qbo_missing_locally` / `flagged` rows is **2026-06-20 07:58:52** — one day before the gate deploy cutoff (2026-06-21). No new high-severity rows since. The bug cannot recur under current/default config.

### Regression test

`tests/test_qbo_reconcile_bill_missing_locally.py::test_bill_missing_locally_autofix_on_routes_through_upsert_from_external` — asserts `upsert_from_external` is called with the raw external bill, and the connector receives the **local** object (`id=99`) returned by upsert, not the raw external object.

---

## Bug 2 — Deleted QBO vendors (refs 11, 13, 269) — permanent, unfixable

### Characterization

Three QBO vendor refs are marked **(deleted)** in QBO itself:

- Ref 11 — Alex Fernando Ordonez-Costro (deleted)
- Ref 13 — Jorge Guzman (deleted)
- Ref 269 — Teran Concrete (deleted)

A deleted QBO vendor can never be pulled into `qbo.Vendor` staging. **189 staged `qbo.Bill` rows** reference these vendors and are permanently un-mappable locally.

### Existing correct behavior (no code fix needed)

`_get_vendor_public_id` (`integrations/intuit/qbo/bill/connector/bill/business/service.py:303-334`) raises `ValueError` for unmapped vendors. The reconciler's `except ValueError` branch catches this, increments `skipped_unmapped`, and skips **without** creating a `ReconciliationIssue` row and **without** crashing.

### Regression test

`tests/test_qbo_reconcile_bill_missing_locally.py::test_bill_missing_locally_autofix_on_skips_unmapped_vendor_without_crash_or_issue` — connector raises `ValueError("No vendor mapping found for QBO vendor ref: 269")`; asserts `skipped_unmapped == 1` and no issues recorded.

### Autofix-off regression test

`tests/test_qbo_reconcile_bill_missing_locally.py::test_bill_missing_locally_autofix_off_counts_only_no_side_effects` — asserts no upsert/connector calls, `missing == 1`, one low-severity summary issue with `qbo_id=None`.

---

## Section 1 — Full triage breakdown (live DB, 2026-08-17)

```
DriftType                            EntityType       Severity   Action         Status         RowCount UniqueKeys FirstSeen            LastSeen
----------------------------------------------------------------------------------------------------------------------------------------------------------------
qbo_missing_locally                  Bill             high       flagged        open              19040        970 2026-04-21 19:22:51  2026-06-20 07:58:52
qbo_missing_locally                  Bill             low        auto_fixed     open                210        210 2026-04-24 03:17:04  2026-04-26 10:06:54
qbo_missing_locally                  Bill             low        flagged        open                 52          0 2026-06-21 07:07:41  2026-08-17 08:30:29
invoice_draw_mismatch                Invoice          medium     flagged        open                 29          0 2026-07-04 15:06:09  2026-08-17 11:34:42
qbo_voided                           Bill             low        flagged        open                 26         26 2026-08-11 07:29:41  2026-08-14 07:24:02
qbo_missing_locally                  Expense          low        flagged        open                 22          0 2026-07-16 17:12:20  2026-08-17 11:26:00
qbo_voided                           Expense          low        flagged        open                 20         20 2026-08-11 10:13:37  2026-08-11 10:14:05
qbo_missing_locally                  BillCredit       low        flagged        open                 18          0 2026-07-18 19:42:30  2026-08-17 11:34:34
watermark_hold_bound_exceeded        bill             critical   manual_review  open                 12          0 2026-08-12 21:06:28  2026-08-12 21:59:26

Total rows: 19,429
Non-open rows: 0 (all Status='open')
```

`UniqueKeys` is `COUNT(DISTINCT QboId)` scoped to each `(DriftType, EntityType, Severity, Action, Status)` group — so each printed number is self-consistent with its row (0 is expected where QboId is always NULL, e.g. `invoice_draw_mismatch` and the low-severity `qbo_missing_locally` summary rows).

---

## Section 2 — Bogus fixture rows

12 `watermark_hold_bound_exceeded` rows (fake QboIds `vc-1`, `staging-only`, one empty QboId; all within a 53-minute window on 2026-08-12):

| Id | PublicId | QboId | CreatedDatetime |
|----|----------|-------|-----------------|
| 19390 | 35427125-E485-499A-90F8-3E0185FBD1E1 | vc-1 | 2026-08-12 21:06:28 |
| 19391 | 49CBCBEF-F022-4E8E-8F90-5C69AC9E86ED | staging-only | 2026-08-12 21:06:28 |
| 19392 | 88E77F95-3478-456D-9738-9AF6F7BC1230 | vc-1 | 2026-08-12 21:06:45 |
| 19393 | CA2F1DF1-5AC4-4467-BC4E-0CFA84E751A2 | staging-only | 2026-08-12 21:06:45 |
| 19394 | 0F18EF05-C066-4139-8B7F-D806BA30B52F | vc-1 | 2026-08-12 21:21:13 |
| 19395 | BB3A7382-BBBF-4350-964C-8DC99841E923 | staging-only | 2026-08-12 21:21:13 |
| 19396 | D4F40BEE-AF68-432B-81B0-5047D285E51B | vc-1 | 2026-08-12 21:34:46 |
| 19397 | A5BC7986-9BFE-40E6-B7AA-70980BE1B3B3 | staging-only | 2026-08-12 21:34:46 |
| 19398 | 030D4DAB-22C2-4211-8D58-9ECAF2A1C70A | vc-1 | 2026-08-12 21:34:59 |
| 19399 | 49111F95-8942-41E9-87A9-DF790806B047 | staging-only | 2026-08-12 21:34:59 |
| 19400 | E7D8E229-5B16-4509-8BC9-5EF639E0446D | (empty) | 2026-08-12 21:59:25 |
| 19401 | E44E03D5-9572-4982-9579-DEE3DCB62D0E | staging-only | 2026-08-12 21:59:26 |

---

## Section 3 — Dead-historical-row canary

Max `CreatedDatetime` for open high-severity Bill `qbo_missing_locally` / `flagged`: **2026-06-20 07:58:52**

Canary cutoff: **2026-06-21 00:00:00** — **OK** (no rows newer than cutoff).

---

## Section 4 — Bulk-resolve policy (TEXT ONLY — pending U-246 sproc apply)

### a. dead-and-stale-missing-locally-bill

- **Count:** 19,250
- **Sub-breakdown** (`CreatedDatetime < 2026-06-21 00:00:00`): 19,040 high/flagged (dead crash-path), 210 low/auto_fixed (successful historical pulls)
- **Rationale:** Pre-cutoff Bill `qbo_missing_locally` rows — dead crash-path noise AND successful historical auto-fixes. `BulkResolveQboReconciliationIssuesByFilter` has no `@Severity`/`@Action` params, so one date-scoped filter resolves both buckets together (see Section 3 canary).
- **Command:**
  ```bash
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py bulk-resolve \
    --drift-type qbo_missing_locally --entity-type Bill \
    --created-before-date 2026-06-21 --status open \
    --max-rows 5000 --apply
  ```
- **NOTE:** count exceeds `--max-rows` cap of 5000; run this command 4 times (or increase filter precision), re-checking remaining row count between runs.

### b. ongoing-low-severity-summary-dedup

| EntityType | Total | Keep newest | Resolve delta |
|------------|-------|-------------|---------------|
| Bill | 52 | 1 | 51 |
| Expense | 22 | 1 | 21 |
| BillCredit | 18 | 1 | 17 |

- **Total dedup delta:** 89
- **GAP:** No bulk-resolve filter for Severity/Action combo.

### c. qbo-voided-acknowledge

- **Count:** 46 (Bill: 26, Expense: 20)
- **Rationale:** Real drift — acknowledge (not resolve) until human confirms.
- **GAP:** No bulk-acknowledge sproc; must run `acknowledge --id <id>` per row (46 commands).

### d. ongoing-invoice-draw-summary-dedup

- **Total open rows:** 29
- **Keep newest:** 1
- **Resolve delta:** 28
- **Current drift state as of 2026-08-17 11:34:42:**
  ```
  Daily invoice-draw summary: QBO drift on 17 invoice(s): KA2-06 (lines dbo=7 qbo=6); TB3-17-2 (lines dbo=97 qbo=95); HP-23.02 (lines dbo=62 qbo=50); KA2-07-2 (total dbo=21116.44 qbo=23212.93, lines dbo=11 qbo=12); OL-11 (lines dbo=30 qbo=31); OL-PH-01 (total dbo=107152.25 qbo=120010.52, lines dbo=9 q...
  ```
- **Rationale:** Same per-run-full-rewrite pattern as (b) — each row is a complete fresh re-summary of currently-drifting invoices, not a repeat of one stuck invoice (`EntityPublicId`/`QboId` are NULL for this drift type; verify by reading `Details`). Safe to dedupe to newest like (b).
- **GAP:** `BulkResolveQboReconciliationIssuesByFilter` still has no way to filter to only non-newest rows (same limitation as (b)); unlike (a)/(e), filtering by `DriftType` alone would also catch the one row to KEEP — needs per-row resolve or a future date-scoped resolve.

### e. bogus-watermark-fixtures

- **Count:** 12 (matches Section 2)
- **Rationale:** Confirmed bogus test-session artifacts.
- **Command:**
  ```bash
  PYTHONPATH=. ./.venv/bin/python scripts/manage_qbo_reconciliation_issues.py bulk-resolve \
    --drift-type watermark_hold_bound_exceeded --status open \
    --max-rows 12 --apply
  ```

### Net effect summary

| Step | Rows |
|------|------|
| Total today | 19,429 |
| Minus (a) + (e) clear-resolve | −19,262 [(a)=19,250 (dead 19,040 + stale 210), (e)=12] |
| Minus (b) low-summary dedup delta | −89 |
| Minus (d) invoice-draw-summary dedup delta | −28 |
| **Estimated remaining open** | **50** |

**What stays genuinely actionable:**

- **(c)** 46 `qbo_voided` — acknowledge-only, stays visible
- **(d)** 1 remaining invoice-draw summary (the newest, current-state row) — needs human/product review of its actual drift content, not the 28 historical duplicate summaries (safe to bulk-resolve like (b))
- **(b)** 3 kept low-summary rows (1 per Bill/Expense/BillCredit)

---

## Artifacts shipped by U-249

| File | Purpose |
|------|---------|
| `tests/test_qbo_reconcile_bill_missing_locally.py` | 3 regression tests |
| `scripts/analyze_qbo_reconciliation_backlog.py` | Read-only backlog triage script |
| `docs/u249_reconciler_triage_findings.md` | This document |
