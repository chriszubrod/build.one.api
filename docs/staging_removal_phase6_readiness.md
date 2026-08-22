# Staging-removal Phase 6 — drop readiness (U-294)

**Status:** read-only audit. No code, SQL, or data changed. Produces the dependency-ordered plan Phase 6
(actually dropping `qbo.*` tables) will execute against. Every claim below was independently re-derived against
**current code and the live production database** on 2026-08-21 — nothing here is copied from
`docs/staging_removal_phase4_5_scoping.md` or the board without being re-checked, per the U-286 discipline. In
several places this re-check overturned a prior doc's claim; those are called out explicitly.

**Method:** a 13-agent parallel fan-out, one agent per QBO connector family (the same 13 families
`staging_removal_phase4_5_scoping.md` used), each independently: (1) confirmed its tables' live row counts and
FK edges against `sys.tables`/`sys.foreign_keys`, (2) grepped and READ the current call sites of every reader/
writer — not trusting any prior doc's "zero callers" claim, (3) queried live prod for sproc/column existence
and data shape where relevant, (4) classified each table and gave a readiness verdict.

**Headline finding: none of the 39 `qbo.*` tables are drop-ready today.** Every table has at least one
confirmed-live reader or writer beyond its identity-resolution role. This is a materially different, and more
useful, answer than "here's what we can drop" — see §0 for why, and §3 for what actually has to happen before
any drop conversation can start.

---

## 0. Why nothing is drop-ready — the patterns that recur across almost every family

Reading all 13 reports together surfaces the same handful of structural gaps, independent of family:

1. **Identity-resolution repoint ≠ table retirement.** Every shipped unit in this program (U-276 through
   U-293) repointed *identity resolution* — "does this dbo row already map to a QBO object" — onto dbo-native
   `QboId`/`RealmId`. None of them touched the *other* reasons a `qbo.*` table gets read: raw field values
   (`Bill.TxnDate`, `PhysicalAddress.city`, `CompanyInfo.legal_name`, `Purchase.PrivateNote`), reconciliation
   jobs, outbox conflict-refresh handlers, or cross-family reference lookups (vendor refs, item/cost-code refs,
   sales-term refs). A table can be "fully repointed" in the identity sense and still be squarely live for one
   of these other reasons.
2. **Mapping tables are still being minted, not just occasionally read.** `create_mapping()` on nearly every
   junction table (`BillBill`, `BillLineItemBillLine`, `InvoiceInvoice`, `CustomerCustomer`, `CustomerProject`,
   `VendorVendor`, `VendorCreditBillCredit`, `CompanyInfoCompany`, `ItemCostCode`, `ItemSubCostCode`,
   `TermPaymentTerm`) is called **unconditionally on every newly-pulled or newly-created row**, fast-path hit or
   miss. These tables are not stale residue draining toward zero — several are confirmed still growing in
   lockstep with their dbo counterpart (e.g. `PurchaseExpense` at exactly 11557/11557, `qbo.Vendor` written same-
   day as this audit).
3. **Two "permanent by design" safety-net reads exist and are not migration scaffolding.** `verify_project_qbo_
   identity()` (customer/project) and `verify_vendor_qbo_identity()` (vendor) read their qbo mapping table on
   **every** dbo-native hit, not just misses — both are documented in their own module as a deliberate,
   permanent theft-detection cross-check ("the discipline being mirrored is the hard stop... never soften the
   push side back toward a fall-back"). Retiring `qbo.CustomerProject`/`qbo.Customer`/`qbo.Vendor`/
   `qbo.VendorVendor` therefore isn't just "finish the remaining repoints" — it requires an explicit decision to
   relax or redesign that safety net, which is a risk call, not pure engineering.
4. **Reconciliation and outbox-conflict code was never in scope for any repoint unit.** `integrations/intuit/
   qbo/reconciliation/business/service.py`'s daily missing/voided detectors, and `integrations/intuit/qbo/
   outbox/business/worker.py`'s conflict-refresh handlers, read `qbo.Bill`/`BillBill`, `qbo.Invoice`/
   `InvoiceInvoice`, and `qbo.VendorCredit`/`VendorCreditBillCredit` directly and unconditionally — confirmed
   actively firing (live `qbo.ReconciliationIssue` rows dated 2026-08-20/21). No unit, shipped or booked, touches
   this layer for any family.
5. **A live, deployed sproc still reaches directly into 3 families' staging tables for exact-identity matching.**
   `ProposeInvoiceSourceLinks`' **Tier 0** (as opposed to the fingerprint Tiers 1-3, which U-274 closed) still
   `INNER JOIN`s `qbo.[Bill]`/`qbo.[BillLine]`/`qbo.[BillLineItemBillLine]` (Tier 0c) and `qbo.[Purchase]`/
   `qbo.[PurchaseLine]`/`qbo.[PurchaseLineExpenseLineItem]` (Tier 0d) — confirmed via live `OBJECT_DEFINITION`,
   not the doc. It matches 0 rows today only because no `LinkedTxnType='Bill'` provenance row exists yet — it is
   dormant-by-data, not dead code, and would fire the moment one does. (VendorCredit's equivalent Tier-3 reach —
   the one the assignment's own brief flagged from §7 — **is** actually closed, by U-274/§9; the brief cited the
   stale §7 section instead of the superseding §9. Confirmed by direct read of the live sproc body.)
6. **Some "deliberate, permanent design" claims from prior docs are themselves stale.** `dbo.payment_term.sql`'s
   `qbo.Term.Active` LEFT JOIN, described in the scoping doc as a considered permanent U-255 design, was in fact
   already superseded by **U-275** the very next day — confirmed by reading the live base file (no such JOIN
   exists) and matching `sys.columns`. Conversely, `docs/staging_removal_phase4_5_scoping.md`'s "No SQL applied
   to prod" note for `company_info` (U-277) is also stale — the SQL is confirmed live now. Treat every date-
   stamped claim in any doc, including this one, as decaying — re-check before acting on it.

---

## 1. Per-table classification, read/write status, and readiness

Legend — **Class**: `mirror` = entity staging mirror (b), `junction` = identity mapping/junction (a), `keep` =
operational-keep, `special` = no dbo entity. **Read**: `full` = fully repointed (zero live readers of any kind),
`fallback` = dbo-native fast path exists but a live branch still reads the table, `none` = no repoint attempted.
**Verdict**: `DROP-READY`, `unit` = blocked on identified/needed engineering work, `deploy` = blocked on
confirming SQL/deploy state, `decision` = blocked on a Chris product call.

| Family | Table | Rows | Class | Read | Verdict | Blocking |
|---|---|---:|---|---|---|---|
| account | Account | 252 | special | fallback | unit | **NEW** — AP-account cache redesign |
| attachable | Attachable | 4556 | mirror | fallback | unit | U-285 (pull side untouched) |
| attachable | AttachableAttachment | 4194 | junction | fallback | unit | U-285 + structural conflict-check tradeoff |
| bill | Bill | 20196 | mirror | none | unit | **NEW** — Tier 0c + reconciliation + outbox repoint |
| bill | BillLine | 24531 | mirror | none | unit | same as Bill |
| bill | BillBill | 19981 | junction | fallback | unit | **NEW** — reconciliation/outbox/delete-cleanup repoint |
| bill | BillLineItemBillLine | 23553 | junction | fallback | unit | U-293-dw (in flight) + Tier 0c + reconciliation/outbox |
| company_info | CompanyInfo | 1 | mirror | none | unit | **NEW** — field-value read + router + CLI tools |
| company_info | CompanyInfoCompany | 1 | junction | fallback | unit | **NEW** — structural conflict-check decision |
| customer | Customer | 209 | mirror | fallback | unit | compound — see §1.5 |
| customer | CustomerCustomer | 73 | junction | fallback | unit | **NEW** (small) — parent-lookup repoint |
| customer | CustomerProject | 135 | junction | fallback | unit | U-284 + permanent safety-net decision |
| invoice | Invoice | 996 | mirror | fallback | unit | **NEW** — draw-matrix/cost-coded-lines/reconcile repoint |
| invoice | InvoiceLine | 30447 | mirror | none | unit | same as Invoice |
| invoice | InvoiceInvoice | 991 | junction | fallback | unit | reconcile_invoice_draws + dual-write |
| invoice | InvoiceLineItemInvoiceLine | 30391 | junction | none | unit | U-293b (not dispatched) |
| item | Item | 554 | mirror | none | unit | **NEW, large** — cost-coding consolidation |
| item | ItemCostCode | 78 | junction | fallback | unit | same as Item |
| item | ItemSubCostCode | 475 | junction | fallback | unit | same as Item |
| physical_address | PhysicalAddress | 944 | mirror | none | unit | **NEW** — producers must pass fields directly |
| physical_address | PhysicalAddressAddress | 780 | junction | fallback | unit | **NEW** — fix ID-collision risk first |
| purchase | Purchase | 12336 | keep | none | **decision (permanent)** | KEPT — feeds `/expense-coding` cockpit |
| purchase | PurchaseLine | 12896 | keep | none | **decision (permanent)** | KEPT — feeds `/expense-coding` cockpit |
| purchase | PurchaseExpense | 11557 | junction | fallback | unit | **NEW** — CREATE path + 2 admin endpoints |
| purchase | PurchaseLineExpenseLineItem | 12069 | junction | none | unit | U-293b + Tier 0d |
| reimburse_charge | ReimburseCharge | 26650 | special | full | **decision** | pure keep-vs-retire call, code/schema clean |
| term | Term | 6 | mirror | fallback | unit | **NEW** — sales-term-ref repoint (high frequency) |
| term | TermPaymentTerm | 6 | junction | fallback | unit | same as Term |
| vendor | Vendor | 1197 | mirror | fallback | unit | permanent safety-net decision + 2 scripts |
| vendor | VendorVendor | 1195 | junction | fallback | unit | same as Vendor |
| vendorcredit | VendorCredit | 439 | mirror | none | unit | **NEW** — reconciliation-service repoint |
| vendorcredit | VendorCreditLine | 446 | mirror | none | unit | U-293b |
| vendorcredit | VendorCreditBillCredit | 438 | junction | fallback | unit | **NEW** — reconciliation-service repoint |
| vendorcredit | VendorCreditLineItemBillCreditLineItem | 445 | junction | none | unit | U-293b |

**Infra (explicitly out of scope per the assignment, not re-audited in depth):** `qbo.Auth`, `qbo.Client`,
`qbo.Outbox`, `qbo.ReconciliationIssue`, `qbo.ApiUsage` — 5 tables, program infrastructure, survive regardless
of Phase 6.

That's 34 family tables + 5 infra = 39, matching the live count confirmed via `sys.tables` before this audit
started.

---

## 1.5 Per-family detail

### account — `qbo.Account` (252 rows)

No dbo entity/projection exists (special_no_dbo_entity) — it's a raw QBO chart-of-accounts mirror, historically
consulted only to answer "which GL account is A/P." U-281 (shipped, live) gave that fact a home:
`dbo.Company.APAccountQboId/Name`. Confirmed live: `BillBillConnector._get_ap_account_ref` reads dbo first,
falls back to a live `qbo.Account` scan only if the Company row is missing/uncached. But the repoint's own
cache-refresh mechanism, `QboAccountService._sync_ap_account_cache`, **re-queries the full local `qbo.Account`
mirror on every sync tick by design** — the table is still the source of truth the repoint depends on, not a
pure fallback artifact. Writer confirmed live via two paths (`POST /admin/sync/qbo/account`, `POST /sync/qbo-
accounts`). **Blocked on a new unit** to (a) prove/remove the fallback branch and (b) redesign the cache
derivation off the full mirror.

### attachable — `qbo.Attachable` (4556), `qbo.AttachableAttachment` (4194)

U-279 (live) gave the pull side a dbo-native fast path with legacy fallback. U-286's dead-sproc drops are
confirmed live. **But the push side (`entities/bill/business/service.py::_sync_attachments_to_qbo`) still
creates a brand-new `qbo.Attachable` + `qbo.AttachableAttachment` row on every successful upload at the
currently-deployed HEAD** — confirmed by a live query catching a same-session write (Id 4556/4233, created
minutes before the audit ran). **A partial fix for the push side exists only as uncommitted working-tree WIP
right now** (a `_stamp_pushed_identity` method, not committed, not deployed, not reviewed — presumably a
parallel in-flight session building a slice of U-285; see §4). Even that slice doesn't touch the pull-side
upsert or `entities/invoice/intelligence/prompt.md`'s live, human/agent-facing manual recovery step (raw SQL +
service calls against these exact tables, independently re-confirmed unchanged). `AttachableAttachment` also
carries a **structural** conflict-corroboration read (U-287's no-opt-out conflict guard) that fires on every
fast-path hit, not just misses — retiring that read means relaxing a safety net U-287 built on purpose.
**Blocked on U-285**, which is bigger than the in-flight uncommitted slice covers.

### bill — `qbo.Bill` (20196), `qbo.BillLine` (24531), `qbo.BillBill` (19981), `qbo.BillLineItemBillLine` (23553)

U-283 (header) and U-293 (line, current HEAD) are both deployed. But:
- `Bill`/`BillLine` are raw pull mirrors, written every scheduled tick, read by the daily reconciliation job
  (`_reconcile_bill_qbo_missing_locally`/`_reconcile_bill_qbo_voided`) and the outbox conflict-refresh handler
  (`_refresh_bill`) with **zero fast-path awareness** — those call sites were never touched by any repoint unit.
- **`ProposeInvoiceSourceLinks`' Tier 0c — confirmed live via `OBJECT_DEFINITION`, not doc — still `INNER JOIN`s
  `qbo.[Bill]`/`qbo.[BillLine]`/`qbo.[BillLineItemBillLine]` directly** for exact LinkedTxn matching. This
  contradicts the assignment's premise that U-274 closed all of Bill's cross-family reach — U-274 only closed
  the *fingerprint* tiers; Tier 0 was explicitly left for "family-level dbo-native line identity" that nobody
  has gone back to wire up. It matches 0 rows today only because no such provenance row exists yet.
- `BillBill`/`BillLineItemBillLine` are read+written unconditionally by 4-5 call sites outside the connector's
  own identity check: `BillService.delete_by_public_id`'s mapping cleanup, both reconciliation detectors, the
  outbox refresh handler, and (for the line table) `create_mapping` on every new line pulled.
- **U-293-dw (the RealmId-NULL dual-write bug) has an uncommitted, undeployed fix in the working tree right
  now** (see §4) — until it ships, dbo-native line identity cannot be trusted as sole source of truth.

**Blocked on a new cross-family unit** repointing Tier 0c + the reconciliation detectors + the outbox handler +
the delete-cleanup path off all 4 tables — plus shipping U-293-dw first.

### company_info — `qbo.CompanyInfo` (1), `qbo.CompanyInfoCompany` (1)

U-277→U-287's identity repoint is live and firing (contradicts the scoping doc's 2026-08-19 "no SQL applied"
note — it has since been applied). But `CompanyInfoCompanyConnector.sync_from_qbo_to_company` reads
`qbo.CompanyInfo` **unconditionally** for `legal_name`/`web_addr` field content, independent of whether identity
resolution hit or missed — the repoint only removed the mapping-table hop for *identity*, not the staging read
for *data*. A live, RBAC-gated `POST /sync/qbo-company-info` writer and `GET /qbo-company-info*` reader surface
exists (no web UI caller found, but live and reachable). `CompanyInfoCompany` carries the same structural
conflict-check tradeoff as the other `identity_fastpath`-consolidated families — it fires on every call, hit or
miss, per design. **No unit anywhere addresses either gap** — the program's Phase-5-style "can the raw mirror
itself go away" question was only ever asked for `attachable`/`reimburse_charge`, never `company_info`.

### customer — `qbo.Customer` (209), `qbo.CustomerCustomer` (73), `qbo.CustomerProject` (135)

U-276→U-287 delivered the identity fast path, confirmed live and hitting in steady state (73/73 Customers,
135/136 Projects mapped). Corrects 2 stale `TODO.md` checkboxes: `bill_line_item`'s and `purchase/
expense_line_item`'s pull-resolvers **already** try dbo-native first (only `invoice`'s does not). Three
compounding blockers beyond the historical "4 pull-resolvers" framing:
1. **A genuine, never-addressed in-family gap**: `CustomerProjectConnector.sync_from_qbo_customer`'s own
   parent-Customer lookup reads `qbo.Customer`/`qbo.CustomerCustomer` unconditionally for every job/sub-customer
   sync — inside the very family both U-276 and U-287 touched, yet never repointed, even though a dbo-native
   substitute already exists.
2. `verify_project_qbo_identity()` — a **documented-permanent** safety check, module docstring: "do not soften
   the push side back toward a fall-back" — reads `qbo.CustomerProject`/`qbo.Customer` unconditionally from
   **5** live call sites across bill, purchase, and invoice (2 more than the historical "4 pull-resolvers").
3. A live diagnostic endpoint (`GET /invoice/{id}/draw-audit`) and 2 ops scripts (`reconcile_project.py`,
   `sync_qbo_invoice.py`) also read these tables directly.

`CustomerCustomer` is the closest of the 3 to clear (no permanent-safety-net dependency, only the in-family
parent-lookup gap). `Customer`/`CustomerProject` are blocked on a compound set including an architectural
decision about the permanent safety net — not purely a repoint.

### invoice — `qbo.Invoice` (996), `qbo.InvoiceLine` (30447), `qbo.InvoiceInvoice` (991), `qbo.InvoiceLineItemInvoiceLine` (30391)

U-284 gave the pull connector's own identity check a real dbo-native fast path (confirmed live and deployed).
`ProposeInvoiceSourceLinks` itself is confirmed genuinely clean of any Invoice-family staging reference (U-272/
273/274 fully delivered on this family's own tables — good corroboration). But **3 other live consumers still
read the raw staging rows directly, for data no repoint ever replaced**:
- `ComputeInvoiceDrawMatrix` (sproc, confirmed live, modified today) still reads `qbo.Invoice.TotalAmt` and a
  `qbo.InvoiceLine` count unconditionally as a hard invariant gate on every draw push, regardless of identity-hit
  status.
- `QboInvoiceService.cost_coded_lines_for_invoice` (U-292) still reads `qbo.InvoiceLine` as its *only* line-data
  source for the Trend PDF path — even though the equivalent data already sits on
  `dbo.InvoiceLineItemSourceProvenance` (populated, unused here).
- `ReconciliationService.reconcile_invoice_draws` (daily cron) does a raw, **zero-fast-path** join across
  `Invoice`/`InvoiceLine`/`InvoiceInvoice`.

`InvoiceInvoice` is additionally still dual-written on every new/adopted invoice (`create_mapping`), and 8 of
991 rows currently exercise the fallback for real. `InvoiceLineItemInvoiceLine` has **zero** repoint work —
confirmed no `run_identity_fastpath` import anywhere in that connector — matching U-293b's not-yet-dispatched
status exactly. Push-direction code (`sync_to_qbo_invoice`) is intact but confirmed **dormant** (stub endpoint,
0 outbox rows ever) — latent risk only, not a live blocker.

### item — `qbo.Item` (554), `qbo.ItemCostCode` (78), `qbo.ItemSubCostCode` (475)

U-289 (live, deployed) repointed this family's *own* identity check via the shared helper — confirmed working.
This is a small win against a much larger, previously un-named dependency: **resolving a QBO line's `ItemRef` to
and from a local CostCode/SubCostCode ("cost coding") has zero dbo-native path anywhere**, in either direction,
across all four transaction families. Confirmed live readers: Bill pull+push, Purchase/Expense pull (push
dormant — 0 outbox rows of that kind, matching a documented supersession by `recode_purchase_line`),
VendorCredit pull, Invoice's bulk cost-code index (live via the Trend/draw path, plus a push path reachable only
via a manual admin route), and the live Expense Coding Cockpit. `ItemSubCostCodeConnector`'s own parent-CostCode
resolution is also still a legacy, un-fastpathed hop (`TODO.md` already flags this piece). **This is a bigger,
unscoped consolidation, distinct from and larger than U-293b** — it needs its own reference-helper program
before any of the 3 item-family tables can be considered.

### physical_address — `qbo.PhysicalAddress` (944), `qbo.PhysicalAddressAddress` (780)

U-277/U-287 repointed *identity resolution* (which `dbo.Address` row) onto native `QboId`/`RealmId` — confirmed
live, 780/786 backfilled. But this doesn't touch two deeper facts: (1) `qbo.PhysicalAddress` is read
**unconditionally on literally every call**, fast-path included, because it's the sole holder of the actual
pulled field values (street/city/state/zip) — the repoint only changed how the *target* `dbo.Address` row is
found, never where the values come from; (2) `PhysicalAddressAddress`'s conflict cross-check
(`resolve_mapping_state`) also fires on every call, not just misses, for the same structural reason as
`CompanyInfoCompany`/`AttachableAttachment`. A **real, unfixed, undefended structural risk** was independently
verified: `qbo.PhysicalAddress.QboId` values are composite strings (`f"{qbo_id}_bill"`/`f"{qbo_id}_ship"`) with
**no unique or type-scoping constraint** — a Customer and a Vendor sharing the same native QBO id in the same
realm would silently collide. Zero current occurrences (verified live), but nothing prevents a future one.
**Blocked on a new unit** to redesign the 3 producers (customer/vendor/company_info) to pass address fields
directly, and to close the collision-safety gap before the mandatory cross-check can be removed.

### purchase — `qbo.Purchase` (12336), `qbo.PurchaseLine` (12896), `qbo.PurchaseExpense` (11557), `qbo.PurchaseLineExpenseLineItem` (12069)

**`Purchase` and `PurchaseLine` are KEPT PERMANENTLY by Chris's explicit 2026-08-20 product decision (U-283b)**
— independently re-verified today: the live `/expense-coding` cockpit's queue/metrics sprocs
(`ReadExpenseCodingQueue`/`ReadExpenseCodingMetrics`) read `qbo.Purchase.PrivateNote` and filter directly on
`qbo.PurchaseLine.AccountRefName LIKE '%NEED TO CATEGORIZE%'` — this is the cockpit's *only* source of that raw
text; `dbo.ExpenseCodingItem` only ever stores derived output. Not Phase-6 candidates under current direction,
independent of any repoint status. `PurchaseExpense` (header junction) had its steady-state identity check
repointed by U-283b, but the CREATE path, two live admin endpoints
(`/cancel-expense-from-qbo-purchase`, `/ensure-expense-from-qbo-purchase`), and a dormant-but-wired push path all
still read/write it directly — blocked on a new unit. `PurchaseLineExpenseLineItem` has zero line-identity
repoint work (U-293b territory) **and** is independently confirmed still read by `ProposeInvoiceSourceLinks`'
Tier 0d (contradicting a same-day claim that this reach had already closed — it hadn't; U-274's own comment says
this was deliberately left for "U-283's territory," i.e. this exact gap).

### reimburse_charge — `qbo.ReimburseCharge` (26650)

The cleanest table in the audit, but still not droppable. U-280's dead-column drop (`SourceTxnType`/`Id`/
`LineId`) is confirmed live. Confirmed **zero live readers of any kind** — the one thing with a similar name,
`_build_reimburse_charge_lookup`, calls the live QBO API directly, not this table; independently re-verified,
not just cited from the prior board note. But the table is **actively written hourly** (live
`MAX(ModifiedDatetime)` = today, matching the scheduler's `:13`-past-the-hour timer), and the **keep-vs-retire
product decision flagged in the original scoping doc (§5) is still open** — no board or TODO entry since U-280
closes it. This is the one table in the whole audit that is **purely a Chris decision away from clean
retirement** — no engineering work is needed either way.

### term — `qbo.Term` (6), `qbo.TermPaymentTerm` (6)

U-282 (live) repointed the pull connector's own identity check. **Corrects a stale claim from the assignment's
own briefing**: `dbo.payment_term.sql`'s "deliberate permanent `qbo.Term.Active` LEFT JOIN" (attributed to
U-255) no longer exists — it was superseded by **U-275** the next day, confirmed by reading the live base file.
The real, previously-unflagged blocker: `BillBillConnector._get_qbo_sales_term_ref` — called on every completed
Bill's QBO push (1187 live Bills carry a `PaymentTermId`) — reads `qbo.TermPaymentTerm`/`qbo.Term` directly with
**zero dbo-native attempt at all**, despite `dbo.PaymentTerm.QboId`/`RealmId`/`Name` being 100% populated
already. This is a fresh, unscoped gap, not a known/deferred one.

### vendor — `qbo.Vendor` (1197), `qbo.VendorVendor` (1195)

U-290 (identity) and U-284v (5 cross-family reference resolvers) are both live and correct — 1195/1197 mapped,
zero disagreements. But `verify_vendor_qbo_identity()` — same permanent-by-design shape as the customer/project
check — reads `qbo.VendorVendor` on **every** vendor-ref resolution across Bill push+pull, Purchase pull,
VendorCredit pull, and the Expense Coding Cockpit, confirmed by its own docstring to be deliberate drift
detection, not scaffolding. Two raw-SQL scripts (`generate_payment_remittance.py`, `backfill_qbo_bills.py`),
flagged at U-284v time as not-yet-touched, are confirmed **still** reading these tables directly today. Writer
confirmed live same-day. `TODO.md`'s only booked cleanup here (`[U-005][reuse] resolve_dbo_vendor_id`) is
explicitly deferred to "when multi-realm lands" and, even then, would only consolidate 5 copies of the
*resolver* — it would not by itself remove the *safety-check* read.

### vendorcredit — `qbo.VendorCredit` (439), `qbo.VendorCreditLine` (446), `qbo.VendorCreditBillCredit` (438), `qbo.VendorCreditLineItemBillCreditLineItem` (445)

**Corrects the assignment's own cited premise**: the invoice-side blocker from scoping-doc §7 ("`ProposeInvoice
SourceLinks` reads `qbo.VendorCreditLine`/`VendorCredit`/mapping directly") is **closed** — §7 was superseded by
§9/U-274 the same day it was written, and direct read of the live sproc body confirms zero remaining references.
U-278's header identity repoint is live and correctly dormant in practice (0 rows currently mapped-but-missing-
QboId). The real, previously-unflagged blocker: **`integrations/intuit/qbo/reconciliation/business/service.py`'s
`_reconcile_vendor_credit_qbo_missing_locally`/`_reconcile_vendor_credit_qbo_voided` were never touched by
U-278** and read `VendorCredit`/`VendorCreditBillCredit` unconditionally — confirmed actively firing (live
`ReconciliationIssue` rows dated 2026-08-20/21: "12 QBO VendorCredit(s) are not projected locally"). Line-level
identity (`VendorCreditLine`/`…BillCreditLineItem`) has zero repoint work — U-293b territory, matching the
pattern in every other line family. **Side finding, unrelated to retirement but worth separate attention**:
3 live `qbo.ReconciliationIssue` rows (ids 19470-19472, 2026-08-19) contain literal `unittest.mock.Mock` repr
strings as column content — a test run against mocked repositories wrote real rows to the **production**
database. Flag to whoever owns test/prod isolation; does not affect this audit's conclusions. **Also flagging as
an open question, not a fact**: `qbo.VendorCredit`'s data is 14 days stale versus same-day-fresh siblings
(`Bill`, `Purchase`), which combined with the "12 missing locally" flag suggests its regular pull may not be
running on its usual cadence — could not confirm scheduler wiring from this repo (the timer lives in the
sibling `build.one.scheduler` repo).

---

## 2. Dependency ordering

Every junction table's live FK graph (confirmed via `sys.foreign_keys`) forms the same shape in every family:

```
<line junction>  →  <line staging>  →  <header junction>  →  <header staging>
```

e.g. Bill: `BillLineItemBillLine → BillLine → BillBill → Bill`. Drop order within a family (once every table
clears its own blockers) must always go children-before-parents in that order. One exception: `qbo.
CompanyInfoCompany` has **no enforced FK** in either direction (confirmed via `sys.foreign_keys` — zero rows)
despite carrying both `CompanyId` and `QboCompanyInfoId` under their own unique indexes — a real but
DB-unenforced dependency; treat it the same as an FK for ordering purposes. Across families, the only hard
cross-family dependency is the shared `ProposeInvoiceSourceLinks` sproc (Tier 0c/0d) and the reconciliation
service — both are consumers that must be repointed before Bill's/Purchase's/VendorCredit's tables can drop,
regardless of within-family readiness.

---

## 3. The sequence — what has to happen before any drop conversation starts

This is not a drop sequence (nothing qualifies yet) — it's the dependency-ordered list of prerequisite work,
grouped into waves by what blocks what. Items marked **NEW** have no unit or TODO entry anywhere today; naming
them is this audit's own contribution.

**Wave 0 — already in flight (uncommitted in the working tree right now, not part of this unit, see §4):**
- U-293-dw fix (Bill line dual-write RealmId-NULL bug) — must ship + deploy before `BillLineItemBillLine`'s
  dbo-native identity can be trusted as sole source anywhere.
- A slice of U-285 (Attachable push-side retire) — even once shipped, only closes one of `Attachable`'s several
  live dependencies.

**Wave 1 — small, high-value, single-family fixes (no cross-family coordination needed):**
- **NEW** — `account`: remove/prove-safe the `_get_ap_account_ref` fallback; redesign `_sync_ap_account_cache`
  off the full `qbo.Account` mirror.
- **NEW** — `term`: repoint `BillBillConnector._get_qbo_sales_term_ref` onto `dbo.PaymentTerm` directly (highest
  live-traffic single fix found in this audit — fires on every Bill push).
- **NEW** (small) — `customer`: repoint `CustomerProjectConnector`'s own parent-Customer lookup.
- **NEW** — `purchase`: give `PurchaseExpense`'s CREATE path a dbo-native uniqueness check; repoint the 2 admin
  endpoints; decide the dormant push path's fate.
- **NEW** — `vendor`: repoint `generate_payment_remittance.py` and `backfill_qbo_bills.py`.

**Wave 2 — cross-cutting "reconciliation + outbox layer was never repointed" (recurs in bill, invoice,
vendorcredit — likely purchase too, not independently confirmed here):**
- **NEW, cross-family** — repoint `qbo/reconciliation/business/service.py`'s per-family missing/voided
  detectors and `qbo/outbox/business/worker.py`'s conflict-refresh handlers onto dbo-native identity. One unit
  covering all affected families is more efficient than four near-identical ones.
- **NEW** — repoint `ProposeInvoiceSourceLinks` Tier 0c/0d off `qbo.Bill*`/`qbo.Purchase*` onto dbo-native line
  identity (blocked on Wave 3's line-identity work landing first for the relevant family).

**Wave 3 — line-identity fan-out (booked, not dispatched):**
- **U-293b** — Invoice/Expense/BillCredit line identity, mirroring Bill's U-293 pilot. Per this audit, finishing
  U-293b's originally-scoped identity work is **not sufficient by itself** to clear Invoice's or Purchase's raw-
  data readers (`ComputeInvoiceDrawMatrix`, `cost_coded_lines_for_invoice`, the reconciliation joins) — those
  need Wave 2's follow-up regardless.

**Wave 4 — cost-coding consolidation (new, large, blocks all 3 `item` tables and touches 4 other families):**
- **NEW, cross-family, larger than U-293b** — give the QBO `ItemRef` ↔ `CostCode`/`SubCostCode` resolution (pull
  **and** push, across Bill/Purchase/VendorCredit/Invoice) a dbo-native fast path. Nothing in `item` can move
  until this exists.

**Wave 5 — the permanent-safety-net decision (customer/project, vendor):**
- Decide whether/how to keep `verify_project_qbo_identity`'s and `verify_vendor_qbo_identity`'s theft-detection
  reads once their backing `qbo.*` mapping tables are gone. This is a risk/architecture call for Chris + eng,
  not pure engineering — flag it as its own Gate-1 conversation whenever `customer`/`vendor` retirement is
  actually proposed.

**Wave 6 — "is the raw staging mirror itself retireable at all" (the deepest, hardest question):**
- `Bill`/`BillLine`, `Invoice`/`InvoiceLine`, `Customer`, `PhysicalAddress`, `CompanyInfo`, `Vendor`, `Term`,
  `Item` are not just identity-resolution aids — several are read unconditionally for raw field values on every
  call. Retiring them means re-architecting the pull to write straight into dbo without a staging landing table,
  which nobody has scoped. Until Chris/eng makes that call, treat "Phase 6" as realistically targeting only the
  **identity-junction** tables (`BillBill`, `CustomerCustomer`, `VendorVendor`, etc.), not the raw entity
  mirrors — a narrower, achievable redefinition of the phase worth deciding explicitly rather than discovering
  by attrition.

**Independent, own track:**
- **U-285** — Attachable full retirement (Chris's standing 3-option menu), already sequenced independently.
- **`reimburse_charge`** — pure product decision, no engineering gate.
- **`Purchase`/`PurchaseLine`** — permanent keep, not on any track.

---

## 4. Foreign in-flight work found in the working tree (not touched by this audit)

This audit is strictly read-only and touched no file. But the shared git index (per standing house convention —
parallel sessions share one working tree) currently carries **substantial uncommitted work from a concurrent
session**, confirmed present both before and after this audit ran:

```
 M docs/staging_removal_phase4_5_scoping.md
 M entities/bill_line_item/sql/dbo.bill_line_item.sql
 M entities/invoice/intelligence/prompt.md
 M integrations/intuit/qbo/attachable/connector/attachment/business/service.py
 M integrations/intuit/qbo/base/identity_drift.py
 M integrations/intuit/qbo/bill/connector/bill_line_item/business/service.py
 M scripts/backfill_qbo_identity_lines.py
 M tests/test_backfill_qbo_identity_lines.py
 M tests/test_qbo_bill_line_item_mapping_exceptions.py
 M tests/test_qbo_identity_lines.py
 M tests/test_u234_attachment_upload_honesty.py
```

This matches, and appears to directly correspond to, two of this audit's own findings: a **U-293-dw fix**
(the `identity_drift.py`/`dbo.bill_line_item.sql`/backfill-script changes) and an **Attachable push-side retire
slice** (the `attachable/connector/attachment` change). Neither is committed, reviewed, or deployed as of this
writing — this audit's readiness verdicts above correctly treat both as **not yet shipped**. Flagging this so
whoever picks up either thread knows it's already mid-flight, and so this unit's own commit is scoped to avoid
touching any of it (see Hygiene below).

## 5. DROP-READY set

**Empty.** Every one of the 39 tables has at least one confirmed-live reader or writer beyond identity
resolution. `qbo.ReimburseCharge` is the closest to clean (zero live readers, code/schema fully settled) but is
still actively written hourly and gated on an open product decision, not code.

## 6. One guarded unit vs. batched per-family — recommendation

**The batching question is premature — there is nothing to batch yet.** Every table in this audit is blocked on
prerequisite work, not on a drop-execution decision. The more useful recommendation right now is about the
*prerequisite* work above (§3): tackle Wave 1's small single-family fixes independently and in parallel (they
don't share files or call sites), then the two genuinely cross-cutting programs (Wave 2's reconciliation/outbox
layer, Wave 4's cost-coding consolidation) as their own dedicated units since each touches 3-4 families at once
and a single unit avoids the hand-copy-then-consolidate anti-pattern this program has hit twice already (U-276→
U-287, and now the reconciliation-layer gap repeating that same shape a third time).

**For the eventual drops themselves**, once a family's tables actually clear every blocker: **batch per-family**,
mirroring U-286's guarded `IF EXISTS`, re-counted-zero-caller precedent — not one program-wide drop unit. Reasons:
(1) families are clearing at very different rates (`reimburse_charge` could clear in one product decision;
`item`'s cost-coding consolidation is a multi-week program) — waiting for all 34 to clear simultaneously means
the fastest-clearing families sit idle for no reason; (2) each family's drop needs its own fresh zero-caller
re-verification immediately before executing (per the standing discipline — "re-verify unreferenced... immediately
before executing", not "it was zero-callers last week"), which is naturally a per-family unit of work anyway;
(3) a family-sized blast radius (2-4 tables) is verifiable end-to-end by one person in one sitting, matching
U-286's own scale. The one exception worth batching together: a family's line-junction + line-staging + header-
junction + header-staging tables should drop together as one guarded unit (they're already one dependency chain
per §2), not four separate ones.
