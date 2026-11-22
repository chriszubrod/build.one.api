# U-797c5 — BillCredit coding reaches QuickBooks on completion

Status: **DRAFT for Gate 1 (Chris), 2026-11-22.** Parent U-797c4. P0-surface: a new QuickBooks write.
Decision already made (Chris, 2026-11-22): **push on completion**, rather than keep-local or QBO-wins.

## Problem

BillCredits are never pushed to QuickBooks. A credit that was reviewed (U-797c4) carries the reviewer's sub cost
code, project and description locally; QBO still carries what it had (usually the holding code). While the credit
is under review the QBO pull keeps the local coding (U-797c4b), but once it is completed QBO wins again, so any
later edit of that VendorCredit in QBO re-pulls QBO's coding over the reviewer's. Bills avoid this by pushing.

Prod, 2026-11-22: 486 credits, 476 completed straight from the pull (they already equal QBO and never pass through
completion), 5 drafts, 0 reviewed so far. Volume is small; correctness matters more than throughput.

## What exists (research 2026-11-22)

- **Bill push is create-only.** A bill with a `qbo_id` short-circuits. Its one update path,
  `update_has_been_billed_in_qbo`, rebuilds every line without Line Ids on a full replace — the duplicate-lines
  hazard CLAUDE.md warns about. Not a model to copy (booked separately, see "Out of scope").
- **Expense recode is the right mechanism** (`PurchaseExpenseConnector.recode_purchase_lines`): raw GET, change only
  the target line dicts in place by `Id`, strip `MetaData`/`domain`/`sparse`, POST the whole raw document back so
  every sibling line keeps its Id and unmodelled fields survive; SyncToken fail-closed; "live token = snapshot + 1
  and lines already carry the target" proves a crash-replay was ours. Its gates, lock, drain-time re-check and
  dead-letter path are reusable; its placeholder-account test and CodingSuggestion revisions are not.
- **The VendorCredit client** has a typed `update_vendor_credit` with no callers. The typed schema drops line
  `LinkedTxn` (ReimburseCharge links) and unmodelled header fields, so a typed update would destroy data. We need
  raw `get_vendor_credit_raw` / `update_vendor_credit_raw`, mirroring the Purchase client.
- **Line identity is already there:** `BillCreditLineItem.QboId == raw Line.Id` (U-361, unique per credit).
- **Reverse mapping exists:** `resolve_qbo_item_ref` (realm-verified, refuses the holding code) for SCC -> ItemRef;
  the Bill connector's `_get_qbo_customer_ref` pattern for Project -> CustomerRef.

## Design

### 1. Enqueue at completion

`BillCreditCompleteService.complete_bill_credit`, after line finalize and the Box fan-outs, calls a new
failure-isolated `_enqueue_qbo_coding_push` (a copy of Bill's `_enqueue_qbo_sync` shape):

- Skip, reported in the completion result, when the credit has no `qbo_id` (created locally: `no_qbo_identity`),
  or the gate is off (`writes_disabled`). Completion itself never fails on the push.
- Otherwise enqueue one QBO outbox row: kind **`push_vendorcredit_coding`**, entity_type `BillCredit`. The outbox
  row has no payload; the drain reads state at drain time (see 4 for why that is safe).

### 2. Gates (default-deny, checked twice)

`ALLOW_QBO_WRITES` **and** a new kill switch `ALLOW_BILL_CREDIT_PUSH_WRITES` (off by default). Checked at enqueue
(no row when off) and again at drain before any read (off parks the row; never dead-letters). Same shape as the
Expense recode's two flags.

### 3. Drain handler `_handle_push_vendorcredit_coding`

1. Re-check the gates. Take applock `qbo_bill_credit_push:<public_id>`.
2. Read the credit; require completed (`IsDraft = 0`) and `verify_identity_dbo_only` on its `qbo_id`.
3. Raw GET the VendorCredit.
4. **SyncToken fail-closed:** the live token must equal `qbo.VendorCredit.SyncToken` (the last pulled revision).
   Different means someone changed it in QBO since our last read: record `vendorcredit_changed_in_qbo`
   (ReconciliationIssue, ids only), write nothing, finish the row. Exception: live = staged + 1 and every target line
   already carries our values means the write was ours (crash replay) -> mark done. The worker's generic
   refresh-and-retry is bypassed for this kind (it does not support BillCredit and would retry blindly).
5. For each local line with a `QboId`, find the raw line by `Id`. A local line with no `QboId`, or a `QboId` missing
   from QBO: record `vendorcredit_line_not_found`, write nothing. **Never append or remove lines.**
6. Resolve each line's ItemRef (holding code -> refused, recorded, nothing written) and CustomerRef.
7. Change only lines whose ItemRef, CustomerRef or Description differ from local. Set `ItemBasedExpenseLineDetail`
   (an AccountBased line is converted, as the Expense recode does); carry ClassRef, TaxCodeRef, MarkupInfo, Qty,
   UnitPrice, BillableStatus and LinkedTxn unchanged. **Never touch Amount, DocNumber or any header field**
   (`preserve_human_edited_ref` stays intact).
8. Nothing differs -> done (idempotent no-op). Otherwise POST the full raw document, then upsert the response into
   `qbo.VendorCredit` / `qbo.VendorCreditLine` so the staged SyncToken is current.
9. Retries reuse the RequestId (worker idempotency context); 5 failures -> dead-letter + ReconciliationIssue
   `push_vendorcredit_coding_dead_letter`; recovery via the existing dead-letter replay runbook.

### 4. The pull must not undo the reviewer before (or after) the push lands

The research's top risk: between completion and the drain (60 s, longer under backoff or a parked gate) the pull
sees a completed credit, QBO wins, it overwrites the local coding, and the drain then finds nothing to push. The
coding is lost silently.

Fix in the pull rule, not with a payload snapshot: a completed credit **whose last review before completion was an
approval** keeps its local SCC, project and description on every pull (extend `under_review_line_coding`'s caller
with a `reviewed_completed` state). Amounts still come from QBO. When QBO's coding differs from the kept local
coding on such a credit, the pull records `vendorcredit_coding_drift` (deduped, ids only) so a QBO-side edit is
visible instead of silently ignored. Once the push lands, QBO and local agree and the rule is a no-op.

Credits completed straight from the pull never went through review, so they are unaffected (QBO stays the source).

### 5. Field ownership

`field_ownership.VENDOR_CREDIT` lists `line_items` as qbo_owned. This unit changes that for reviewed credits:
update the registry entry and `docs/qbo-field-ownership.md` (SCC / project / description become app-owned on a
credit with an approved review; amounts, qty, price, billable stay QBO-owned).

### 6. Runbook

New `docs/runbooks/qbo-vendorcredit-coding-push.md` (Symptom / Severity / Diagnosis / Recovery / Verification /
Prevention) covering: changed_in_qbo, line_not_found, holding refused, dead-letter, gate parked, coding drift.

## Slices

- **c5a — the pull guard (section 4) + field ownership (5).** No QBO write; protects reviewed credits from the moment
  it deploys. Ships first.
- **c5b — the push (1-3) + raw client + runbook (6).** Ships with `ALLOW_BILL_CREDIT_PUSH_WRITES` off; Chris turns it
  on after one supervised push.

Each slice runs the full pipeline (Pass 1 x2, Pass 3, mutation, suite) and its own Gate 2.

## Verification

Pure tests with fakes for every branch above, including: sibling lines and LinkedTxn round-trip byte-identical;
Amount/DocNumber untouched; changed-in-QBO writes nothing; replay proof; gate off parks; no line added or removed;
the pull keeps reviewed-completed coding and records drift. Live: the first push is supervised — read the
VendorCredit in QBO before and after, compare every line and header field.

## Out of scope (booked separately)

- Creating QBO VendorCredits for credits made locally (`no_qbo_identity` is reported, not pushed).
- Backfill of already-completed credits (none have been reviewed yet, so nothing to recover).
- `update_has_been_billed_in_qbo` (Bill) sends Id-less lines on a full replace — possible duplicate Bill lines on
  the next pull. Needs its own investigation unit.

## Decisions for Chris (Gate 1)

1. **Credits created locally (no QBO id):** skip and report (proposed), or create them in QBO on completion?
2. **What is pushed:** SCC (ItemRef), project (CustomerRef) and description (proposed), or only SCC + project?
3. **Changed in QBO since the last pull:** fail closed and record an issue, keeping the local coding (proposed),
   or overwrite QBO?
4. **Kill switch:** a separate `ALLOW_BILL_CREDIT_PUSH_WRITES`, off until a supervised first push (proposed)?
5. **Pull guard:** keep the reviewer's coding on reviewed-completed credits permanently, recording drift
   (proposed), or only while a push is pending?
6. **Slices:** c5a (pull guard) first, then c5b (push) (proposed)?
