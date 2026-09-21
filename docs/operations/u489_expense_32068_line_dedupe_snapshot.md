# U-489 — expense 32068 line duplication (rollback record)

Captured 2026-09-20, before correcting a duplicated ExpenseLineItem.

> ## ⚠️ CORRECTED 2026-09-20 — the stated cause below is WRONG
>
> This document originally blamed a missing `qbo.PurchaseLineExpenseLineItem` map row, and step 3 inserted
> one "to stop the NEXT external recode duplicating again." **That map row is inert and step 3 prevents
> nothing — expense 32068 is still exposed.**
>
> **U-364 (`b484ed5c`, 2026-09-04) retired that table from the pull.** Identity now resolves dbo-natively via
> `ExpenseLineItem.QboId` / `RealmId` (`read_by_qbo_identity`). The pull neither reads nor writes the mapping
> table for line identity. Verified 2026-09-20.
>
> **The real mechanism** is `_readopt_stale_line`
> (`integrations/intuit/qbo/purchase/connector/expense_line_item/business/service.py:288-328`). It DID run and
> DID consider the stale line — but its fingerprint requires **all four** of
> `(description, amount, quantity, rate)` to match. Here:
>
> | field | stale line 12916 | incoming QBO line 2 | |
> |---|---|---|---|
> | description | `Cassidy Andrews - Approved for payment…` | `Hardscapes` | ❌ |
> | amount / qty / rate | 197.98 / 1 / 197.98 | 197.98 / 1 / 197.98 | ✅ |
>
> **Recoding a line in QuickBooks is DEFINED as changing its description.** So the readopt is structurally
> guaranteed to miss on exactly the population it exists to protect. Amount is the stable anchor; description
> is actively the wrong signal.
>
> **Steps 1 and 2 remain correct** — the duplicate line and its attachment link were genuinely stale, and
> avoiding `delete_by_public_id` genuinely protected the shared receipt. Only the causal story and step 3
> were wrong.
>
> Consequence for the 229 backfill: **a map-row-based tiering signal does not mean what it appears to.**
> Post-U-364 lines have no map row by design, so "mapped vs unmapped" separates old from new, not live from
> stale. Re-derive the tiering before repairing.

**How it happened.** Chris recoded QBO purchase 76602 directly in QuickBooks. An external recode REPLACES the
line: `Line.Id` 1 → 2. The pull deleted staging line 13855 and inserted 13903 — correct. But there was **no
`qbo.PurchaseLineExpenseLineItem` map row** for this purchase, so the pull could not tell that the new QBO
line was the old one. It created a SECOND `ExpenseLineItem` (12965) and left the original (12916) orphaned.

Result: header $197.98, two line items summing to **$395.96**, against ONE line in QuickBooks.

**The correction** (three statements, guarded to single ids):
1. delete the `ExpenseLineItemAttachment` LINK for eli 12916 — ⛔ the Attachment row (29956) and its Azure
   blob are **shared with eli 12965** and are NOT touched. `ExpenseLineItemService.delete_by_public_id` was
   deliberately NOT used: it cascades "delete the Attachment record + Azure blob", assuming a 1-1 link, and
   would have destroyed the receipt 12965 still needs.
2. delete `ExpenseLineItem` 12916 — the stale line whose QBO counterpart no longer exists. Verified: 0
   InvoiceLineItem references, `IsBilled = 0`.
3. insert the map row `qbo.PurchaseLineExpenseLineItem (13903 → 12965)` — what stops the NEXT external
   recode duplicating again on this record.

Direct SQL, guarded per id: expense 32068 is `completed`, so U-468's terminal lock refuses the service path.

Coding item 616 needs no action — it is orphaned (points at the deleted staging line 13855) and U-483's
Arm 1 closes it as `resolved_externally` on the next reconcile.

**Rollback:** `u489_..._snapshot.json` holds every affected row as it was. Re-insert `ExpenseLineItem` 12916
and its attachment link from the snapshot, and delete the map row, to restore exactly.

**Not a one-off.** 229 expenses currently have line-sum ≠ header. The mechanism is confirmed here; the
assessment of those 229 is scoped separately.
