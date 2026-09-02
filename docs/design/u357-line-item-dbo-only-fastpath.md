# U-357 — `run_line_identity_fastpath_dbo_only` + retire `qbo.VendorCreditLineItemBillCreditLineItem` (DESIGN)

**Status:** Phase-1 design, awaiting `/em` Gate-2 approval. Approval dispatches the build (U-357).
**Class:** FOUNDATIONAL / shared-primitive → **two-phase, design-gated** (`feedback_two_phase_dispatch_design_gated.md`).
**Origin:** U-349 program §7. The 4 remaining families (#8–#11) are LINE-ITEM connectors, and there is
**no dbo-only line fast path** — only the header `run_identity_fastpath_dbo_only` (U-350) and the
WITH-mapping `run_line_identity_fastpath`. #8 (vendorcredit_line_item, the simplest line family: 0
reconciliation consumers, 0 real cross-family — the only refs are comments) is the pattern-setter that
BUILDS the shared line helper, then #9–#11 clone it.

---

## 1. Why a NEW primitive (not just reuse the header dbo-only helper)

Line identity is **parent-scoped**, not globally unique. `run_identity_fastpath_dbo_only` keys on
`(qbo_id, realm_id)`; a line keys on **`(parent_local_id, qbo_line_id)`** — QBO reuses line ids `1,2,3…`
across every parent transaction (verified: `run_line_identity_fastpath`'s own Gate-1 note). The existing
`run_line_identity_fastpath` already encodes this shape (parent+line key, **no realm param** — the parent
header pins the realm, resolved before the line helper ever runs). So the new dbo-only line helper must be
the **dbo-only variant of `run_line_identity_fastpath`**, not a re-key of the header helper.

## 2. Proposed signature (the analog, mapping-free)

Mirror `run_identity_fastpath_dbo_only`'s dbo-only shape, but parent-scoped like `run_line_identity_fastpath`:

```python
def run_line_identity_fastpath_dbo_only(
    *,
    parent_local_id: int,
    qbo_line_id: Optional[str],
    entity_label: str,
    external_label: str,
    lock_resource_label: str,                 # create-lock resource; see §3
    read_direct_by_parent_and_qbo_line_id: Callable[[int, str], Any],   # dbo-native, e.g. BillCreditLineItemService.read_by_qbo_identity
    resolve_candidate: Callable[[], Any],     # MISS branch: create the line (lines have no adopt key — see §4)
    stamp_identity: Callable[[Any], Any],     # stamp dbo.<Line>.QboId scoped by parent + return re-read
    apply_fields: Optional[Callable[[Any], Any]] = None,
    on_apply_returned_none: Optional[Callable[[Any], None]] = None,
    lock_timeout_ms: int = 15000,
) -> FastPathOutcome
```

**DROPPED vs `run_line_identity_fastpath`** (all mapping-specific): `read_by_local_id`,
`read_by_external_id`, `record_conflict_issue`, `conflict_message`, `external_id_attr`. With
`dbo.<Line>.QboId` (parent-scoped) as the SOLE identity store there is no second store to disagree with,
so the mapping-vs-dbo conflict check vanishes (same reasoning as every header dbo-only connector).
**ADDED vs the with-mapping line helper:** `resolve_candidate` + `stamp_identity` + the create lock — the
dbo-only MISS branch (create-then-stamp under lock), identical in spirit to the header helper's.

## 3. OPEN DESIGN DECISION #1 — the create lock (needs /em ruling)

The header dbo-only helper takes a create lock (`lock_resource_label`, 15s) to serialize two concurrent
syncs of the SAME `qbo_id`. For a line, the question is whether it needs its OWN parent+line-scoped lock,
or whether the **parent header's create lock already serializes it** (a line is only ever created inside
its parent's sync, which the header helper already locks on `(parent qbo_id)`).
- **Option A (recommended):** parent+line-scoped lock `f"{lock_resource_label}:{parent_local_id}:{qbo_line_id}"`
  — defense-in-depth, mirrors the header helper exactly, cheap. Matches U-350/U-353's "carry the lock even
  when the race is unlikely" precedent UNLESS /simplify refutes it.
- **Option B:** no line lock, rely on the parent lock — matches the *existing* `run_line_identity_fastpath`
  (which has no lock). Lighter, but couples correctness to the caller always holding the parent lock.
Recommend **A** for symmetry + independence; flag for the builder to /simplify-review (U-352 dropped an
unreachable line lock as over-engineering — the builder must confirm which case this is with a test, not assert).

## 4. OPEN DESIGN DECISION #2 — MISS branch: create-only (no adopt)

Header connectors' `resolve_candidate` can ADOPT a pre-existing row by a business key (Company by name).
A line has **no independent adopt key** — it is always created fresh under its parent (the parent adopt/create
already happened). So `resolve_candidate` for lines = **create fresh**, no adopt, no `_check_no_conflicting`
guard (there is no side-channel key two syncs could both resolve to). This matches Term's shape (U-352,
no-adopt) more than Company's. Confirm in build: the pre-U-357 line create path has no adopt step.

## 5. First use — retire `qbo.VendorCreditLineItemBillCreditLineItem` (the #8 payload)

- Connector `integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/business/service.py`:
  `sync_from_qbo_line` currently uses `run_line_identity_fastpath` +
  `VendorCreditLineItemBillCreditLineItemMappingRepository` (read_by_qbo_line_id / read_by_bill_credit_line_item_id
  + create/delete). Repoint onto `run_line_identity_fastpath_dbo_only` with dbo-native identity via
  `entities/bill_credit_line_item/business/service.py:112 read_by_qbo_identity(bill_credit_id, qbo_id)` +
  `set_qbo_identity`. 0 reconciliation consumers; the 2 entities/bill_credit_line_item refs are comments.
- Also fold in the U-341 `create_mapping_then_stamp` / `stamp_line_identity_or_warn` usage: those wrap the
  mapping WRITE — with the mapping gone, the write collapses to the bare `set_qbo_identity` stamp (mirror how
  U-350/U-353 collapsed `create_mapping` to a pure identity stamp).
- RETIRE: the mapping repo, model, the connector's `qbo.vendorcredit_line_item_bill_credit_line_item.sql`
  (table DDL + sprocs), the `_resolve_mapping_state` seam. Prune any identity_drift.py entry (NOTE: line
  families are NOT `FlatEntitySpec` header entities — likely no registry row; confirm).
- **DROP** (builder hands /em, live sys.procedures-verified): the mapping table + its sprocs. FK topology:
  the earlier census (§2 of u349 doc) showed 0 incoming FKs to this mapping; confirm at build. `qbo.*` 24 → 23.

## 6. Testing / mutation (the primitive earns extra rigor)

- **Unit-test the helper directly** (not only through the connector): HIT (dbo identity resolves → apply),
  MISS (no dbo identity → resolve_candidate creates → stamp), stamp-failure rollback (the U-354/U-355 race
  fix, now a helper-level guarantee), apply-returns-None → `raise_concurrent_write_race`.
- **Mutation-prove** the helper's dbo-native resolution AND the create-lock (whichever §3 option lands).
- The connector repoint gets the standard mutation proof (stamp neuter → targeted test RED).
- Full suite green at a CLEAN git-worktree checkout (avoid the AST-scanner pollution that bit U-354).
- `_MIN_CALL_SITES`: recompute if the recorder count changes (U-354's miss); run at a clean checkout.

## 7. Clone path (#9–#11, informs the primitive's shape NOW)

Design the helper so these clone cleanly — each is a line family with a parent-scoped `read_by_qbo_identity`:
- #9 invoice_line_item (InvoiceLineItemInvoiceLine) — 0 recon; clones directly.
- #10 bill_line_item (BillLineItemBillLine) — **has a reconciliation JOIN** → also needs the U-356 dbo-native
  recon re-expression on top (serial on reconciliation/service.py).
- #11 expense_line_item (PurchaseLineExpenseLineItem) — **reconciliation JOIN** → same, serial.
So the helper must NOT bake in anything vendorcredit-specific; keep it purely `(parent, qbo_line_id)`-generic.

## 8. Decisions for `/em` (Gate-2 of this design)

1. **Approve building the shared `run_line_identity_fastpath_dbo_only` primitive** in this unit (vs inlining
   dbo-only resolution per line connector — rejected: 4 hand-copies, the exact anti-pattern the header helper avoided).
2. **§3 create-lock:** Option A (parent+line-scoped lock, recommended) vs B (rely on parent lock).
3. **§4 MISS branch:** confirm create-only (no adopt) is correct for lines.
4. **Dispatch:** approve → the U-357 BUILD is dispatched as a separate prompt (Composer/Claude writes, Claude
   8-angle reviews — Codex out of credits); builder hands /em the DROP; /em deploys + drops per the usual runbook.
