# QBO BillCredit create push (sync_bill_credit_to_qbo)

Introduced by U-797c5a. An app-created BillCredit that completes with no QBO identity is queued as outbox kind
`sync_bill_credit_to_qbo` (entity_type `BillCredit`). The worker creates the QBO VendorCredit from the local
lines, the same way the Bill push creates a QBO Bill. Credits already carrying a QboId are not queued (coding
updates for those are U-797c5b).

## Symptom

- A completed BillCredit has no QboId and no VendorCredit appears in QBO.
- `qbo.Outbox` rows with `Kind = sync_bill_credit_to_qbo` sit in `failed`, or are dead-lettered.
- Completion responses carry `errors: [{"step": "qbo_enqueue", ...}]` (the credit still completed).

## Severity

- P2 for a single credit blocked by a mapping gap (holding code, unmapped SubCostCode, missing vendor identity).
- P1 when the push fails for many credits at once (auth, gate, or a systemic QBO error).
- P0 for any duplicate VendorCredit in QBO (a DocNumber collision or a stamp that did not land after a create).

## Diagnosis

1. Find the outbox row: `SELECT * FROM qbo.Outbox WHERE Kind = 'sync_bill_credit_to_qbo' AND EntityPublicId = '<public_id>'`.
2. Read `LastError` and `Attempts`. Map the error to a cause:
   - `HoldingCodeRefused`: a line is still on the suspense SubCostCode. Recode the line, then re-queue.
   - `no QBO Item mapping`: the SubCostCode has no QboId for this realm. Map it, then re-queue.
   - `No QBO vendor mapping`: the vendor has no verified QboId. Sync or map the vendor first.
   - `billable line on project_id=... has no QBO CustomerRef mapping`: stamp the project's QboId, then re-queue.
   - `QboWriteRefusedError` or a parked row: `ALLOW_QBO_WRITES` is not `true` (app settings).
   - `QboDuplicateError`: see the `duplicate_qbo_vc_docnumber` ReconciliationIssue. Do not retry
     blindly. Link the existing VendorCredit by hand or rename the local credit number first.
3. `ReconciliationIssue` drift types to check: `bill_credit_identity_conflict`, `bcli_line_push_stamp_failed`,
   `bill_credit_push_stamp_failed`, `duplicate_qbo_vc_docnumber`.
4. If the create succeeded in QBO but the local stamp did not land (`bill_credit_push_stamp_failed` or
   `bcli_line_push_stamp_failed`), the VendorCredit exists in QBO. Find it by DocNumber in QBO and stamp the
   BillCredit and its lines by hand (`SetBillCreditQboIdentity` / `SetBillCreditLineItemQboIdentity`).
   Then re-queue the row: the create is skipped (the credit now carries its QboId), and the line
   attachments are pushed. The stamp-failure ReconciliationIssue names the VendorCredit id.

## Before a manual retry of a dead-lettered row

QBO may accept a second VendorCredit with the same DocNumber and vendor, so a blind retry can
duplicate a credit that already exists. Before re-queuing:

1. Search QBO for the credit's DocNumber (the local `credit_number`) under the same vendor. Use the QBO
   UI (Expenses > Vendor credits) or a read-only query. Do not rely on a 6140 rejection.
2. If a VendorCredit exists, stamp the local BillCredit and its lines by hand (see Diagnosis step 4), then
   re-queue: the create is skipped and only the attachments are pushed. If none exists, the retry is safe.
3. Note on the dead-letter record: the ReconciliationIssue drift type falls back to
   `outbox_dead_letter` for this kind, because the kind name `sync_bill_credit_to_qbo` is too long for the
   32-character DriftType column. Filter on `EntityType = BillCredit` and the outbox public id, not on the
   drift type alone.

## Recovery

- Fix the cause in the diagnosis list, then re-queue dead letters with
  `python scripts/retry_qbo_outbox_dead_letters.py --kind sync_bill_credit_to_qbo` (dry-run first, then `--apply`),
  after the QBO search above.
- `SyncToken` mismatch on the refresh path re-pulls the single VendorCredit (`_refresh_bill_credit`) and retries once.
  A genuine identity conflict is recorded and dead-lettered immediately.

## Verification

- The outbox row reaches `done`, and the BillCredit reads a non-null `QboId` and `RealmId`.
- Every BillCreditLineItem with a LineNum match has a non-null `QboId`.
- Open the VendorCredit in QBO: DocNumber, date, vendor, and each line's item, customer, and amount match the app.

## Prevention

- Completion refuses holding-code lines (`_assert_no_holding_code_on_complete`) before the enqueue, so holding
  lines should not reach the drain.
- Map every SubCostCode used on credits to a QBO Item before completing the credit.
- Keep the first live create supervised: read the document in QBO before and after.
