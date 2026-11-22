# U-797c5 — Reviewed coding reaches QuickBooks on completion (BillCredit, and pulled Bills)

Status: **DRAFT v2 for Gate 1 (Chris), 2026-11-22.** Parent U-797c4. P0-surface: new QuickBooks writes.

## Chris's decisions (2026-11-22)

1. App-created credits are submitted for review, then run through the Complete pipeline, **in parallel with the Bill
   complete pipeline, sharing code where possible**.
2. What is pushed: **the same as a Bill**.
3. When QBO changed in the meantime: **the system updates as needed, the same as Bills**.
4. Kill switch: asked whether Bills have one. **They do not**: Bills are gated only by the global `ALLOW_QBO_WRITES`
   (plus the per-entity completion gate, which ships off). Same here.
5. Pull behaviour: **the same as the Bill entity**.
6. Slice order: as needed.
7. Pulled records (follow-up question): **close the gap for both Bills and BillCredits** — a pulled document whose
   coding changed in review gets a safe update in QBO on completion, one shared code path.

## How Bills work today (research 2026-11-22)

- **App-created bill, completion:** `_enqueue_qbo_sync` -> QBO outbox kind `sync_bill_to_qbo` -> `push_to_qbo` ->
  `sync_to_qbo_bill` creates the QBO Bill and stamps each `BillLineItem.QboId` from the response `LineNum` (U-363).
  Retries reuse the RequestId; on a SyncToken mismatch the worker refreshes from QBO and retries once; 5 failures
  dead-letter with a ReconciliationIssue.
- **Pulled bill, completion:** nothing is pushed (`sync_to_qbo_bill` short-circuits on an existing `qbo_id`).
- **Pull:** QBO owns line coding (`field_ownership.BILL`: sub_cost_code_id, project_id, description). The one exception:
  an *approved* bill whose QBO line resolves to the holding code keeps the local code and records it — and loses it
  once the bill is completed.
- **The gap (both entities):** a pulled document on the holding code, recoded in review and completed, never tells QBO
  the reviewer's code; the next pull puts QBO's coding back.
- **Dead code:** `update_has_been_billed_in_qbo` rebuilds every Bill line without its Line Id on a full replace (it
  would re-id every line). Its only caller, `_sync_billed_status_to_qbo`, has zero call sites. It ran once by hand in
  July (14 OVH bills). It is the wrong shape for any update and is removed in c5b.

## Design

### c5a — BillCredit create on completion (Bill parity)

- `complete_bill_credit` gains the Bill completion's QBO step: for a credit with no `qbo_id`, enqueue QBO outbox kind
  **`sync_bill_credit_to_qbo`** (entity_type `BillCredit`). Failure-isolated like Bill's (completion reports it, never
  fails on it).
- Drain: build the VendorCredit from local lines exactly as `sync_to_qbo_bill` builds a Bill — vendor, date, credit
  number, `ItemBasedExpenseLineDetail` with ItemRef (from the SCC; holding refused), CustomerRef (project),
  BillableStatus, description, amounts — create it, stamp `BillCredit.QboId` and each `BillCreditLineItem.QboId` from
  the response `LineNum`, verify the stamps landed, store the staging rows.
- **Shared code:** lift the line-build and line-id-stamp steps out of the Bill connector into `qbo/base/` helpers both
  connectors call (the Bill path's behaviour stays byte-identical, pinned by its existing tests); one enqueue helper
  for both completion services; the worker's dispatch table + `KNOWN_KINDS` gain the new kind;
  `_refresh_from_qbo` gains BillCredit so the SyncToken refresh-and-retry works as it does for Bills.
- Gate: `ALLOW_QBO_WRITES` only, checked at enqueue and at drain (parks when off), as for Bills.

### c5b — reviewed coding update for pulled Bills and BillCredits (one shared path)

- **Trigger:** completion of a document that already has a `qbo_id` (pulled) **and** whose local line coding differs
  from the staged QBO lines (ItemRef / CustomerRef / Description, compared through the push mapping). Equal -> nothing
  enqueued. Kinds `update_bill_coding_in_qbo` and `update_bill_credit_coding_in_qbo`, one handler implementation.
- **The write is surgical** (the Expense recode mechanism, generalised in `qbo/base/`): raw GET; find each local line's
  raw line by `Id` (`QboId`); change only ItemRef, CustomerRef and Description on lines that differ (switch an
  AccountBased line to ItemBased, as the recode does); carry every other field — amounts, qty, price, BillableStatus,
  ClassRef, TaxCodeRef, MarkupInfo, LinkedTxn — and every sibling line unchanged; strip MetaData/domain/sparse; POST
  the whole raw document; store the response in staging. **Never add or remove lines; never touch the header**
  (DocNumber stays under `preserve_human_edited_ref`). Needs raw get/update on the Bill and VendorCredit clients (the
  typed schemas drop LinkedTxn and unmodelled fields).
- **QBO changed meanwhile (decision 3):** on a SyncToken mismatch, re-GET and re-apply the same surgical change to the
  fresh document (our change is only the coding of our lines, so re-applying it is "update as needed"). A local line
  whose `QboId` is no longer in QBO, or a holding/unmapped code, is recorded (ReconciliationIssue, ids only) and
  nothing is written for that document.
- **Protecting the window before the drain:** between completion and the drain the pull would see a completed document
  and write QBO's coding back, leaving nothing to push. While a coding-update row for the document is pending, failed
  or in progress, the Bill and VendorCredit line pulls keep the local SCC / project / description (amounts still from
  QBO). Once the update lands, QBO and local agree and the pull is QBO-owned again (decision 5: same as Bill).
- **Dead code removed:** `update_has_been_billed_in_qbo` and `_sync_billed_status_to_qbo`. The billable-status write
  path (parked U-778d2) will build on the surgical helper instead.
- **First rollout:** the update path is new for Bills, which complete daily. Proposed: a `QBO_CODING_UPDATE_MODE`
  setting, `log` (default: compute and record the intended change, write nothing) then `write`, so the first days show
  exactly which documents and lines would change before anything is sent.

### Field ownership and runbooks

`field_ownership.BILL` / `VENDOR_CREDIT` note the exception: line coding is app-owned while a coding update is in
flight, and a reviewed pulled document's coding is pushed on completion. New runbook
`docs/runbooks/qbo-reviewed-coding-push.md` (Symptom / Severity / Diagnosis / Recovery / Verification / Prevention):
create failures, update conflicts, line_not_found, holding refused, dead-letter, gate parked.

## Verification

Pure tests with fakes: the Bill create path byte-identical after the shared-helper lift; credit create stamps every
line id; the update changes only target coding fields, round-trips sibling lines and LinkedTxn byte-identical, never
adds/removes lines or touches the header; SyncToken mismatch re-applies on the fresh document; the window guard
keeps coding only while a row is in flight; gate off parks. Mutation testing on every rule. Live: the first credit
create and the first `write`-mode update are supervised (read the document in QBO before and after).

## Slices and order

1. **c5a** — BillCredit create on completion (independent; Bill parity).
2. **c5b** — shared coding update for pulled Bills and BillCredits, in `log` mode first, then `write`.

Each runs the full pipeline (Pass 1 x2, Pass 3, mutation, suite) and its own Gate 2.

## Open for Gate 1

1. **c5b first rollout in `log` mode** (proposed) before `write`, or straight to `write`?
2. **Remove the dead `update_has_been_billed_in_qbo` in c5b** (proposed)?
3. Anything in "the same as a Bill" you read differently from the table above?
