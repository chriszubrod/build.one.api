# Family-11 follow-ups (U-497 … U-502)

**Booked 2026-09-21, then SCOPED the same day — four of the five original bookings were wrong in a material way.** The "what scoping changed" table is the fastest way in; each entry records what was measured, not what was assumed.

They belong in `TODO.md`, but a parallel session has held that file uncommitted throughout, so they live here. **Move them into `TODO.md` once that session lands, then delete this file.**

Context: `qbo.PurchaseLineExpenseLineItem` was dropped 2026-09-21 17:38:15 UTC, closing the last of the eleven U-349 mapping families. `qbo.*` is now 20 base tables.

---

## What scoping changed

| Unit | Booked as | Actually |
|---|---|---|
| U-497 | repoint 139 stale anchors | **repointing reads recovers ZERO** — both resolutions agree 613/613. 134 already closed; **5** live. The real fix is the MERGE key, not the readers. |
| U-498 | 5 copies of one chain | **three different shapes**; the worker has no staging hop at all. A cross-schema view **cannot be created in the SQLite fixture** that provides the only non-vacuous coverage. |
| U-499 | extract a helper | equivalence verified — plus **three constraints the booking missed**, one of which would silently void 14 specs. |
| U-500 | add a CompanyId check | **DECLINE.** CompanyId is never written, so the guard would be born vacuous and stay vacuous through the exact event it guards. Replaced by U-500a. |
| U-501 | 5 inert bridges | **EIGHT** — families 9 and 10 were both missed. And deleting them carelessly **silently breaks the delete cascade**. |

---

## U-497 — the 139 stale anchors: fix the write key, not the readers

**Measured, and it inverts the booking.** Across all 613 `ExpenseCodingItem` rows, staging-PK resolution and dbo-native resolution **agree 613/613** (474 both resolve, 139 both fail). There is not one row where the PK is stale but `QboPurchaseQboId + QboLineId + RealmId` would find a line. **Repointing the readers recovers nothing.** The read anchor is not the defect.

`qbo.PurchaseLine.Id` is `IDENTITY`, so PK reuse is impossible — a stale PK resolves to nothing, never to a wrong line. Every reader is fail-closed. One visible inconsistency: the cockpit work list is line-driven so it cannot surface these, while the funnel's per-status counts are `ExpenseCodingItem`-driven and *do* count them — 5 phantom open items.

**The real defect is one level up.** `QboPurchaseLineId` is the MERGE/dedupe key on create (`dbo.expense_coding_item.sql:96-97`, unique index `:62-65`) *and* durable provenance (`entities/expense/business/service.py:1853`). Pinning a coding item's identity to a row the pull is licensed to delete is what makes staleness terminal instead of self-healing. Move the MERGE key to `(QboPurchaseQboId, RealmId, QboLineId)`, keeping `QboPurchaseLineId` in the `WHEN MATCHED` UPDATE so it refreshes on every reseed — then every staging-PK reader starts re-resolving with no reader edit. **Preconditions measured clean: 0 NULLs, 0 duplicate groups across all 613 rows.**

**Required companion:** arm 1 of `ReadExternallyResolvedCodingItemCandidates` (`:908-909`) currently *means* "my staging PK is gone". After the key change it must be restated dbo-natively as "no live `qbo.PurchaseLine` under my purchase carries my `QboLineId`" — verified to return the same 139. Pins at `tests/test_u483_externally_resolved_coding_items.py:82,121-122` move with it.

**Data repair is 96% done: 134 of 139 are already `resolved_externally`. Only 5 are live work**, and all 5 are already candidates for the existing U-483 sweep (`MarkExpenseCodingResolvedExternally`) — which is manual-only, DRAIN_SECRET-gated, `apply` defaults false, and has no scheduler timer. One apply-run closes them. No new close-out code.

**Surface:** `entities/expense_coding_item/` (sql :20-24, :62-65, :96-97, :908-909; repo; service) + a DDL migration. **No reader files.** This drops U-497 almost entirely out of the collision matrix.

**⚠️ Inverts the original ordering:** U-497 no longer gates U-498.

---

## U-498 — consolidate the identity chain (FOR, but narrower and later)

**The "five copies of one chain" framing was wrong — they are three shapes:**

- **Shape A, 6 legs, Expense-driven** — `_read_qbo_purchase_payment_type` (`service.py:448`), `ReadUncodedCompletedExpenseCandidates`
- **Shape B, 7 legs, CodingItem-driven** — `enqueue_coding_recode_on_submit` (`service.py:486`), `ReadExpenseCodingStateByExpenseIds`; adds the staging-PK entry `pl.[Id] = eci.[QboPurchaseLineId]`
- **Shape C, 4 legs, dbo-only** — the worker. **It never reads `qbo.Purchase` or `qbo.PurchaseLine` at all**, and is parent-scoped by `expense.id` (a PK) before `qbo_line_id` is used. The stated hazard does not apply to it; the equivalent mistake is structurally impossible.

A view can still serve all four SQL sites — their selective predicates all sit outside the six legs — but it must be **wide** (~20 columns). An identity-only view puts all four tables in the plan twice, and site 3 is unparameterized, so the doubled read would be table-wide.

**Two findings that change the cost:**

1. **The SQLite fixture cannot host a cross-schema view.** Probed directly: `CREATE VIEW dbo.v AS SELECT ... FROM dbo.E JOIN qbo.P` → `view v cannot reference objects in database qbo`, and symmetrically from `main`. Both `_sqlite_fixture_conn` helpers build `dbo`/`qbo` as separate `ATTACH ':memory:'` databases precisely so each site's literal SQL runs unmodified. So the repoint breaks the **behavioral** half of `test_u491_*` and `test_u494_*`, not just the textual half. Workaround verified: `CREATE VIEW temp.v ...` works (temp is exempt) — a one-token fixture rewrite.
2. **The cited precedent is dead code.** `vw_QboCustomerIdentity` / `vw_QboItemIdentity` have **zero consumers** repo-wide. The live precedent is `vw_AgentSession` (`intelligence/persistence/sql/dbo.agent_session.sql:149`), read by seven sprocs **in the same file** — so one `run_sql.py` apply creates them in order.

**Deployment hazard:** deferred name resolution means `CREATE OR ALTER PROCEDURE` succeeds against a view that does not exist and fails on first call, in prod. `run_sql.py` applies one file at a time with no ordering manifest. The view must be applied first, and the runbook must say so.

**The cheaper alternative, which should be evaluated first:** leave the four copies and add **one shared test** asserting all four sites carry byte-identical leg sets. Catches divergence with no prod object, no fixture rewrite, and no landing-order failure. If 498a's showplan gate returns anything but "identical", take this instead.

**Stage as:** 498a view + tests (additive, zero sites repointed, gated on a live showplan comparison) → 498b site 3 → 498c site 4 (RBAC-bearing) → 498d site 2 → 498e site 1 (which may not belong in the view at all — its line hop is a pure existence filter).

**The Python half is not a separate thing — it IS U-499.** `read_by_purchase_qbo_identity(...)` cannot be a drop-in: the worker needs the intermediate `Expense` that signature swallows (`expense.id` appears in two of three reason strings, and three outcomes need distinguishing). A signature preserving that returns `(expense, line, reason)` — which is exactly `_resolve_recode_line_economics`.

**Doc correction:** the original booking cited `service.py:466`/`:538`; actual defs are `:448`/`:486`.

---

## U-499 — extract `_resolve_recode_line_economics` (verified, with three added constraints)

**The equivalence argument holds.** No counterexample exists, for a citable reason: the only assignment to `realm_mismatch` (`worker.py:885`) is lexically inside a branch guarded by `local_line is not None` (`:881`) which immediately nulls `local_line` (`:886`). So `realm_mismatch is not None` ⟹ a line was found, making the two reason-branches disjoint and the two precedence orders identical partitions. Five adversarial cases were tried and refuted (falsy-not-None line; non-None `line_realm` with `local_line` None; reason C touching `expense.id` when expense is None; `row.realm_id is None`; exception-surfacing order).

**Three constraints the booking missed — these are what will actually break it:**

1. **The `try` boundary is load-bearing.** The helper call, `record_mapping_issue` (`:917`), *and* the lazy imports (`:850-855`) must all stay inside the `try:` at `:847`. Move the recorder out and a `ReconciliationIssueRepository()` failure stops being swallowed and starts retrying the outbox row.
2. **⚠️ The lazy imports are a TEST CONTRACT, not style.** `tests/test_u492_*:26-34` patches *source modules*; those patches only bite because names resolve at call time. Hoist any import to worker module scope and **S1–S14 patch nothing** — S4/S14 would hit the real recorder. This is the one that voids 14 specs silently.
3. **Partial-stamp hazard** at `:887-891`: four sequential attribute reads, a failure on `.rate` leaves `quantity` set, swallowed at `:939`. Pre-existing — keep them inside the `try` so a behavior-preserving unit does not quietly change it.

Carry the U-492-round-2 comment block (`:869-878`) with the `getattr`, not orphaned at the call site — it records why NULL is not a mismatch (124 live NULL-realm rows must keep resolving).

**New specs needed:** S15 helper contract (exactly one of `(line, reason)` non-None); S16 reason strings byte-identical — assert with `==` on the full string, since `"refusing" in details` stays GREEN against a one-hyphen mutation; S17 precedence, **documenting that swapping the two early returns does NOT go RED and that non-firing is the proof of disjointness**, so a future reader does not "fix" it; S18 try boundary (no coverage today); S19 imports stay lazy.

---

## U-500 — DECLINE as scoped. Replaced by U-500a.

**Why.** `ExpenseCodingItem.CompanyId` exists (`BIGINT NOT NULL DEFAULT 1`) — **but the MERGE's INSERT column list omits it**, so every row takes the default. Verified: 613 ECI rows, all `CompanyId = 1`. On the other side, `dbo.Expense.CompanyId` exists but is **not projected** by `ReadExpenseByQboIdAndRealmId` and is **not a field on the `Expense` dataclass** (verified: 0 occurrences of `company_id` in the model). 11,826 Expense rows, all `CompanyId = 1`.

So the comparison would be `1 != 1`, and it would **stay** `1 != 1` the day a second Company is onboarded, because neither write path derives CompanyId from anything. **The guard would be born vacuous and remain vacuous through exactly the event it claims to protect against** — the U-495 failure mode reproduced rather than fixed, and worse than absent because it reads as scoping.

**U-500a — the real fix.** The boundary on this path is `dbo.Company.RealmId`: today realm-scoping *is* Company-scoping, exactly. Make that database-enforced rather than a data accident. `UQ_Company_QboId_RealmId` is on `(QboId, RealmId)` and **does not forbid two Companies sharing a RealmId** — verified against `sys.indexes`; 0 sharing today. Add a filtered unique index on `Company.RealmId` alone. One additive DDL object plus one pin, and it upgrades the existing realm guard into a *proven* Company guard.

**If a CompanyId check is ever revived it needs four pins, not two** — model field, sproc projection, repo mapping, and decisively **population** (the ECI INSERT must name `[CompanyId]` from authz context). Without the fourth, the first three all pass with both sides constant `1`.

**Open for `/pm`:** the unique index forecloses "two Companies, one realm" but leaves "one Company, many realms" open. The existing `(QboId, RealmId)` index hints multi-realm was contemplated.

---

## U-501 — delete the inert bridges. There are EIGHT, and the deletion is not safe by default.

**All three guarded tables confirmed dropped live** (`PurchaseLineExpenseLineItem`, `BillLineItemBillLine`, `InvoiceLineItemInvoiceLine`), and **no FK survives from `qbo` onto `dbo.BillLineItem` / `ExpenseLineItem` / `InvoiceLineItem`**. All eight are inert.

| # | Family | Location |
|---|---|---|
| B1 | 11 | `entities/expense/sql/dbo.expense.sql:966-975` (in `DeleteExpenseCascadeById`) |
| B2 | 11 | `entities/expense_line_item/business/service.py:26-55` + call `:388` |
| B3 | 11 | `integrations/intuit/qbo/purchase/business/service.py:24-55` + calls `:399`, `:470` |
| B4 | 10 | `integrations/intuit/qbo/bill/business/service.py:23-54` + calls `:220`, `:490` — one function, two call sites (the booking's "two bridges") |
| **B5** | 10 | `entities/bill/sql/dbo.bill.sql:931-941` — **MISSED** |
| **B6** | 10 | `entities/bill_line_item/sql/dbo.bill_line_item.sql:616-623` — **MISSED** |
| **B7** | 9 | `entities/invoice_line_item/business/service.py:23-55` + call `:308` — **whole family missed** |
| **B8** | 9 | `integrations/intuit/qbo/invoice/business/service.py:23-53` + call `:439` — **MISSED** |

Earlier families left none — U-365 and U-226 cleaned those up. Eight is the complete set.

### ⚠️ The dangling-`IF` hazard — the real risk in this unit

All three SQL bridges are `IF <cond>` followed by an **unbraced single statement**. Delete only the guarded `DELETE` and leave the `IF`, and it rebinds to the next statement:

```sql
IF OBJECT_ID('qbo.PurchaseLineExpenseLineItem') IS NOT NULL   -- left behind
DELETE FROM dbo.[ExpenseLineItem] WHERE [ExpenseId] = @Id;    -- now CONDITIONAL, always false
```

**The cascade then silently stops deleting expense line items.** The sproc compiles, `sp_refreshsqlmodule` passes, and every existing pin here is a *text* assertion on SQL source, so all stay green. It surfaces later as a 547 on the header delete. Identical shape in `dbo.bill.sql:939`. `dbo.bill_line_item.sql` is benign — it rebinds to a bare `END`, a loud syntax error.

**Mandate: delete each `IF` and its `DELETE` as one contiguous block, comment included.**

### One behavior change, not pure deletion

B3/B4's reconcile paths append `"...mapping"` to `destructive_labels`, feeding `record_partial_delete_issue`. Today that label is added as soon as the staging parent has any line, so a later failure records work that never happened. Removing it makes the label truthful — correct, but it *is* a change in a reconciliation recorder and belongs in the commit message.

### Tests
Baseline 378 tests across the ten affected files, 0 failures (needs `PYTHONPATH="$PWD/tests:$PWD"`). Must be removed or rewritten: `test_u241_*` (five blocks + the now-false docstring at `:236-245`), `test_u446c_atomic_cascade.py:235-241` (delete) and `:151` (remove one tuple entry, **keep the test** — it is U-446c's P0 moved-line guard), `test_u364_*` (four blocks, keep `:578-594`), `test_u363_*` (four blocks, **keep `:565-660`** — self-contained SQLite fixture), `test_u362_*:573-612`, `test_u468_*:982,1060` (remove the two patch entries only — they would `AttributeError` at patch time), `test_u491_*:276-283`.

**Test strategy that actually proves it:** replace each "guard present" pin with its inverse (`"qbo." not in body`); add a direct dangling-`IF` pin; mutation-prove both (delete only the inner `DELETE` → dangling pin must go RED; re-insert the mapping DELETE → no-`qbo.` pin must go RED); and **one behavioral proof per cascade** under `system_authz()` inside a rolled-back transaction, since `sp_refreshsqlmodule` proves compile, not deletion.

### Historical SQL — annotate at EOF, do not delete
`u493_drop_...sql:156-165` cites `add_fk_constraints_to_mapping_tables.sql` **lines 75-98 by line number** as the DROP's mechanical rollback. Deleting it guts a rollback path a live migration points at. **Prepending a header would stale that citation** — append the RETIRED banner at EOF, or update `u493:156,161` in the same commit. `TODO.md:1805` also cites it.

---

## U-502 — correct two factually wrong FK comments

`FK_PurchaseLineExpenseLineItem_QboPurchaseLine` was **CASCADE**, not NO ACTION (only the `_ExpenseLineItem` side was NO ACTION — both measured this session). So the stale-line delete was never blocked by a 547, and two comments assert otherwise:

- `integrations/intuit/qbo/purchase/business/service.py:30-31,48-51` — the bridge docstring's entire stated rationale.
- `entities/expense/sql/dbo.expense.sql:1386-1388` — **mine, written in U-495.** The ROW_NUMBER tie-break is still correct (there is genuinely no unique constraint on `(QboPurchaseId, QboLineId)` — verified against `sys.indexes`), but the stated *cause* of surviving stale rows is wrong.

A wrong explanation attached to a safety property invites someone to remove the protection. Fold into U-501, which already deletes the first.

---

## Revised landing order

1. **U-497 step 1** — run the existing U-483 sweep with `apply=true`. Closes 5. Independent, no code.
2. **U-501** — eight bridges + U-502's comment fixes. Most independent; pure deletion *if* the dangling-`IF` mandate is followed.
3. **U-499** (absorbing U-498's Python half) — one extraction in `worker.py` returning `(expense, line, reason)`.
4. **U-500a** — filtered unique index on `Company.RealmId`. U-500 as booked is declined.
5. **U-497 steps 2-3** — move the MERGE key; restate arm 1 dbo-natively.
6. **U-498, SQL only**, staged 498a-e — and only if the showplan gate and the cheaper shared-test alternative both fail to satisfy.

Collision note: U-497's revised surface no longer touches `entities/expense/business/service.py`, and U-499 absorbs U-498's Python half — together these remove the two largest cells from the original matrix.
