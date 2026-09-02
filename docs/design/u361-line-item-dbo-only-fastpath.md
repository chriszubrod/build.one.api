# U-361 — `run_line_identity_fastpath_dbo_only` + retire `qbo.VendorCreditLineItemBillCreditLineItem` (DESIGN — APPROVED)

**Status:** `/em` Gate-2 APPROVED 2026-09-01 (decisions §8 resolved below). Build dispatches as U-361.
**Build (2026-09-01, U-361 build session):** implemented as designed, with these build-time findings —
(a) §3 create lock KEPT (Option A): it is reachable, shown by test (`tests/test_u361_bill_credit_line_item_
mapping_retire.py::test_header_hit_path_syncs_lines_with_no_lock_held`) — the header's HIT path syncs lines
with no lock held, and reconciliation's missing-locally autofix holds no pull-level lock; on the header
CREATE path the line lock nests one-directionally inside `qbo_dbo_identity_create:*`. (b) §4 "confirm no adopt
step": the pre-U-361 path DID have a content-fingerprint adopt over *unmapped* lines; it has no dbo-only
translation (a stamped orphan never looks unmapped), so create-only means a QBO line-id regeneration creates
a sibling row instead of re-adopting the stamped orphan — accepted per §8.3. (c) §2 signature gained ONE
param, `rollback_candidate`, to make the U-354/U-355 stamp rollback a helper guarantee (stamp raises OR the
re-read does not carry `qbo_id` → rollback + re-raise; stamp returns None → race, no rollback). (d) §5 "0
reconciliation consumers" was wrong: `vendorcredit/business/service.py` had two executed consumers (deleted-
credit reconcile Step 1, stale-line cleanup), both repointed; "likely no identity_drift row" was wrong: the
`LINE_ENTITY_SPECS` row + 3 script consumers were pruned. (e) Live: the 4 sprocs exist in BOTH dbo and qbo
schemas (8 DROPs), 0 incoming FKs, map_rows 445 == dbo_stamped 445, null_realm 0, drift 0.
**Unit id:** was U-357 in the u349 running order; **renumbered to U-361** because a concurrent session
claimed U-357 for an unrelated "unified lifecycle status + review_status" design (commit 53f39d32). The
line-item block is now U-361 (vendorcredit_line_item) → U-362 (invoice_line_item) → U-363 (bill_line_item)
→ U-364 (expense_line_item). u349 §7 updated to match.
**Class:** FOUNDATIONAL / shared-primitive → **two-phase, design-gated** (`feedback_two_phase_dispatch_design_gated.md`).
**Origin:** U-349 program §7. The 4 remaining families are LINE-ITEM connectors, and there is **no dbo-only
line fast path** — only the header `run_identity_fastpath_dbo_only` (U-350) and the WITH-mapping
`run_line_identity_fastpath`. U-361 (vendorcredit_line_item, the simplest line family: 0 reconciliation
consumers, 0 real cross-family — the only refs are comments) is the pattern-setter that BUILDS the shared
line helper, then U-362–364 clone it.

---

## 1. Why a NEW primitive (not just reuse the header dbo-only helper)

Line identity is **parent-scoped**, not globally unique. `run_identity_fastpath_dbo_only` keys on
`(qbo_id, realm_id)`; a line keys on **`(parent_local_id, qbo_line_id)`** — QBO reuses line ids `1,2,3…`
across every parent transaction (verified: `run_line_identity_fastpath`'s own Gate-1 note). The existing
`run_line_identity_fastpath` already encodes this shape (parent+line key, **no realm param** — the parent
header pins the realm, resolved before the line helper ever runs). So the new dbo-only line helper is the
**dbo-only variant of `run_line_identity_fastpath`**, not a re-key of the header helper.

## 2. Approved signature (the analog, mapping-free)

Mirror `run_identity_fastpath_dbo_only`'s dbo-only shape, parent-scoped like `run_line_identity_fastpath`:

```python
def run_line_identity_fastpath_dbo_only(
    *,
    parent_local_id: int,
    qbo_line_id: Optional[str],
    entity_label: str,
    external_label: str,
    lock_resource_label: str,                 # create-lock resource; keyed parent+line — §3 (Option A approved)
    read_direct_by_parent_and_qbo_line_id: Callable[[int, str], Any],   # dbo-native, e.g. BillCreditLineItemService.read_by_qbo_identity
    resolve_candidate: Callable[[], Any],     # MISS branch: create the line (create-only, no adopt — §4 approved)
    stamp_identity: Callable[[Any], Any],     # stamp dbo.<Line>.QboId scoped by parent + return re-read
    apply_fields: Optional[Callable[[Any], Any]] = None,
    on_apply_returned_none: Optional[Callable[[Any], None]] = None,
    lock_timeout_ms: int = 15000,
) -> FastPathOutcome
```

**DROPPED vs `run_line_identity_fastpath`** (all mapping-specific): `read_by_local_id`,
`read_by_external_id`, `record_conflict_issue`, `conflict_message`, `external_id_attr`. With
`dbo.<Line>.QboId` (parent-scoped) as the SOLE identity store there is no second store to disagree with, so
the mapping-vs-dbo conflict check vanishes (same reasoning as every header dbo-only connector).
**ADDED vs the with-mapping line helper:** `resolve_candidate` + `stamp_identity` + the create lock.

## 3. Create lock — APPROVED: Option A (parent+line-scoped)

Take a create lock keyed `f"{lock_resource_label}:{parent_local_id}:{qbo_line_id}"` (15s), mirroring the
header helper's own create lock — defense-in-depth + independence from the caller always holding the parent
lock. **Build note:** /simplify-review whether it is genuinely reachable (U-352 dropped an *unreachable* line
lock as over-engineering); if a test proves the parent lock already fully serializes and the line lock is
unreachable, dropping it is acceptable — but that must be shown with a test, not asserted.

## 4. MISS branch — APPROVED: create-only (no adopt)

A line has no independent adopt key — it is always created fresh under its parent (the parent adopt/create
already happened). `resolve_candidate` for lines = **create fresh**, no adopt, no `_check_no_conflicting`
guard (no side-channel key two syncs could both resolve to). Matches Term's shape (U-352, no-adopt), not
Company's. Confirm in build that the pre-U-361 line create path has no adopt step.

## 5. First use — retire `qbo.VendorCreditLineItemBillCreditLineItem` (the U-361 payload)

- Connector `integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/business/service.py`:
  `sync_from_qbo_line` currently uses `run_line_identity_fastpath` +
  `VendorCreditLineItemBillCreditLineItemMappingRepository` (read_by_qbo_line_id / read_by_bill_credit_line_item_id
  + create/delete). Repoint onto `run_line_identity_fastpath_dbo_only` with dbo-native identity via
  `entities/bill_credit_line_item/business/service.py:112 read_by_qbo_identity(bill_credit_id, qbo_id)` +
  `set_qbo_identity`. 0 reconciliation consumers; the 2 entities/bill_credit_line_item refs are comments.
- Fold in the U-341 `create_mapping_then_stamp` / `stamp_line_identity_or_warn` usage: those wrap the mapping
  WRITE — with the mapping gone, the write collapses to the bare `set_qbo_identity` stamp (mirror how
  U-350/U-353 collapsed `create_mapping` to a pure identity stamp).
- RETIRE: the mapping repo, model, the connector's `qbo.vendorcredit_line_item_bill_credit_line_item.sql`
  (table DDL + sprocs), the `_resolve_mapping_state` seam. Line families are NOT `FlatEntitySpec` header
  entities → likely no identity_drift.py row; confirm.
- **DROP** (builder hands /em, live sys.procedures-verified — watch for qbo-schema duplicates per U-353):
  the mapping table + its sprocs. FK topology: earlier census showed 0 incoming FKs; confirm at build.
  `qbo.*` 24 → 23.

## 6. Testing / mutation (the primitive earns extra rigor)

- **Unit-test the helper directly** (not only through the connector): HIT, MISS (create→stamp),
  stamp-failure rollback (the U-354/U-355 race fix, now a helper-level guarantee), apply-returns-None →
  `raise_concurrent_write_race`.
- **Mutation-prove** the helper's dbo-native resolution AND the create lock (Option A).
- The connector repoint gets the standard mutation proof (stamp neuter → targeted test RED).
- Full suite green at a CLEAN git-worktree checkout (avoid the AST-scanner pollution that bit U-354).
- `_MIN_CALL_SITES`: recompute if the recorder count changes (U-354's miss); run at a clean checkout.

## 7. Clone path (U-362–364 — informs the primitive's shape NOW)

Design the helper so these clone cleanly — each is a line family with a parent-scoped `read_by_qbo_identity`:
- U-362 invoice_line_item (InvoiceLineItemInvoiceLine) — 0 recon; clones directly.
- U-363 bill_line_item (BillLineItemBillLine) — **has a reconciliation JOIN** → also needs the U-356 dbo-native
  recon re-expression on top (serial on reconciliation/service.py).
- U-364 expense_line_item (PurchaseLineExpenseLineItem) — **reconciliation JOIN** → same, serial.
Keep the helper purely `(parent, qbo_line_id)`-generic — nothing vendorcredit-specific.

## 8. Decisions — RESOLVED (`/em` Gate-2, 2026-09-01)

1. **Build the shared `run_line_identity_fastpath_dbo_only` primitive in this unit** — APPROVED (inlining
   dbo-only resolution 4× is the anti-pattern the header helper avoided).
2. **Create lock:** APPROVED **Option A** (parent+line-scoped, 15s) — with the build /simplify-reachability check (§3).
3. **MISS branch:** APPROVED **create-only, no adopt** (§4).
4. **Dispatch:** approved → U-361 BUILD dispatched as a separate prompt (Claude writes, Claude 8-angle reviews —
   Codex out of credits); builder hands /em the DROP; /em deploys + drops per the usual runbook.
