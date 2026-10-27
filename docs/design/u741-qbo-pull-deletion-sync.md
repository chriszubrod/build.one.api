# U-741 — QBO pull mirrors bills and invoice lines deleted in QuickBooks

**Status:** DESIGN APPROVED by Chris, 2026-10-27, with the recommended answers in §8. Nothing built yet.
Build sub-units U-741a/b/c (§7). P0-surface (money + cascade deletes) — every build sub-unit runs all three
review passes.

## 0. The problem

QuickBooks is the book of record, and build.one is supposed to mirror it. Two kinds of QBO deletion are
never mirrored today:

1. **A Bill deleted in QBO stays in `dbo.Bill`.** The scheduled bill pull is watermark-incremental, so a
   deleted bill simply stops appearing. The daily reconcile only *flags* it (`qbo_voided`). The one sweep
   that deletes (`_reconcile_deleted_bills`) runs only on a manual, unfiltered full sync.
2. **An invoice line removed in QBO stays in `dbo.InvoiceLineItem`.** The invoice pull creates and updates
   the lines QBO returns, and never removes a local line QBO no longer has.

The two compound. Deleting a bill whose billable lines were invoiced makes QBO remove **two** invoice lines
per charge — the cost line (item, `42600 Construction Income`) and a separate markup line (no item,
`48997 Markup`), both linked through the bill line's `ReimburseCharge`. On a paid invoice QBO then reduces the
payment's application, leaving the gap as unapplied credit. Locally nothing changes. When the operator then
restores the charge in QBO as a plain line and re-applies the payment, the next pull *adds* that line, so the
local invoice now double-counts it.

## 1. Evidence (October 2026 Selvin payroll cleanup)

| When deleted in QBO | What | Local effect until hand-cleaned |
|---|---|---|
| 2026-08-24 | Selvin 1/31 bills (HP, MR2-MAIN, BR) | Bill copies + stale lines sat on local HP-23.02 (+$1,181.25) and MR2-MAIN-05 (+$1,443.75) for two months |
| 2026-09-01 | Associated Masonry duplicate bill QBO-72896 | Local BR-MAIN-26 +$184.97 |
| 2026-10-13 | Jackson VanOrman 2026.06.04.HA, Weston Parker 2026.06.01.OVH | Local HA-04 +$30.19, OVH-01 +$120.11 |
| 2026-10-22 → 10-27 | ~25 Selvin bills (deleted by Claude, then by Chris) | Every one needed a hand-run `BillService.delete_by_public_id` + `InvoiceLineItemService.delete_by_public_id` on each stale markup line, one at a time, under explicit approval |

Every case was found by a person or by an ad-hoc audit. No detector raised it.

## 2. Goals and non-goals

**Goals**
- G1. A bill deleted in QBO is removed from `dbo` within one scheduled cycle, safely.
- G2. An invoice line removed in QBO is removed from the local invoice copy on the next pull of that invoice,
  including the standalone markup lines that no bill cascade can reach.
- G3. Never mass-delete on a degraded or partial QBO read (the U-212 lesson).
- G4. Stay well inside the 500K/month CorePlus cap.
- G5. Surface the money consequence: when a deletion leaves a client payment with new unapplied credit, raise
  a reconciliation issue so a person decides whether to backfill the invoice.

**Non-goals**
- Re-adding lines in QBO automatically. Whether a dropped charge belongs on the invoice is a business decision
  (BR-MAIN-26 proved a deletion can be a legitimate dedupe). The app mirrors QBO; it does not repair it.
- Purchases and VendorCredits beyond what shares the same primitive (noted as follow-ons).
- App-only invoices (`dbo.Invoice.QboId IS NULL`) — see §5; they are not in QBO, so "mirror" does not apply.

## 3. What exists today (code map, 2026-10-27)

**Bills.**
- The scheduled pull (`sync_qbo_bill`, every 15 min) queries `Metadata.LastUpdatedTime > X`
  (`bill/external/client.py:135-137`), which never returns deletes. `_reconcile_deleted_bills`
  (`bill/business/service.py:121-210`) runs only when the watermark is null, so never in steady state, and its
  local set is **`qbo.Bill` staging**, not `dbo.Bill`.
- U-718's `StagingDeleteService` (`staging_delete/business/service.py`) reads the QBO CDC feed hourly (:43,
  `base/cdc.py`), GET-confirms each candidate (`confirm_deleted_ids`, only 404 / fault 610 confirms; auth,
  budget and rate-limit errors abort the set), applies the 50-candidate ceiling, and deletes the **staging**
  row. For a Bill still bound in dbo it only counts `dbo_still_bound` (281-304). Its weekly full mode
  (Sun 06:00) id-diffs every entity.
- Consequence: after the hourly job deletes staging, **no staging-keyed path can find the bill again**. Only the
  daily `qbo_voided` detector (`reconciliation/business/service.py:1034-1155`) is dbo-keyed, and it flags, never
  deletes (its docstring cites invoice lines as the reason).
- `BillService.delete_by_public_id` → `DeleteBillCascadeById` (`dbo.bill.sql:884-985`): one transaction,
  header UPDLOCK. Deletes attachment links, then **every** `InvoiceLineItem` sourced from the bill's lines
  (no filter on `Invoice.QboId` — app-only invoices lose lines too; `Invoice.TotalAmount` is not recomputed),
  nulls `ContractLabor.BillLineItemId` (status stays `billed`), then lines, reviews, coding suggestions, bill.
  Attachment rows and blobs are kept. A system caller is exempt from the completed-bill lock.

**Invoice lines.**
- The scheduled pull (`sync_qbo_invoice`, every 15 min) is incremental, but each pulled invoice carries its
  full line set, and the staging layer already deletes `qbo.InvoiceLine` rows absent from it
  (`invoice/business/service.py:385-404`). The dbo row is **deliberately** left under its stale identity so the
  re-adopt matcher can reuse it if QBO regenerates a Line.Id (U-361b).
- `_sync_line_items` (`connector/invoice/business/service.py:851-893`) builds `live_qbo_line_ids` from
  staging, then syncs each line and **swallows** per-line exceptions (load-bearing for the create path).
- The only ILI delete for a single line, `InvoiceLineItemService.delete_by_public_id`, **hard-deletes the
  Attachment row and blob** when nothing else references it. Deleting an ILI never flips the source line's
  `IsBilled` back, and never untags the DETAILS workbook (column H).
- Rows with a NULL `QboId` on a pulled invoice are legitimate: user-added lines, unstamped lines from the
  complete flow (recognised later through LinkedTxn), and siblings cleared by the identity theft-clear.

## 4. Design

### 4.1 Bills — extend the U-718 deletion job to act on dbo (G1)

Reuse U-718's detection exactly; add one dbo action behind a mode flag. **No new QBO calls.**

1. **Candidate source becomes staging ∪ dbo.** In CDC mode, a CDC-deleted Bill id is a candidate if it is in
   `qbo.Bill` **or** bound in `dbo.Bill` (`read_by_qbo_identity`). In full mode, the local set becomes
   staging ids ∪ dbo identity ids (the live-id list is already fetched and cached). Same ceiling, same
   GET-confirm, same systemic abort.
2. **Per confirmed-deleted bill bound in dbo, decide** (pure function, unit-tested):

   | Condition | Action |
   |---|---|
   | Bill sources any `InvoiceLineItem` on an **app-only** invoice (`Invoice.QboId IS NULL`) | Do not delete. Flag `qbo_deleted_bill_app_inv` (high). An operator decides, as Chris did on 10-27. |
   | Bill sources an ILI on a QBO-linked invoice whose `QboId` is **still live** on that QBO invoice (one `invoice/{id}` GET per affected invoice — rare, and bounded by the ceiling) | Do not delete. Flag (inconsistent: QBO removes the line when the bill goes). |
   | Otherwise | Delete through `BillService.delete_by_public_id` under `system_authz()`. |

3. **Audit trail.** Before each delete, write one `qbo_deleted_bill_removed` issue (info, `auto_fixed`) whose
   Details carry the pre-image: bill number, vendor, total, lines, and the ILIs the cascade will remove with
   their invoice numbers and amounts. That is the rollback record and the input to §4.3.
4. **Mode flag** `QBO_DELETE_SYNC_DBO_BILLS` = `off` | `dry_run` | `on`. Ships `dry_run`: counts and would-be
   actions in the job result and the log, nothing deleted.
5. **Close two silent gaps** found in the map: a CDC-mode ceiling abort and an id-fetch failure
   (`delete_reconcile.py:313-324`) must write a `pull_delete_reconcile` issue, not just a log line.

Completed bills are deleted like any other: every Selvin bill was completed, and QBO is the book of record.
Contract labor keeps `Status='billed'` with no bill link, which is what prevents a re-bill.

### 4.2 Invoice lines — prune after the per-line pass (G2)

Add a prune step at the **end** of `_sync_line_items`, after every incoming line has been processed, so a
re-adopt has always had its chance first.

1. **Candidates:** this invoice's ILIs with a non-NULL `QboId` (same realm) whose normalised id is not in
   `live_qbo_line_ids`, read fresh from the DB after the loop. NULL-`QboId` rows are never candidates.
2. **Skip the whole invoice (prune nothing)** when any of these hold:
   - any line raised during this pass (track it instead of only swallowing it);
   - the live set is empty;
   - the invoice contains a `GroupLineDetail` line (group children are not staged with their own Ids);
   - **completeness check fails:** the staged line amounts do not sum to the QBO `TotalAmt` (after discount
     and tax lines) — a truncated payload can never cause a prune;
   - candidates exceed a per-invoice ceiling (`QBO_INVOICE_LINE_PRUNE_MAX`, default 10). Record
     `pull_delete_reconcile` and leave it for a person.
3. **Delete with a new prune path that keeps files:** a sproc that, in one transaction, deletes the
   `InvoiceLineItemAttachment` links, the provenance row and the ILI, and leaves `Attachment` rows and blobs —
   the same file policy as `DeleteBillCascadeById`. Not `delete_by_public_id`, which can destroy evidence.
4. **Source-linked candidate whose source bill still exists locally:** prune the ILI and do not touch the
   source's `IsBilled`. Usually the bill is only waiting for the hourly job (§4.4), so the line is listed in
   the §4.3 issue as *source pending*. The daily reconcile re-checks pending entries: if the bill has since
   been removed, the entry is marked settled; if the bill is still live in QBO after 24 hours, the entry is
   escalated as "line removed from invoice while its bill stays" so a person decides whether to re-bill it.
5. **Mode flag** `QBO_INVOICE_LINE_PRUNE` = `off` | `dry_run` | `on`, shipping `dry_run`.

Cost: zero QBO calls; the payload is already in hand. Idempotent across overlap re-pulls.

### 4.3 The money signal (G5)

Whenever either path removes lines from a QBO-linked invoice, write one `invoice_lines_pruned` issue per
invoice per run, listing each removed line (description, amount, service date, source, removal path).
Severity is **high** when the QBO invoice carries a linked Payment, because QBO will have reduced that
payment's application and left unapplied credit. The issue says so in plain words: "$X removed from paid
invoice MR2-MAIN-05; the client payment now has unapplied credit — decide whether to restore the charge."
That replaces the manual hunt for variances.

### 4.4 Ordering between the two paths

Either order converges on QBO's state:
- **Invoice pull first** (the usual case — the deletion bumps the invoice's LastUpdatedTime, and the pull runs
  every 15 minutes): the prune removes the cost and markup lines. The hourly bill job then finds no
  QBO-linked ILIs and deletes the bill.
- **Bill job first:** the cascade removes the bill-sourced cost line; the next invoice pull prunes the
  standalone markup line.
- **An operator restores the charge in QBO** as a plain line: the pull creates it as a new Manual ILI; the
  prune removes the old pair. No double count.

## 5. Out of scope, by decision

- **App-only invoices** (`QboId IS NULL`) are never pulled and never pruned. A deleted bill that feeds one is
  flagged, not deleted (§4.1).
- **Unstamped stray lines on pulled invoices** (e.g. TB3-17's $345 `Manual` lines with no `QboId`) stay with
  the existing daily `invoice_draw_mismatch` detector.
- **DETAILS workbook / Box rows** for a deleted bill or pruned line are left as they are; the issue names them.
  Clearing them is its own unit if wanted.
- **Purchases and VendorCredits** reuse §4.1 in a follow-on once Bills have run clean for a month.
- **Voided-but-present** QBO bills (return 200) remain the existing 🔴 TODO item.

## 6. Rollout

1. **Ship both paths in `dry_run`.** For a week, the job results and logs report what each path *would*
   delete; nothing changes. Review the would-delete lists against QBO by hand.
2. **Turn on bills first** (`QBO_DELETE_SYNC_DBO_BILLS=on`), then the invoice prune a few days later. Bills
   first means the cascade handles the bill-sourced cost lines while the prune is still observable in dry run.
3. **Backlog.** Ghosts that already exist (see §6.1) are cleared by one operator-approved full run of each
   path, dry-run first, with `max_candidates` raised only after the dry-run list is checked. CDC can only see
   30 days back; anything older is reachable only through the full modes.
4. **Rollback.** Set either flag to `off`. Deleted rows are recoverable from the `qbo_deleted_bill_removed` /
   `invoice_lines_pruned` pre-images plus the next pull (QBO still holds everything that is live).

### 6.1 Backlog measured 2026-10-27 (read-only census)

23 QBO calls; nothing written. Taken after the Selvin 2/15 and 2/28 cleanups.

| Measure | Count | Amount |
|---|---|---|
| `dbo.Bill` rows with a QboId | 20,648 | |
| …not live in QBO (ghost bills) | 46, all `completed`; 43 from 2026, 3 from 2025 | $106,638.45 |
| …of those, feeding lines on app-only invoices (§4.1 → flag) | 2 bills, 3 lines | |
| Contract labor rows linked to ghost bills | 13 | |
| QBO-linked `dbo.Invoice` rows whose QBO invoice is gone | 4 (`TB3-DRAFT-17`, `OL-DRAFT-10`, `HP-DRAFT-23`, `HP2-DRAFT-07`) — already U-718 `qbo_deleted` territory | |
| Stale **stamped** lines on QBO-linked invoices | 15 on 2 invoices | $206,058.98 |
| Unstamped lines (QboId NULL) on QBO-linked invoices | 5 on 4 invoices | $30,600.16 |

**The headline is OHR2-37.** Its QBO total is $271,800.48, but the local copy sums to $506,884.14 — overstated
by **$235,083.66**: 14 stale stamped lines created 2026-08-27 (12 of them `BillLineItem`-sourced, e.g.
Cabinetry $80,093.90, LED Lighting $53,410.00, Custom Furniture $25,320.00) that QBO no longer carries after a
2026-10-09 edit, plus one $28,869.92 unstamped line. Because the G702/G703 coded rollup reads non-Manual lines,
these inflate local draw financials for OHR2. It is also exactly the case the per-invoice ceiling exists for:
14 candidates > 10, so the prune would refuse and flag it for a person rather than delete $206K on its own.
The other invoice, KA2-08, carries one stale line ("Hardscapes REFUND", −$154.76).

## 7. Build units, tests and review

Two code units plus a runbook unit, each through the full per-unit pipeline (Pass 1 adversarial, Pass 2,
Pass 3 security — P0 surface):

| Unit | Scope | Key tests |
|---|---|---|
| U-741a — bill deletion sync | `staging_delete/business/service.py` (dbo candidate source, decision function, mode flag, issues); `base/delete_reconcile.py` (issue on id-fetch failure); stale docstrings in `bill/business/service.py:132-139` and `entities/bill/business/service.py:1833`; new drift types in `base/drift_types.py` | decision table (app-only → flag; live line → flag; else delete); dry_run deletes nothing; ceiling and systemic abort still win; mutation-proof the app-only guard |
| U-741b — invoice line prune | `connector/invoice/business/service.py::_sync_line_items` (post-loop prune, failure tracking, completeness check, ceiling, mode flag); new prune sproc in `dbo.invoice_line_item.sql` (base file, `CREATE OR ALTER`, `SET NOCOUNT ON`, always-COMMIT); `invoice_lines_pruned` issue | NULL-QboId never pruned; any line failure → no prune; truncated payload (sum ≠ TotalAmt) → no prune; re-adopt then prune leaves the re-adopted row; attachments survive; idempotent on re-pull; mutation-proof each guard |
| U-741c — runbook + /docs | extend `docs/runbooks/qbo-staging-freshness.md` (new flags, issue types, backlog procedure); entity docstrings for Bill and InvoiceLineItem; `/docs` refresh | n/a |

Sub-agents build in their own worktrees and never touch prod; the session applies the SQL and runs the
backlog after review.

## 8. Decisions (Chris, 2026-10-27)

1. **App-only invoices — FLAG.** A deleted bill that feeds a line on an app-only invoice is flagged
   (`qbo_deleted_bill_app_inv`), never deleted by the job. Stripping the line stays an operator decision.
2. **A line removed from an invoice while its bill stays — FLAG only.** The source line's `IsBilled` is not
   reset automatically.
3. **Budget workbook rows — LEAVE in v1.** DETAILS / Box rows for deleted bills and pruned lines are not
   cleared; the issue names them.
4. **Rollout — one week of `dry_run` per path** before it is switched to `on` (§6).

The OHR2-37 overstatement in §6.1 is backlog, not a v1 build step: it is reviewed by a person, because its
14 candidates exceed the per-invoice ceiling by design.
