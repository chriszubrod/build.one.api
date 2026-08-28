# Design proposal — thread real QboId through every sync_qbo_*.py projection loop

**Status:** design proposal, no code changed. Read-only investigation against current code at tip `968e2d72`
(master, 2026-08-28), post the Phase-6 `qbo.*` staging-removal drops (U-300c/U-314/U-307d, all applied +
verified per `project_qbo_trust_dbo_identity_alone` memory). **Deferred item being picked back up:** the
"U-307c follow-ups" section of `TODO.md` (lines 2066–2079), booked 2026-08-24 when U-307c fixed this for the
`item` family alone and explicitly deferred the general fix as its own unit. This doc re-verifies the map
against current code (the deferred TODO entry pre-dates the Phase-6 drops) and turns the TODO's "right-depth
fix" sketch into a buildable design.

---

## 0. The bug, restated precisely

`SyncOutcome.record_projection_error`'s first parameter is named `qbo_id` (`integrations/intuit/qbo/base/
sync_outcome.py:117`) and `qbo.ReconciliationIssue.QboId` is documented as "QBO entity id." The shared
`project_records()` loop helper (`sync_outcome.py:45-57`) already honors that contract — it calls
`outcome.record_projection_error(record.qbo_id, e, ...)`.

But `project_records()` is not what runs in production. Every `scripts/sync_qbo_*.py` incremental-pull script
explicitly calls its service's `sync_from_qbo(..., sync_to_modules=False)` — bill's script even comments why:
*"passing sync_to_modules=True here would double-sync"* — and then hand-rolls its **own** per-record
projection loop for header/line/attachment/Excel fan-out that `project_records()` doesn't do. That hand-rolled
loop is the one every scheduler tick actually runs, and 8 of the 9 non-trivial families call
`record_projection_error(<local_obj>.id, ...)` — the **internal staging-table PK**, not the QBO id — because
the local staging object (`bill`, `purchase`, `customer`, …) is right there in scope and `.id` is the obvious
attribute to reach for. `project_records()`'s correct `record.qbo_id` call is, today, essentially inert in
production: grepping every non-test call site of `sync_to_modules=True` turns up only `sync_attachables_for_bill`
(a different, unrelated `sync_to_modules` parameter on the attachable service) — the service-level
`project_records()` path is exercised by unit tests, not by any live entry point.

`watermark.py`'s `_QBO_SYNC_ENTITY_META` registry + `_resolve_staging_qbo_id()` (added by the watermark
hold-bound-force-advance unit, then extended by U-307c with a `projection_ids_are_qbo_ids` flag) exist **only**
to paper over this: best-effort, failure-isolated, resolve-at-write-time translation from a recorded staging PK
back to a real QBO id, so the `ReconciliationIssue` row it eventually writes isn't nonsense. U-307c made `item`
the first (and, until this unit, only) family to pass the *correct* value at the source instead — because
`qbo.Item` became transient (never persisted) in that same unit, so there was no staging PK left to pass by
mistake. The flag models that as a per-entity special case; TODO.md's own U-307c follow-up already named the
right-depth fix: **thread the real `qbo_id` through every script's projection loop** (each already has the
staging row in scope, same shape as item's own fix), **then delete the resolve machinery** once no family needs
PK→qbo_id translation anymore.

---

## 1. Re-map — every sync_qbo_*.py projection loop, current code, post-drops

11 entities have a `WatermarkRun`; `_QBO_SYNC_ENTITY_META` currently carries all 11 plus the two with no
projection step at all.

| Entity | Projects into | `record_projection_error`/`_failure` call site(s) | Passes today | Fix |
|---|---|---|---|---|
| `bill` | Bill (+ lines, attachments, Excel) | `sync_qbo_bill.py:383` | `bill.id` (staging PK) | → `bill.qbo_id` |
| `purchase` | Expense | `sync_qbo_purchase.py:239-241` | `purchase.id` | → `purchase.qbo_id` |
| `invoice` | Invoice | `sync_qbo_invoice.py:229-231` | `invoice.id` | → `invoice.qbo_id` |
| `vendorcredit` | BillCredit | `sync_qbo_vendorcredit.py:320-323` (`record_projection_failure`, no-BillCredit-row case) **and** `:326-328` (`record_projection_error`, exception case) | `vendor_credit.id` (both) | → `vendor_credit.qbo_id` (both) |
| `vendor` | Vendor | `sync_qbo_vendor.py:91-93` | `vendor.id` | → `vendor.qbo_id` |
| `customer` | Customer **and** Project (2 loops, same script) | `sync_qbo_customer.py:101-103` (Customer), `:124-126` (Project) | `customer.id` (both) | → `customer.qbo_id` (both) |
| `term` | PaymentTerm (2 loops, same script — the incremental pull loop **and** `sync_existing_terms_to_payment_terms`, called unconditionally every run at `sync_qbo_term.py:276` to catch up any never-mapped `qbo.Term` rows) | `sync_qbo_term.py:100-102`, `:172-174` | `term.id` (both) | → `term.qbo_id` (both) |
| `company_info` | Company | `sync_qbo_company_info.py:126-128` | `company_info.id` | → `company_info.qbo_id` |
| `company_info` (sub-case) | Address (×3: company/legal/customer-communication) | `sync_qbo_company_info.py:109-111` | `addr_id` — a `PhysicalAddr.Id` **embedded in the CompanyInfo API response**, not a `qbo.<Entity>.Id` staging PK (there is no `qbo.Address` staging table; Address projects straight from the embedded sub-object) | **Not the same bug** — see §1.1, left out of scope |
| `item` | CostCode, SubCostCode | `sync_qbo_item.py:144-146`, `:167-169` | `item.qbo_id` (already fixed, U-307c) | none — reference shape |
| `account` | *(none)* | *(no call site — `sync_qbo_account.py`'s `sync_qbo_to_local` returns the staging outcome directly; Account is never projected into a dbo entity)* | n/a | none — not a projecting family |
| `reimburse_charge` | *(none — staging only)* | *(no call site — `sync_qbo_reimburse_charge.py`'s own docstring: "Upsert-only — NO module / Excel / Box fan-out")* | n/a | none — not a projecting family |

**11 call sites across 8 families** need the one-word fix (`.id` → `.qbo_id`); every staging model already
carries a `qbo_id: Optional[str]` field (`bill`, `purchase`, `invoice`, `vendorcredit`, `vendor`, `customer`,
`term`, `company_info` business models all confirmed by grep) and every call site already has the local staging
object in scope — same shape as `sync_qbo_item.py`'s own U-307c fix, mechanically applied 8 more times.

### 1.1 `company_info`'s address sub-case — why it's out of scope here

`addr_id` (`company_info.company_addr_id` / `.legal_addr_id` / `.customer_communication_addr_id`) is not a
`qbo.<Entity>.Id` staging-table PK at all — there's no `qbo.Address` staging table to have one. It's QBO's own
`PhysicalAddr.Id`, read straight off the embedded sub-object in the `CompanyInfo` API payload and handed to
`address_connector.sync_from_qbo_to_address(qbo_physical_address_id=addr_id)`. Whether that value belongs in
`qbo.ReconciliationIssue.QboId` the same way a Bill/Purchase/etc. qbo_id does is a genuinely different
question — a real QBO-assigned id, but for a different entity than the reconciliation issue's own `entity_type`
label ("CompanyInfo") would suggest, and CompanyInfo/Address projection failures are not part of any
watermark-hold-bound path that's fired in practice (single-row entity, no incremental volume). Recommend
leaving this call site untouched in the build unit and re-flagging it only if it ever shows up in a real
`watermark_hold_bound_exceeded` issue — not blocking, not the same class of bug as the other 11.

---

## 2. The consistent fix

**Step A — 11 one-line call-site changes**, each `<local>.id` → `<local>.qbo_id` (or `.qbo_id` where the
second positional arg is a literal, for the `record_projection_failure` no-row case in vendorcredit). No other
line in any of the 8 scripts changes; the staging object is already in scope, already has the field, already
used at nearby log lines (e.g. `sync_qbo_bill.py:336` already logs `bill.qbo_id` two lines from the call site
this fixes).

**Step B — collapse `watermark.py`'s resolve machinery**, now dead once every family passes the real id at the
source:
- Delete `_QboSyncEntityMeta.staging_repo` and `.projection_ids_are_qbo_ids` fields — collapse the dataclass to
  just `label: str` (or fold the registry into a plain `Dict[str, str]` entity→label map; `_QboSyncEntityMeta`
  as a one-field dataclass is no longer earning its keep, but that's a naming call for the build unit, not a
  design blocker).
- Delete `_resolve_staging_qbo_id()` entirely.
- Delete the 9 now-unused staging-repo imports at the top of `watermark.py` (`QboAccountRepository`,
  `QboBillRepository`, `QboCompanyInfoRepository`, `QboCustomerRepository`, `QboInvoiceRepository`,
  `QboPurchaseRepository`, `QboTermRepository`, `QboVendorRepository`, `QboVendorCreditRepository`) — none of
  these repos are used anywhere else in the file.
- Simplify `_record_bound_forced_advance`'s `projection_failed_ids` loop to record at face value, the same way
  `staging_failed_ids` already does two blocks above it — no branch, no resolve call, no "could not resolve"
  fallback detail string. The two loops become symmetric, which is what the existing comment on the
  `staging_failed_ids` loop already gestures at ("real QBO id ... recorded at face value") without the
  `projection_failed_ids` loop being able to say the same — after this fix, it can.

This isn't a bigger-hammer rewrite of the hold-bound-force-advance path — `commit()`, `_write()`, the hold/bound
timing, and the `ReconciliationIssue` recording *shape* (severity, drift_type, details string) are all
unchanged. Only the "how do we get a real qbo_id for this failed row" question changes, from "look it up later,
best-effort" to "it was already correct."

### 2.1 Net effect on test surface (for the build unit's Test step, not decided here)

`tests/test_qbo_watermark_runner.py`, `tests/test_u307c_sync_qbo_item_projection_error.py`,
`tests/test_qbo_reconciliation_recorder.py`, and `tests/test_qbo_sync_outcome_u257.py` all reference
`_QBO_SYNC_ENTITY_META` / `projection_ids_are_qbo_ids` / `_resolve_staging_qbo_id` today and will need updating
— most likely simplifying, since the branch they exercise (resolve success / resolve failure / already-a-
qbo-id) collapses to one path. Each of the 8 scripts' own test files will need their `record_projection_error`
assertions updated from the staging PK they assert today to the qbo_id. Not scoped here in detail; flag as the
build unit's Test-step inventory.

---

## 3. Confirm — did anything break on the Phase-6 drops?

**No.** Verified by cross-referencing `watermark.py`'s current imports/registry against the three drop
migrations, at tip `968e2d72`:

| Drop | Tables removed | Referenced by `watermark.py`? |
|---|---|---|
| U-300c | `qbo.Attachable`, `qbo.AttachableAttachment` | No — `attachable` isn't a `WatermarkRun`-pulled entity (push-only), never had a registry row. |
| U-314 | `qbo.CustomerCustomer`, `qbo.CustomerProject`, `qbo.VendorVendor` | No — the registry's `"vendor"`/`"customer"` rows point at `QboVendorRepository`/`QboCustomerRepository`, which wrap the **raw staging mirrors** `qbo.Vendor`/`qbo.Customer` (deliberately **kept** per `project_qbo_trust_dbo_identity_alone`, still written every pull tick) — a different table family from the dropped identity-**mapping** tables. Confirmed live: `ReadQboVendorById`/`ReadQboCustomerById` (the sprocs behind both repos' `read_by_id`) live in `qbo.vendor.sql`/`qbo.customer.sql`, untouched; U-314's migration only drops `dbo.*VendorVendor*`/`dbo.*CustomerCustomer*`/`dbo.*CustomerProject*`-prefixed sprocs. |
| U-307d | `qbo.Item`, `qbo.ItemCostCode`, `qbo.ItemSubCostCode` | No — `item`'s registry row already had `staging_repo=None` (set by U-307c, *before* this drop), and `watermark.py`'s current import list has no `QboItemRepository` import at all. The drop deleted the `QboItemRepository` class itself (per U-307d's own summary), and there was already nothing in `watermark.py` pointing at it. |

The registry's remaining 9 `staging_repo` values (`account`, `bill`, `company_info`, `customer`, `invoice`,
`purchase`, `term`, `vendor`, `vendorcredit`) all wrap tables that were never in scope for any Phase-6 drop.
Nothing in `watermark.py` referenced a dropped table before this unit, and nothing will after it — the
resolve-machinery deletion in §2 Step B is a cleanup enabled by the drops being long done and the flag pattern
having proven itself not worth keeping, not a fix *for* the drops.

---

## 4. Scope, sequencing, risk

- **Files touched:** 8 `scripts/sync_qbo_*.py` (bill, purchase, invoice, vendorcredit, vendor, customer, term,
  company_info) + `integrations/intuit/qbo/base/watermark.py` + their test files. No SQL, no schema change, no
  new migration. Behavior-preserving on the happy path (every projection that already succeeds keeps
  succeeding); the only observable change is what value lands in a `ReconciliationIssue.QboId` (or a force-
  advance log line) when a projection genuinely fails — real qbo_id instead of a best-effort-resolved-or-null
  staging PK. Strictly more useful to whoever reads that row, never less.
- **Not P0-surface** by the tier test (no money math, no RBAC, no ROWVERSION concurrency, no cascade, no new
  external write) — this is error-path bookkeeping for an already-existing reconciliation mechanism. Standard
  Composer-build tier, `high`-effort Codex review.
- **One unit, not eleven** — the 11 call sites are mechanically identical; no reason to split by family. The
  watermark.py cleanup (§2 Step B) rides the same unit since it's dead the moment Step A lands everywhere,
  same "fold once every consumer is done" pattern as U-325's active-mirror decoupling.
- **No live-data verification needed before landing** (unlike the Phase-6 drops) — this changes what gets
  *written* to a reconciliation table on a rare failure path, not any read/write path that touches Bill/Vendor/
  Customer/etc. data itself. A mutation test on the 11 call sites (assert the recorded id is the qbo_id, not the
  staging PK, by constructing a staging object with a different value in each) is the right proof shape — no
  live-prod re-verify query needed the way the Phase-6 drops required.
