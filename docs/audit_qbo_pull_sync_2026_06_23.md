# QBO Pull-Sync Audit Report

## Executive Summary

This audit closed out seven concerns against the QBO pull/sync path. After adversarial verification, the standing severity distribution is:

- **P0: 0**
- **P1: 3** — Invoice 1057 SubCostCode back-fill gap; cross-entity (Invoice-keyed) attachables never linked; non-transactional/non-idempotent InvoiceInvoice projection.
- **P2: 4** — three of which are confirmed **non-issues / stale** (Purchase↔VendorCredit SCC parity, VendorCredit live duplication, both connector Known Issues KI#15/KI#21) and one true-but-benign data shape (Bill header `(vendor, billnumber)` duplicates that are legitimately distinct recurring QBO bills).

**Fix first (in order):**

1. **Non-transactional/non-idempotent InvoiceInvoice projection (P1, code bug).** This is the only finding that is actively *minting bad customer-facing artifacts* — 38 phantom `-N` duplicate invoices, several over $200K, with cannibalized line items. It is a live code defect that re-fires on every re-run, so it has the worst trajectory.
2. **Cross-entity attachables never linked (P1, code bug + operational gap).** Source Bill/Expense/BillCredit lines silently ship doc-less because 1,448 Invoice-keyed attachables are structurally invisible to the per-entity pull. Directly degrades invoice packets and reconcile.
3. **Invoice 1057 SubCostCode back-fill (P1, both).** Blanks in packet TOC / Excel reconcile for agent-authored draft lines whose out-of-band mappings skipped SCC back-fill. Smaller blast radius than the first two but customer-facing.

The connector translation logic itself (ItemRef→SubCostCodeId) is **correct on every path** — none of the fixes touch it. Per constraints, no IsDraft/push changes, the attachment-required rule is preserved, and no agent-layer changes are proposed.

---

## Finding 1 — Invoice 1057 source bill lines have NULL SubCostCodeId from an out-of-band mapping path, not a connector translation gap

**Finding.** Invoice 1057 (`HA-04`) has 8 source BillLineItems with NULL `SubCostCodeId`, producing blank sub-cost-code / cost-code columns in the packet TOC and Excel reconcile. The cause is not a connector translation defect — the connector resolves SCC correctly — but agent-authored draft lines whose QBO line mappings were minted out-of-band and never SCC-back-filled.

**Root cause.** Design-level, two-part.
1. The connector translation is **correct and present on both paths**: `integrations/intuit/qbo/bill/connector/bill_line_item/business/service.py:107-120` resolves `qbo.BillLine.ItemRefValue → qbo.Item → qbo.ItemSubCostCode.SubCostCodeId` and passes it into both `update_by_public_id` (L186) and `create` (L210). The update sproc is guarded (`entities/bill_line_item/sql/dbo.bill_line_item.sql:272`, `CASE WHEN @SubCostCodeId IS NULL THEN [SubCostCodeId] ELSE @SubCostCodeId END`), so it only sets, never nulls.
2. The actual gap: the 8 BLIs were created 2026-05-06/05-08 by the email/bill_specialist pipeline (parent Bills carry `SourceEmailMessageId`, `IsDraft=True`, agent-style 6-word summaries) **without** a `SubCostCodeId`, and the connector create/update path never ran on them (`CreatedDatetime == ModifiedDatetime`). Their `qbo.BillLineItemBillLine` mappings were created later, on 2026-06-23, by a direct-SQL mapping-repair path that calls `mapping_repo.create()` only — bypassing `sync_from_qbo_bill_line`, so the SCC the connector would have resolved was never written back. **Verification correction:** the responsible script is `scripts/reconcile_project.py:382-435` (matches each unmapped BLI to its QboBillLine by description+amount, then calls `bill_line_item_bill_line_repo.create(...)` at L432 — mapping only, no SCC, no BLI update), **not** the `fix_duplicate_bill_line_items.py` family cited in the original draft (those only DELETE/re-point and reference the mapping table read-only). The clustered 2026-06-23 20:23 `CreatedDatetime`s are consistent with a single `reconcile_project.py` run.

**Severity.** P1 — customer-facing invoicing error (blank SCC/cost-code in packet TOC and Excel reconcile), bounded to lines that received out-of-band mappings.

**Classification.** Both — operational (the out-of-band mappings already minted leave a one-time data hole) and code (the direct mapping-creation path lacks SCC back-fill, and the upstream draft-create flow never populates SCC).

**Fix shape.** Leave the connector translation untouched. Apply three complementary fixes. Operationally, run a one-time back-fill that, for every BillLineItem with NULL `SubCostCodeId` that now has a `qbo.BillLineItemBillLine` mapping resolving to a `qbo.Item` with an `ItemSubCostCode` row, sets `SubCostCodeId` from that mapping — compute the set read-only first, then apply via the existing guarded update so nothing else is disturbed. In code, the out-of-band mapping-creation path (`reconcile_project.py` and any other caller that inserts a `BillLineItemBillLine` row directly) must route through, or replicate, the connector's post-mapping field reconciliation so newly-linked lines inherit SCC (and project/billable) from the QBO line rather than only writing the mapping row. Longer term, close the upstream hole so the bill_specialist create-bill flow resolves and populates `line_sub_cost_code_id` at draft time, so a QBO pull isn't the only chance for a line to get an SCC.

**Acceptance test.** After back-fill, re-run the audit join `dbo.BillLineItem → qbo.BillLineItemBillLine → qbo.BillLine → qbo.Item → qbo.ItemSubCostCode` for the 8 lines (22371, 22373, 22376, 22432, 22435, 22443, 22444, 22454) and confirm `SubCostCodeId` now equals the mapped value (96, 96, 157, 96, 58, 96, 96, 142 respectively) and is non-NULL. Confirm Invoice 1057 `enrich_line_items` returns populated `sub_cost_code_number` / `cost_code_number` so the packet TOC and Excel reconcile show no blanks. Regression guard: create a fresh BLI mapping via the repair path and verify `SubCostCodeId` is populated from the QBO item, not left NULL.

---

## Finding 2 — Purchase→Expense and VendorCredit→BillCredit SubCostCode propagation has full parity (no data gap)

**Finding.** The SCC back-fill gap that exists on the Bill side does **not** exist for Purchase→Expense or VendorCredit→BillCredit. Both per-line connectors resolve `ItemRef→SubCostCodeId` symmetrically on create and update; prod shows zero gap lines in either path.

**Root cause.** Design-level, and correct. Purchase: `integrations/intuit/qbo/purchase/connector/expense_line_item/business/service.py:64-66` resolves via `_get_sub_cost_code_id` (L198-229: `QboItem` by qbo_id → `ItemSubCostCode` by qbo_item_id), then passes `sub_cost_code_id` into both `update_by_public_id` (L140) and `create` (L164). VendorCredit: `integrations/intuit/qbo/vendorcredit/connector/bill_credit_line_item/business/service.py:51-53` resolves via `_get_sub_cost_code_id` (L217-232, same two-hop lookup), then passes into both `update_by_public_id` (L104) and `create` (L119). This mirrors the Bill connector exactly.

**Severity.** P2 — no live impact; tracked only as a parity confirmation and a minor observability nit (silent drift potential if a future code change broke a path unnoticed).

**Classification.** Non-issue (confirmed).

**Fix shape.** No data-correctness fix required. The only latent asymmetry worth noting for future hardening: the Purchase connector logs a WARNING (`"ExpenseLineItem will have no SubCostCode (billing gap)"`) when an ItemRef resolves to no QboItem or no ItemSubCostCode mapping (`service.py:217, 224`), whereas the VendorCredit connector silently returns None on the same conditions (`service.py:226-229`) and swallows resolution errors via a broad try/except (L230-232). If observability of unmapped credit-memo items is ever wanted, raise the VendorCredit resolver's miss-logging to WARNING to match Purchase/Bill. This is logging-only, not a data change.

**Acceptance test.** Re-run the two read-only gap queries periodically (or after each VendorCredit/Purchase pull batch): join `dbo.ExpenseLineItem` / `dbo.BillCreditLineItem` → their qbo line-mapping table → `qbo.PurchaseLine`/`qbo.VendorCreditLine` → `qbo.Item` (by `QboId=ItemRefValue`) → `qbo.ItemSubCostCode`, filtering `WHERE local SubCostCodeId IS NULL AND qbo ItemRefValue IS NOT NULL`. Both must return 0 gap lines (they return 0 today, across 11,681 Purchase→Expense and 435 VC→BillCredit mappings).

---

## Finding 3 — Cross-entity (Invoice-keyed) attachables are never downloaded or linked, leaving source lines doc-less

**Finding.** The scheduler incremental sync pulls attachables per-entity via a QBO query QBO does not support, with no app-side cross-entity fallback. Receipts/credit-memos attached to a different QBO entity than the source (typically the customer Invoice) are never downloaded or linked, leaving the underlying Bill/Expense/BillCredit lines doc-less. HA-04 confirmed.

**Root cause.** `integrations/intuit/qbo/attachable/external/client.py:213-258` `query_attachables_for_entity` issues `SELECT * FROM Attachable WHERE AttachableRef.EntityRef.Type=... AND ...Value=...`, but QBO does not support a WHERE on `AttachableRef` (the file's own comment at L140 says so). On HTTP `>= 400` it falls back to fetch-all+filter (L245-248), but on a **200 with an empty/misapplied array** it silently returns whatever QBO sent (L250-254) with no app-side filter. (Note the sibling method `query_attachables`, L171-179, *does* apply an app-side filter — but the sync path uses `query_attachables_for_entity`, which does not.) The service wrapper `_query_attachables_with_fallback` (`integrations/intuit/qbo/attachable/business/service.py:79-116`) catches only *exceptions* (auth/transport), so a 200-empty is trusted as authoritative "0 attachments." All three sync loops key the lookup on the source's own `qbo_id` and pass the source entity type, never `'Invoice'`: `sync_qbo_bill.py:386-403`, `sync_qbo_purchase.py:244-258`, `sync_qbo_vendorcredit.py:324-338`. So an attachable whose `AttachableRef` points at the Invoice is structurally invisible to the scheduler. Only the InvoiceAgent on-demand path uses `query_all_attachables()` + app-side filter (Known Issue #27, `entities/invoice/intelligence/prompt.md`).

**Severity.** P1 — customer-facing invoicing error (packets ship without supporting docs) compounded by silent data drift (the source line is recorded as having no attachment when the document exists in QBO).

**Classification.** Both — code bug (the 200-empty path is trusted; the unsupported WHERE is never re-resolved) and operational gap (the per-entity pull model cannot capture cross-entity attachments at all).

**Fix shape.** Two parts. First, make the per-entity lookup authoritative by never trusting QBO's unsupported `AttachableRef` WHERE on a 200: either delete `query_attachables_for_entity`'s broken query path and always route through `query_all_attachables()` + exact app-side `(entity_ref_type, entity_ref_value)` filter, or have `_query_attachables_with_fallback` treat an empty 200 from the entity-specific call as indeterminate and re-resolve via the full-list filter. Second, close the cross-entity gap with a **separate, low-frequency scheduler timer** (e.g. daily) that pulls `query_all_attachables()` once per realm, builds the full `AttachableRef` map in memory, and for each Invoice-keyed attachable cross-references the local source (fingerprint or LinkedTxn) to download and link the blob onto the underlying Bill/Expense/BillCredit line item via the existing connectors. Walking all entities per tick is the wrong tradeoff (N entity-queries × rate limit); one paginated all-attachables call (~19k rows, a few seconds, well under the 500 req/min ceiling) amortizes far better and is naturally idempotent because `_upsert_attachable` and `sync_from_qbo_attachable` are hash/qbo_id keyed. Reuse the existing `_link_attachments_to_bill_line_items` / `sync_purchase_attachments_to_expense_line_items` linkers.

**Acceptance test.** In prod, take the HA-04 cross-entity case — Expense 10986 / BillCredit 434 / BillCredit 427, all currently doc-less (zero rows in `ExpenseLineItemAttachment` / `BillCreditLineItemAttachment` for ExpenseLineItem 11973, BillCreditLineItem 930, BillCreditLineItem 923). Run the new full-attachable reconcile pass, then confirm each source line item now has an Attachment join row pointing at a blob downloaded from the QBO attachable originally keyed to the Invoice. Negative test: a normal same-entity bill still links exactly one attachment with no duplicate Attachment rows on a second run (idempotency). Aggregate check: the count of ExpenseLineItems with no attachment (currently 10,649) and BillCreditLineItems with no attachment (currently 428) should drop by the number of genuinely-cross-entity docs recovered. *(Note: linkage is via the join tables, not an `AttachmentId` column on the line item — the source lines are doc-less because they have zero join rows.)*

---

## Finding 4 — VendorCredit live duplication (concern #3) is RESOLVED/STALE

**Finding.** The previously-reported VendorCredit live duplication is resolved and stale: zero duplicate mappings, zero header dups, cited HA-04 credits 427/434 clean, now backstopped by hard DB unique constraints.

**Root cause.** Dedup is enforced at two layers: (1) the connector mapping-table lookup at `integrations/intuit/qbo/vendorcredit/connector/bill_credit/business/service.py:64` (`read_by_qbo_vendor_credit_id` → update-in-place rather than re-create), and (2) three DB unique constraints that structurally prevent the old reported duplication — `UQ_VendorCreditBillCredit_QboVendorCreditId`, `UQ_VendorCreditBillCredit_BillCreditId` (both on `qbo.VendorCreditBillCredit`), and `UQ_BillCredit_VendorId_CreditNumber` on `dbo.BillCredit`. The header pre-check at `entities/bill_credit/business/service.py:52-55` raises before any second row can be created. These constraints did not exist when the 2026-06-18 P0 report flagged credits 409/410/418/419 as live dups, which is why that concern is now stale.

**Severity.** P2 — no live impact; closed both in data and structurally.

**Classification.** Non-issue (confirmed).

**Fix shape.** No fix required for the live-duplication concern. The only residual defensive gap: when a re-pull races such that the mapping row is missing but the BillCredit header already exists, the connector's create path (`service.py:85-92`) surfaces `BillCreditService.create`'s "already exists for this vendor" ValueError, which the connector re-raises as a permanent skip (`service.py:108-110`). Unlike the Bill path (which backfills `SourceEmailMessageId` on the `(vendor, number)` conflict), the VendorCredit connector neither catches the conflict to re-link the orphaned mapping nor backfills. If desired, harden by catching the uniqueness conflict in the connector and re-binding the existing BillCredit into `qbo.VendorCreditBillCredit` so the next pull updates-in-place rather than perpetually skipping. This is hardening, not a live bug — the unique constraints guarantee no duplicate row is ever written regardless.

**Acceptance test.** Re-run `scripts/sync_qbo_vendorcredit.py` (pull-only) and confirm: (a) `COUNT(*)`, `COUNT(DISTINCT QboVendorCreditId)`, `COUNT(DISTINCT BillCreditId)` on `qbo.VendorCreditBillCredit` stay equal (currently 428/428/428); (b) no rows from `GROUP BY VendorId, CreditNumber HAVING COUNT(*)>1` on `dbo.BillCredit`; (c) credits QboId 72058/71621 each still map to exactly one BillCredit (427/434). Any attempt to insert a second mapping or duplicate header now fails at the DB with a 2627 unique-violation.

---

## Finding 5 — Bill `(vendor, billnumber)` header duplicates: 94 groups / 319 rows; dedup guard keyed on `(vendor, number, BillDate)` never fires on QBO re-imports

**Finding.** 94 duplicate groups / 319 rows share a `(vendor, billnumber)` pair. The write-side dedup guard includes `BillDate` in its key, so it never fires for these — but ~99% of the rows are legitimately distinct monthly QBO bills that reuse one DocNumber (recurring insurance premiums), each with a distinct `QboBillId`. This is a detection concern, not a data-fix concern.

**Root cause.** `entities/bill/business/service.py:305-306` + `entities/bill/persistence/repo.py:299-312` + `entities/bill/sql/dbo.bill.sql:309-311` — `ReadBillByBillNumberAndVendorId`'s WHERE includes `(@BillDate IS NULL OR [BillDate] = @BillDate)`, so distinct dates never collide. The connector at `integrations/intuit/qbo/bill/connector/bill/business/service.py:115, 145-159` dedups only by `QboBillId` (`mapping_repo.read_by_qbo_bill_id`), so a new distinct QBO bill with the same DocNumber always creates a new internal Bill — which is the correct behavior for genuine recurring charges.

**Severity.** P2 — operational toil / detection visibility, not a data-loss or invoicing error. The duplicate rows are mostly valid.

**Classification.** Both — the write-side guard is bypassable by design (code), and surfacing genuine accidental dups is an operational/reporting need.

**Fix shape.** Detection-only (per instruction; do not fix the existing data). Of three options: (1) a periodic **reconciliation report** — a scheduled read-only job fitting the existing `build.one.scheduler` + `qbo.ReconciliationIssue` pattern that runs the `GROUP BY (VendorId, BillNumber) HAVING COUNT>1` query, tiers output by "same QBO id family vs truly orphaned," and emails offenders; lowest risk, zero false-positive harm, surfaces both QBO-origin and manual dups, after-the-fact. (2) a SQL UNIQUE on `(VendorId, BillNumber)` — **reject**: Cincinnati genuinely has 24 separate monthly QBO bills under DocNumber 0688708, so the constraint would reject valid pulls and break the connector. (3) write-side guard hardening (drop `BillDate` from the key) — also wrong for this data model, since it would block legitimate distinct recurring bills. **Recommend option 1**, tiered so an operator can distinguish genuine recurring-charge families (distinct `QboBillId`s) from accidental re-keys (same `QboBillId` mapped twice — currently zero).

**Acceptance test.** Run the detection query on a schedule and confirm it lists the known offenders (Cincinnati 0688708=24, 0699248=22, 0689122=20, MJ CC=14) with per-row `QboBillId` so an operator can separate legitimate families (distinct ids) from accidental dups (same id). A new genuine monthly Cincinnati pull must NOT be flagged (distinct `QboBillId`); any future row sharing both DocNumber AND `QboBillId` must flag as a true bug. Evidence baseline: all 24 Cincinnati rows map to distinct `QboBillId`s (1:1, zero missing links); across all 94 groups, 94/94 have entirely distinct dates and 316/319 rows (99%) carry a `qbo.BillBill` link.

---

## Finding 6 — Both QBO connector Known Issues are STALE (KI#15 fixed; KI#21 latent only)

**Finding.** KI#15 (BillBillConnector broken for new bills) is fixed. KI#21 (PurchaseExpenseConnector ELI orphans) does not manifest in prod, though a latent delete-on-line-removal gap remains.

**Root cause.** KI#15: `integrations/intuit/qbo/bill/connector/bill/business/service.py:158` passes `require_attachment=False` into `BillService.create`; `entities/bill/business/service.py:191-233` gates the universal attachment rule behind that flag, so QBO-origin bills create cleanly without a PDF. This is the canonical QBO-origin bill-creation route, proven by the 9 HA-04 bills — it does **not** relax the attachment-required rule for the user-facing path (the flag defaults `True`). KI#21 (latent): `integrations/intuit/qbo/purchase/connector/expense/business/service.py:196-224` (`_sync_line_items`) only upserts/fingerprint-adopts QboPurchaseLines present in the current pull and never deletes ExpenseLineItems whose QboPurchaseLine was removed in QBO — a line genuinely deleted in QBO would leave an orphan ELI. The duplicate-on-regen case is already mitigated by position-aware fingerprint adoption (`expense_line_item/business/service.py:318-367`, task #17).

**Severity.** P2 — documentation accuracy / operational hygiene; no live data error.

**Classification.** Non-issue (confirmed) — both are doc cleanups plus one optional hardening.

**Fix shape.** No code fix required for either KI. Strike KI#15 from the issues list — BillBillConnector for new bills works, with `require_attachment=False` as the proven QBO-origin route. Re-scope KI#21 from "orphans ELIs under normal incremental sync" to "latent: does not reconcile QBO line REMOVALS" — the fingerprint adoption (task #17) closed the regenerated-line-id duplication path, which is why prod shows zero drift. Optionally add a reconciliation pass in `_sync_line_items` that, after upserting present lines, soft-detects ExpenseLineItems mapped to QboPurchaseLine ids absent from the current pull and either deletes them (mirroring the BillLineItem cascade — nullify dependent FKs first) or flags them to `qbo.ReconciliationIssue` — but build this only if a future audit shows nonzero drift. Until then, leave a TODO and a periodic orphan-count check.

**Acceptance test.** KI#15: pull a brand-new QBO bill (no local attachment) via `sync_qbo_bill` and confirm a `dbo.Bill` + mapped `dbo.BillLineItem` rows are created with no ValueError. KI#21: schedule the orphan-detection query `SELECT COUNT(*) FROM dbo.ExpenseLineItem eli INNER JOIN qbo.PurchaseExpense pe ON pe.ExpenseId=eli.ExpenseId LEFT JOIN qbo.PurchaseLineExpenseLineItem m ON m.ExpenseLineItemId=eli.Id WHERE m.Id IS NULL` and confirm it stays near zero (currently 1 of 11,185 expenses — a likely manual add, not a regen orphan), and that `qbo.PurchaseLine count == mapped ELI count` holds per expense (currently true for all 11,185).

---

## Finding 7 — InvoiceInvoiceConnector.sync_from_qbo_invoice is non-transactional and non-idempotent on re-run

**Finding.** `sync_from_qbo_invoice` is non-transactional and non-idempotent: a partial failure orphans lines, and any re-run where the header mapping is missing mints phantom `-N` duplicate Invoices (38 found in prod, several over $200K) and cannibalizes the master invoice's line items.

**Root cause.** `integrations/intuit/qbo/invoice/connector/invoice/business/service.py:79-180` and `integrations/intuit/qbo/invoice/connector/invoice_line_item/business/service.py:41-154`. Three compounding flaws: (1) **No transaction** wraps the header create (L146-167) + `InvoiceInvoice` mapping (L171-172) + the per-line loop in `_sync_line_items` (L244-248), so a DB failure mid-loop leaves a committed header with a partial set of ILIs (HA-04: 86/89). (2) **Re-run is gap-blind**: the UPDATE branch is taken only if the header-level `InvoiceInvoice` mapping exists (L113); if that mapping is absent (lost, or never committed because a prior run died after the header but before `create_mapping`), the code falls into CREATE. (3) **The CREATE path is destructively non-idempotent**: the duplicate-invoice-number retry loop (L146-163) blindly mints `HP-23.02-2`, `-3`, `-4` on each re-run; `create_mapping` then raises "QboInvoice already mapped," which is **silently swallowed** (L174-175), leaving an orphan header with no mapping; `_sync_line_items` finds every QBO line already mapped (`UQ_InvoiceLineItemInvoiceLine_QboInvoiceLineId`), so the orphan gets 0 lines, while the Shape-B fingerprint adopter (`_find_and_match_manual_by_fingerprint`, invoice_line_item service L173-225) re-parents the original master's Manual lines onto the duplicates.

**Severity.** P1 — customer-facing invoicing error of the worst kind: phantom high-value duplicate invoices and line-item cannibalization that corrupts what gets billed.

**Classification.** Code bug.

**Fix shape.** Make the ILI projection both transactional and gap-detecting/idempotent. (A) Wrap the per-invoice header upsert + mapping + full line projection in a single DB transaction (one connection threaded through `invoice_service.create`, `create_mapping`, and every `sync_from_qbo_invoice_line` call) so a mid-loop failure rolls the whole invoice back instead of committing a partial header. (B) Before taking CREATE, look up an existing local Invoice by the QBO doc_number (not just the `InvoiceInvoice` mapping) and re-adopt/re-attach the mapping when found, so a lost header mapping self-heals into UPDATE instead of minting a new `-N` invoice — resolve identity by `(project, doc_number)` as a fallback to the mapping cache. (C) Stop swallowing the `create_mapping` ValueError in CREATE: a duplicate-QboInvoiceId mapping failure must abort/roll back the just-created invoice rather than leaving an orphan, and the duplicate-number retry loop must not run when the conflict is actually "this QBO invoice is already synced." (D) Restrict/gate the fingerprint adopter so it cannot re-parent lines already belonging to a different mapped invoice for the same QBO invoice. Separately, a one-time cleanup script should delete the 38 empty unmapped `-N` orphan headers and re-consolidate the cannibalized families (TB3-17, HP-23.0x) — this audit performed read-only assessment only, no mutation. *(All within the stated constraints: no IsDraft/push changes, attachment rule untouched, no agent layer.)*

**Acceptance test.** After deploy, run `scripts/sync_qbo_invoice.py` for a project TWICE in a row and assert: (1) no new `dbo.Invoice` rows with `InvoiceNumber` matching `'%-2'/'%-3'/'%-4'` on the second run; (2) every non-draft `dbo.Invoice` has exactly one `qbo.InvoiceInvoice` mapping and a non-zero ILI count matching its QBO line count; (3) inject a simulated failure mid-line-loop (raise after N lines) and confirm the header is rolled back (no committed header with a partial ILI set). Standing prod check: `SELECT COUNT(*) FROM dbo.Invoice i WHERE NOT EXISTS(SELECT 1 FROM dbo.InvoiceLineItem ili WHERE ili.InvoiceId=i.Id) AND NOT EXISTS(SELECT 1 FROM qbo.InvoiceInvoice m WHERE m.InvoiceId=i.Id) AND i.InvoiceNumber LIKE '%-_'` returns 0 (currently 38).

---

## Cross-cutting recommendations

### 1. Where should cross-entity attachable sync live — baked into the per-entity loop, or a separate scheduler timer?

**Recommendation: a separate, low-frequency scheduler timer — do not bake it into the per-entity sync loop.**

The cross-entity problem (Finding 3) is fundamentally a *whole-realm* question — "which of all attachables belong to one of my local source lines?" — and the answer cannot be derived from a single source entity's `qbo_id`. Baking it into the per-entity loop forces one of two bad shapes:

- **API volume / rate limit.** A per-entity model that wanted cross-entity coverage would have to issue an attachable query per source entity per tick (N queries). With thousands of bills/expenses/credits, that multiplies against the QBO 500 req/min realm ceiling and starves the rest of the incremental sync. A single paginated `query_all_attachables()` call pulls the full set (~19k rows) in seconds — one cheap, bounded cost amortized across every source line, comfortably under the rate limit.
- **Idempotency.** A standalone reconcile pass is naturally idempotent because `_upsert_attachable` and `sync_from_qbo_attachable` are hash/`qbo_id` keyed and the existing linkers (`_link_attachments_to_bill_line_items`, `sync_purchase_attachments_to_expense_line_items`) are upsert-shaped — re-running creates no duplicate Attachment rows. Folding the same all-realm pull into the per-entity loop would mean re-pulling the full attachable set on every entity iteration (wasteful) or maintaining cross-tick shared state inside the per-entity loop (fragile).

The per-entity attachable lookup should still be **fixed** (never trust the unsupported `AttachableRef` WHERE on a 200 — route through `query_all_attachables()` + app-side filter, or treat 200-empty as indeterminate) so same-entity docs are reliably captured inline. The new daily timer is the *only* mechanism that can recover Invoice-keyed (cross-entity) docs, and it should run at low frequency (e.g. daily) precisely because cross-entity recovery is not latency-sensitive and the full-list pull is the expensive part. Net: fix the inline path for same-entity correctness; add a separate low-frequency all-realm reconcile for cross-entity coverage.

### 2. How to detect `(vendor, billnumber)` header-dup accumulation going forward

**Recommendation: a periodic reconciliation report. Do NOT add a SQL UNIQUE constraint, and do NOT tighten the write-side guard. (No fix to the existing Cincinnati data is proposed.)**

- **SQL UNIQUE on `(VendorId, BillNumber)` — reject.** The data model legitimately reuses one DocNumber across many monthly recurring QBO bills (Cincinnati: 24 distinct `QboBillId`s under DocNumber 0688708). A UNIQUE constraint would reject those valid pulls and break the connector. It enforces a rule that is *false* for this domain.
- **Write-side guard hardening (drop `BillDate` from the dedup key) — reject.** Same reason: it would block legitimately-distinct recurring bills. The connector already dedups correctly by `QboBillId`; the per-DocNumber guard is intentionally permissive and should stay that way.
- **Reconciliation report — recommend.** A scheduled read-only job fitting the existing `build.one.scheduler` + `qbo.ReconciliationIssue` pattern runs `GROUP BY (VendorId, BillNumber) HAVING COUNT>1` and surfaces offenders **tiered by QBO-id family**: groups whose rows all carry *distinct* `QboBillId`s are legitimate recurring-charge families (informational); a group containing two rows mapped to the *same* `QboBillId`, or `(vendor, billnumber)` rows with no `qbo.BillBill` link, is a true accidental duplicate worth an operator's attention. This catches both QBO-origin and manual dups, never produces a false-positive block on a valid pull, and is the only approach compatible with the recurring-charge reality. It is after-the-fact by nature, which is acceptable because the genuine-dup rate here is effectively zero (currently no same-`QboBillId` collisions) — the report is a tripwire, not a gate.