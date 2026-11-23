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

## c5a as built (U-797c5a)

- **Shared helper:** `integrations/intuit/qbo/base/line_push.py`. `build_item_expense_line_plan` holds the Bill line decisions
  (ItemRef, CustomerRef, BillableStatus, qty/rate/amount, markup). Resolvers are passed as callables so each connector keeps its
  own resolution methods. `qbo_identity_stamp_landed` is the read-back check. Schema construction stays per connector. The
  Bill connector's `_build_qbo_line` calls the helper; its output is pinned by `tests/test_u797c5a_line_push_golden.py`.
  The record_mapping_issue calls stay at each call site, because the reconciliation width guard requires string-literal drift types.
- **Credit create:** `VendorCreditBillCreditConnector.sync_to_qbo_vendor_credit(bill_credit_id, realm_id)`. It short-circuits on an
  existing QboId after verify_identity_dbo_only, refuses holding and unmapped lines, creates through `QboVendorCreditClient`, records
  and re-raises QboDuplicateError, stages through the pull's `QboVendorCreditService.upsert_from_external`, stamps each line by
  LineNum, then the header, and verifies each stamp.
- **Enqueue (round 2, R1):** the enqueue is shared. `shared/completion/qbo_enqueue.py::enqueue_qbo_push` is called by Bill's
  `_enqueue_qbo_sync` (read_auths=`qbo_auth_service.read_all`) and by BillCredit's (its own `qbo_auth_service`). The qbo_id guard stays
  in the credit caller and reads by internal id (inside the try). Enqueue is NOT gated: ALLOW_QBO_WRITES is enforced at drain and in
  the HTTP layer, as for Bill. Failures are reported in the completion result, never raised.
- **Worker:** dispatch entry `sync_bill_credit_to_qbo` (`_handle_sync_bill_credit`), a BillCredit branch in `_refresh_from_qbo`
  (`_refresh_bill_credit`), and the kind in `KNOWN_KINDS`.
- **Runbook:** `docs/runbooks/qbo-bill-credit-create-push.md`.

### c5a round 1 (as built, review fixes)

- **Unit price:** the helper takes explicit `unit_price_of` / `markup_of` accessors. Bill passes `.rate` and `.markup`;
  a BillCreditLineItem (which has `unit_price` and no markup) passes `.unit_price` and `None`. Bill output is pinned by the golden.
- **JSON:** `QboVendorCreditClient` create and update dump with `mode="json"`, as the Bill client does. Decimal amounts go out as strings.
- **By-id reads:** the public-id read does not return QboId/RealmId, so the completion enqueue guard and the worker's refresh
  gate read by internal id. Nothing in SQL changed.
- **Refresh conflict:** the identity-conflict ValueError severs `__context__`, as Bill's does, so a genuine conflict dead-letters
  rather than retrying.
- **AP account (Bill parity):** `APAccountRef` comes from the same cache-then-scan logic as Bill's `_get_ap_account_ref`. Round 2 shares the body
  (`base/push_refs.py::resolve_ap_account_ref`). A missing AP account is not fatal.
- **Attachments (Bill parity):** each line's attachment uploads through the shared walk
  (`attachable/line_attachments.py::sync_line_attachments`, dedupe as Bill's) with `entity_type="VendorCredit"`. It runs after the
  create, in the worker handler, so a re-drain (which skips the create) still syncs attachments. The whole post-create step is guarded
  (round 2, F2): a read error is logged and does not fail the row; budget and write refusal park it, as Bill.
- **Negative amounts (round 2, F3):** the guard reads the COMPUTED line amount (qty x unit price when the stored amount is absent),
  before any write. ValueError, permanent. Credits are stored positive.
- **Stamp failures (round 2, F1):** a line stamp whose write raises or does not land is recorded (`bcli_line_push_stamp_failed`); the
  push continues. A header stamp whose write raises is re-raised, as Bill does: the retry re-POSTs with the same RequestId, QBO dedups,
  and the stamp is retried (tests: `test_header_stamp_retry_reuses_the_request_id_so_qbo_does_not_create_twice`).
- **Drift type length:** `duplicate_qbo_vc_docnumber` (the longer name was 37 characters; the column allows 32). The dead-letter
  record for this kind falls back to `outbox_dead_letter`; see the runbook.
- **Deferred:** none of the Bill push's behaviour is deferred. Coding updates for pulled credits remain U-797c5b.


### c5a round 2 (review fixes and Pass-2 sharing)

- **Return value (E2):** `sync_to_qbo_vendor_credit` returns `VendorCreditPush(qbo_id, line_items)`. On a short-circuit the id is the
  verified one; after a create it is the created id only if the header stamp landed. The handler uses it and no longer re-reads the credit.
- **Shared with Bill:** `shared/completion/qbo_enqueue.py` (enqueue), `integrations/intuit/qbo/base/push_refs.py` (vendor, CustomerRef,
  item, AP refs; each connector keeps a thin `_get_qbo_*_ref` wrapper), `integrations/intuit/qbo/attachable/line_attachments.py`
  (line-attachment walk). Bill's push and enqueue output is unchanged: its tests and the line golden pass without edits.
- **Worker:** `_raise_non_retryable` (the SyncToken `__context__` severing) is used by the Bill, BillCredit and invoice conflict sites.
  The BillCredit block moved into `_record_bill_credit_identity_conflict`, mirroring Bill.
- **Not shared (kept as mirrors):** the line-id stamp loop in the Bill connector (the R5 lift, booked by EM); the stamp detail text
  (per-connector, kept so each record_mapping_issue stays a string literal at its call site).
- **Runbook (F4):** after a manual stamp the row is re-queued (the create is skipped; the attachments are pushed).
