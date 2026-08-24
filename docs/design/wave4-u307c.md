# U-307c design proposal — retire the qbo.Item* staging writes

**Status:** design proposal, no code/SQL changed. Read-only investigation against current code at the
U-307b tip (`e3bacedc`, **not yet deployed** — deployed tip is `2c6d330f`/`870b4998`) + live prod, 2026-08-24.
**Decision in force:** `project_qbo_trust_dbo_identity_alone` (umbrella memory) — trust `dbo.<Entity>.QboId`/
`RealmId` as the sole identity store; drop the redundant `qbo.*` mapping second-stores. **Precedent:** U-300a
(built `run_identity_fastpath_dbo_only`) → U-300b (repointed Attachable's pull off it, wired but **undeployed**)
→ U-315 (removed Attachable's dead pull-side fallback reads, also undeployed). U-307c is Wave 4's analogue of
that same move for the `item` family — the "Phase-6 enabler" the assignment names it.

**Scope — the write side of 3 tables:** `qbo.Item` (parent staging mirror), `qbo.ItemCostCode`,
`qbo.ItemSubCostCode` (identity junctions). U-307a (`a8295ec1`) + U-307b (`e3bacedc`) already repointed every
**pull + push consumer's own cost-code resolution** onto the shared `cost_code_resolver.py` (dbo-native first,
legacy hop on a miss). U-307c is the next slice: stop the item connectors' and `QboItemService`'s own writes to
all three tables, while keeping the dbo-native stamp they already write on every create+update (U-289). This
document does **not** propose dropping any of the 3 tables yet — see §4 for why write-retirement alone doesn't
clear that bar.

---

## 0. What U-307a/b already changed, restated precisely (so §1 isn't re-litigating it)

`integrations/intuit/qbo/base/cost_code_resolver.py` (U-307a) is the shared reference-resolver: forward
`resolve_dbo_sub_cost_code`/`resolve_dbo_cost_code_direct` (dbo-native `SubCostCode.QboId`/`CostCode.QboId`
first, **legacy `qbo.Item → qbo.ItemSubCostCode`/`ItemCostCode` hop on a miss** — deliberately kept, U-307a's
own Gate-1 call) and reverse `resolve_qbo_item_ref` (dbo-native only, **no legacy hop** — U-307a built it,
U-307b wired it). U-307b wired the 3 push consumers (bill/purchase/invoice) onto the reverse resolver. Together
these closed the gap `docs/staging_removal_phase6_readiness.md` (U-294, 2026-08-21) flagged for `item`: *"resolving
a QBO line's ItemRef to and from a local CostCode/SubCostCode has zero dbo-native path anywhere ... needs its own
reference-helper program before any of the 3 item-family tables can be considered."* That reference-helper program
is U-307a/b. **What it did not touch: the item connectors' own writes to the 3 staging tables, or the resolver's
own legacy-hop fallback code.** Both are still exactly as they were before U-307a. U-307c is the former; the
legacy-hop fallback is explicitly out of scope here (§4).

---

## 1. Premise — every remaining reader/writer of qbo.Item / qbo.ItemCostCode / qbo.ItemSubCostCode, post-U-307a/b

Grepped every reference to `QboItemRepository`, `QboItemService`, `ItemCostCodeRepository`,
`ItemSubCostCodeRepository`, `ItemCostCodeConnector`, `ItemSubCostCodeConnector`, and the 3 table names
across `*.py`/`*.sql`, at the U-307b tip (2026-08-24). Excludes `tests/*` (12 files reference these classes;
all via constructor injection of fakes, no live-table dependency, unaffected by this unit's design).

| Consumer | Shape | Live? |
|---|---|---|
| `ItemCostCodeConnector.sync_from_qbo_item` / `ItemSubCostCodeConnector.sync_from_qbo_item` (`item/connector/{cost_code,sub_cost_code}/business/service.py`) | **WRITER** — `create_mapping`/`mapping_repo.create`/`.update_by_id` on every create/update/heal/adopt branch, plus a read-before-write on `mapping_repo.read_by_{sub_}cost_code_id`/`read_by_qbo_item_id` in the SAME branches (existing-mapping check, heal, dedup) | Yes — the target of this unit |
| `QboItemService._upsert_item` (`item/business/service.py`) | **WRITER** — `self.repo.create`/`update_by_qbo_id` on `qbo.Item` itself, on every pull | Yes — the target of this unit |
| `ItemSubCostCodeConnector.sync_from_qbo_item`'s parent-CostCode lookup (`qbo_item_repo.read_by_qbo_id(parent_qbo_id)` → `cost_code_mapping_repo.read_by_qbo_item_id(parent_qbo_item.id)`, lines ~96-114) | LIVE reader, structurally required today (finds the parent's `cost_code_id` to stamp on the child) | Yes — **not** touched by U-307a/b; already flagged in `TODO.md`'s "U-289 follow-ups" as needing a dbo-native repoint. §3 folds this fix into the same unit. |
| `cost_code_resolver._legacy_resolve_sub_cost_code` / `_legacy_resolve_cost_code` (`base/cost_code_resolver.py:166-205`) | LIVE reader — the U-307a-built fallback, reached on a dbo-native miss. **Not** a dead fallback: it is reachable code, just 0-hit today (§1's live parity numbers below) | Yes, dormant — deliberately out of scope (§4) |
| `entities/expense_coding_item/business/service.py:171-173` (`confirm()`'s pre-flight) | LIVE reader — `ItemSubCostCodeRepository().read_by_sub_cost_code_id(sub_cost_code_id)`, drops the recode with `mapping_missing` if `None` | Yes — §2 |
| `entities/expense_coding_item/business/suggestion_service.py:38,95` | LIVE reader — same repo, same "no mapping ⇒ drop the suggestion" contract, upstream of the confirm check | Yes — §2 |
| `scripts/reconcile_project.py:340-462` (`repair_qbo_line_item_mappings`, the 446-448 hop) | LIVE reader — hand-rolled `qbo_item_repo.read_by_qbo_id` → `item_scc_repo.read_by_qbo_item_id` hop, **not** routed through `cost_code_resolver` (predates it) | Yes — §2 |
| `scripts/sync_qbo_item.py::sync_local_to_qbo` (lines 187-397) | Reads `cost_code_mapping_repo.read_by_cost_code_id`/`sub_cost_code_mapping_repo.read_by_sub_cost_code_id`/`qbo_item_repo.read_by_id` | **DEAD** — grepped every call site in the repo; `sync_qbo_item()`'s own batch entrypoint hardcodes `local_to_qbo_result = {...: 0, ...: 0}` and never calls it (line 451). No other caller. Same shape as the identical dead `sync_local_to_qbo` in `sync_qbo_{customer,term,vendor}.py` (pre-existing, unrelated pattern, out of scope). |
| `integrations/intuit/qbo/base/watermark.py`'s `_QBO_SYNC_ENTITY_META["item"]` → `_resolve_staging_qbo_id` | LIVE reader, **best-effort only** — resolves a projection failure's staging PK back to a real QBO id for `ReconciliationIssue` readability; failure-isolated (`except Exception: return None`), "not load-bearing for the force-advance itself" per its own docstring | Yes, non-blocking — §3 |
| `integrations/intuit/qbo/base/identity_drift.py`'s `REFERENCE_ENTITY_SPECS` rows for `cost_code`/`sub_cost_code`, and their 2 driver scripts (`check_qbo_identity_drift_reference.py`, `backfill_qbo_identity_reference.py`) | LIVE reader — generic, spec-driven SQL against `spec.mapping_table`/`spec.staging_table` | Yes — tooling-only, **not** part of the production pull/push path. §3 flags the false-positive risk this creates. |
| `integrations/intuit/qbo/bill/{connector/bill,connector/bill_line_item}/business/service.py`, `purchase/connector/expense_line_item/business/service.py`, `vendorcredit/connector/bill_credit_line_item/business/service.py`, `invoice/business/service.py` — constructor-injected `qbo_item_repo`/`item_sub_cost_code_repo`/`item_cost_code_repo` params | **Pass-through only**, threaded into `cost_code_resolver`'s legacy-hop fallback calls; these connectors do not read the tables directly themselves (already repointed by U-307a/b) | Same as the resolver's own fallback — dormant, out of scope |
| Everything else in `docs/staging_removal_phase6_readiness.md §1.5`'s `item` entry (Bill pull+push, Purchase/Expense pull, VendorCredit pull, Invoice's cost-code index, Expense Coding Cockpit) | Already repointed onto `cost_code_resolver.py` by U-307a/b | No longer a direct reader — reaches these tables only via the resolver's dormant fallback, same bucket as above |

**No admin router, no reconciliation/outbox handler, no other raw-SQL reference** to any of the 3 tables
outside connectors/scripts/tests (grepped `ItemCostCode`/`ItemSubCostCode`/`\[qbo\]\.\[Item\]` across
`*.py`/`*.sql`; the 3 SQL-file hits outside the base table files are a filtered unique index on `qbo.Item`
itself (`u218d`, unrelated) and two doc-comments in `dbo.costcode.sql`/`dbo.subcostcode.sql` — no sproc joins
either table).

**Live parity, re-confirmed this session (2026-08-24), read-only:**

| | dbo total | dbo QboId-stamped | `qbo.Item*CostCode` rows | Orphans (mapping→dbo, no dbo row) | Orphans (dbo stamped, no mapping) |
|---|---:|---:|---:|---:|---:|
| SubCostCode | 475 | 475 | `ItemSubCostCode` 475 | 0 | 0 |
| CostCode | 76 | 76 | `ItemCostCode` **78** | **2** (see below) | 0 |

`qbo.ItemCostCode` carries 2 pre-existing dangling rows (mapping ids 51/62, `CostCodeId` 51/62 — **no matching
`dbo.CostCode` row at all**, so they don't appear in the standard "mapping exists but QboId is NULL" check;
found via an explicit `LEFT JOIN ... WHERE cc.Id IS NULL`). Both point at active QBO Items (QboId `59`/`69`,
"50 Door Hardware & Closet Org"/"60 Awnings, Porch Screen & Win") whose cost-code numbers (`50`/`60`) are now
held by **different** `dbo.CostCode` rows with **different** QboIds (`433`/`510`) — old QBO Items superseded in
Intuit's own catalog at some point, with the mapping row never cleaned up. Harmless (nothing resolves through
them today; `qbo.Item.Active=True` doesn't matter since nothing pulls them by number anymore), and they vanish
for free when `qbo.ItemCostCode` eventually drops. Flagged for completeness, not a blocker. `qbo.Item` itself
carries 554 rows.

`integrations/intuit/qbo/base/identity_fanout.py::check_item_fanout_overlap` (the CostCode/SubCostCode
identity-collision check run by `check_qbo_identity_drift_reference.py`) is **already fully dbo-native**
(`dbo.CostCode` JOIN `dbo.SubCostCode` on `QboId`/`RealmId`, no staging hop) — unaffected by this unit, no
follow-up needed there.

---

## 2. The remaining live readers to repoint

### Expense-coding cockpit mapping-exists check

`entities/expense_coding_item/business/service.py:171-173` (inside `confirm()`) and
`suggestion_service.py:38,95` (inside `suggest_for_item()`) both call
`ItemSubCostCodeRepository().read_by_sub_cost_code_id(sub_cost_code_id)` and treat `None` as "this SubCostCode
has no writable QBO item — drop the suggestion / refuse the confirm with `mapping_missing`."

**Equivalence claim:** *"SubCostCode has a writable QBO item"* ≡ *`dbo.SubCostCode.QboId IS NOT NULL`* (i.e.
`cost_code_resolver.resolve_qbo_item_ref(sub_cost_code_id, realm_id)` — U-307b's already-built, already-tested
reverse resolver — would return non-`None`).

**Proven against live prod, this session:** the §1 table shows **0** SubCostCodes with an `ItemSubCostCode`
mapping but `NULL` `dbo.SubCostCode.QboId`, and **0** with `dbo.SubCostCode.QboId` set but no mapping — the two
directions are in exact 475/475/475 lockstep today, with no numerical room for a hidden mismatch (unlike
CostCode, whose 78-vs-76 gap is fully accounted for by the 2 dangling orphans above, neither of which is a
SubCostCode). The equivalence holds today, unconditionally.

**Repoint:** both call sites swap `ItemSubCostCodeRepository().read_by_sub_cost_code_id(...)` for
`cost_code_resolver.resolve_qbo_item_ref(sub_cost_code_id, realm_id=...)` and check the result for `None`
instead of the mapping row. `realm_id` is already in scope at both call sites (the cockpit operates within one
realm). This also **tightens** the check slightly: the reverse resolver's realm guard (built by U-307b) refuses
a SubCostCode whose `QboId` is stamped but `RealmId` is `NULL` or mismatched, where the raw mapping-table read
today would not have distinguished that case at all — a strictly safer behavior change, not a new failure mode
(a mismatched-realm SubCostCode was never actually a valid coding target to begin with).

### Scripts

- **`scripts/sync_qbo_item.py`** — the WRITER; its fate is §3.
- **`scripts/reconcile_project.py:340-462`** (`repair_qbo_line_item_mappings`) — the `_qbo_item_repo`/
  `_item_scc_repo` hop at lines 446-448 back-fills a `BillLineItem.sub_cost_code_id` from a QBO line's
  `item_ref_value` when a repaired `BillLineItemBillLine` mapping bypassed the normal connector path (the
  "Invoice-1057 gap"). This predates `cost_code_resolver.py` and was never folded into it. **Repoint:** replace
  the 3-line hop with one call, `cost_code_resolver.resolve_dbo_sub_cost_code(matched_qbo_line.item_ref_value,
  realm_id=...)` — same dbo-native-first/legacy-hop-fallback behavior this script's hand-rolled version never
  had (today it reads the mapping table directly with **no** dbo-native fast path and no realm check at all),
  so this is a strict improvement, not just a mechanical swap. `realm_id` needs threading in from the bill's
  realm (not currently a local in this function — check the enclosing `sync_qbo_to_db_bills` caller's scope
  when this repoint is built).

---

## 3. The item connectors' staging write — design

### Reuse, don't reinvent: `run_identity_fastpath_dbo_only`

U-300a already built the exact primitive this needs — `run_identity_fastpath_dbo_only`
(`integrations/intuit/qbo/base/identity_fastpath.py:522`), a dbo-only fast path with **no mapping-table
read/write of any kind**: direct hit on `dbo.<Entity>.QboId`, or (on a miss) a `qbo_dbo_identity_create:{label}:
{qbo_id}:{realm}` app-lock, one re-read under the lock (adopts a racer's row instead of minting a duplicate),
then the caller's `resolve_candidate`/`stamp_identity` callbacks mint-or-find and stamp a new row. U-300b already
wired it for Attachable (`AttachableAttachmentConnector.sync_from_qbo_attachable`, still undeployed) — **that is
the concrete template to copy**, not a new design:

```
outcome = run_identity_fastpath_dbo_only(
    qbo_id=qbo_item.qbo_id,
    realm_id=qbo_item.realm_id,
    entity_label="CostCode",  # or "SubCostCode"
    external_label="QboItem",
    lock_resource_label="CostCode",  # or "SubCostCode"
    read_direct_by_qbo_identity=self.cost_code_service.read_by_qbo_identity,
    apply_fields=lambda row: self._apply_cost_code_fields_and_sync(row, number=number, incoming_name=name, description=description),
    resolve_candidate=lambda: self._resolve_cost_code_candidate(number, name, description),
    stamp_identity=lambda candidate: self.cost_code_service.repo.set_qbo_identity(
        id=coerce_id(candidate.id), qbo_id=qbo_item.qbo_id, realm_id=qbo_item.realm_id,
    ),
)
```

This alone deletes the entire "check for existing mapping → heal (mapping exists, bound row reads empty) →
adopt-by-number → create" branch structure in both connectors (~230 combined lines today) — **not incidentally,
structurally**: the "heal" scenario exists only because a *second store* (the mapping row) can point at a
first store's (dbo) row that has since disappeared. With no second store, that class of drift is impossible by
construction, the same reasoning `run_identity_fastpath_dbo_only`'s own docstring gives for Attachment's
`UQ_Attachment_QboId_RealmId` guarantee.

### `resolve_candidate` — the one genuinely new piece, and its correctness-critical guard

`resolve_candidate` (called only under the create-lock, only on a confirmed miss) must replicate today's
adopt-by-number-then-create logic, **using `read_by_number` directly, no mapping-table check**:

1. `existing = {cost_code,sub_cost_code}_service.read_by_number(number)` (SubCostCode also scoped by parent
   `cost_code_id`, matching `_match_sub_cost_code_by_number_and_parent`'s existing logic).
2. If `existing` is `None` → create fresh (today's create path, unchanged).
3. If `existing` is found: **it must be re-checked for an existing, DIFFERENT `QboId` before being returned as
   the candidate.** This is not optional and is the one place a naive "port the mapping-table check to a
   number-match check" translation can silently regress: today's `_raise_duplicate_qbo_item_issue` guard exists
   because a number-matched local row can already be bound (via the mapping table) to a **different** QboItem —
   in dbo-only mode the equivalent live fact is `existing.qbo_id not in (None, qbo_item.qbo_id)`. If that's true,
   `resolve_candidate` must **not** return `existing` — `stamp_identity`'s `Set<Entity>QboIdentity` call would
   silently overwrite `existing`'s current identity with the new one (the theft-clear semantics in that sproc
   protect the *incoming* `(QboId, RealmId)` pair's uniqueness, not the *target* row's prior identity — they
   would not stop this). Record a `duplicate_qbo_item` reconciliation issue (mirrors today's
   `_raise_duplicate_qbo_item_issue`) and raise, same as today's contract. When `existing.qbo_id` is `None`
   (the ordinary adopt-by-number case) or already equals `qbo_item.qbo_id` (a benign re-resolve), proceed
   normally.

This is flagged explicitly for /em sign-off in §5 (Decision 2) because it's the one place this repoint is not
purely mechanical — everything else (the CONSISTENT/direct-hit path, the field-write path, the lock mechanics)
is a byte-for-byte reuse of an already-shipped, already-reviewed primitive and pattern.

### The parent-CostCode resolution fix (folds in the standing TODO.md item)

`ItemSubCostCodeConnector.sync_from_qbo_item`'s parent lookup (`qbo_item_repo.read_by_qbo_id(parent_qbo_id)` →
`cost_code_mapping_repo.read_by_qbo_item_id(parent_qbo_item.id)`, lines ~96-114) is the one piece of this
connector `TODO.md`'s "U-289 follow-ups" already flagged as needing a dbo-native repoint, deliberately deferred
out of U-289's scope at the time. Under this design it isn't just fixed, it's **structurally required** — once
`qbo.Item`/`qbo.ItemCostCode` stop being written, that 2-hop lookup has no data to find. Replace it with:

```
parent_cost_code = self.cost_code_service.read_by_qbo_identity(parent_qbo_id, realm_id=qbo_item.realm_id)
if not parent_cost_code:
    raise ValueError(f"Parent CostCode for QboItem {qbo_item.id} (ParentRef={parent_qbo_id}) not yet dbo-stamped")
cost_code_id = coerce_id(parent_cost_code.id)
```

This preserves the existing "sync parent items before child items" ordering contract already enforced by
`scripts/sync_qbo_item.py::sync_qbo_to_local` (parents processed first in a separate loop) — in steady state the
parent's `dbo.CostCode.QboId` is already stamped by the time a child is processed in the same batch. It also
closes the TODO's identity-conflict gap for free: `read_by_qbo_identity` returning the wrong/no row is no longer
possible via a stale mapping-table hop, because there's no mapping table left to be stale.

### `QboItemService` — go fully transient, mirroring `QboAttachableService._upsert_attachable` exactly

`_upsert_item` (`item/business/service.py:109-179`) always persists to `qbo.Item` via `self.repo.create`/
`update_by_qbo_id`, gated on an existence check (`read_by_qbo_id_and_realm_id`). Once nothing downstream reads
`qbo.Item` (§1 confirms nothing does, once the connectors above are repointed), that existence check and both
branches collapse to building an **in-memory-only `QboItem`** straight from the external API response —
`id=None, public_id=None, row_version=None`, everything else copied verbatim from `qbo_item` (the external
schema object), exactly matching `QboAttachableService._upsert_attachable`'s already-shipped shape (U-300b,
`attachable/business/service.py:241-283`, whose own docstring names this exact pattern and even points at a
push-side sibling, `_transient_attachable_from_response`, U-285 — this is a **twice-precedented** pattern in
this codebase, not a novel one). `sync_from_qbo`'s `parent_items`/`child_items` split (by `qbo_item.parent_ref
is None`) is untouched — it operates on the transient objects' own fields, no DB round-trip needed either way.

### Is the write removable outright? What (if anything) still depends on a `qbo.Item*` row existing?

**Yes, outright — confirmed nothing load-bearing depends on it:**

- **Watermark/reconciliation** (`_resolve_staging_qbo_id`, `base/watermark.py:138-157`): reads
  `QboItemRepository.read_by_id(staging_pk)` **only** to enrich a held/failed run's `ReconciliationIssue` with
  the human-readable real QBO id, when a projection error is recorded via `outcome.record_projection_error
  (item.id, e, ...)`. Its own docstring: *"Failure-isolated: any lookup problem returns None rather than
  raising — this is a readability nice-to-have ... not load-bearing for the force-advance itself."* This already
  degrades gracefully today for `reimburse_charge` (`_QBO_SYNC_ENTITY_META["reimburse_charge"]` sets
  `staging_repo=None` for the same reason — no PK to look up). Once `qbo.Item` is transient, `item.id` is
  `None`; `read_by_id(None)` fails, is caught, and the reconciliation issue is recorded without the extra
  readability field — never blocks the watermark from advancing. **A cheap, strictly better fix is available**
  in the same unit: `sync_qbo_item.py`'s call sites already have `qbo_item.qbo_id` in hand at the point they call
  `record_projection_error` — pass that directly (the parameter is already named `qbo_id`, and every other
  synced family already passes a real QBO id there; `item` passing the staging PK today is itself a pre-existing,
  unrelated inconsistency) instead of `item.id`, and drop `_QBO_SYNC_ENTITY_META["item"]`'s `staging_repo` to
  `None` to match — no lookup needed at all, strictly more correct than today, not just a safe degrade.
- **Dedup/existence check**: was `qbo.Item`'s own reason to exist (`_upsert_item`'s create-vs-update branch) —
  moot once nothing is persisted; nothing else in the codebase performs a dedup check against `qbo.Item`.
- **`sync_qbo_item.py::sync_local_to_qbo`**: already confirmed dead code (§1) — its dependency on
  `qbo_item_repo`/the 2 mapping repos is moot regardless of this unit; can be deleted in the same pass since it
  would no longer even import cleanly once `QboItem` stops carrying a meaningful `.id`, or left as visibly-dead
  code if /em prefers not to scope-creep a cleanup of the 3 sibling scripts' identical dead function into this
  unit. Recommend deleting it here since this unit already breaks its only remaining reason to exist.
- **`identity_drift.py`'s `REFERENCE_ENTITY_SPECS` rows for `cost_code`/`sub_cost_code`**: not load-bearing for
  any production path (§1 — tooling only), but flagged as a real consequence, not silently absorbed: once these
  2 families stop writing their mapping tables, every **new** CostCode/SubCostCode created from a QBO Item after
  the cutover will read as `orphan_dbo_value` in `check_qbo_identity_drift_reference.py`'s generic
  mapping-table-driven scan (`dbo.QboId IS NOT NULL`, no mapping row — exactly the shape that scan's own
  docstring calls "investigate dual-write bugs," a false alarm here) and the script will exit 1. **This is not a
  new problem U-307c invents** — the identical `attachment` `FlatEntitySpec` row was left in this same registry,
  unmodified, through U-300b and U-315; both are still undeployed, so this false-positive hasn't fired in prod
  yet for `attachment` either, but will the moment the batch deploys. §5 Decision 4 flags this as a shared
  follow-up rather than something U-307c should re-solve in isolation for a 3rd family.

---

## 4. Drop-eligibility + FK dependency order

**FK order, confirmed via `sys.foreign_keys`/`scripts/migrations/u225_qbo_mapping_fk_gaps.sql`:**
`FK_ItemCostCode_QboItem` and `FK_ItemSubCostCode_QboItem` (both `NO ACTION`) FK `[QboItemId]` on the two
mapping tables to `qbo.Item.Id`. **No other table FKs into `qbo.Item`, `qbo.ItemCostCode`, or
`qbo.ItemSubCostCode`** (grepped `REFERENCES [qbo].[Item]` / `REFERENCES qbo.Item` across every `*.sql` file —
only these 2 hits, both already accounted for). Drop order, when a drop is eventually gated: `ItemCostCode` +
`ItemSubCostCode` (children) **before** `qbo.Item` (parent) — no other sequencing constraint, and (unlike
Bill/Invoice/VendorCredit's header→line chains) no line-junction sits beneath either mapping table.

**Is anything drop-eligible after U-307c ships?** **No, not yet — write-retirement is necessary, not
sufficient**, for the same reason Wave 5's own doc (§0.1) generalizes: *"Identity-resolution repoint ≠ table
retirement."* Even after this unit (stop writing) and U-307a/b (repoint every production reader onto
`cost_code_resolver.py`), the tables remain **live-but-dormant** for as long as:

1. `cost_code_resolver._legacy_resolve_sub_cost_code`/`_legacy_resolve_cost_code` still reference them as a
   fallback on a dbo-native miss (§1) — reachable code, deliberately kept by U-307a's own Gate-1, out of scope
   here.
2. The pass-through `qbo_item_repo`/`item_{sub_}cost_code_repo` constructor params on the 5 push/pull connectors
   (§1) still exist to feed that fallback.
3. `identity_drift.py`'s `REFERENCE_ENTITY_SPECS` rows (§3) still name the mapping tables for the drift/backfill
   tooling.

None of these are touched by U-307c's scope (repoint writes, repoint 2 remaining readers). **Full drop-readiness
for `item` requires a follow-on unit** that removes the resolver's legacy-hop fallback (converting
`resolve_dbo_sub_cost_code`/`resolve_dbo_cost_code_direct` to "no dbo match ⇒ `None`, no fallback" — the same
shape `resolve_qbo_item_ref` already has), deletes the now-truly-dead pass-through constructor params, and
retires or adapts the 2 `identity_drift.py` rows — mirroring exactly what Wave 5's own doc proposes as its final
guarded-drop unit (U-313) for Customer/Project/Vendor, and what U-315 already did for Attachable's dead
pull-side fallback. **Recommend that follow-on be its own two-phase design-gated unit** (call it U-307d,
proposed but not designed here — its scope is materially different in kind from this one: removing a
*deliberately-kept* fallback is a real behavior change with its own blast-radius analysis, not a mechanical
repoint) once U-307c has soaked. Not designed further in this document — flagged as the actual remaining path to
"drop-ready," per the assignment's own framing that U-307c is the *enabler*, not the drop itself.

---

## 5. Decisions needing /em sign-off

### Decision 1 — reuse `run_identity_fastpath_dbo_only` as-is, or does Item need its own variant?
**Question:** the primitive was built for Attachment (a family with no "adopt an existing unmapped local row by
a business key" concept — file-hash dedup only). CostCode/SubCostCode's `resolve_candidate` needs the
number-match-and-guard logic in §3. Does that fit cleanly inside the existing `resolve_candidate`/`stamp_identity`
callback contract, or does it expose a gap in the primitive worth widening?
**Recommendation:** fits cleanly — `resolve_candidate` is already a fully family-owned callback (Attachment's own
version does hash-dedup, its own family-specific logic); CostCode/SubCostCode's number-match-and-guard is just a
different implementation of the same seam, no primitive change needed. Confirmed by direct read of
`run_identity_fastpath_dbo_only`'s contract (§3) — it makes no assumption about what `resolve_candidate` does
internally, only that it returns a bindable row.

### Decision 2 — the duplicate-QboId guard inside `resolve_candidate` (§3)
**Question:** this is the one piece of behavior that isn't a mechanical translation of existing code — should
it reject (record + raise, matching today's `_raise_duplicate_qbo_item_issue` contract exactly), or is a softer
behavior (e.g. steal the identity with a warning) acceptable given 0 occurrences of this shape found live today?
**Recommendation:** reject, matching today's contract exactly — this is a genuine identity-theft-adjacent
scenario (a number match landing on a row that already carries a *different* QBO identity), and the whole
"theft-clear only protects the incoming pair" reasoning in §3 is precisely the class of bug
`run_identity_fastpath`'s own module docstring documents as having caused the 2026-08-20 live-prod P0 across 6
other families. No reason to relax the guard just because this path is reached less often (via number-match
rather than mapping-table lookup).

### Decision 3 — `qbo.Item` transient-ification and the 2 mapping tables' write-retirement: one unit or two?
**Question:** §3 proposes retiring all 3 writes together (mirroring U-300b, which did the same for Attachable's
staging mirror + its one junction table in a single unit). Item has *two* junction tables instead of one, and
the parent-CostCode-resolution fix (a real, if small, behavior change beyond pure repoint) sits in the middle of
it. Is that still one right-sized unit, or does /em want it split (e.g. `QboItemService` transient-ification +
parent-lookup fix as one unit, the 2 connectors' `run_identity_fastpath_dbo_only` wiring as a second)?
**Recommendation:** one unit — the three writes are tightly coupled (the connectors' own cold-path logic already
depends on `qbo_item.id`/`.qbo_id` being populated correctly regardless of whether that row is persisted; there
is no clean intermediate state where `qbo.Item` is transient but the mapping tables are still being written
against a staging row that no longer exists to FK against). Splitting would create an artificial in-between
state with no independent value. Flagging because it's a real sizing call, not because there's a design
ambiguity.

### Decision 4 — `identity_drift.py`'s drift/backfill tooling false-positive risk (§3)
**Question:** once cost_code/sub_cost_code stop writing their mapping tables, `check_qbo_identity_drift_
reference.py` will misclassify every newly-created CostCode/SubCostCode as `orphan_dbo_value` and exit 1 — the
same latent gap already exists, undeployed, for `attachment` since U-300b/U-315. Fix both families' registry
rows in this unit, defer it as U-307c's own follow-up, or fix it once for all currently-migrated dbo-only
families in a single shared cleanup unit (spanning `attachment` + `item`)?
**Recommendation:** **one shared cleanup unit**, not per-family — the fix (exclude a dbo-only family's row from
the generic mapping-table-driven scan, or give it a dbo-only variant of the drift query) is identical in shape
for both families, and doing it once catches `attachment`'s already-existing exposure at the same time instead
of leaving it to surface as a surprise the next time someone runs the drift checker after the undeployed batch
ships. Not urgent enough to block U-307c itself — the false positive is diagnostic-only (a CLI tool's exit code),
not a production path.

### Decision 5 — unit breakdown/sizing for the read-side repoints (§2)
**Question:** the cockpit repoint (§2) and the `reconcile_project.py` repoint (§2) are both small, mechanical,
and independent of the connector-write-retirement work in §3. Bundle them into the same U-307c-build unit as the
write-retirement, or split into their own smaller unit(s) since they touch entirely different files (entities/
scripts, not integrations/)?
**Recommendation:** bundle into one unit — both repoints are small (a handful of lines each), share this
document's design, and there's no benefit to a separate Gate-1/Gate-2 cycle for work this size. Splitting is
warranted for scope or risk, and neither repoint carries either.

---

## 6. Proposed unit breakdown + sequence

Assuming Decisions 3 and 5 land as recommended (one build unit):

- **U-307c-build — retire the qbo.Item* staging writes.** Everything in §2 and §3: `QboItemService` goes fully
  transient (mirrors `QboAttachableService._upsert_attachable`); both connectors repointed onto
  `run_identity_fastpath_dbo_only` with the `resolve_candidate`/`stamp_identity` contract in §3 (including the
  duplicate-QboId guard, Decision 2); `ItemSubCostCodeConnector`'s parent-CostCode lookup repointed to
  `read_by_qbo_identity` (closes the standing `TODO.md` item); the expense-coding cockpit's 2 call sites and
  `reconcile_project.py`'s 446-448 hop repointed onto `cost_code_resolver` (§2); `sync_qbo_item.py::sync_
  local_to_qbo` deleted (confirmed dead, §1/§3); `record_projection_error`'s `item.id`→`item.qbo_id` swap +
  `_QBO_SYNC_ENTITY_META["item"].staging_repo=None` (§3). One unit — P0-surface (touches QBO cost-code mapping,
  the same tier U-307a/b were built at), built directly rather than dispatched to Composer, Codex `xhigh`.
  Depends on nothing further — U-307a/b are already shipped (staged, not yet deployed) and this unit does not
  touch `cost_code_resolver.py`'s forward/reverse functions themselves, only its callers plus the item
  connectors.
- **U-307d (proposed, not designed here) — remove `cost_code_resolver`'s legacy-hop fallback + drop
  `qbo.Item`/`ItemCostCode`/`ItemSubCostCode`.** Its own two-phase design-gated unit (§4) — needs a live
  re-verification that the fallback is still 0-hit after U-307c has soaked, a decision on the pass-through
  constructor params' fate, and the guarded DROP itself (children-before-parent per §4's FK order). Sequenced
  strictly after U-307c ships and soaks; not started, not scoped further in this document.
- **Shared cleanup unit (proposed, cross-cutting, not `item`-specific) — `identity_drift.py` false-positive
  fix for dbo-only families** (Decision 4). Spans `attachment` (already-shipped-but-undeployed exposure) and
  `item` (this unit's new exposure) in one pass. Not blocking U-307c; can land any time before or shortly after
  the undeployed batch (which carries both U-300b/U-315 and, once built, U-307c) actually deploys.

Sequencing: U-307c-build has no blocking dependency and can be dispatched as soon as /em signs off on Decisions
1-5 above. U-307d and the shared cleanup unit are both follow-ons, sequenced after U-307c ships (and, for
U-307d, after a soak period — length TBD at that unit's own Gate-1, mirroring Wave 5's Decision 3 discipline
rather than picking a number here).
