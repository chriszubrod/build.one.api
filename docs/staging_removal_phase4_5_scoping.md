# Staging-removal Phase 4 + 5 — scoping (U-273)

**Status:** scoping only — no connector code changed. **Read-only investigation**, per-entity classification, and a proposed sub-unit split for Chris to pick from.

**Method:** a 28-agent fan-out — each of the 13 entity connector families got an independent classification pass, then an independent *adversarial* re-derivation pass (told nothing it should trust, re-read the code itself, empowered to disagree). Every `phase3_blocked` verdict below survived the adversarial pass unchanged; where the verify pass found a real correction, it's folded in and the discrepancy is called out. A separate 2-pass investigation covered Phase 5 (`qbo.Attachable`/`qbo.AttachableAttachment`).

---

## 0. Two things that changed the shape of this unit before it started

**Unit-number collision, twice.** The assignment specified `U-271`; that ID was already shipped same-day to unrelated work (`20443bba`, invoice Trend). Renumbered to `U-272` per Chris's call — which then turned out to *also* be live, uncommitted WIP in this same shared tree: a peer session is mid-flight building **"U-272 — staging-removal Phase 3: dbo-native QBO source-link provenance"** for the Invoice family (`entities/invoice/sql/dbo.invoice.sql`, `entities/invoice_line_item/**`, a new `dbo.InvoiceLineItemSourceProvenance` mirror table, a backfill script, a dedicated test file — 296 uncommitted insertions across 6 files as of this writing). Renumbered again to **U-273**, the actual next-free ID. Nothing in that peer WIP was touched — it was read-only input to the Invoice section below.

**The assignment's Phase-3 premise was stale, but not wrong in spirit.** The prompt guessed Phase 3 (provenance re-home) hadn't started anywhere. That was true when read from committed state (`TODO.md:123`, the `U-238` board row) but is no longer true for one narrow slice: the peer's uncommitted `U-272` work re-homes the Invoice line's *own* `LineNum`/`ServiceDate`/`LinkedTxnType`/`ItemRefValue`/`LinkedTxnId` off `qbo.InvoiceLine` onto the new dbo mirror, and repoints `ProposeInvoiceSourceLinks`' header identity lookup off `qbo.Invoice`/`qbo.InvoiceInvoice` onto `dbo.Invoice`/`dbo.Project` directly. It does **not** touch the candidate-matching side of that same sproc (the joins into `qbo.BillLine`/`qbo.PurchaseLine`/`qbo.VendorCreditLine` — those still read staging). So Bill/Purchase remain blocked exactly as scoped below; Invoice's own blocked surface is narrower than the assignment guessed, not zero. See §4 (Invoice) for what's left.

---

## 1. Scope verification (the assignment's counts, corrected)

| Claimed | Verified | Note |
|---|---|---|
| 244 sprocs | **252** | 216 across the 13 entity families (table below) + 36 infra (`Auth` 7, `Client` 7, `Outbox` 11, `ReconciliationIssue` 9, `ApiUsage`+views 2) |
| 57 `qbo.*.sql` files | **57 — exact match** | 37 real (31 entity + 6 infra) + 20 `dev/*.samples.sql` seed-data fixtures (0 sprocs each) |
| 45+ Python files | **materially higher, no single number** | Every family's verify pass found call sites the classify pass missed — cross-family reach-ins, shared identity-drift scripts, shared test files. There is no clean per-family count that doesn't also count shared infrastructure multiple times; treat "45+" as a floor, not a spec, exactly as the assignment warned. |
| 19 connector "families" | **13 entity families + 5 infra + 1 no-op** | Directories under `integrations/intuit/qbo/`: 13 map to a dbo entity (below); `auth`/`base`/`client`/`outbox`/`reconciliation` are the explicitly-protected infra the assignment said not to touch; `report` has **zero** `qbo.*` schema dependency — it calls the live QBO Reports API directly and only imports `auth`/`base` infra modules. Out of scope for Phase 4/5 entirely. |

---

## 2. Per-family classification

`a` = pure staging bookkeeping (Phase 4 eliminates, no external value) · `b` = raw mirroring / real business-logic reach (flagged, disposition is Chris's call) · `c` = already obsolete (safe immediate delete, independent of Phase 4 timing)

| Family | dbo entity | Sprocs | Phase-3 blocked? | Blocker (if any) |
|---|---|---|---|---|
| account | *(none — no dbo projection)* | 8 | **Yes** | `BillBillConnector._get_ap_account_ref` reads `qbo.Account.AccountType` on every live Bill push; no dbo home for "which GL account is AP" |
| attachable | Attachment | 13 | No | Phase-4-ready. (Phase 5 retirement is a separate, deeper question — §6.) |
| bill | Bill + BillLineItem | 23 | **Yes** | `ProposeInvoiceSourceLinks` (Invoice family) reads `qbo.BillLine.{CustomerRefValue,ItemRefValue,Amount,Description}` + `qbo.Bill.TxnDate` for cross-family source-link fingerprinting |
| company_info | Company | 14 | No | Phase-4-ready. Sequence with `physical_address` (real coupling, see below). |
| customer | Customer + Project | 20 | No | Phase-4-ready. One cross-family read (`DisplayName` in Bill's/Invoice's push helpers) has a ready dbo substitute (`dbo.Project.Name`/`.QboId`) — no new schema needed. |
| invoice | Invoice + InvoiceLineItem | 23 | **Yes (narrower than scoped)** | `draw_financials.py` parses `qbo.InvoiceLine.ItemRefName` (Trend feature, workaround for corrupt `qbo.ItemSubCostCode`); `push.py`'s `ComputeInvoiceDrawMatrix` reads `qbo.Invoice.TotalAmt` + a `qbo.InvoiceLine` count as a hard invariant gate on every draw push. The 3rd reach the assignment flagged (audit.py/reconciliation.py ItemRefName) is resolved by the in-flight peer `U-272` work. |
| item | CostCode + SubCostCode | 20 | **Yes** | `dbo.vw_SubCostCode` LEFT JOINs `qbo.Item.Active` (no dbo-native Active column); Bill's *live* push connector reads `qbo.Item.Name` for outbound `ItemRef.name` (Purchase's and Invoice's equivalent reads exist but are dormant/unreachable code paths) |
| physical_address | Address | 14 | No | Phase-4-ready. Real blast radius is 3 producer families (company_info, customer, **and** vendor — the classify pass only found company_info), not 1. |
| purchase | Expense + ExpenseLineItem | 26 | **Yes** | The U-005 expense-coding cockpit (`/expense-coding`, live web route) is *built on* `qbo.Purchase.PrivateNote`/`qbo.PurchaseLine.AccountRefName` — `dbo.ExpenseCodingItem` stores only derived output, never the source text. `ProposeInvoiceSourceLinks` also reaches in (no `DirectDbo` fallback tier exists for Purchase/Expense, unlike Bill's). |
| reimburse_charge | *(none — QBO-synthetic, no dbo entity)* | 4 | No | Phase-4-ready in the sense that nothing blocks it — but "Phase 4" doesn't map cleanly (see §5). |
| term | PaymentTerm | 14 | **Yes** | `dbo.payment_term.sql`'s own read sprocs LEFT JOIN `qbo.Term.Active` — shipped as a **deliberate** design decision (U-255, `89885a07`, 2026-08-17), not a stopgap. ⚠️ SQL committed but **not yet applied to prod** per `TODO.md`. |
| vendor | Vendor | 14 | **Yes** | Identical `QboActive` LEFT JOIN pattern as term (same U-255 commit, same not-yet-applied caveat). Separately: `qbo.VendorVendor`'s mapping-table repoint has much wider fan-out than scoped — Bill, Purchase/Expense, VendorCredit, and the live expense-coding cockpit all resolve vendor refs through it. |
| vendorcredit | BillCredit + BillCreditLineItem | 23 | No | Phase-4-ready. No `ItemRefName`-shaped business computation found anywhere in this family — see the open discrepancy in §7. |

**Verified total: 216 sprocs across the 13 families** (matches the independently-grepped 216 exactly — `rollup.totalVerifiedSprocs` cross-validated against a plain `grep` count done before the fan-out ran).

**Blocked: account, bill, invoice, item, purchase, term, vendor (7). Ready: attachable, company_info, customer, physical_address, reimburse_charge, vendorcredit (6).**

---

## 3. Two shared prerequisites — fix once, unblock three families

**3a. The `QboActive` mirror gap (unblocks term, and half of vendor and item).** Three families' *own entity-layer* read sprocs (not the connector layer) LEFT JOIN their staging table's `Active` column because Phase 2 (U-238a/b/c) moved `QboId`/`RealmId`/`SyncToken` to dbo but not deactivation status:
- `entities/payment_term/sql/dbo.payment_term.sql` → `qbo.Term.Active`
- `entities/vendor/sql/dbo.vendor.sql` → `qbo.Vendor.Active`
- `entities/sub_cost_code/sql/dbo.subcostcode.sql` → `qbo.Item.Active`

All three shipped the same day (U-255, `89885a07`, 2026-08-17) as a considered decision to keep the join rather than add a mirror column (`TODO.md` item (j)) — this is settled design, not an oversight. **Caveat found independently by two verify passes: the SQL is committed but not yet applied to prod** — confirm live sprocs actually carry the join before treating any of these three as "currently blocked in prod" vs. "blocked once this ships." Disposition is Chris's: either accept these three staging columns as a permanent narrow Active-mirror (cheapest), or add a real dbo-native Active column + dual-write and retire all three joins in one small cross-family sub-unit (mirrors U-238c's own precedent). One sub-unit either way, not three.

**3b. The reference-lookup-helper pattern in Bill's push connector.** `integrations/intuit/qbo/bill/connector/bill/business/service.py` has five near-identical helpers (`_get_qbo_vendor_ref`, `_get_qbo_item_ref`, `_get_qbo_customer_ref`, `_get_ap_account_ref`, `_get_qbo_sales_term_ref`) — each hops a mapping table to a reference family's `qbo.*` staging row to build an outbound `QboReferenceType(value=qbo_id, name=name)` for the live Bill-push payload. Four independent family agents (account, customer, item, term) flagged this same file and independently recommended **not** touching it five separate times across five separate family units. Once each reference family's own Phase-4 work lands (all five already have dbo-native `QboId`; `Name` is the only piece still needing a source), rewire all five helpers in one pass.

---

## 4. Invoice — what's actually left after the in-flight peer work

Two independent, currently-uncovered reaches into Invoice's own `qbo.*` tables, both outside the connector layer, neither touched by the peer `U-272` WIP:

1. **`draw_financials.py::_qbo_derived_draw`** parses `qbo.InvoiceLine.ItemRefName` directly to derive historical/migrated all-Manual invoices' cost-code rollup for the Trend feature — an explicit workaround for a corrupt `qbo.ItemSubCostCode` mapping table. The real fix (repair the mapping table, expose a `QboInvoiceService.cost_coded_lines_for_invoice()` seam) is an **open, unchecked** `TODO.md` item from the U-271 review, not built.
2. **`push.py`'s `ComputeInvoiceDrawMatrix`** reads `qbo.Invoice.TotalAmt` and a `qbo.InvoiceLine` row count as a hard invariant gate that halts every draw push on mismatch. The peer `U-272` unit's own diff left this sproc's business-column joins untouched while rewriting everything else it touched in the same file — no comment or doc anywhere explains why (one verify pass caught a fabricated citation in the first-pass writeup claiming an explicit "deliberately not waste" rationale exists in a migration file; that file and quote do not exist — treat the omission as **undocumented**, not deliberate, until confirmed with that session). Disposition: keep `qbo.Invoice`/`qbo.InvoiceLine` alive permanently as a narrow drift-check mirror, or redesign the gate against a live QBO API call at push time.

Cross-family entanglement, out of Invoice's own scope but relevant to sequencing: `ProposeInvoiceSourceLinks` also reaches into Bill's and Purchase's *own* staging tables (§2) — Invoice's Phase 4 can't be the last domino; it has to land after (or alongside) Bill's and Purchase's line-level work.

---

## 5. reimburse_charge — doesn't fit the Phase-4 pattern at all

`qbo.ReimburseCharge` has no corresponding dbo entity — it's a QBO-synthetic object the app never creates, not something Phase 4's "write directly to dbo, drop the staging table" pattern maps onto. Its identity/business columns are confirmed to have **zero current production readers**: the one sproc that ever joined it for real reconciliation logic (a Tier-0 arm in `ProposeInvoiceSourceLinks`) was built (U-186) then explicitly removed as "provably dead" by the already-shipped `U-244` (`74203bac`, 2026-08-17), which delivered its replacement via a different mechanism entirely (tightening the existing Bill/Purchase-line fingerprint tiers, never touching `qbo.ReimburseCharge`). Its `SourceTxnType`/`SourceTxnId`/`SourceTxnLineId` columns are provably 100%-NULL across all 26,582 live rows — safe, immediate, zero-risk deletion regardless of any other decision.

This needs a **keep-vs-retire product decision**, not a mechanical repoint: is a 26K-row, hourly-refreshed QBO mirror worth keeping now that its intended consumer took a different path? Scope as its own small disposition sub-unit.

---

## 6. Phase 5 — `qbo.Attachable` / `qbo.AttachableAttachment`

Two-pass investigation (folding in U-261 without re-deriving it — its findings are local `dbo.Attachment`-only intake-path bugs, confirmed independent of whatever happens to these two tables).

**Retirement is a repoint, not a bare delete.** `dbo.Attachment` already has native `QboId`/`RealmId` (U-238c) and even an index ready for it (`UQ_Attachment_QboId_RealmId`), but **no read-by-QboId sproc exists yet** — today's pull/push idempotency checks and the per-batch Bill/Purchase/VendorCredit attachment-linking scripts all key off `qbo.Attachable`'s internal staging PK, not `dbo.Attachment.QboId` directly. That's the one missing (but trivially addable) piece.

**What actually depends on it beyond identity:**
- Pull/push idempotency ledger (`qbo.AttachableAttachment`, live, both directions)
- Three line-item-linking call sites (`sync_qbo_bill.py`, `sync_qbo_vendorcredit.py`, `purchase/connector/expense/business/service.py`) — all confirmed **live** via scheduler timers, including the VendorCredit path a stale 2026-08-07 audit finding had flagged dead (re-verified: it's fixed, currently wired)
- The live Bill-completion push path (`entities/bill/business/service.py::_sync_attachments_to_qbo`, outbox-drain triggered) writes a *new* `qbo.Attachable` row on every successful upload — retirement needs a new landing spot for that write
- A dead-but-real cross-package SQL reach: `qbo.purchase.sql`'s `ReadQboPurchaseLinesNeedingUpdate` joins `qbo.Attachable.EntityRefType/EntityRefValue` — the whole Python call chain down to it has zero live callers, but the sproc itself must be dropped/updated before the columns can go, or the migration won't apply
- **Newly found:** `entities/invoice/intelligence/prompt.md` (the InvoiceAgent operational playbook) directly instructs manual `QboAttachableService`/`QboAttachableRepository` calls and a raw `qbo.AttachableAttachment` SQL lookup as a documented recovery step — a live, human/agent-facing dependency, not dead weight, that a retirement plan needs to update

**A genuinely low-risk slice exists today independent of the bigger decision:** `SyncToken`, `FileAccessUri`, `TempDownloadUri`, `EntityRefType`/`Value`, and `read_by_entity_ref` are stored but never read by anything outside the package (confirmed exhaustively, matches the 2026-08-07 audit's independent finding) — near-zero-blast-radius to drop, modulo the dead `qbo.purchase.sql` sproc above.

**Menu for Chris:** (a) leave as-is, revisit when the broader program reaches its deletion phase; (b) authorize dropping just the confirmed-dead column slice (no schema-shape change, no call-site rewrite); (c) authorize the full repoint-and-retire as a standalone unit, using U-238c as the template. Structurally this table has no provenance coupling (unlike the 4 transactional staging tables) and no irreplaceable data (unlike `reimburse_charge`), so it can move ahead of or independent of the rest of the program's sequencing — either timing is defensible.

---

## 7. Open discrepancy to confirm before finalizing sequencing

Invoice's classify pass listed `qbo.VendorCreditLine`/`qbo.VendorCredit`/`qbo.VendorCreditLineItemBillCreditLineItem` among the tables `ProposeInvoiceSourceLinks` reaches into for cross-family fingerprinting (alongside Bill's and Purchase's, which are independently confirmed live). VendorCredit's own family classify **and** verify passes — reading the same current sproc body, specifically looking for exactly this — found **no** such reach, and independently re-derived `phase3_blocked=false` for the whole family. The likely explanation (per the Bill-family verify's finding that BillLineItem gets a `DirectDbo`-only fallback tier while Purchase's doesn't) is that BillCredit-sourced lines may get the same `DirectDbo`-only treatment Bill's do, needing no `qbo.VendorCreditLine` reach at all — but this wasn't directly confirmed by either side. **Five-minute check before treating VendorCredit as unconditionally Phase-4-ready:** read `ProposeInvoiceSourceLinks`' BillCredit-sourced tier directly and confirm whether it has a live `qbo.VendorCredit*` join or a `DirectDbo` tier like Bill's.

---

## 8. Proposed sub-unit split (mirrors Phase 2's shape-based split — U-238a/b/c)

Not "one unit does all 13" — a dependency-ordered punch list:

**Track A — prerequisites (small, unblock multiple families):**
1. `QboActive` dbo-native mirror decision + build (§3a) — unblocks term fully, half of vendor, half of item
2. Confirm-and-apply the U-255 SQL to prod if not already live (verification step, not new code)
3. Reference-lookup-helper consolidation in Bill's push connector (§3b) — do once other reference families are ready

**Track B — ready today, no prerequisite (6 families, can start independently, any order):**
4. `vendorcredit` — full repoint (~3,500 LOC, 23 sprocs) — confirm §7 first
5. `customer` — split `CustomerCustomer` (self-contained, small) from `CustomerProject` (wide fan-out — 5 other families read through it) as two sub-steps if a small pilot is wanted first
6. `company_info` + `physical_address` — **must land together** (real FK-shaped coupling, no enforced constraint but `qbo.CompanyInfo.*AddrId` columns point at `qbo.PhysicalAddress.Id`); `physical_address`'s true producer surface is company_info **and** customer **and** vendor, not company_info alone
7. `attachable` — Phase-4 repoint (~750 LOC, 13 sprocs); sequence back-to-back with whatever Phase 5 disposition is chosen so the connector isn't touched twice
8. `reimburse_charge` — needs the keep-vs-retire decision (§5) before any code

**Track C — blocked, needs its own prerequisite first:**
9. `account` — small, self-contained prerequisite (give `_get_ap_account_ref`'s one business fact a dbo-native home — a cached AP-account field on Settings/Company), then the rest of the family (no dbo projection exists or is needed) is a straightforward RETIRE, same shape as U-218c's account-delete-reconcile precedent
10. `term`, `vendor`, `item` (SubCostCode side) — unblocked by Track A #1; `item`'s CostCode side has no blocker and could split off earlier
11. `bill`, `purchase` — both blocked by `ProposeInvoiceSourceLinks`' cross-family reach; their **mapping tables** (`BillBill`/`BillLineItemBillLine`, `PurchaseExpense`/`PurchaseLineExpenseLineItem`) are NOT blocked and can repoint independently of the business-column question. `purchase` additionally needs the expense-coding-cockpit raw-field disposition decided (keep `qbo.Purchase`/`PurchaseLine` as permanent audit mirror vs. give `PrivateNote`/`AccountRefName` a dbo-native home) — shared with the Invoice fingerprint question, decide once.
12. `invoice` — the narrower remaining blockers (§4): repair `qbo.ItemSubCostCode` + build the seam `draw_financials.py` needs (already scoped in `TODO.md`'s U-271 follow-ups, not picked up), and decide `ComputeInvoiceDrawMatrix`'s disposition. Sequence after Bill/Purchase land, since `ProposeInvoiceSourceLinks` depends on both.

**Track D — independent:**
13. Phase 5 (`qbo.Attachable`/`qbo.AttachableAttachment`) — sequencing-independent, Chris's 3-option menu (§6)

**Low-risk, ship-anytime cleanup (no Phase-4 timing dependency, found across nearly every family):** several dead sprocs/methods with zero callers repo-wide — `DeleteQboAccountByQboId`, `ReadQboAttachablesByEntityRef`/`ReadQboAttachablesByRealmId`/`DeleteQboAttachableByQboId`/`ReadAttachableAttachmentById`, `ReadItemCostCodeById`/`ReadItemSubCostCodeById` + their connector wrapper methods, `ReadQboPurchaseLinesNeedingUpdate` (purchase family — also blocks the Attachable column-drop, see §6), 3-4 orphaned `[qbo]`-schema-qualified duplicate sprocs in the vendorcredit family (unreachable by the app's `EXEC dbo.*` convention, same bug class U-225 partially fixed for the table declaration but missed for the sproc). None of these need to wait for a Phase-4 migration window.

---

## Appendix — infra tables, explicitly untouched

Per the assignment's own instruction, no disposition proposed for `qbo.Auth`, `qbo.Client`, `qbo.Outbox`, `qbo.ReconciliationIssue`, `qbo.ApiUsage` — these are program infra, not staging, and survive regardless of Phase 4/5/6 outcome.
