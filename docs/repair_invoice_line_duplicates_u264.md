# U-264 — Repair duplicate unmapped Manual InvoiceLineItem rows

## Background (U-247)

Commit `797320a0` (U-247) fixed a bug in
`integrations/intuit/qbo/invoice/connector/invoice_line_item/business/service.py`:
`_find_and_match_manual_by_fingerprint` used to return `None` when multiple unmapped
Manual candidates shared the same content fingerprint `(description, amount)`. The
invoice line connector then fell through to `create()`, minting a new duplicate
`InvoiceLineItem` with `SourceType='Manual'` on every subsequent QBO pull instead of
adopting the existing unmapped row.

The fix deterministically adopts the **lowest-id** unmapped candidate on ambiguous
matches. That stops new duplicates but does not remove rows already created while the
bug was live.

## Predicate (exact)

An `InvoiceLineItem` row **R** is in the **repair set** if and only if **all** of:

1. `R.SourceType = 'Manual'`
2. **R** has no row in `qbo.InvoiceLineItemInvoiceLine` (unmapped)
3. There exists at least one **other Manual-sourced** `InvoiceLineItem` **S** on the same
   `InvoiceId` where
   `normalize_fingerprint(S.Description, S.Amount) == normalize_fingerprint(R.Description, R.Amount)`
   **and** **S** has a `qbo.InvoiceLineItemInvoiceLine` mapping (S is mapped)

   Manual-only scope mirrors the U-247 fingerprint-adoption bug itself: Bill/Expense-sourced
   lines are matched via their source FK, never by content fingerprint.
4. **QBO ground-truth cross-check:** join R's invoice → `qbo.InvoiceInvoice` →
   `qbo.InvoiceLine`. Let `qbo_line_count` = count of QBO staging lines whose
   fingerprint matches R's; let `local_mapped_count` = count of **identity-verified**
   mapped Manual siblings on the same invoice — each counted sibling's **mapped QBO line**
   (via `qbo.InvoiceLineItemInvoiceLine` → `qbo.InvoiceLine`) must share R's fingerprint,
   not merely its local `Description`/`Amount`. **R** is repairable only
   when `qbo_line_count <= local_mapped_count` **and** `local_mapped_count >= 1` —
   every genuine QBO line with this fingerprint is already represented by a mapped
   local row, so **R** corresponds to zero remaining QBO content and is a pure local
   artifact.

Rows that fail condition 4 (`qbo_line_count > local_mapped_count`, or
`local_mapped_count == 0`) are **held back** and never deleted:

| Reason | Meaning |
|--------|---------|
| `no_sibling` | No mapped sibling with the same fingerprint (legacy unique Manual line, or no collision) |
| `unclaimed_qbo_line` | A QBO staging line with this fingerprint is not fully covered by mapped local rows — missing-mapping problem, out of scope for this tool |

### Fingerprint normalization

```python
description = (description or "").strip()
amount = "" if amount is None else format(Decimal(str(amount)).normalize(), "f")
return (description, amount)
```

## Measured prod scope (build time)

| Snapshot | Unmapped Manual rows | Repair set | Held back | Invoices w/ activity |
|----------|----------------------:|-----------:|----------:|----------------------:|
| U-247 ship time (commit message) | 185 | — (not derived) | — | 17 |
| U-264 build (fresh re-measurement, live dry-run) | **186** | **181** | **5** (all `no_sibling`) | **10** |

**`186` is the total unmapped-Manual count, not the repair-set size — do not pass it
to `--expected-count`.** The script's own dry-run prints `repair_set=181` on its PRE
line; that is the number `--expected-count` gates on.

Seven invoices that appeared in the ship-time list **self-healed**: subsequent QBO
pulls under U-247's adopt-lowest-id fix mapped some previously-unmapped duplicates,
removing them from the repair set without this cleanup script (confirmed: 3 rows show
a `qbo.InvoiceLineItemInvoiceLine` mapping created after the U-247 commit timestamp).

Every one of the 186 unmapped rows **predates** the U-247 fix commit timestamp
(`797320a0`, 2026-08-17 12:38:28) — confirming no new duplicates are being minted
post-fix; the ratchet is stopped.

U-247's original 185/17 snapshot was never persisted as a query or row list, so the
185→186 delta cannot be reconciled row-for-row. It does not need to be: this unit's
repair set is derived and cross-verified against **today's live data** (including the
QBO ground-truth staging table), independent of the historical count.

## Script

`scripts/repair_invoice_line_duplicates.py`

Same reviewed-manifest family as `scripts/repair_email_attachment_phantom_drift.py`
(dry-run `--report`/`--output` there; `--manifest-out`/`--manifest` here) — review
dry-run output, then apply consumes the manifest and re-verifies live state.

- **Dry-run by default** — SELECT-only; prints fleet-wide counts, per-invoice
  breakdown, full repair-set listing, and held-back rows with reasons.
- **`--apply`** — deletes via `InvoiceLineItemService.delete_by_public_id` (attachment
  cascade + mapping cleanup); one invoice at a time; sub-batches of
  `--batch-size` (default 20); re-runs the live predicate between sub-batches and
  after each invoice; re-verifies each row immediately before delete (skips with a
  loud log if a concurrent QBO pull mapped it in the meantime).
- **`--expected-count N`** — **required** with `--apply`. Hard-refuses (nonzero exit,
  zero mutations) if the live repair-set size ≠ N. Optional in dry-run (warns loudly
  on mismatch but continues read-only).
- **`--manifest-out PATH`** — dry-run only. Writes the reviewed repair-set PublicIds
  (JSON list) for use with `--apply --manifest`.
- **`--manifest PATH`** — **required** with `--apply`. Restricts deletes to the exact
  PublicIds from an earlier dry-run manifest; `--expected-count` must match the manifest
  line count (cheap cross-check against composition drift between dry-run and apply).

During `--apply`, the script acquires the same `qbo_sync:invoice` applock used by
`POST /api/v1/admin/sync/qbo/invoice` for the full delete pass, so concurrent scheduled
QBO invoice pulls self-skip instead of racing the per-row re-verify/delete gap.

### Recommended workflow

```bash
# 1. Read-only inventory + human review. Read "repair_set=N" off the PRE line.
#    Write the exact PublicIds you reviewed to a manifest file.
PYTHONPATH=. python scripts/repair_invoice_line_duplicates.py \
  --manifest-out repair_manifest.json

# 2. Apply only after re-reviewing the printed repair-set listing, using the N
#    the dry-run just reported (181 as of this build -- re-check, it may have
#    shifted since) AND the manifest from step 1. --apply refuses outright if the
#    live count or manifest line count no longer matches --expected-count.
PYTHONPATH=. python scripts/repair_invoice_line_duplicates.py \
  --apply --manifest repair_manifest.json --expected-count N
```

Requires CLI system-admin context (`assert_cli_system_admin()`), same as other
prod-mutating scripts.

## Tests

Pure classification logic lives in the script module and is covered by
`tests/test_repair_invoice_line_duplicates.py` (no live DB).
