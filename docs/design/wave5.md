# Wave 5 design proposal — Customer/Project/Vendor QBO identity drop

**Status:** design proposal, no code/SQL changed. Read-only investigation against current code
(2026-08-24) + live prod. **Decision in force:** `project_qbo_trust_dbo_identity_alone` (umbrella
memory, 2026-08-23) — trust `dbo.<Entity>.QboId`/`RealmId` as the sole identity store; drop the
redundant `qbo.*` mapping second-stores. **Precedent:** U-300a (built the dbo-only fast-path
primitive) → U-300b (repointed Attachable's pull off it) — both shipped 2026-08-23, Attachable's own
Phase-6 drop (U-300c) has not happened yet. Wave 5 is the next family group per that memory: *"Wave 5
(customer/vendor) reuses U-300a, sequenced after U-300b soaks."*

**Scope — 3 tables:** `qbo.CustomerCustomer`, `qbo.CustomerProject`, `qbo.VendorVendor`. These are
the identity-**junction** tables for the customer/project/vendor families — not the raw staging
mirrors (`qbo.Customer`, `qbo.Vendor`). Per `docs/staging_removal_phase6_readiness.md` §0/Wave 6, the
raw mirrors are still read unconditionally for field *values* (email, phone, DisplayName, etc.) on
every pull, independent of identity resolution — retiring them is a materially bigger, unscoped
re-architecture (write straight into dbo without a staging landing table) and is explicitly **out of
scope** for Wave 5.

---

## 1. Premise confirmation — live numbers (queried 2026-08-24, this session)

All three families: **100% parity, 0 orphans in both directions, filtered unique index live, theft-clear
sproc base==live.**

| Family | dbo table (total / QboId-populated) | `qbo.*` mapping rows | Mapping↔dbo QboId disagreement | dbo rows w/ QboId, no mapping | Mapping rows, no dbo row |
|---|---:|---:|---:|---:|---:|
| Customer | `dbo.Customer` 73 / 73 | `qbo.CustomerCustomer` 73 | **0** | **0** | **0** |
| Project | `dbo.Project` 136 / 135 | `qbo.CustomerProject` 135 | **0** | **0** | **0** |
| Vendor | `dbo.Vendor` 1197 / 1195 | `qbo.VendorVendor` 1195 | **0** | **0** | **0** |

(`dbo.Project` carries 1 row and `dbo.Vendor` carries 2 rows with no `QboId` at all — manual/local-only
records, not identity drift; they were never expected to have a mapping row and don't.)

**Filtered unique index, live** (`sys.indexes`, confirmed 2026-08-24):

```
Customer  UQ_Customer_QboId_RealmId  UNIQUE  filter: ([QboId] IS NOT NULL)
Project   UQ_Project_QboId_RealmId   UNIQUE  filter: ([QboId] IS NOT NULL)
Vendor    UQ_Vendor_QboId_RealmId    UNIQUE  filter: ([QboId] IS NOT NULL)
```

**Theft-clear sproc, live and base==live** (`OBJECT_DEFINITION` vs. `entities/{customer,project,vendor}/sql/dbo.*.sql`,
normalized `CREATE OR ALTER`→`CREATE` per `reference_base_vs_live_sproc_diff`): `SetCustomerQboIdentity`,
`SetProjectQboIdentity`, `SetVendorQboIdentity` all **MATCH**. Each unconditionally NULLs `[QboId]`/`[RealmId]`
on any *other* row that currently holds the incoming `(QboId, RealmId)` pair, in the same statement that
grants it to the target row — the theft-clear half of the "sole identity store" guarantee.

**FK/child check** (`sys.foreign_keys`, live): all three tables are **leaf junctions** — each has exactly 2 FKs
outward (to its `dbo.<Entity>` and its `qbo.<Entity>` staging table) and **zero** tables FK into any of
them. No line-junction sits beneath any of the three (unlike Bill/Invoice/VendorCredit's
header→line chains) — so, per §2's dependency-ordering discipline in `staging_removal_phase6_readiness.md`,
all three can drop **together**, in one guarded unit, once every consumer below clears. No family-internal
sequencing constraint.

**Reconciliation/outbox:** grepped `integrations/intuit/qbo/reconciliation/business/service.py` and
`integrations/intuit/qbo/outbox/business/worker.py` for `Customer`/`Project`/`Vendor` — **zero** hits against
any of the 3 mapping tables. Unlike Bill/Invoice/VendorCredit, there is no daily
missing/voided reconciliation detector for these families (they aren't QBO "transactions" with a
void state), so Wave 5 does **not** inherit U-301a/U-301b's cross-cutting blocker.

All three premises hold. Nothing here blocks proceeding to design.

---

## 2. Option A vs. Option B — and why the answer is both-per-path

### The two existing primitives

**`run_identity_fastpath_dbo_only`** (`integrations/intuit/qbo/base/identity_fastpath.py:522`, U-300a,
already built and merged, currently wired only for Attachable/U-300b) does two things in one call:
direct-read a hit (no lock), or — on a miss — take a `qbo_dbo_identity_create:{label}:{qbo_id}:{realm}`
app-lock, re-read once under the lock (catches a racer who won first and adopts their row instead of
minting a duplicate), then call the caller's `resolve_candidate`/`stamp_identity` to mint and stamp a
**new** row. It is a **create-primitive** — its whole reason to hold a lock is to serialize concurrent
*creates* of the same identity.

**`verify_project_qbo_identity` / `verify_vendor_qbo_identity`** (and `verify_customer_qbo_identity`,
`verify_bill_qbo_identity`) (`integrations/intuit/qbo/base/identity_consistency.py`) are a completely
different shape: given an entity **already resolved** via its own `dbo.<Entity>.QboId`, read the
family's mapping table (one JOIN'd sproc since U-306) and refuse (`return None`) if the mapping
disagrees forward or reverse-binds the same QboId to a different row. **No create, no candidate, no
lock** — it's a pure read-and-compare gating an already-resolved reference before something trusts it.

### Do Customer/Project/Vendor pulls have a CREATE/STAMP path?

**Yes — confirmed by direct read of all three connectors.** `CustomerCustomerConnector.sync_from_qbo_customer`,
`CustomerProjectConnector.sync_from_qbo_customer`, and `VendorVendorConnector.sync_from_qbo_vendor` all call
`run_identity_fastpath` (the **mapping-table** mode, `integrations/intuit/qbo/base/identity_fastpath.py:309`)
with a `create_mapping=` callback, and on a full miss (no dbo match *and* no mapping match) fall through to
`self.{customer,project,vendor}_service.create(...)` — a genuine new-row mint. This is structurally identical
to what Attachable had before U-300b. **Two concurrent pulls of the same brand-new QBO customer/project/vendor
race exactly the way U-300a's docstring describes** — the dbo-only mode's lock exists precisely for this.

### Recommendation: both-per-path

- **Pull-side CREATE/STAMP (3 connectors) → Option B.** Reuse `run_identity_fastpath_dbo_only` exactly as
  U-300b did for Attachable. It already handles this shape correctly; there is no design work left, only
  wiring (`resolve_candidate` = the family's existing miss-path construction logic, `stamp_identity` = the
  family's `Set<Entity>QboIdentity` call).
- **Reference-resolver / safety-net verify call sites (12 of them, enumerated in §4) → Option A.** Forcing
  these through `run_identity_fastpath_dbo_only` would require passing `resolve_candidate`/`stamp_identity`
  callbacks that must **never** fire at a verify call site (a "miss" here means the row this caller already
  believes it resolved no longer holds that identity — a real anomaly to refuse on, never a green light to
  mint a new row). Blurring "verify an already-trusted reference" into a create-shaped contract is exactly
  the class of mistake `run_identity_fastpath_dbo_only`'s own docstring calls out as its reason for *not*
  being a mode flag on `run_identity_fastpath` — the same argument applies one level down. **Propose a new,
  small, no-lock primitive**, `verify_identity_dbo_only(entity, *, read_direct_by_qbo_identity)`:
  read `dbo.<Entity>` fresh by `(entity.qbo_id, entity.realm_id)` and return `entity.qbo_id` iff the fresh
  read's `.id == entity.id`, else `None`. No lock: this is a plain read, and the one race it could hit
  (identity stolen between the caller's original read and this verify call) is caught *by the comparison
  itself*, not by serialization — there's nothing to serialize, only a fact to re-check. This mirrors exactly
  what `run_identity_fastpath_dbo_only`'s own **hit** branch already does (its `direct = read_direct_by_qbo_identity(...)`
  step, unlocked) — Option A is that one branch, extracted and named for reuse outside a create context.

### A consequence worth flagging, not a 6th decision

Every one of the 12 verify call sites sits beside a **legacy 2-hop fallback** that fires when the verify
returns `None` — confirmed identical in shape at `CustomerProjectConnector._resolve_parent_customer_id`
(`customer/connector/project/business/service.py:442-451`) and `BillLineItemConnector._resolve_project_public_id`
(`bill_line_item/business/service.py:486-499`): re-read `qbo.Customer` by QBO id, then hop through
`qbo.CustomerProject`/`qbo.VendorVendor` by hand. `identity_consistency.py`'s own module docstring
distinguishes "hard stop" call sites (pushes — must refuse, never fall through) from "advisory" ones
(read-only resolvers — a miss today gracefully degrades to the slower legacy hop). **Once the mapping table
drops, the legacy hop has no data source left** — advisory call sites become hard-stop-equivalent by
construction, not by choice. Given 0 disagreements today (§1), this should be a no-op in practice, but it
is a real behavior change (a future identity disagreement that used to degrade gracefully now hard-fails)
and belongs in each unit's own risk note, not silently absorbed into "delete the dead code."

---

## 3. Five decisions needing /em sign-off

### Decision 1 — Option A vs. B vs. both-per-path
**Question:** build a new no-lock `verify_identity_dbo_only`, reuse `run_identity_fastpath_dbo_only`
everywhere, or split by path shape?
**Recommendation:** **both-per-path** — Option B for the 3 connectors' own pull-side create/stamp, Option A
(new primitive) for the 12 verify/reference-resolver call sites. See §2.

### Decision 2 — U-311 sizing (Project family)
Project has, beyond its own pull connector and 5 verify call sites, **3 consumers that are not
verify-shaped at all** — they read `qbo.CustomerProject` directly for a *different* purpose (resolving an
unknown project→QBO-CustomerRef mapping, not verifying an already-known one): the `GET /invoice/{id}/draw-audit`
diagnostic endpoint's `missing_qbo_mapping` gap class (`entities/invoice/business/audit.py:219-226`),
`scripts/reconcile_project.py::_get_project_qbo_customer_ref`, and
`scripts/sync_qbo_invoice.py::_resolve_project_to_customer_ref` (an operator CLI helper, `--project` filter).
**Question:** fold all of Project's work into one unit (U-311), or split the 3 non-identity consumers into
their own follow-on unit?
**Recommendation:** **split them out.** They're a different shape of change (repoint a "resolve a QBO ref
from a name/id" helper onto `dbo.Project.QboId` directly, not swap a verify primitive), touch ops
scripts/diagnostics rather than the hot pull/push path, and keep U-311 itself mechanical and parallel in
shape to U-310/U-312. If accepted, the unit count becomes 6 (U-309–U-314) instead of 5 — noted in §5.

### Decision 3 — the U-300b-soak sequencing gate
U-300a/U-300b shipped together same-day (2026-08-23); Attachable's own soak-then-drop (U-300c) hasn't
happened yet, so there's no proven soak-length precedent to copy. QBO customer/project/vendor pulls run
on a 4h timer (`sync_qbo_customer`/`sync_qbo_vendor`), so a 24h soak covers 6 pull cycles per family.
**Question:** how long does each family's connector-repoint unit need to soak in prod before Wave 5's final
drop (U-313) executes, and do the 3 families' build units (U-310/U-311/U-312) need to ship **sequentially**
(each soaking before the next starts) or can they ship **in parallel** (they touch disjoint files/tables) with
one shared soak window before the drop?
**Recommendation:** build in parallel (Customer/Project/Vendor share no files or call sites beyond the
already-shipped U-300a primitive + the new U-309 primitive) — build U-310/U-311/(U-312 split)/U-312(vendor) in
any order, gate the **single combined drop unit (U-313)** on all of them soaking together for at least 24h
(6 pull cycles) with zero new reconciliation issues logged for these 3 families in that window. This is a real
risk call, not a technical one — flagging for explicit sign-off rather than picking a number silently.

### Decision 4 — `backfill_qbo_bills.py`'s fate
Confirmed **not** a completed one-shot — it's a general-purpose, bucketed, dry-run-by-default staged backfill
tool (`genuinely_missing_creatable` / `already_exists_unlinked` / `unmapped_vendor` / `null_docnumber`
buckets, `--apply --limit N` / `--qbo-id` controlled application) that already calls `verify_vendor_qbo_identity`
dbo-first (U-299). Since it was last touched, `ReconciliationService._reconcile_bill_qbo_missing_locally`
(`integrations/intuit/qbo/reconciliation/business/service.py:433`) now runs the **same** "QBO Bill not
projected locally" detection on the daily reconciliation job, gated `QBO_RECONCILE_BILL_AUTOFIX` (default
`false` — count-only). The script's bucket classification (especially `unmapped_vendor`/`null_docnumber`,
which the blunt reconciliation autofix doesn't distinguish) is real value the daily job doesn't replace.
**Question:** repoint the script's `verify_vendor_qbo_identity` call onto the new dbo-only primitive and keep
it as the safe/staged tool for the cases the reconciliation autofix can't distinguish, or retire it now that
the daily job covers the common case?
**Recommendation:** **repoint, don't retire** — its bucket classification is not fully subsumed by the
reconciliation autofix, and it's a cheap swap (one import, one call-site) once U-309 exists. Revisit retirement
separately if `QBO_RECONCILE_BILL_AUTOFIX` is ever flipped on and proves sufficient on its own.

### Decision 5 — the draw-audit endpoint's `missing_qbo_mapping` gap class
`GET /invoice/{id}/draw-audit`'s `assemble_audit_report` (`entities/invoice/business/audit.py:215-226`) flags
`missing_qbo_mapping` (severity `halt`) whenever `qbo.CustomerProject` has no row for the project — a
diagnostic gap class that only makes sense while the mapping table is the thing being audited for presence.
**Question:** once the table is gone, does this gap class (a) get repointed to check `dbo.Project.QboId IS
NULL` instead (a structurally equivalent replacement — "this project was never QBO-identity-stamped"), or
(b) get dropped as redundant, since `dbo.Project.QboId` population is already visible elsewhere and the
audit's other halt-classes (`duplicate_project`, `stale_staging`) don't depend on it?
**Recommendation:** **(a), repoint** — it's one field swap (`CustomerProjectRepository().read_by_project_id(project_id)`
→ `project.qbo_id is not None`) and keeps the audit endpoint's existing halt-class semantics intact for
whoever consumes this diagnostic today; dropping a halt-class silently changes the audit's contract for a
tool nobody in this investigation confirmed has no callers.

---

## 4. Every remaining consumer — grep + live, 2026-08-24

Legend: **shape** = `create` (pull-side mint, Option B target) / `verify` (reference-resolver or push
safety-net, Option A target) / `direct-read` (reads the mapping table for a purpose neither create nor
verify covers) / `historical` (already-applied migration/cleanup, not a going-forward consumer).

### Customer (`qbo.CustomerCustomer`)

| Consumer | Shape | What it needs |
|---|---|---|
| `CustomerCustomerConnector.sync_from_qbo_customer` (customer/connector/customer/business/service.py) | create | Repoint onto `run_identity_fastpath_dbo_only` (Option B) |
| `CustomerProjectConnector._resolve_parent_customer_id` (customer/connector/project/business/service.py:409-452), via `verify_customer_qbo_identity` | verify + direct-read fallback | Repoint verify call onto Option A; delete the legacy `qbo.Customer`→`qbo.CustomerCustomer` 2-hop fallback (lines 442-451) |
| `scripts/sync_qbo_customer.py` | driver only | No change — instantiates the repo and hands it to the connector above; not a separate call site |

### Project (`qbo.CustomerProject`)

| Consumer | Shape | What it needs |
|---|---|---|
| `CustomerProjectConnector.sync_from_qbo_customer` (customer/connector/project/business/service.py) | create | Option B |
| `BillBillConnector` push resolver (`bill/connector/bill/business/service.py:1030`) | verify | Option A |
| `BillLineItemConnector._resolve_project_public_id` (`bill_line_item/business/service.py:457-499`) | verify + direct-read fallback | Option A + delete legacy hop |
| `ExpenseConnector` (`purchase/connector/expense/business/service.py:999`) | verify | Option A |
| `ExpenseLineItemConnector` (`purchase/connector/expense_line_item/business/service.py:479`) | verify + direct-read fallback (same shape as bill_line_item) | Option A + delete legacy hop |
| `InvoiceInvoiceConnector` (`invoice/connector/invoice/business/service.py:1132`) | verify | Option A |
| `GET /invoice/{id}/draw-audit` (`entities/invoice/business/audit.py:219-226,420`) | direct-read | Decision 5 — repoint to `dbo.Project.QboId IS NULL` |
| `scripts/reconcile_project.py::_get_project_qbo_customer_ref` (line 178-190) | direct-read | Repoint to `dbo.Project.QboId` directly, no mapping hop |
| `scripts/sync_qbo_invoice.py::_resolve_project_to_customer_ref` (line 53-92) | direct-read | Repoint to `dbo.Project.QboId` directly, no mapping hop |
| `intelligence/persistence/sql/cleanup.project_duplicates.sql`, `cleanup.project_136.sql`, `scripts/migrations/{u225_qbo_mapping_fk_gaps,dedupe_project_rows,add_uq_project_name_customerid_active}.sql` | historical | Already applied to prod; no action — retained as historical record only |

### Vendor (`qbo.VendorVendor`)

| Consumer | Shape | What it needs |
|---|---|---|
| `VendorVendorConnector.sync_from_qbo_vendor` (vendor/connector/vendor/business/service.py) | create | Option B |
| `BillBillConnector` (`bill/connector/bill/business/service.py:445`) | verify | Option A |
| `BillBillConnector` (`bill/connector/bill/business/service.py:949`) | verify | Option A |
| `VendorCreditBillCreditConnector` (`vendorcredit/connector/bill_credit/business/service.py:524`) | verify | Option A |
| `ExpenseConnector` (`purchase/connector/expense/business/service.py:485`) | verify | Option A |
| `entities/expense_coding_item/business/service.py:247` (Expense Coding Cockpit confirm/recode) | verify | Option A |
| `scripts/backfill_qbo_bills.py:73` | verify (already dbo-first, U-299) | Option A swap + Decision 4 |
| `scripts/generate_payment_remittance.py:429` | verify (already dbo-first, U-299) | Option A swap |
| `scripts/sync_qbo_vendor.py` | driver only | No change |

**Non-identity consumers that would block a straight drop:** none found beyond the 3 Project-family
direct-reads above (§4/Project table) — no admin router, no reconciliation/outbox handler (§1), no other
raw-SQL reference to any of the 3 tables outside connectors/scripts/tests (grepped
`qbo\.CustomerCustomer`/`qbo\.CustomerProject`/`qbo\.VendorVendor` and the 3 repo class names across
`*.py`/`*.sql`, live 2026-08-24).

---

## 5. Proposed unit breakdown — U-309 → U-313

Mirrors U-300a → U-300b → Phase-6-drop's shape, fanned out per family. Default proposal is **5 units**;
if Decision 2 is taken as "split," it becomes **6** (U-309–U-314, Vendor and Drop each shift by one).

- **U-309 — Build `verify_identity_dbo_only`.** New shared primitive in
  `integrations/intuit/qbo/base/identity_consistency.py` (or `identity_fastpath.py`, TBD at build time —
  whichever keeps it beside its sibling, not a design question). No lock, no mapping-table read, no
  connector changes. Design-gated (touches a shared `base/` primitive per
  `feedback_two_phase_dispatch_design_gated`) — **this document is that unit's design phase**; §2 spells out
  the full contract. Foundational for U-310/U-311/U-312.
- **U-310 — Customer family repoint.** `CustomerCustomerConnector`'s pull onto Option B;
  `CustomerProjectConnector._resolve_parent_customer_id`'s verify call onto Option A + delete its legacy
  hop. Smallest, cleanest family (§1) — proves both primitives together before the larger families, mirroring
  U-300's own "pilot the pattern on the safest case first" discipline.
- **U-311 — Project family repoint.** `CustomerProjectConnector`'s own pull onto Option B; the 5 verify call
  sites (bill, bill_line_item, purchase/expense, purchase/expense_line_item, invoice) onto Option A + delete
  their legacy hops. *(If Decision 2 = split: this unit is verify/connector-only; the 3 non-identity
  consumers move to a new U-312, and Vendor/Drop below become U-313/U-314.)*
- **U-312 — Vendor family repoint.** `VendorVendorConnector`'s own pull onto Option B; the 7 verify call
  sites onto Option A + delete legacy hops; repoint `backfill_qbo_bills.py` and `generate_payment_remittance.py`
  per Decision 4.
- **U-313 — Phase-6 drop.** After the soak gate (Decision 3) clears for all of U-310/U-311/U-312: drop
  `qbo.CustomerCustomer`, `qbo.CustomerProject`, `qbo.VendorVendor` together in one guarded unit (§1 — no
  child junctions, no family-internal sequencing constraint, matching `staging_removal_phase6_readiness.md`
  §6's per-family-batch recommendation). Re-verify zero callers immediately before executing, per the standing
  discipline (not "it was zero last week") — re-run this doc's §4 grep fresh, not from memory.

Sequencing: U-309 first (foundational). U-310/U-311/U-312 can build in parallel once U-309 ships (disjoint
files, Decision 3). U-313 is gated on all three soaking per Decision 3's recommendation.
