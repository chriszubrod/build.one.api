# Family-11 follow-ups (U-497 … U-501)

**Booked 2026-09-21.** These came out of the family-11 retirement (U-491..U-496) and its three review passes. They belong in `TODO.md`, but a parallel session has held that file uncommitted for the whole of this work, so they live here rather than evaporating into commit messages. **Move them into `TODO.md` once that session lands, then delete this file.**

Context for all five: `qbo.PurchaseLineExpenseLineItem` was dropped 2026-09-21 17:38:15 UTC, closing the last of the eleven U-349 mapping families. `qbo.*` is now 20 base tables.

---

## U-497 — 139 ExpenseCodingItem rows point at a deleted staging line

**Found by:** Pass 2 (quality), whose specific example I then refuted by measurement — the underlying divergence is real and larger than it framed.

`dbo.ExpenseCodingItem` carries both a staging PK (`QboPurchaseLineId` → `qbo.PurchaseLine.Id`) and a string identity (`QboPurchaseQboId` + `QboLineId`). The SQL readers enter through the staging PK; the outbox recode handler enters through the string identity. **Measured live: 139 coding items whose `QboPurchaseLineId` points at a `qbo.PurchaseLine` row that no longer exists.** For those, the SQL readers resolve nothing while the Python path may still resolve.

**Verified not urgent:** the write fails closed. `recode_purchase_line` targets `item.qbo_line_id`; when that line is gone from the live QBO document the connector returns `line_not_found` and the handler calls `mark_changed_in_qbo`. No wrong-line write. Also measured: `0` coding items whose `eci.QboLineId` disagrees with a *surviving* staging row — every disagreement is with a deleted one.

Pre-existing data class, same family as U-489 (expense 32068). Not introduced by family 11.

**Decide:** is the staging PK still the right anchor now that the mapping table is gone, or should `ExpenseCodingItem` resolve purely dbo-natively like the worker does? That is the real question; the 139 are the symptom.

**Surface:** `entities/expense_coding_item/` (model, sql, service), `entities/expense/business/service.py:486` (`enqueue_coding_recode_on_submit`).

---

## U-498 — the identity chain is hand-copied five times

**Found by:** Pass 2 (altitude), which also corrected my own undercount — I said four, it is five.

The same `dbo.Expense ↔ qbo.Purchase ↔ qbo.PurchaseLine ↔ dbo.ExpenseLineItem` parent-scoped chain is written out at:

1. `entities/expense/business/service.py:466` — `_read_qbo_purchase_payment_type`
2. `entities/expense/business/service.py:538` — `enqueue_coding_recode_on_submit`
3. `entities/expense/sql/dbo.expense.sql` — `ReadUncodedCompletedExpenseCandidates`
4. `entities/expense_coding_item/sql/dbo.expense_coding_item.sql` — `ReadExpenseCodingStateByExpenseIds`
5. `integrations/intuit/qbo/outbox/business/worker.py` — the two-hop Python resolve

**Why it matters, with evidence rather than principle:** the invariant is six join legs, and a QBO `Line.Id` is unique only *within* its parent — live data has `QboLineId='1'` on 7,741 distinct Purchases. Dropping any one leg cross-matches across purchases on a path that writes to live QuickBooks. Five hand-copies is five chances to drop a leg. This already happened: U-480 hand-wrote the hop on 2026-09-17, thirteen days after U-364 retired the table it depended on, and pinned the wrong mechanism.

**Recommended shape (Pass 2's, and I agree):** split by layer rather than unifying.
- SQL: add `dbo.vw_ExpensePurchaseLineIdentity` next to `integrations/intuit/qbo/base/sql/qbo.identity_reverse_lookup_views.sql` (U-238c precedent — read-only, purely additive). Repoint the four SQL sites one at a time and move each site's six-leg assertions onto the view, so the pin gets *stronger*: one place asserting the equalities, four asserting they use it.
- Python: do **not** share the SQL object — the worker needs the service seams for their authz gates. Give it `ExpenseLineItemService.read_by_purchase_qbo_identity(purchase_qbo_id, realm_id, qbo_line_id)` instead.

Takes five expressions to two. **Deliberately not done inside the DROP sequence** — a new prod object did not belong in a precondition-heavy migration.

---

## U-499 — extract `_resolve_recode_line_economics`

**Found by:** Pass 2 (simplification), which supplied a proof of equivalence rather than an assertion.

`_handle_recode_purchase_line` carries a `realm_mismatch` tuple across ~20 lines so a three-branch `if/elif/else` can reconstruct, after the fact, which of three things went wrong — information known at the moment each check failed. Extract a method returning `(line, reason)` and let each failure exit at its own site. ~50 lines → ~25.

**Equivalence argument, worth preserving:** the three reason strings are byte-identical, so recorded `details` are unchanged; `realm_mismatch` can only be set when `local_line is not None`, so no input reaches both the line-is-None branch and the realm branch — the current precedence (expense → realm → line) and the flattened one (expense → line → realm) partition the same inputs identically; and the lazy imports stay inside the same `try`, so an `ImportError` still lands in the same `except`.

**Surface:** `integrations/intuit/qbo/outbox/business/worker.py`, `tests/test_u492_recode_dbo_native_line_resolve.py`. Strictly behavior-preserving.

---

## U-500 — no CompanyId scoping between the resolved Expense and the coding item

**Found by:** Pass 3 (security), rated Low/Info.

Nothing checks that the resolved `Expense.CompanyId` matches the `ExpenseCodingItem`'s. **Realm is the only scoping on this path**, before and after family 11. Not exploitable today — one realm, one Company in prod — and it becomes real only if two build.one Companies ever share a QBO realm.

Pass 3's related note: the realm guard's specs (S9-S11) drive the handler with `SimpleNamespace` mocks. U-495 closed that with model- and sproc-level pins (S12/S13); apply the same discipline to any CompanyId check, or it will be vacuous the same way.

**Surface:** `integrations/intuit/qbo/outbox/business/worker.py`, `entities/expense/business/service.py`.

---

## U-501 — delete the guarded bridges (three from family 11, two orphaned from family 10)

Now unblocked: the table is dropped, so all five are permanent no-ops.

**Family 11 (this work):**
- `entities/expense/sql/dbo.expense.sql` — inside `DeleteExpenseCascadeById`
- `entities/expense_line_item/business/service.py`
- `integrations/intuit/qbo/purchase/business/service.py`

**Family 10, orphaned since 2026-09-03 — fold in:**
- `integrations/intuit/qbo/bill/business/service.py` (two bridges; their own docstring says to delete them once `/em` applies the DROP)

**Verified safe before booking:** the bridge pattern was proven post-drop by replicating it exactly in a temp proc — the `OBJECT_ID` guard no-ops and the proc completes — and `sp_refreshsqlmodule 'dbo.DeleteExpenseCascadeById'` confirms the cascade still compiles. So this is cleanup, not a fix. Do it anyway: family 10 booked the identical cleanup as "PROVEN SAFE — quick follow-up" and never did it, which is how its `identity_drift.py` row silently broke three scripts for 17 days (fixed by U-493a).

**Also update:** `tests/test_u241_invoice_and_line_qbo_mapping_cleanup.py:236-245` (docstring now false — the table *is* dropped), `tests/test_u468_expense_terminal_lock.py:982,1060` (patch the bridge), `tests/test_u364_expense_line_item_mapping_retire.py:564`, and the historical SQL in `integrations/intuit/qbo/purchase/sql/{cleanup_orphaned_line_mappings,add_fk_constraints_to_mapping_tables}.sql`.

---

## Collision matrix — these are NOT all parallel-safe

| | worker.py | expense svc | dbo.expense.sql | eci sql | eli svc | bill svc |
|---|---|---|---|---|---|---|
| U-497 | — | ✎ | — | ✎ | — | — |
| U-498 | ✎ | ✎ | ✎ | ✎ | ✎ | — |
| U-499 | ✎ | — | — | — | — | — |
| U-500 | ✎ | ✎ | — | — | — | — |
| U-501 | — | — | ✎ | — | ✎ | ✎ |

**U-498 touches nearly everything** — it should land alone, after the others, or absorb them. **U-499 and U-500 both touch only `worker.py`** and should be one unit or strictly sequenced. **U-501 is the most independent** and is pure deletion.

Suggested order: **U-501** (independent, unblocks nothing else) → **U-499 + U-500 together** (same file, same handler) → **U-497** (decides the anchor question U-498 depends on) → **U-498** last, informed by U-497's decision.
