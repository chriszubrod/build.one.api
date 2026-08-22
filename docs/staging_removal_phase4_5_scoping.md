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
| company_info | Company | 14 | No | Phase-4-ready. Sequence with `physical_address` (real coupling, see below). **Built (U-277, 2026-08-19, see §12).** |
| customer | Customer + Project | 20 | No | Phase-4-ready. One cross-family read (`DisplayName` in Bill's/Invoice's push helpers) has a ready dbo substitute (`dbo.Project.Name`/`.QboId`) — no new schema needed. **Built (U-276, 2026-08-19, see §10).** |
| invoice | Invoice + InvoiceLineItem | 23 | **Yes (narrower than scoped)** | `draw_financials.py` parses `qbo.InvoiceLine.ItemRefName` (Trend feature, workaround for corrupt `qbo.ItemSubCostCode`); `push.py`'s `ComputeInvoiceDrawMatrix` reads `qbo.Invoice.TotalAmt` + a `qbo.InvoiceLine` count as a hard invariant gate on every draw push. The 3rd reach the assignment flagged (audit.py/reconciliation.py ItemRefName) is resolved by the in-flight peer `U-272` work. |
| item | CostCode + SubCostCode | 20 | **Yes** | `dbo.vw_SubCostCode` LEFT JOINs `qbo.Item.Active` (no dbo-native Active column); Bill's *live* push connector reads `qbo.Item.Name` for outbound `ItemRef.name` (Purchase's and Invoice's equivalent reads exist but are dormant/unreachable code paths) |
| physical_address | Address | 14 | No | Phase-4-ready. Real blast radius is 3 producer families (company_info, customer, **and** vendor — the classify pass only found company_info), not 1. **Built (U-277, 2026-08-19, see §12) — all 3 producers repointed via one connector change, confirmed by call-site audit.** |
| purchase | Expense + ExpenseLineItem | 26 | **Yes** | The U-005 expense-coding cockpit (`/expense-coding`, live web route) is *built on* `qbo.Purchase.PrivateNote`/`qbo.PurchaseLine.AccountRefName` — `dbo.ExpenseCodingItem` stores only derived output, never the source text. `ProposeInvoiceSourceLinks` also reaches in (no `DirectDbo` fallback tier exists for Purchase/Expense, unlike Bill's). |
| reimburse_charge | *(none — QBO-synthetic, no dbo entity)* | 4 | No | Phase-4-ready in the sense that nothing blocks it — but "Phase 4" doesn't map cleanly (see §5). |
| term | PaymentTerm | 14 | **Yes** | `dbo.payment_term.sql`'s own read sprocs LEFT JOIN `qbo.Term.Active` — shipped as a **deliberate** design decision (U-255, `89885a07`, 2026-08-17), not a stopgap. ⚠️ SQL committed but **not yet applied to prod** per `TODO.md`. |
| vendor | Vendor | 14 | **Yes** | Identical `QboActive` LEFT JOIN pattern as term (same U-255 commit, same not-yet-applied caveat). Separately: `qbo.VendorVendor`'s mapping-table repoint has much wider fan-out than scoped — Bill, Purchase/Expense, VendorCredit, and the live expense-coding cockpit all resolve vendor refs through it. |
| vendorcredit | BillCredit + BillCreditLineItem | 23 | No | Header/reference surface Phase-4-ready (no `ItemRefName`-shaped business computation anywhere in this family). **Line-level staging BLOCKED:** `qbo.VendorCreditLine`/`qbo.VendorCredit`/`…BillCreditLineItem` have a live invoice-side reader in `ProposeInvoiceSourceLinks` (§7 resolved) — coupled to the invoice-side line-provenance sub-unit (§4). |

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
- The live Bill-completion push path (`entities/bill/business/service.py::_sync_attachments_to_qbo`, outbox-drain triggered) writes a *new* `qbo.Attachable` row on every successful upload — retirement needs a new landing spot for that write. **U-285 built that landing spot (2026-08-21) — see §15.** The PULL side below is unaffected and still stages `qbo.Attachable` for now.
- A dead-but-real cross-package SQL reach: `qbo.purchase.sql`'s `ReadQboPurchaseLinesNeedingUpdate` joins `qbo.Attachable.EntityRefType/EntityRefValue` — the whole Python call chain down to it has zero live callers, but the sproc itself must be dropped/updated before the columns can go, or the migration won't apply
- **Newly found:** `entities/invoice/intelligence/prompt.md` (the InvoiceAgent operational playbook) directly instructs manual `QboAttachableService`/`QboAttachableRepository` calls and a raw `qbo.AttachableAttachment` SQL lookup as a documented recovery step — a live, human/agent-facing dependency, not dead weight, that a retirement plan needs to update

**A genuinely low-risk slice exists today independent of the bigger decision:** `SyncToken`, `FileAccessUri`, `TempDownloadUri`, `EntityRefType`/`Value`, and `read_by_entity_ref` are stored but never read by anything outside the package (confirmed exhaustively, matches the 2026-08-07 audit's independent finding) — near-zero-blast-radius to drop, modulo the dead `qbo.purchase.sql` sproc above.

**Menu for Chris:** (a) leave as-is, revisit when the broader program reaches its deletion phase; (b) authorize dropping just the confirmed-dead column slice (no schema-shape change, no call-site rewrite); (c) authorize the full repoint-and-retire as a standalone unit, using U-238c as the template. Structurally this table has no provenance coupling (unlike the 4 transactional staging tables) and no irreplaceable data (unlike `reimburse_charge`), so it can move ahead of or independent of the rest of the program's sequencing — either timing is defensible.

---

## 7. Discrepancy — RESOLVED (VendorCredit is NOT unconditionally Phase-4-ready)

Invoice's classify pass listed `qbo.VendorCreditLine`/`qbo.VendorCredit`/`qbo.VendorCreditLineItemBillCreditLineItem` among the tables `ProposeInvoiceSourceLinks` reaches into for cross-family fingerprinting (alongside Bill's and Purchase's, which are independently confirmed live). VendorCredit's own family classify **and** verify passes — reading the same current sproc body, specifically looking for exactly this — found **no** such reach, and independently re-derived `phase3_blocked=false` for the whole family. The likely explanation (per the Bill-family verify's finding that BillLineItem gets a `DirectDbo`-only fallback tier while Purchase's doesn't) is that BillCredit-sourced lines may get the same `DirectDbo`-only treatment Bill's do, needing no `qbo.VendorCreditLine` reach at all — but this wasn't directly confirmed by either side. **Resolved (U-273 Gate-2, direct read of the live prod sproc post-U-272 apply, 2026-08-19):** the Invoice classify pass was **correct** and the VendorCredit family classify **missed the reach**. `ProposeInvoiceSourceLinks` reaches `qbo.VendorCredit*` in **two** live places: (1) the **Tier-3 `BillCreditLineItem`-source arm** — `INNER JOIN qbo.VendorCreditLine → qbo.VendorCredit → qbo.VendorCreditLineItemBillCreditLineItem map → dbo.BillCreditLineItem`, carrying `DirectDbo=0` (the amount/description/date match is performed against `qbo.VendorCreditLine`/`qbo.VendorCredit`, resolving to `dbo.BillCreditLineItem` only through the staging map); and (2) the **Tier-1 Bill `DirectDbo` arm's third `NOT EXISTS` guard**, which anti-joins the same `qbo.VendorCreditLine`/`map` chain to avoid double-matching. There is **no** `DirectDbo`-only BillCredit tier — unlike Bill (which has a genuine `DirectDbo=1` arm matching `dbo.BillLineItem`/`dbo.Bill` directly), BillCredit's *only* tier is the staging-based Tier-3. **Consequence:** `qbo.VendorCreditLine`/`qbo.VendorCredit`/`qbo.VendorCreditLineItemBillCreditLineItem` each have a live invoice-side reader, so this family's staging cannot be retired in isolation — its line-provenance removal is **coupled to the same invoice-side sub-unit that re-homes the Bill/Purchase line-matching in this sproc** (§4). Treat `vendorcredit` as Phase-4-ready **only for the header/reference surface**; its line-level staging is blocked behind that sub-unit. Update §2's `vendorcredit` row and §8 item 4 accordingly.

---

## 8. Proposed sub-unit split (mirrors Phase 2's shape-based split — U-238a/b/c)

Not "one unit does all 13" — a dependency-ordered punch list:

**Track A — prerequisites (small, unblock multiple families):**
1. `QboActive` dbo-native mirror decision + build (§3a) — unblocks term fully, half of vendor, half of item
2. Confirm-and-apply the U-255 SQL to prod if not already live (verification step, not new code)
3. Reference-lookup-helper consolidation in Bill's push connector (§3b) — do once other reference families are ready

**Track B — ready today, no prerequisite (6 families, can start independently, any order):**
4. `vendorcredit` — header/reference repoint now (~3,500 LOC, 23 sprocs); **line-level staging deferred** — blocked behind the invoice-side line-provenance sub-unit (§7 resolved, coupled via `ProposeInvoiceSourceLinks`). **Also carries a customer-staging-drop prerequisite from U-276 (§10):** its line connector's `_get_project_public_id` pull resolver still hops `qbo.Customer → qbo.CustomerProject` for Project identity — repoint it onto direct `dbo.Project.QboId`/`RealmId` lookup (mirror U-276's pattern) before `qbo.Customer` can actually be dropped in Phase 6.
5. `customer` — **Built (U-276, 2026-08-19, see §10)** — split `CustomerCustomer` (self-contained, small) from `CustomerProject` (wide fan-out) as two sub-steps. Pull-resolver repoint deferred as an explicit prerequisite on U-278/283/284 (§10).
6. `company_info` + `physical_address` — **must land together** (real FK-shaped coupling, no enforced constraint but `qbo.CompanyInfo.*AddrId` columns point at `qbo.PhysicalAddress.Id`); `physical_address`'s true producer surface is company_info **and** customer **and** vendor, not company_info alone
7. `attachable` — Phase-4 repoint (~750 LOC, 13 sprocs); sequence back-to-back with whatever Phase 5 disposition is chosen so the connector isn't touched twice
8. `reimburse_charge` — needs the keep-vs-retire decision (§5) before any code

**Track C — blocked, needs its own prerequisite first:**
9. `account` — small, self-contained prerequisite (give `_get_ap_account_ref`'s one business fact a dbo-native home — a cached AP-account field on Settings/Company), then the rest of the family (no dbo projection exists or is needed) is a straightforward RETIRE, same shape as U-218c's account-delete-reconcile precedent
10. `term`, `vendor`, `item` (SubCostCode side) — unblocked by Track A #1; `item`'s CostCode side has no blocker and could split off earlier
11. `bill`, `purchase` — both blocked by `ProposeInvoiceSourceLinks`' cross-family reach; their **mapping tables** (`BillBill`/`BillLineItemBillLine`, `PurchaseExpense`/`PurchaseLineExpenseLineItem`) are NOT blocked and can repoint independently of the business-column question. `purchase` additionally needs the expense-coding-cockpit raw-field disposition decided (keep `qbo.Purchase`/`PurchaseLine` as permanent audit mirror vs. give `PrivateNote`/`AccountRefName` a dbo-native home) — shared with the Invoice fingerprint question, decide once. **Also carries a customer-staging-drop prerequisite from U-276 (§10):** `bill_line_item`'s and `purchase`'s line connectors' `_get_project_public_id` pull resolvers still hop `qbo.Customer → qbo.CustomerProject` for Project identity — repoint both onto direct `dbo.Project.QboId`/`RealmId` lookup (mirror U-276's pattern) before `qbo.Customer` can actually be dropped in Phase 6.
12. `invoice` — the narrower remaining blockers (§4): repair `qbo.ItemSubCostCode` + build the seam `draw_financials.py` needs (already scoped in `TODO.md`'s U-271 follow-ups, not picked up), and decide `ComputeInvoiceDrawMatrix`'s disposition. Sequence after Bill/Purchase land, since `ProposeInvoiceSourceLinks` depends on both. **Also carries a customer-staging-drop prerequisite from U-276 (§10):** the invoice connector's `_get_project_public_id` pull resolver still hops `qbo.Customer → qbo.CustomerProject` — repoint it too (same pattern; this one already has an auto-heal path calling `CustomerProjectConnector.heal_missing_mapping()`, verify that seam still makes sense post-repoint).

**Track D — independent:**
13. Phase 5 (`qbo.Attachable`/`qbo.AttachableAttachment`) — sequencing-independent, Chris's 3-option menu (§6)

**Low-risk, ship-anytime cleanup (no Phase-4 timing dependency, found across nearly every family):** several dead sprocs/methods with zero callers repo-wide — `DeleteQboAccountByQboId`, `ReadQboAttachablesByEntityRef`/`ReadQboAttachablesByRealmId`/`DeleteQboAttachableByQboId`/`ReadAttachableAttachmentById`, `ReadItemCostCodeById`/`ReadItemSubCostCodeById` + their connector wrapper methods, `ReadQboPurchaseLinesNeedingUpdate` (purchase family — also blocks the Attachable column-drop, see §6), 3-4 orphaned `[qbo]`-schema-qualified duplicate sprocs in the vendorcredit family (unreachable by the app's `EXEC dbo.*` convention, same bug class U-225 partially fixed for the table declaration but missed for the sproc). None of these need to wait for a Phase-4 migration window.

---

## 9. U-274 resolved — invoice-side line-matching re-homed off qbo.* (2026-08-19)

The cross-family blocker §4 and §7 both pointed at — `ProposeInvoiceSourceLinks` reaching into
`qbo.{Bill,Purchase,VendorCredit}Line` for line-fingerprint matching — is closed. `entities/invoice/sql/dbo.invoice.sql`:
Bill's, Purchase's, and VendorCredit's fingerprint tiers now all match directly against
`dbo.BillLineItem`/`dbo.Bill`, `dbo.ExpenseLineItem`/`dbo.Expense`, and `dbo.BillCreditLineItem`/`dbo.BillCredit`
(Amount/Description/ServiceDate, `DirectDbo=1`) — the same dbo-native shape Bill's own fallback tier already used
since U-177. The three prior `qbo.*Line`-staged arms (each `CustomerRefValue`-scoped) and the 3-clause `NOT EXISTS`
"staging-preferred" guard they fed are removed; nothing is left to defer to. Tier 0 (LinkedTxn exact-identity
match) is untouched at the time of this note — it's QBO-string identity, not fingerprint matching.

**Correction (U-301c, 2026-08-22):** the "family-level dbo-native line identity" framing above was wrong. Tier
0c/0d never needed line identity at all — `LinkedTxnId` only ever names the *parent* Bill/Purchase transaction,
never a specific line, so there's no per-line QBO id for a line-identity fastpath (U-293/U-293b) to resolve
against. The actual (and much older) enabler was `dbo.Bill`/`dbo.Expense`'s own header `QboId`/`RealmId`
(U-238a — the columns both tables' Tier-0 join depends on; the read-function units that later consumed them,
U-283 for Bill and U-283b for Expense, are a separate citation, not this SQL's dependency) plus an amount
fingerprint against that header's own dbo lines, gated on the line's own `QboId IS NOT NULL` (U-293/U-293b line
identity — see the fix note below) — the same general shape as the Tier 1-3 fix above, just anchored on an exact
`LinkedTxnId` header match instead of a header-level fingerprint. U-301c repointed both tiers onto that shape;
`qbo.Bill`/`qbo.BillLine`/`qbo.BillLineItemBillLine` and `qbo.Purchase`/`qbo.PurchaseLine`/
`qbo.PurchaseLineExpenseLineItem` are no longer read by this sproc at all. This same misattribution was also
carried into `docs/staging_removal_phase6_readiness.md` (§0 point 5, the `### bill`/`### purchase` sections, and
Wave 2) — corrected there too.

**U-301c fix, same day (adversarial review caught this live on prod data):** the first repoint fingerprinted
against *every* dbo line under the identified Bill/Expense, with no gate on whether that specific line had ever
been individually synced to QBO. The old `qbo.BillLineItemBillLine`/`qbo.PurchaseLineExpenseLineItem` mapping
tables are strictly 1:1, so the old join could only ever surface an individually-mapped line — verified live on
Bill.Id=16897 (QboId `65749`): BillLineItem 21553 (individually mapped) and sibling 20210 (never mapped) share
Amount 25676.76; the old join returns exactly 1 row, the un-gated new join returned 2, turning a clean match into
a false "ambiguous". Fix: require `dbli.[QboId]`/`deli.[QboId] IS NOT NULL`, restoring the same "was this line
actually synced" precision fully dbo-natively (no `qbo.*` reach). Mutation-proven: removing the guard reproduces
the exact 2-row regression (`scripts/verify_propose_invoice_source_links_tier0.py`); a static text guard
(`tests/test_propose_invoice_source_links_no_staging.py`) now runs in the standard pytest suite so an accidental
revert of either the `qbo.*` staging read or this gate fails loudly.

**Residual, accepted (not fixed — no dbo equivalent exists to fix it with):** dbo.BillLineItem/dbo.ExpenseLineItem
carry no `LineNum` column, so `SourceLineNum` is `NULL` for Tier 0 (same as Tiers 1-3 always were). Two
individually-mapped sibling lines sharing the same amount can still both match Tier 0 — the `QboId IS NOT NULL`
gate narrows the population back to legitimately-synced lines, it doesn't disambiguate between two of them.
`resolve_link_proposals`' one positional auto-apply tie-break (index-for-index pairing when an ambiguity group's
line-count exactly equals its source-count) then falls back to `dbo.BillLineItem`/`dbo.ExpenseLineItem` `Id`
order — an insertion-order proxy for true QBO line order, not a guarantee of it. Zero live rows exercise this
today (no `LinkedTxnType='Bill'`/`'Purchase'` provenance rows exist), and it fails toward the safe outcome
(`status="ambiguous"`) far more often than not (this exact shape is why the module docstring already documents
same-fingerprint multi-line invoices as this file's dominant known ambiguity case) — but a future engineer
capturing QBO's `LinkedTxn.TxnLineId` into `InvoiceLineItemSourceProvenance` (not currently ingested — see
`integrations/intuit/qbo/invoice/business/service.py`'s pull path, which discards it — though the field exists in
the schema and is already used on this connector's push side) would close this for real without touching Tier 0's
shape again.

**Accepted tradeoff, verified empirically against live prod data (80-invoice real sample, 2026-08-19):** the new
tiers carry no `CustomerRefValue`/project narrowing.
- **0.5% precision loss** (15 of 2940 old-sproc candidate rows) — every one traced individually; 100% caused by a
  human-edited `Description` post-QBO-sync (e.g. "Topsoil" → "Topsoil/Dumptruck delivered 6/18"), never an amount
  mismatch. The old qbo-staged fingerprint matched against the QBO-pull-time snapshot; the new one matches against
  the current (possibly since-edited) dbo value.
- **Cross-project false-positive noise** on recurring flat-rate line items — e.g. "Portable Toilet" $130.00 on the
  same date recurs across 4-5 unrelated projects, and without project scoping the new tier proposes all of them as
  candidates. Spot-checked 10/10 gained-Purchase-candidate cases in the sample were exactly this pattern. **Not a
  live risk**: the existing, untouched `_apply_cross_project_guard` (KI-37) in
  `entities/invoice/business/reconciliation.py` rejects any candidate whose `SourceProjectId` disagrees with the
  invoice's own project before it can reach a human as a suggested link — the practical effect is
  `status="cross_project_rejected"` or added ambiguity, never a wrong auto-link.
- Chris's explicit Gate-1 call (2026-08-19): ship unscoped, exact parity with Bill's existing tier, rather than
  adding `@ProjectId` narrowing to cut the noise at the SQL layer — the KI-37 guard was judged sufficient.

**Net coverage change:** Purchase and VendorCredit gain fallback coverage for UNMAPPED rows they never had before
(only Bill had a `DirectDbo` fallback pre-U-274, so unmapped Purchases/VendorCredits were simply invisible to this
sproc); previously QBO-mapped rows across all three families now match on a narrower (no-CustomerRef) fingerprint.

**Gate-2 addendum (2026-08-19) — project scope RESTORED, reversing the ship-unscoped call.** An independent
equivalence run (old prod sproc vs the new one over 25–80 real invoices, candidate-set diff) sized the "narrower
fingerprint" effect the ship-unscoped decision accepted: dropping the old tiers' `CustomerRefValue` scope produced
**66 gained cross-project candidates per 25 invoices**, all leaning on KI-37 as the *primary* filter (not a
backstop) — plus candidate-set ambiguity KI-37's reject-status doesn't remove. That load and the parity gap were
judged too large. The three dbo tiers now carry `AND (<line>.[ProjectId] = @ProjectId OR <line>.[ProjectId] IS NULL)`
— the dbo-native equivalent of the old `CustomerRefValue = @CustomerRefValue` scope, NULL-permissive so a
no-project source still matches. Re-verified: cross-project gains **66 → 8** (the residual 8 are NULL-project
sources, still KI-37-backstopped); same-project gains (4, unmapped-row coverage — spot-check as correct-vs-coincidental)
and description-drift losses (5) unchanged. KI-37 is back to being defense-in-depth, not the sole guard.

No new dbo mirror table or backfill was needed — unlike U-272, Purchase's and VendorCredit's `DirectDbo` targets
(`dbo.ExpenseLineItem`/`dbo.Expense`, `dbo.BillCreditLineItem`/`dbo.BillCredit`) were already live, populated entity
tables with the needed columns. Deploy is a single `CREATE OR ALTER PROCEDURE` apply, no ordering trap.

Consequence for §8 item 11: `bill`'s and `vendorcredit`'s (§7) invoice-side line-matching coupling is resolved —
their own family repoints (U-278 vendorcredit header/ref already ready, U-283 bill/purchase) are no longer blocked
by this sproc. `purchase`'s family repoint is still separately gated on the expense-coding-cockpit raw-field
disposition (§2's `purchase` row) — unrelated to this unit, which only stopped `ProposeInvoiceSourceLinks` from
reading `qbo.Purchase*`, not retired the tables themselves.

---

## 10. U-276 resolved — `customer` family Phase-4 repoint, the pilot pattern (2026-08-19)

Built as scoped: `CustomerCustomer` (self-contained pilot) and `CustomerProject` (wider fan-out) both
repointed, per Chris's tight-scope call at Gate-1 — fix the doc-flagged `DisplayName` cross-family read plus
each connector's own identity resolution; explicitly defer the 4 pull-resolvers (they don't read
`DisplayName`, so out of the doc-flagged scope) to their owning families' own units.

**The repoint pattern (template for U-277–284):**
1. **Expose native identity on the primary read path.** `ReadCustomerById`/`ReadProjectById` gained
   `QboId`/`RealmId` to their SELECT lists (columns already live since U-238a/c — purely additive, no schema
   change); the dataclasses and repo `_from_db` mappers gained matching `qbo_id`/`realm_id` fields
   (`getattr(row, "QboId", None)`-guarded, since most other Read* sprocs for the same entity don't select
   them).
2. **Add a reverse-identity lookup sproc + repo/service method.** New `Read{Entity}ByQboIdAndRealmId`
   sproc (RealmId NULL-safety mirrors `Set{Entity}QboIdentity`'s own theft-detection comparison — `([RealmId]
   = @RealmId) OR ([RealmId] IS NULL AND @RealmId IS NULL)`), a repo `read_by_qbo_identity()`, and a service
   passthrough (threading RBAC actor scope where the entity has row-level RBAC — Project does, Customer
   doesn't).
3. **Connector fast path: check-then-write, not write-then-check.** The connector's `sync_from_qbo_*` tries
   `{entity}_service.read_by_qbo_identity(...)` FIRST. If it hits, the mapping-table state is resolved via a
   `_resolve_mapping_state()` helper — checking **both** directions (by-local-id and by-external-id, mirroring
   `create_mapping`'s own existing 1:1 guards) — **before any field is written**, returning one of
   `consistent` / `missing` / `conflict`. Only `consistent`/`missing` proceed to write; `conflict` records a
   reconciliation issue (drift type registered in `drift_types.py`, message naming every conflicting row so a
   two-directions-at-once "crossed" conflict doesn't silently drop either side) and falls through unchanged
   to the pre-existing mapping-table-based path below it, which stays authoritative when the two identity
   sources disagree. **Getting this ordering right took 3 review rounds** (Codex `xhigh`) — write-then-check
   was the actual defect the first two rounds' fixes still carried: detecting a conflict *after* already
   writing the dbo-identity-matched row corrupts that row's data in the (rare but real) case the mapping
   table, not dbo identity, was still the correct side. Any repoint that skips a staging→mapping-table hop in
   favor of dbo-native identity needs this same check-before-write discipline, not just conflict detection.
4. **Push helpers read dbo-native, but VERIFY before trusting it externally.** `_get_qbo_customer_ref` in
   Bill/Purchase/Invoice reads `dbo.Project.Name`/`.QboId` directly via the entity's own `read_by_id` (step 1
   already exposed the identity on that path) — but round 4 found dbo-internal uniqueness
   (`Set{Entity}QboIdentity`'s theft-detection) does NOT by itself guarantee the mapping table has caught up
   to the latest holder, and an outbound push blindly trusting a stale/"stolen" `QboId` would misroute a live
   Bill/Expense/Invoice to the wrong QBO customer's books. Fix: a small shared helper
   (`integrations/intuit/qbo/base/identity_consistency.py::verify_project_qbo_identity`) checks the Project's
   own mapping row (if any) agrees before the push helper trusts `.qbo_id` — trusts when there's no mapping
   row yet (the ordinary not-fully-migrated state) or when it agrees, refuses (returns `None`, same "can't
   resolve" contract the caller already handles) on disagreement. This is a **deliberate, documented residual
   read** of `qbo.CustomerProject`/`qbo.Customer` on every push — narrower than the pre-repoint reach (identity
   only, no `DisplayName`) but not fully eliminated; revisit when Phase 6 needs `qbo.Customer` gone entirely.

**Deferred, booked as prerequisites (§8 items 4/11/12):** the 4 `_get_project_public_id` pull resolvers
(`bill_line_item`, `vendorcredit`'s line connector, `purchase`'s line connector, `invoice`) still resolve an
inbound QBO CustomerRef by hopping `qbo.Customer → qbo.CustomerProject` — not blocking this unit (they never
read `DisplayName`, so outside the doc's flagged scope), but `qbo.Customer` can't actually be **dropped**
(Phase 6) until they're repointed too. Same pattern as step 3 above, minus the write side (pull resolvers
only ever read); each owning family's own U-277–284 unit should do this as part of its own repoint, not as a
separate pass.

**Verification:** full pytest green throughout (2322, up from 2285 baseline); mutation-proved RED→GREEN in
an isolated worktree at every fix round (round-1's swallowed-conflict, round-3's write-then-check ordering,
round-4's push-side blind-trust) — each reverted mutation caught by exactly the test(s) meant to catch it,
nothing else regressing. Codex `xhigh` **4 rounds**, each finding something real: round 1 (P1, self-heal only
checked one mapping direction and silently swallowed the resulting constraint violation), round 2 (P1/P2, the
round-1 fix's own reconciliation call reused the wrong helper / didn't cover the local-side conflict at all),
round 3 (P1, the conflict check ran *after* the dbo row was already written — could corrupt the wrong
Project/Customer before anyone was told), round 4 (P1, the *push* helpers this unit also repointed had no
analogous conflict check at all — financial misrouting risk; P2, a self-heal `create()` failure was logged as
a bare warning instead of being re-checked and recorded when it's actually a race-induced conflict). Round 4's
two fixes are self-verified (mutation-proved, full suite green) but not yet re-reviewed by Codex — 4 rounds
already exceeds the standing 2-round cap, so per that guidance this is the point to switch fully to
self-verification rather than loop further; flagging this explicitly rather than silently presenting a 5th
"Codex PASS" that didn't happen. No SQL applied to prod — new sprocs verified via temp-named copies against
real Customer/Project rows + a real mutation test (NULL-safe RealmId scoping, RBAC EXISTS guard) then dropped;
base==live confirmed for every touched existing sproc (found + fixed one pre-existing drift along the way:
`CreateCustomer`'s base-file copy was missing the already-live `@CreatedByUserId` param — reconciled as a
precondition, not a feature change; **Chris's own Gate-2 SQL-apply step should still re-diff `sys.sql_modules`
at apply-time**, standard practice for any base file with documented prod-drift history, not special to this
unit). **`/simplify` pass (self-run, behavior-preserving):** `_resolve_mapping_state` was unconditionally
reading the mapping table from both directions even on the common "consistent" path — since `QboCustomerId`
is unique on the mapping table, a matching local-side row IS provably the same row the external-side lookup
would return, so the second read is skipped on that path now (one fewer round trip on the steady-state case
this whole unit exists to make cheap). Full suite re-confirmed green after.

---

## 11. U-280 resolved — `reimburse_charge` dead identity columns retired (2026-08-19)

The narrow slice of §5/§8-item-8 — drop `SourceTxnType`/`SourceTxnId`/`SourceTxnLineId` — is done, independent of
the broader keep-vs-retire call. Live re-verified immediately before building: **26,645/26,645 rows 100% NULL**
on all three columns (up from the 26,582 measured for §5 — table keeps growing via the hourly pull, same result);
repo-wide grep + manual read confirmed **zero live readers** anywhere outside this package's own internal
preserve-on-repull merge loop, which existed only to defend columns QBO has never once populated. One
near-miss ruled out: `invoice/connector/invoice/business/service.py::_build_reimburse_charge_lookup` has a
same-named local `source_txn_id`, but it's sourced from a live QBO API call at invoice-push time, unrelated to
this table.

**Built:** dropped the 3 columns + their index (`ALTER TABLE`/`DROP INDEX`, guarded + idempotent, index-before-column
ordering) and stripped the read/write/preserve-on-repull handling from the dataclass, parse module (the whole
`merge_reimburse_charge` function goes away — nothing left to preserve), repo, and service. `CreateQboReimburseCharge`/
`ReadQboReimburseChargeByQboIdAndRealmId`/`ReadQboReimburseChargesByRealmId`/`UpdateQboReimburseChargeByQboId` all
shrink to the remaining 7 fields. Codex was confirmed out-of-credits this session (ladder followed: retried at
`high`, then on `gpt-5.4`, both failed identically) — fell back to a Workflow-driven 4-lens hunt (SQL migration
safety / Python signature consistency / dead-code-removal completeness / test adequacy) with adversarial
verification; all 4 PASS, 2 non-blocking P3s confirmed real but pre-existing/out-of-scope. `/simplify` 4-lens:
1 real altitude finding (the analysis script's `SOURCE_TXN_TYPES` constant was inlined instead of living in
`fingerprint.py`, its documented sanctioned home) — fixed; reuse/simplification/efficiency clean, efficiency lens
flagged a genuine positive effect (dropping the dead index removes real write-amplification on every future
upsert). New `tests/test_reimburse_charge_service.py` covers `_upsert`'s create/update kwarg wiring against the
repo's real signatures via `create_autospec` (a plain `Mock()` would silently swallow the exact signature-drift
this test exists to catch) — mutation-proven RED→GREEN in an isolated worktree. `qbo.reimburse_charge.sql`'s
README refreshed (a hunt finding — it still described the retired columns as live). base==live confirmed for
3/4 touched sprocs exact match pre-edit; `UpdateQboReimburseChargeByQboId` carried comment-only prod drift
(no param/logic difference, irrelevant since this unit rewrites that whole section). Full pytest green (2365).
⛔ **SQL NOT applied** — deploy owed (index drop + 3-column drop + 4 sproc `CREATE OR ALTER`).

**Still open, unresolved by this unit:** the broader §5 question — whether the whole 26K-row `qbo.ReimburseCharge`
mirror is worth keeping now that its intended Tier-0 consumer is gone — remains its own future disposition call.

---

## 12. U-277 resolved — `company_info` + `physical_address` Phase-4 repoint (2026-08-19)

Built as scoped: both families repointed onto `dbo.Company`/`dbo.Address` native `QboId`/`RealmId` (U-238a/c),
mirroring §10's pilot pattern exactly (`ReadXByQboIdAndRealmId` sproc, repo `read_by_qbo_identity()`,
check-before-write mapping guard, conflict→defer-to-mapping). Cross-family-reader audit (§8 item 6's own mandate)
confirmed `physical_address`'s 3 producers — company_info's own driver (`scripts/sync_qbo_company_info.py`),
`customer`'s `CustomerProjectConnector` (Bill/ShipAddr), `vendor`'s `VendorVendorConnector` (BillAddr) — all call
the **same** `PhysicalAddressAddressConnector.sync_from_qbo_to_address(qbo_physical_address_id)` method with an
unchanged signature, so repointing its internals covers all 3 without touching any producer's own code and without
booking a new deferred prerequisite (unlike §10's 4 pull-resolvers, which had to be booked onto their owning
families' own units). No outbound push anywhere reads `dbo.Company.QboId`/`dbo.Address.QboId` — confirmed by audit
— so §10 step 4 (push-side verify-before-trust) has no analog here.

Codex confirmed out-of-credits this session (ladder followed: retried once at `high`/`gpt-5.4`, failed identically —
same workspace-wide outage U-280 hit) — fell back to a Workflow-driven 5-lens adversarial hunt (QBO identity/mapping
logic, SQL sproc correctness, concurrency/cross-family blast radius, test adequacy, money/RBAC/lifecycle patterns)
with refute-by-default verification: 14 raw findings, 10 confirmed. **Fixed:** (1) P1 — the fast path's "never write
to a conflicted row" guarantee was defeated one hop later by the legacy fallback's own by-name/by-street-city
rediscovery re-finding the exact same row and overwriting it in Step 3 regardless of the mapping-repair skip; added
a `protected_company_id`/`protected_address_id` guard immediately before each connector's Step-3 write, mirrored
symmetrically to both connectors even though the hunt's own repro was Company-specific. (2) P1×2 — `dbo.company.sql`/
`dbo.address.sql`'s base `CREATE TABLE` blocks never declared `QboId`/`RealmId` (only added out-of-band via
238a/238c), so this diff's new column references in `ReadCompanyById`/`ReadAddressById`/the two new sprocs would
abort a from-scratch build at `CREATE PROCEDURE` time (SQL error 207); added the same idempotent `ALTER TABLE ADD`
+ `UNIQUE INDEX` blocks §10's `dbo.customer.sql` already carries for the identical migration family — verified
no-op-safe against live prod (columns AND unique indexes already exist there from 238a/238c). (3) P2 — fast-path
`update_by_id()` result was never None-checked unlike the adjacent legacy path's own guard in the same method
(ROWVERSION-race gap); added the matching guard to both connectors. (4) P2 — the "names both sides" conflict-message
tests used bare-digit substring assertions trivially satisfied by the always-emitted first sentence; strengthened to
phrase-level assertions. (5) P3 — service-layer passthrough tests never asserted the return value was propagated;
fixed. All 5 fixes mutation-proven RED→GREEN in an isolated worktree. **Investigated, not confirmed:** the hunt's
altitude lens flagged the already-shipped `CustomerProjectConnector` (§10/U-276) as sharing the same write-on-conflict
exposure — traced directly and refuted: Project's legacy fallback structurally differs (its rediscovery-with-existing-mapping
branch raises rather than falling through to a write). `VendorCreditBillCreditConnector` (U-278, concurrent/uncommitted
this session) was flagged as unverified either way — not checked further, out of this unit's file scope while that
session had it in flight. **Deferred, not fixed (documented, not blocking):** a P2 finding that the fast path's QboId
lookup isn't collision-safe across the Customer/Vendor address namespaces sharing this connector (composite
`f"{qbo_id}_bill"`-style keys can coincide across entity types) — the existing conflict guard prevents data
corruption, but `SetAddressQboIdentity`'s identity-steal path only `logger.warning`s, no `ReconciliationIssue`; fixing
observability would mean changing `Set*QboIdentity`'s return contract, a bigger change than this unit's scope,
shared by every `Set*QboIdentity` caller codebase-wide — own follow-up unit. `/simplify` 4-lens: reuse lens caught 2
new drift-type constants defined but not imported at their own call sites (fixed); a `state`/`state_` naming
collision in the physical_address connector, from copy-pasting into a scope where `state` was already the
Address.state field (renamed to `mapping_state` in both connectors for consistency); the now-5-way duplication of
`_resolve_mapping_state`/`_raise_identity_mapping_conflict_issue` across company_info/physical_address/customer/
project/vendorcredit flagged by 2 of 4 lenses as ripe for extraction into `integrations/intuit/qbo/base/` — deferred
as its own follow-up (would touch 3 already-shipped/concurrent files well outside this diff); a proposed
`_apply_qbo_fields` extraction for the 2–5 line field-assignment block duplicated once per connector was
considered and skipped (too small to be worth the indirection). Full pytest green throughout (2396, up from 2365
baseline after U-280); 31 new tests in `tests/test_u277_company_address_qbo_identity_repoint.py`. No SQL applied to
prod — new sprocs + the idempotent ALTER TABLE/index guards verified via temp-named copies and read-only schema
checks against real Company/Address rows, never touching live objects; base==live confirmed for both existing
sprocs before editing.

**Commit-hygiene note:** `integrations/intuit/qbo/base/drift_types.py` and `tests/test_qbo_identity_reference.py`
are shared files this unit legitimately touches (2 new constants; 1 existing test fixed) that are ALSO
mid-edit, uncommitted, in the same working tree by the concurrent U-278 (`vendorcredit`) session — both files show
git `MM` status (U-278's edits already staged, this unit's edits layered unstaged on top). A plain pathspec-commit
of these 2 paths would pull in U-278's uncommitted work ahead of its own review. Recommend sequencing: let U-278
commit first, then this unit's diff on these 2 files reduces to just its own 2-constant/1-fixture addition for a
clean pathspec-commit.

**Still open, unresolved by this unit:** the deferred cross-namespace collision observability gap above; the
5-way `_resolve_mapping_state` duplication (own follow-up, generalize once all in-flight repoints — U-277, U-278 —
have landed, so it doesn't touch a moving target). — _`_resolve_mapping_state` duplication RESOLVED by U-287, §13._

---

## 13. U-287 resolved — the fast-path pattern now has ONE home (2026-08-20)

The duplication §10/§12 kept booking is closed. `integrations/intuit/qbo/base/identity_fastpath.py` is now the
single implementation of the §10-step-3 recipe, and all six copies (customer/customer, customer/project,
company_info, physical_address, vendorcredit/bill_credit, attachable/attachment) are deleted. Net −602/+446.

**The interface** — two functions:
- `resolve_mapping_state(*, local_id, external_id, read_by_local_id, read_by_external_id, external_id_attr)` —
  pure; owns the both-directions algorithm and the deferred second read (§10's own `/simplify` optimisation).
- `run_identity_fastpath(...) -> FastPathOutcome(hit, entity)` — owns the **control flow**: the falsy-`qbo_id`
  short circuit, check-before-write ordering, the conflict hard stop, and the self-heal create-race re-check.

Each family passes only what is genuinely family-specific, as callbacks: `apply_fields` (the field write),
`create_mapping` (the mapping-row kwargs), `record_conflict_issue` (its own
`_raise_identity_mapping_conflict_issue`, whose message names different downstream readers per family — kept
per-connector on purpose), and `conflict_message`. `attachable` passes **no** `apply_fields`: it uses the helper
for identity resolution only and continues to its shared blob-handling block, which is why the helper returns an
outcome rather than assuming the caller returns.

**Why this is the load-bearing change, not a tidy-up:** `conflict → RAISE` is now structural. There is no
parameter to opt out, so a future repoint cannot reintroduce the fall-through by copying a stale template — which
is exactly how the 2026-08-20 live-prod P0 happened (§10 shipped the fall-through; U-278's review of the mirrored
vendorcredit unit caught it; the hotfix then had to patch two copies by hand).

**It also closed that same P0 in two families the hotfix never reached.** `company_info` and `physical_address`
(§12, shipped 2026-08-19 — one day BEFORE the hotfix) still fell through on conflict, mitigated only by the
`protected_{company,address}_id` guard. That guard covers exactly one shape: the legacy path re-resolving to the
SAME row. It left open (a) **different-row identity theft** — most directly in `physical_address`, where a
qbo-side conflict falls through, updates the other Address, and the trailing `set_qbo_identity` stamp fires on it,
and `SetAddressQboIdentity`'s theft-clear UPDATE then NULLs the original's identity; and (b) **duplicate mint +
theft** in either family when the by-name / by-street-city rediscovery MISSES (a renamed row, a changed QBO
legal_name). Both now raise; the guards were provably dead afterward and were deleted. Four of their tests
asserted the fall-through and were deliberately rewritten to demand the raise — mirroring how the U-276 hotfix
rewrote its own. Every caller of both connectors already isolates per-record (`try/except Exception` →
`logger.error` or `outcome.record_projection_error`), so the raise skips one record, never a run.

**Verification.** 2442 pytest green. The equivalence proof is that the **U-276 / U-278 / U-279 suites pass
completely UNCHANGED** — only the 4 deliberate `company_info`/`physical_address` conflict tests moved. Mutation
matrix **12/12 caught** in an isolated git worktree (hermetic, `__pycache__` cleared per run, green before and
after): conflict-never-raises, conflict-never-recorded, conflict-branch-keyed-on-MISSING, missing-needs-only-one-
side, any-by_local-counts-as-consistent, mapping-keyed-on-pre-apply-row, race-never-escalates, no-None-guard,
write-then-check (the §10 round-3 ordering bug), conflict-swallowed-as-a-miss, plus the two regression mutants
below. Codex was out of credits this session (ladder followed: retried at `high` on `gpt-5.4`, failed
identically) — Pass 1 ran as a 5-lens Workflow hunt with refute-by-default verification: 9 raw findings,
**7 refuted, 1 confirmed** (found independently by 2 lenses).

**The confirmed finding, fixed:** `customer/customer` was the only family whose `apply_fields` could return
`None` with no handler. Pre-U-287 that path crashed with `AttributeError` (no `None` guard, and the except
handler's own f-string re-raised), which `record_projection_error` rule 3 sends to failure/**HOLD** — so the
record self-healed next tick. The extraction's uniform `None` guard turned it into a silent `return None`, and
`project_records` counts a `None` return as a projected **SUCCESS**, advancing the watermark past a Customer whose
fields were never written and whose mapping row was never created. Fixed with an `on_apply_returned_none` that
raises **`RuntimeError` — deliberately not `ValueError`**, because rule 2 classifies a plain `ValueError` as a
permanent skip, which would advance the watermark too. (Both verifiers proposed `ValueError` "to match
company_info/physical_address"; tracing the classifier showed that would not have restored the old behavior. The
two U-277 families carry that same mis-classification as a **pre-existing** issue — booked in `TODO.md`, not
fixed here.)

**Still open:** the two `TODO.md` follow-ups — converging `company_info`/`physical_address` onto hold-not-skip for
the ROWVERSION race, and deciding whether the six now-production-dead `_resolve_mapping_state` wrappers stay as
tested seams or get deleted with their tests repointed at the shared function.

**For the queued repoints (§8: account, term/vendor/item, bill/purchase, invoice):** call the helper. Do not
re-copy the recipe. If a family does not fit the interface, extend the helper deliberately rather than forking it
— forking is the failure mode this section exists to prevent.

---

## 14. U-290 resolved — `vendor` family Phase-4 header/reference repoint (2026-08-20)

Built as scoped: `VendorVendorConnector.sync_from_qbo_vendor` now resolves identity directly against
`dbo.Vendor.QboId`/`.RealmId` (native since U-238a/c) via `run_identity_fastpath()` (U-287), falling back to the
pre-existing `qbo.VendorVendor` mapping-table path unchanged on a miss. **Renumbered from the assignment's stale
`U-289`** — that ID was claimed same-session by a concurrent `/em` session's `item` (CostCode/SubCostCode) repoint,
which was already at Gate-1/Building when this unit's own Gate-1 Step 0 ran; same ID-collision class §0 and the
`item` unit's own board row already hit. Gate-1 Step 0 re-verified live: `VendorVendorConnector` still resolved
identity purely via `mapping_repo.read_by_qbo_vendor_id`, no dbo-native fast path existed — unit confirmed open.

**Built**, mirroring §10's pilot pattern: new `ReadVendorByQboIdAndRealmId` sproc (NULL-permissive RealmId) +
`VendorRepository`/`VendorService.read_by_qbo_identity()` (bare passthrough — Vendor has no row-level RBAC) + the
connector's `run_identity_fastpath()` wiring, conflict→RAISE structural from day one (no hand-rolled copy, calls
the shared helper directly, per §13's closing instruction). **Base-file gap found and fixed** (the same
from-scratch-build trap §12/§13 found for their own entities): `dbo.vendor.sql`'s base `CREATE TABLE` never
declared `QboId`/`RealmId`/the unique index, even though `SetVendorQboIdentity` (already in the file) silently
depended on them — added the matching idempotent `ALTER TABLE ADD`/index guard block, confirmed no-op-safe against
live prod (columns + `UQ_Vendor_QboId_RealmId` already exist there via `238c_qbo_identity_reference.sql`).

**QboActive refresh deviates from §13's own precedent, deliberately.** U-282 (PaymentTerm) accepted a documented
staleness tradeoff — its QboActive mirror can go stale after a Vendor's first fast-path hit if later deactivated in
QBO, since only the legacy mapping-table path re-stamps Active. The concurrent `item`/`U-289` session fixed this
same gap for its own SubCostCode family (refresh Active on every fast-path hit via a QboId/RealmId-omitted
`set_qbo_identity` call, so only the Active `CASE WHEN` branch fires — no re-stamp, no theft-detection re-trigger).
Vendor adopts SubCostCode's fix rather than PaymentTerm's tradeoff, since it's strictly more correct for the
identical "family also carries an Active mirror" shape. **Follow-up, not fixed here:** this refresh block is now
duplicated in 2 places (SubCostCode, Vendor) — a candidate for an optional refresh-callback on the shared helper
once both repoints have landed; PaymentTerm's own staleness gap remains open too, its own separate follow-up.

**Cross-family fan-out audit** (mandatory per the assignment, verified by direct grep+read, not doc-trust):
confirms §2's claim exactly — Bill (`_get_vendor_public_id` pull, `_get_qbo_vendor_ref` push), Purchase/Expense
(`_get_vendor_public_id` pull), VendorCredit (`_get_vendor_public_id` pull), and the expense-coding cockpit
(`ExpenseCodingItemService._resolve_vendor_id`) all resolve vendor refs through `qbo.VendorVendor` and are
explicitly deferred, untouched by this unit — the whole point of the tight scope. Two additional raw-SQL script
consumers found, not named in §2 (`scripts/generate_payment_remittance.py`, `scripts/backfill_qbo_bills.py`) —
unaffected by this unit either way (tables aren't dropped), noted for the eventual Phase-6 drop list.

**Codex confirmed out-of-credits this unit** (ladder followed: `xhigh` then `high`/`gpt-5.4`, same workspace-wide
outage prior units this session hit) — fell back to a Claude Workflow 5-lens hunt (identity-conflict-safety, SQL
correctness, QboActive-refresh correctness, call-site blast-radius, test adequacy) with adversarial refute-by-default
verification (10 agents, each finding independently re-checked against the live diff/code, not trusted on the raw
report): 4 lenses PASS outright; the test-adequacy lens found 1 confirmed P1 (fast-path-hit tests reused the same
Mock for both the identity-lookup result and the update result, so a dropped/broken `apply_fields` wiring on the
steady-state path would ship with zero test failures anywhere) + 1 confirmed P2 (the `on_apply_returned_none`
RuntimeError branch was untested) + 2 confirmed P3s (a misleading test comment; an asymmetric missing assertion) —
all four fixed. The SQL-correctness lens independently confirmed the base-file no-op claim against
`238c_qbo_identity_reference.sql` byte-for-byte and flagged one P3 cosmetic (`[RealmId]` unqualified in the new
sproc's WHERE clause vs. every other `v.`-qualified reference) — fixed. Zero P0/P1 survived in the other four
lenses. **Mutation-proven in an isolated worktree**, 3 targeted mutations on this unit's own highest-risk wiring
(not re-proving the shared helper's own already-proven conflict-raise): breaking the QboActive-active threading,
swapping the `create_mapping` mapping's `qbo_vendor_id` argument for the wrong field, and corrupting the
`drift_type` string — all three confirmed RED, all three confirmed GREEN after revert.

**Verify:** full pytest suite green for every vendor-touching test (`test_u290_vendor_qbo_identity_repoint.py` new,
+`test_qbo_vendor_vendor_heal.py`/`test_u219_qbo_reference_pulls.py`/`test_u275_qbo_active_mirror.py` fixture
defaults fixed to keep exercising their legacy-path scope). A same-session full-suite run also shows 7 failures in
`test_qbo_zombie_rollback.py`/`test_u283_bill_qbo_identity_repoint.py`/`test_qbo_reconciliation_recorder.py`,
traced directly to concurrent `/em` sessions' in-flight, uncommitted work on `bill` (U-283) — zero Vendor
references in any of those files, confirmed unrelated. base==live confirmed for all 10 pre-existing `dbo.vendor.sql`
sprocs (normalized `OBJECT_DEFINITION` diff, `CREATE OR ALTER`→`CREATE`); new sproc verified via a temp-named copy
(`_TEMP290`/`_TEMP290b`, the second after the `v.`-qualify fix) against real Vendor rows, then dropped both times.
No `/docs` (web) refresh owed — backend integration-internals only, no API/feature-surface change.

**Shared-file commit-hygiene note** (this session hit an unusually busy shared tree — 4 concurrent `/em` sessions:
`item`/U-289, `account`/U-281, `bill`/U-283, and this unit): `integrations/intuit/qbo/base/drift_types.py`,
`tests/test_u219_qbo_reference_pulls.py`, and `tests/test_u275_qbo_active_mirror.py` each carry a second,
cleanly-separated hunk from the concurrent `item` session alongside this unit's own — confirmed via `git diff`
that the hunks never touch the same lines. Commit for this unit uses `git add -p`/targeted patch application to
stage only this unit's own hunks in those 3 files, leaving the `item` session's uncommitted work untouched and
committable on its own later.

---

## 15. U-285 in progress — Phase 5 `qbo.Attachable`/`qbo.AttachableAttachment`, push side only (2026-08-21)

Chris picked **phased retire, push first** (option (b) of a 2-option Gate-1 menu, not the §6 "3-option menu"
this doc originally posed for the *full* retire — that menu is superseded by this narrower, sequenced approach).

**Gate-1 map, re-verified against live prod** (not doc-trust): confirmed §6's claim that the push path
(`AttachableAttachmentConnector.sync_attachment_to_qbo`, called from `entities/bill/business/service.py::
_sync_attachments_to_qbo` on every Bill-to-QBO push) still creates a **new** `qbo.Attachable` row plus a
`qbo.AttachableAttachment` mapping row on every successful upload — live and actively written (most recent
row created the day of this unit). Live counts: `qbo.Attachable`=4,556 rows, `qbo.AttachableAttachment`=4,194
rows (2 orphaned, dangling FK — pre-existing, no-CASCADE, not touched by this unit), `dbo.Attachment`=29,108
rows (4,192 carrying `QboId`, 0 duplicate `(QboId,RealmId)` pairs — clean substrate). 362 `qbo.Attachable` rows
were never linked to any Attachment (dead pull-side staging noise, unaffected either way).

**Built:** the push path's fresh-upload branch now stamps `dbo.Attachment.QboId`/`RealmId` directly
(`_stamp_pushed_identity`) instead of creating a `qbo.Attachable` row + calling `_create_mapping` — no new
staging row, no new mapping row. The two pre-existing "already synced, skip" early-return checks (dbo-native
fast path, legacy mapping-table fallback) are preserved unchanged for backward compatibility with rows the
(untouched) pull side still writes, **plus a third fallthrough** added by this unit: when both legacy checks
miss but `dbo.Attachment.QboId` is already set (the normal shape for anything pushed by this unit's own new
code), trust it directly and skip re-uploading — without this, any retry (outbox re-drain after a mid-loop
`QboBudgetExceededError`, or the same Attachment shared across two Bills) would have silently re-uploaded a
duplicate to QBO and only discovered it afterward via the race-guard, mis-recorded as a "concurrent mapping
race" instead of a deterministic re-push. (This was caught by the adversarial hunt below, not by initial
review — see the confirmed-findings note.)

**Explicitly deferred to a follow-up unit** (do not re-derive — already scoped): the **pull side**
(`QboAttachableService`, `scripts/sync_qbo_bill.py`/`sync_qbo_vendorcredit.py`/`sync_qbo_purchase.py`,
`scripts/backfill_qbo_bills.py`) still creates/updates `qbo.Attachable` rows on every pull — untouched,
deliberately. The 3 read-only line-item-linking call sites (§6's list) still resolve dbo-native identity first,
falling back to the mapping table on a miss (per U-279) — also untouched, and unaffected by this unit (the
push-side change doesn't remove any row those call sites currently depend on; freshly-pushed and pulled
attachments are non-overlapping populations). `entities/invoice/intelligence/prompt.md`'s manual recovery
playbook (§6's "newly found" dependency) still documents a `qbo.Attachable`/`qbo.AttachableAttachment` recovery
step that silently stops applying to attachments pushed via this unit's new code path (no staging row exists
for it to find) — that file is foreign in-flight WIP from a concurrent session and was correctly left
untouched; **owed as a follow-up for whoever owns that file**, not fixed here.

**Hunt:** Codex confirmed out-of-credits (same workspace-wide outage this whole migration has hit) — fell back
to a Claude Workflow 5-lens adversarial hunt (identity-conflict-safety, call-site-contract, test-adequacy,
legacy-interaction, scope-hygiene) with 2-skeptic refute-by-default verification: 9 raw findings, 4 confirmed,
5 refuted. **1 confirmed P1, fixed** (the idempotency-fallthrough gap above — mutation-proven RED→GREEN in an
isolated worktree: reverting just the fallthrough branch reproduces the exact silent-re-upload the finding
predicted). **3 confirmed P3s**: this doc's own stale §6 sentence (fixed, this section); the InvoiceAgent
playbook dependency above (deferred, file off-limits this session); a test-coverage completeness gap on the
happy-path test's return-object assertions (fixed — strengthened to cover sync_token/content_type/entity_ref_*/
public_id/row_version). Full pytest suite green (2,647 including the new regression test). No SQL, no schema
change, no `/docs` (web) refresh owed — backend integration-internals only.

⛔ **Not yet shipped** — held at Gate-2 pending Chris's commit+push decision. No deploy owed (pure Python,
no SQL). Pull-side repoint + the eventual `DROP TABLE` for both `qbo.Attachable`/`qbo.AttachableAttachment` is
the follow-up unit, sequenced whenever the pull side's own soak/verification is ready — not blocked by
anything in this unit.

---

## Appendix — infra tables, explicitly untouched

Per the assignment's own instruction, no disposition proposed for `qbo.Auth`, `qbo.Client`, `qbo.Outbox`, `qbo.ReconciliationIssue`, `qbo.ApiUsage` — these are program infra, not staging, and survive regardless of Phase 4/5/6 outcome.
