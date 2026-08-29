# U-333 — QBO ReconciliationIssue recorder consolidation (DESIGN)

**Status:** Phase-1 design, awaiting `/em` Gate-2. No code in this unit — approval dispatches the build as a separate unit.
**Class:** behavior-preserving (`/simplify`-grade), but design-gated because it touches shared `integrations/intuit/qbo/base/` + ~16 connector call sites + the AST width-guard in lockstep.
**Flagged by:** U-331 `/simplify` (2026-08-28). Booked `TODO.md:16`.

---

## 1. Problem

Between two already-shared layers sits a duplicated **middle layer**:

- **below:** `base/reconciliation_recorder.py::record_mapping_issue(...)` — a module-level free function (U-218a): fail-isolated insert into `qbo.ReconciliationIssue`, width-clamps `drift_type`/`entity_type`/`severity`/`action`, validates `drift_type ∈ KNOWN_DRIFT_TYPES`. **Never raises.**
- **above:** `base/identity_fastpath.py` helpers (`run_identity_fastpath`, `run_line_identity_fastpath`, `stamp_dbo_identity_with_lock`, …) — own the control flow and the `raise`, and take the per-family recorder **as a callback** (`record_conflict_issue=` / `on_conflict=`).

The middle layer is **~16 near-identical per-connector `_raise_*` methods** that each build a `details` string and call `record_mapping_issue`. They are **misnamed — none of them raise** (verified across all sites: the `raise` lives in the caller or the fastpath helper). So collapsing them changes **zero raise semantics** — a clean, behavior-preserving seam.

There is **no shared base class** and we are not adding one — the codebase idiom (CLAUDE.md; `TODO.md:1867`) is composition via free functions in `base/*.py`; a base class over materially-different shipped connectors would be a leaky abstraction.

## 2. The three parallel shapes (consolidation targets)

| Shape | Method | Count | Connectors | Key traits |
|---|---|---|---|---|
| **A** | `_raise_identity_mapping_conflict_issue` | 7 | bill, invoice, expense(purchase), company_info, payment_term(term), bill_credit(vendorcredit), physical_address | `entity_public_id=None`; `qbo_id` from `.qbo_id`; `realm_id` off the object; three-part details (qbo-side / local-side / both); default severity |
| **B** | `_raise_line_identity_mapping_conflict_issue` | 4 | bill_line_item, expense_line_item, invoice_line_item, bill_credit_line_item | Identical to A except: adds `realm_id: Optional[str]=None` param (lines carry no realm), `qbo_id` from `.qbo_line_id`, mapping args untyped |
| **C** | `_raise_duplicate_qbo_*_issue` / `_raise_project_identity_conflict_issue` | 5 | customer, vendor, item→cost_code, item→sub_cost_code, attachable→attachment, **+ project** (structurally a 5th... it "mirrors `_raise_duplicate_qbo_customer_issue` exactly") | `entity_public_id = str(local.public_id)`; `existing_qbo_id` param used only in `details`; customer/project add a same-realm-vs-different-QboId `conflict_desc` branch |

**Note:** the two item variants (`cost_code`, `sub_cost_code`) share one `drift_type="duplicate_qbo_item"` across two entity labels — so `drift_type` and `entity_type` are independent inputs, not one derived from the other.

Shapes A and B are byte-for-byte parallel (docstrings literally say "Mirrors …identically named/shaped method"); the only A→B deltas are the `realm_id` origin, the `qbo_id` source attribute, and the entity label/drift literal — all already just arguments. Shape C's details wording differs (name-match duplicate, not a mapping conflict).

## 3. Proposed consolidation — TWO shared free functions

Add to `base/reconciliation_recorder.py` (the file is already in the AST guard's `_SKIP_FILES`, so its own internal variable-`drift_type` call to `record_mapping_issue` is not scanned):

```python
def record_identity_mapping_conflict(
    repo, *, drift_type: str, entity_type: str,
    qbo_id: Optional[str], realm_id: str,
    local_side_mapping, qbo_side_mapping,
    entity_public_id: Optional[str] = None,
) -> None:
    """Shapes A + B (11 connectors). Builds the three-part conflict details
    (qbo-side / local-side / both) from the two mapping objects + entity label,
    then record_mapping_issue(...). Never raises — the fastpath owns the raise."""

def record_duplicate_identity_conflict(
    repo, *, drift_type: str, entity_type: str,
    qbo_id: Optional[str], realm_id: str,
    entity_public_id: Optional[str],
    existing_qbo_id: str, conflict_desc: Optional[str] = None,
) -> None:
    """Shape C (5 connectors incl. project). Name-match-vs-different-identity
    wording; conflict_desc overrides the same-realm/different-QboId phrasing."""
```

Each of the 16 wrappers collapses from a ~30-line details-building body to a **3–5 line forward** passing its family literals + typed values, e.g. in `BillBillConnector`:

```python
def _record_identity_mapping_conflict(self, *, qbo_bill, dbo_bill_id, local_side_mapping, qbo_side_mapping):
    record_identity_mapping_conflict(
        self.reconciliation_repo,
        drift_type="bill_identity_conflict",   # literal — AST-guard-visible
        entity_type="Bill",                    # literal — AST-guard-visible
        qbo_id=str(qbo_bill.qbo_id) if qbo_bill.qbo_id else None,
        realm_id=qbo_bill.realm_id or "",
        local_side_mapping=local_side_mapping, qbo_side_mapping=qbo_side_mapping,
    )
```

The fastpath callback contract is **unchanged** (`record_conflict_issue=lambda …: self._record_identity_mapping_conflict(…)`), so `identity_fastpath.py` is not touched — smallest possible blast radius.

**Rename the misnomer** while we're in here: `_raise_*` → `_record_*` (they record, they don't raise). Low-risk, high-clarity; only in-file callbacks reference them.

### Details-builder caveat (build-unit verification point)
The shared A/B builder references fields on `local_side_mapping`/`qbo_side_mapping`. Shapes A/B are "byte-for-byte parallel," which implies parallel field names across the mapping types (`BillBill`, `InvoiceInvoice`, `BillCredit…`, line variants). **The build unit must confirm** those objects expose the same attributes the details strings read; if any family diverges, that family passes the 2–3 varying values as primitives (or a `details` closure) rather than the object — do **not** force-fit. Same check for Shape C's `conflict_desc`.

## 4. The hard constraint — the AST width-guard (co-design surface, not an obstacle)

`tests/test_qbo_reconciliation_recorder.py::test_reconciliation_issue_write_literals_fit_prod_column_widths` AST-scans prod for `record_mapping_issue` call sites and **requires `drift_type` + `entity_type` be string literals at the call site** (so it can width-check them against the NVARCHAR(32)/(16) columns). It already recognizes a `_record_reconciliation_issue` wrapper name (0 sites today — the seam was pre-wired for exactly this refactor) and hardcodes entity_type-by-path for a couple cases in `_resolve_entity_type_for_call`.

**The build unit MUST update the guard in lockstep:**
1. Add `record_identity_mapping_conflict` + `record_duplicate_identity_conflict` to `_classify_reconciliation_issue_write_call` so the guard discovers the (literal-carrying) call sites inside the thin wrappers.
2. Bump `_MIN_CALL_SITES` to match the new discovered count.
3. The literals still originate per-family at each wrapper, so the width + `KNOWN_DRIFT_TYPES` checks stay green with no registry change (all 16 drift types are already registered in `drift_types.py`; the NVARCHAR(32) name-shortening — `bc_line_item_identity_conflict`, `expense_line_identity_conflict`, `invoice_line_identity_conflict` — is preserved verbatim as the passed literal).

This is the reason the unit is design-gated: the refactor and its guard move together, and getting the guard's matcher wrong would silently stop width-checking 16 writers.

## 5. Explicitly OUT of scope (altitude discipline — don't over-abstract)

Leave these on raw `record_mapping_issue` — genuinely different details, forcing them into a shared shape adds coupling for no reuse:
- `_raise_deleted_vendor_holds_identity_issue` (soft-deleted vendor holds identity)
- `_raise_blank_display_name_issue` (fewest params; no local entity) — closed U-214(g)
- attachment's `attachment_mapping_orphaned` (:688) and `attachment_upload_failed` (:729)

## 6. Relationship to U-214(b) — KEEP SEPARATE

`TODO.md:16` asks whether U-333 folds into U-214(b) (`TODO.md:443`). **Recommendation: no — adjacent layers, sequence don't merge.**
- **U-333** = the *recorder-wrapper* middle layer (this doc). All 16 wrappers already call `record_mapping_issue`.
- **U-214(b)** = the *heal-branch control flow* layer below it (the 4-member repointable family's duplicated heal logic) + migrating a few stragglers that still hand-roll their own try/except insert. The survey confirms U-214(b) item (1) is largely obsolete (the surveyed recorders all now call `record_mapping_issue`), and item (2) (heal-branch extraction) is a distinct prize on a different seam.

They touch the same connectors but different layers; doing U-333 first leaves the recorder layer clean for U-214(b) to build the heal-branch extraction on top.

## 7. Test plan (build unit)

- Keep green: the full `test_qbo_reconciliation_recorder.py` guard suite (width, registry, discovery-count) + `tests/test_sproc_single_source.py` (n/a here) + any per-connector recorder tests.
- Add: a characterization test asserting the two shared recorders emit the **same** `(drift_type, entity_type, entity_public_id, qbo_id, realm_id, details)` tuple to `record_mapping_issue` as the pre-refactor wrappers did, for one representative of each of A/B/C (mock `record_mapping_issue`, assert kwargs). **Mutation-proof** one (perturb a passed literal → test RED).
- Verify the AST guard still discovers ≥ the new `_MIN_CALL_SITES` (mutation: drop one wrapper's literal → guard RED).

## 8. Blast radius / deploy

- Files: `base/reconciliation_recorder.py` (+2 functions), 16 connector `service.py` (body → forward), `tests/test_qbo_reconciliation_recorder.py` (guard matcher + count), + new characterization test. `identity_fastpath.py` untouched; `drift_types.py` untouched.
- **Pure Python, behavior-preserving, no SQL, no schema.** Deploy is a normal `az acr build :latest` + restart after Pass-1/Pass-2 review + full suite green. No sproc apply.

## 9. Recommended dispatch

Single build unit (`U-333-build`), Composer-writes / Codex-reviews per the writer/reviewer split. Sequence: consolidate A/B first (11 sites, one shared fn), then C (5 sites, second shared fn), guard update alongside, then rename `_raise_*`→`_record_*`. One coherent diff; Pass-1 correctness + Pass-2 simplify, then deploy.
