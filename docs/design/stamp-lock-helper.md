# Design proposal — shared `stamp_dbo_identity_with_lock` helper

**Status:** design proposal, no code changed. Read-only investigation against current code (HEAD,
2026-08-28, same day as the Phase-6 `qbo.*` DROPs — every file cited below was re-read live, post-drop, not
recalled from an earlier session). **Assignment:** map every current hand-copy of the dbo-only pull
"stamp under a per-row app lock" pattern, propose extracting it into a shared helper in
`integrations/intuit/qbo/base/identity_fastpath.py` (alongside `run_identity_fastpath_dbo_only`), spell out
the shared signature + which connectors migrate onto it, flag behavior differences between the copies.
STOP at this doc — no code, no `/em` build dispatch — per two-phase design-gated discipline
(`feedback_two_phase_dispatch_design_gated`).

**This is not a new discovery.** The extraction is already recorded twice in `TODO.md`, both times
deferred as out-of-scope for the unit that found it:
- `AttachableAttachmentConnector._stamp_pulled_identity`'s own docstring (U-300b,
  `integrations/intuit/qbo/attachable/connector/attachment/business/service.py:320-324`) flags itself as the
  pattern a "next adopter" will need to fold into a shared primitive.
- `TODO.md:2051-2065` (U-307c follow-up, 2026-08-24) — CostCode/SubCostCode became the *second and third*
  hand-copy, flagged independently by 3 of `/simplify`'s 4 lenses, deferred because fixing it would touch
  the already-shipped Attachable connector, outside that unit's Gate-1 scope. It already sketches the shape:
  *"a shared `stamp_dbo_identity_with_lock(...)` helper in `base/identity_fastpath.py` (entity label,
  `read_by_id`/`update_by_id`/`set_qbo_identity` callables, a small `apply_fields(current)` closure)"* — this
  doc's §3 signature is a direct refinement of that sketch, checked against all 6 live copies rather than
  just the 3 known at the time.

This doc's job is to turn "worth folding in once a second family needs it" (now the sixth) into a concrete,
reviewable design.

---

## 1. Every current hand-copy, confirmed live

`run_identity_fastpath_dbo_only` (`base/identity_fastpath.py:522`) delegates its MISS branch to two
caller-supplied callbacks: `resolve_candidate` (find-or-create the local row) and `stamp_identity` (bind the
identity to it). Six connectors pass a `stamp_identity=` callback; all six independently hand-roll the same
five-step shape inside it:

1. Acquire a **second, row-scoped app lock** — `qbo_dbo_identity_stamp:<EntityLabel>:<candidate_id>` —
   nested inside `run_identity_fastpath_dbo_only`'s own `qbo_dbo_identity_create:*` lock. Needed because
   `resolve_candidate` binds by a **side-channel business key** (hash, number, or name) rather than by
   `qbo_id`, so two *different* incoming QBO records (different `qbo_id`s — no contention on the outer,
   qbo_id-keyed lock) can resolve to the **same** local candidate row concurrently.
2. Re-read the candidate via `read_by_id` immediately under that lock.
3. **Theft-guard:** if the re-read row already carries a QBO identity that is NOT this exact
   `(qbo_id, realm_id)` pair, refuse to overwrite it.
4. Optionally write QBO-derived fields to the row (deferred here, not in `resolve_candidate`, specifically so
   the write happens *after* the theft-guard confirms the row is still genuinely unclaimed).
5. Call the family's own `Set<Entity>QboIdentity` (`repo.set_qbo_identity`), then re-read and return.

| # | Family | Unit | File | `_stamp_*` method | Lock line | Re-read line | Theft-guard | Field write | `set_qbo_identity` line(s) |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Attachment | U-300b | `attachable/connector/attachment/business/service.py` | `_stamp_pulled_identity` (298-350) | 333-334 | 340 | 341-348, inline | none | 349 |
| 2 | CostCode | U-307c | `item/connector/cost_code/business/service.py` | `_stamp_cost_code_identity` (168-229) | 202-203 | 210 | 211-220, inline | 221-225, **unguarded** | 226-228 |
| 3 | SubCostCode | U-307c | `item/connector/sub_cost_code/business/service.py` | `_stamp_sub_cost_code_identity` (241-312) | 281-282 | 289 | 290-299, inline | 300-305, **unguarded** | 306-311 |
| 4 | Customer | U-310 | `customer/connector/customer/business/service.py` | `_stamp_customer_identity` (164-236) | 196-197 | 204 | 216, via `_check_no_conflicting_identity` | 217-223, ROWVERSION-checked (224-232) | 233-235 |
| 5 | Project | U-311 | `customer/connector/project/business/service.py` | `_stamp_project_identity` (213-272) | 245-246 | 253 | 260, via `_check_no_conflicting_project_identity` | 261-263, conditional, ROWVERSION-checked (264-267) | 268-270, plus `_sync_addresses` at 271 |
| 6 | Vendor | U-313 | `vendor/connector/vendor/business/service.py` | `_stamp_vendor_identity` (247-291) | 272-273 | 280 (with explicit `current is None: return None` at 281-282) | 283, via `_check_no_conflicting_vendor_identity` | none | 284-289, plus `_sync_addresses` at 290 |

**Re-confirmed live, not stale:** all 6 files were re-read this session, after today's Phase-6 drop batch
(`qbo.Attachable`+`AttachableAttachment`, `qbo.CustomerCustomer`/`CustomerProject`/`VendorVendor`,
`qbo.ItemCostCode`/`ItemSubCostCode`/`Item` — BOARD.md, 2026-08-28 14:01Z deploy). A targeted grep for
`{AttachableAttachment,CustomerCustomer,CustomerProject,VendorVendor,ItemCostCode,ItemSubCostCode}Repository`
and `mapping_repo.` across all 6 files found exactly one hit — a historical comment in
`customer/connector/project/business/service.py:65` noting `customer_mapping_repo` was already removed by
U-314, not a live reference. **All 6 hand-copies are live in their current, fully dbo-only form; none carry
dead mapping-table code to clean up alongside the extraction.**

**Out of scope, confirmed by the same grep:** the *header* `run_identity_fastpath` (families still on a
`qbo.*` mapping table: Bill, BillCredit/VendorCredit, Invoice, Term, CompanyInfo, PhysicalAddress) and
`run_line_identity_fastpath` (BillLineItem, ExpenseLineItem, InvoiceLineItem, BillCreditLineItem) never mint
a local row via a side-channel business key inside a MISS branch the way these 6 do — their MISS/CONFLICT
handling is the mapping-table cross-check `run_identity_fastpath` already owns structurally. Neither family
has a hand-rolled per-row stamp lock to migrate.

---

## 2. Why hand-copying this (specifically) is the wrong shape, restated for this pattern

This is the same argument the module's own header docstring (`identity_fastpath.py:1-25`) already makes for
`run_identity_fastpath` itself, and the same argument `docs/design/u316.md` made for the ROWVERSION-race
guard — both already conceded and fixed. The stamp-lock is the one remaining hand-copied layer underneath
`run_identity_fastpath_dbo_only`, and it has drifted exactly the way six independent copies of anything
drift:

- **Two real latent gaps**, not just cosmetic drift (§4 D1, D2).
- **Six slightly different call shapes** for what is conceptually one operation, which makes "did every
  copy get the fix" a search-and-manually-diff exercise instead of "did the shared function change" — precisely
  the failure mode that caused the 2026-08-20 identity-theft P0 this whole module exists to prevent
  (`identity_fastpath.py:16-24`).
- **A 7th adopter is not hypothetical.** `TODO.md:83-91` already names Bill/Expense/VendorCredit as
  "plausible future Wave-6+ candidates" for `run_identity_fastpath_dbo_only`, and Vendor's soft-delete guard
  is explicitly parked pending "a SECOND soft-delete-bearing family" — i.e. this primitive is still actively
  gaining adopters, not winding down.

---

## 3. Proposed shared helper

```python
def stamp_dbo_identity_with_lock(
    *,
    candidate_id: int,
    entity_label: str,
    qbo_id: str,
    realm_id: Optional[str],
    read_by_id: Callable[[int], Any],
    write_identity: Callable[[Any], None],
    apply_fields: Optional[Callable[[Any], Optional[Any]]] = None,
    on_conflict: Optional[Callable[[Any], None]] = None,
    lock_timeout_ms: int = 15000,
) -> Optional[Any]:
```

Placed in `base/identity_fastpath.py` beside `run_identity_fastpath_dbo_only`, in the same
callback-passing idiom that function and `run_identity_fastpath` already use — not a new style for this
module. Body (mechanically extracted from the six copies in §1, unioning every step that appears in at
least one of them):

1. `lock_resource = f"qbo_dbo_identity_stamp:{entity_label}:{candidate_id}"`; acquire via `qbo_app_lock`;
   `RuntimeError` on timeout (identical text/shape across all 6 today — trivially parameterized on
   `entity_label`/`candidate_id`/`qbo_id`/`realm_id`).
2. `current = read_by_id(candidate_id)`.
3. `if current is None: return None` — Vendor's explicit shape (line 281-282), adopted as canonical. The
   other 5 copies reach the same ultimate outcome implicitly (an unconditional `set_qbo_identity` call on a
   nonexistent row, then a re-read that comes back `None`, which `run_identity_fastpath_dbo_only`'s own
   `stamped is None` check at line 676-679 turns into the same `raise_concurrent_write_race`) — this is a
   **behavior-preserving** simplification (one fewer no-op SQL round trip on the other 5), not a Decision.
4. **Theft-guard:** compare `current`'s existing `(qbo_id, realm_id)` against the incoming pair (the
   identical 3-line comparison hand-copied 6 times — `existing_qbo_id and not (existing_qbo_id == qbo_id and
   (realm_id_of(current) or "") == (realm_id or ""))`); on conflict, call `on_conflict(current)` if provided,
   then raise `ValueError` with the same message shape all 6 already use.
5. If `apply_fields` is provided: `updated = apply_fields(current)`; `if updated is None:
   raise_concurrent_write_race(entity_label=entity_label, entity_id=candidate_id, path_label="identity
   stamp")` — reusing the module's own existing helper (`identity_fastpath.py:264`), the same one
   `run_identity_fastpath_dbo_only`'s HIT branch already calls. This is the ROWVERSION-race check Customer
   and Project hand-roll today (§4 D1) and CostCode/SubCostCode are structurally missing.
6. `write_identity(current)` — the family's own `repo.set_qbo_identity(...)` call **plus** any same-lock
   side effect a family needs alongside it (Project's/Vendor's `_sync_addresses`), since forcing address
   sync into its own callback slot would just be a second family-specific hook for no shared benefit — every
   live copy already treats "stamp identity" and "sync addresses" as one atomic post-guard step.
7. `return read_by_id(candidate_id)`.

**What stays a caller callback, and why** (mirrors `run_identity_fastpath_dbo_only`'s own "what the caller
still owns" section, `identity_fastpath.py:585-593`):
- `apply_fields` — field shape is entirely family-specific (name/description/cost_code_id for
  CostCode/SubCostCode; name/email/phone for Customer; conditional CustomerId-only for Project per its U-303
  adopt-by-name contract; none for Attachment/Vendor).
- `write_identity` — `set_qbo_identity`'s kwargs are NOT uniform across families: SubCostCode and Vendor pass
  `active=` (both carry a `QboActive` dbo-native mirror, U-275); Attachment/CostCode/Customer/Project do not
  (no such column). This is a genuine per-entity schema difference, not copy-drift (§4 D3) — the helper must
  never hardcode an `active` param; keeping this a plain callable is what leaves that decision with the
  family that actually knows its own schema.
- `on_conflict` — optional, so families that don't currently record a `ReconciliationIssue` on this race
  (Attachment, CostCode, SubCostCode — §4 D2) aren't forced to grow one just to adopt the helper; see
  Decision 2 below on whether they should.

---

## 4. Behavior differences between the 6 copies — flagged, not silently resolved

Per two-phase design-gated discipline, differences that would change observable behavior are Decisions for
`/em`, not defaults this doc picks.

**D1 — ROWVERSION-race guard on the pre-identity field write.** Present: Customer (224-232), Project
(264-267, conditional). Absent: CostCode (221-225), SubCostCode (300-305) — `repo.update_by_id(current)`'s
return value is discarded, so a concurrent edit racing the stamp would silently succeed at
`set_qbo_identity` while the Name/Description write never landed. N/A: Attachment, Vendor (no field write in
this step). **This is a known, already-tracked latent bug** — `TODO.md:44-51` (U-316 follow-up, 2026-08-25)
names this exact CostCode/SubCostCode gap and proposes the same fix shape (an `on_stamp_returned_none`-style
hook) this doc's §3 step 5 delivers structurally. **Migrating CostCode/SubCostCode onto the shared helper's
`apply_fields` slot fixes this gap as a side effect of the extraction** — a real behavior change (a
previously-silent race now raises and holds for retry, same as Customer/Project already do), not a bug in
the new helper.

**D2 — `ReconciliationIssue` recorded on a stamp-time theft-guard trip.** Present: Customer, Project, Vendor
— their shared `_check_no_conflicting_*_identity` helpers call `_raise_duplicate_qbo_*_issue` before raising.
Absent: Attachment (bare `ValueError`, `attachment/business/service.py:344-348`), CostCode, SubCostCode
(bare `ValueError` inside `_stamp_*_identity`; note their `resolve_candidate`-side pre-check on the *same*
condition DOES call `_raise_duplicate_qbo_item_issue` — only the lock-guarded re-check inside the stamp
itself is silent). Attachment's own asymmetry with its push-side counterpart is already flagged at
`TODO.md:2033-2041`, deliberately left unfixed there because "`/simplify` is quality-only, never changes
behavior." **This doc surfaces the same fork for all 3 non-recording families at once, via the same
extraction** — see Decision 2.

**D3 — `set_qbo_identity`'s `active=` kwarg.** Present only for SubCostCode (line 310) and Vendor (line
288) — both carry a `QboActive` dbo-native mirror (U-275). Not present for Attachment, CostCode, Customer,
Project (no such column). **Not copy-drift** — a real per-entity schema difference, already reflected in
§3's design (kept inside the `write_identity` callback, never a fixed helper param).

**D4 — `read_by_id` argument type.** CostCode (`str(candidate_id)`, line 210) and SubCostCode
(`str(candidate_id)`, line 289) pass a string; Attachment, Customer, Project, Vendor pass the raw `int`. Every
one of the 6 services' own `read_by_id(self, id: int)` signature is typed `int`
(`entities/{cost_code,sub_cost_code,customer,project,vendor,attachment}/business/service.py`) — CostCode/
SubCostCode's `str()` call is passing the wrong static type today and "works" only because Python doesn't
enforce it and the repo/pyodbc layer tolerates the coercion. Harmless in practice, but exactly the class of
silent divergence a shared helper eliminates by construction — each family's `read_by_id` closure is free to
coerce however it likes, but the helper itself only ever passes `candidate_id` through untouched, so there is
one less place for a stray `str()` to appear un-reviewed in a 7th copy.

**D5 — `current is None` short-circuit.** Explicit only in Vendor (line 281-282); implicit (a redundant
no-op `set_qbo_identity` call followed by a `None` re-read) in the other 5. Already resolved as a
behavior-preserving simplification in §3 step 3 — not a Decision.

**Out of this unit's scope, cross-referenced for continuity:** Vendor's soft-delete guard
(`read_deleted_by_qbo_identity`, `vendor/connector/vendor/business/service.py:195-208`) runs inside
`_resolve_vendor_candidate`, **before** the stamp lock is ever acquired — it is not part of the stamp-lock
pattern this doc extracts. `TODO.md:83-91` already parks its own generalization ("once a SECOND
soft-delete-bearing family migrates onto `run_identity_fastpath_dbo_only`") as its own future design-gated
unit; this doc does not fold it in, since doing so would be a second, unrelated primitive change riding this
one's diff.

---

## 5. Migration mapping — all 6 connectors onto the shared helper

| Family | `entity_label` | `read_by_id` closure | `apply_fields` closure | `write_identity` closure | `on_conflict` closure |
|---|---|---|---|---|---|
| Attachment | `"Attachment"` | `self.attachment_service.read_by_id` | `None` | `lambda c: self.attachment_service.repo.set_qbo_identity(id=c.id, qbo_id=qbo_id, realm_id=realm_id)` | `None` today (Decision 2) |
| CostCode | `"CostCode"` | `self.cost_code_service.read_by_id` (int, dropping the stray `str()` — D4) | `lambda c: (setattr(c,'name',name), setattr(c,'description',description), self.cost_code_service.repo.update_by_id(c))[-1]` | `lambda c: self.cost_code_service.repo.set_qbo_identity(id=c.id, qbo_id=qbo_item.qbo_id, realm_id=qbo_item.realm_id)` | `None` today (Decision 2) |
| SubCostCode | `"SubCostCode"` | `self.sub_cost_code_service.read_by_id` (int, dropping `str()` — D4) | same shape + `cost_code_id` | `lambda c: self.sub_cost_code_service.repo.set_qbo_identity(id=c.id, qbo_id=qbo_item.qbo_id, realm_id=qbo_item.realm_id, active=qbo_item.active)` | `None` today (Decision 2) |
| Customer | `"Customer"` | `self.customer_service.read_by_id` | writes name/email/phone via `repo.update_by_id` (unchanged) | `lambda c: self.customer_service.repo.set_qbo_identity(id=c.id, qbo_id=qbo_customer.qbo_id, realm_id=qbo_customer.realm_id)` | `lambda c: self._raise_duplicate_qbo_customer_issue(qbo_customer=qbo_customer, local_customer=c, existing_qbo_id=c.qbo_id)` (unchanged) |
| Project | `"Project"` | `self.project_service.read_by_id` | conditional CustomerId write via `repo.update_by_id` (unchanged) | `lambda c: (self.project_service.repo.set_qbo_identity(id=c.id, qbo_id=qbo_customer.qbo_id, realm_id=qbo_customer.realm_id), self._sync_addresses(qbo_customer, c.id))` | `lambda c: self._raise_project_identity_conflict_issue(qbo_customer=qbo_customer, local_project=c, existing_qbo_id=c.qbo_id)` (unchanged, `project_identity_conflict` DriftType) |
| Vendor | `"Vendor"` | `self.vendor_service.read_by_id` | `None` | `lambda c: (self.vendor_service.repo.set_qbo_identity(id=c.id, qbo_id=qbo_vendor.qbo_id, realm_id=qbo_vendor.realm_id, active=qbo_vendor.active), self._sync_addresses(qbo_vendor, c.id))` | `lambda c: self._raise_duplicate_qbo_vendor_issue(...)` (unchanged) |

Every family's existing `_check_no_conflicting_*_identity` / theft-guard-raising code (Customer, Project,
Vendor) is **replaced** by the shared helper's own theft-guard (§3 step 4) — their `on_conflict` closure
keeps only the reconciliation-recording half, since the raise itself now lives in the shared function.
`_check_no_conflicting_*_identity` stays alive for its OTHER call site (each family's `resolve_candidate`,
pre-lock — out of this helper's scope, since that guard runs before the lock is even acquired) — only the
stamp-time duplicate call is removed.

Each connector's `stamp_identity=lambda candidate: ...` argument to `run_identity_fastpath_dbo_only`
becomes a call to `stamp_dbo_identity_with_lock(candidate_id=coerce_id(candidate.id), entity_label=...,
qbo_id=..., realm_id=..., read_by_id=..., apply_fields=..., write_identity=..., on_conflict=...)` — the
outer `run_identity_fastpath_dbo_only` call site (§1 table) is otherwise untouched; this is purely a
same-shape swap of what the `stamp_identity=` lambda calls internally.

---

## 6. Decisions needing `/em` sign-off

**Decision 1 — fix D1 (CostCode/SubCostCode's missing ROWVERSION guard) as part of this extraction, or
ship the helper without `apply_fields`'s guard and fix D1 as a separate follow-up?**
**Recommendation: fix it as part of this extraction.** It is the single already-tracked (`TODO.md:44-51`)
correctness gap this whole unit is motivated by closing structurally; shipping the helper without the guard
it was explicitly designed to provide, then filing a second unit to turn the guard on, adds a coordination
step for no benefit — the guard is a straight port of what Customer/Project already do safely in prod today.
Low risk: the new failure mode (raise + hold for retry on a genuine race) is strictly narrower than today's
silent-success failure mode, and races are rare by construction (the lock this guard sits inside already
serializes the common case).

**Decision 2 — should Attachment/CostCode/SubCostCode gain `on_conflict` recording (closing D2), or should
this unit only wire the parameter and leave those 3 callers passing `None` (preserving today's exact
observable behavior)?**
**Recommendation: wire `on_conflict` for all 6 in this same unit**, closing D2 uniformly rather than leaving
a 3-vs-3 split immediately after the extraction that unified everything else. The asymmetry was already
flagged as worth closing at `TODO.md:2033-2041` (deferred there only because it was out of that unit's
quality-only, no-behavior-change scope — an extraction unit is not under that constraint). Low-stakes either
way: at most it adds 3 new `ReconciliationIssue` rows per race event, which is exactly the intended signal
this table already exists to carry for the other 3 families. Flagging as a genuine fork, not a default this
doc silently picked, since it is an observable behavior change for build/on-call to be aware of.

**Decision 3 — one build unit for all 6 migrations, or split per-family (mirroring Wave 5's U-310/311/312
split)?**
**Recommendation: one unit**, following `docs/design/u316.md`'s Decision/precedent for the identical
"touches all 6 dbo-only callers" shape (`u316.md:281-289`): the correctness-bearing change lives entirely in
the new shared function; each connector's own edit is a small, uniform, mechanical swap (§5's table is
already the diff, per family) with no connector-specific design work the way Wave 5's family units carried
(name-match adopt logic, parent-Customer resolution, etc.). Splitting into 6 units would add review
overhead with no corresponding risk reduction.

**Decision 4 — test strategy?**
Not decided here (belongs to the build unit's own Gate-1), but flagging the shape: `tests/test_u300a_identity_fastpath_dbo_only.py` already exercises `run_identity_fastpath_dbo_only`'s lock/re-read contract at the primitive level; the new `stamp_dbo_identity_with_lock` needs its own equivalent unit tests (lock-timeout raise, theft-guard raise + `on_conflict` invocation, `apply_fields`-returns-`None` → `raise_concurrent_write_race`, `current is None` → `None` return), and each of the 6 connectors' existing stamp-path tests should continue to pass unmodified against the new call shape — a genuine mutation check (flip the theft-guard's boundary, confirm the existing per-family regression test catches it) is exactly the kind of thing D1's fix needs proven red-before-green per `feedback_code_review_protocol`.

---

## 7. What this doc does NOT do

No code, no SQL, no test changes. No `/em` build dispatch. Per assignment: **STOP at the doc; `/em` reviews.**
