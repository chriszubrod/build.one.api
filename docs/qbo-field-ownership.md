# QBO Field-Level Source of Truth

This is the human-readable companion to
`integrations/intuit/qbo/base/field_ownership.py`. It explains **which side
of the sync owns each field** for every bidirectional QBO entity, and why.

## The contract

Every QBO-synced field on a local entity is in one of three categories:

| Category | Pull (QBO → local) | Push (local → QBO) |
|---|---|---|
| **QBO-owned** | Overwrites local | NOT sent in payload |
| **App-owned** | NOT touched | Sent in payload |
| **Both-editable** | Overwrites local | Conflict check on push |

Most fields are cleanly QBO-owned or app-owned. Both-editable is rare in
this codebase and currently used for zero fields — any new "both-editable"
field should get explicit conflict-resolution logic via task #20.

## How the rules are enforced

**Pull path (`sync_from_qbo_*` in connectors):** the connector extracts
QBO-owned fields from the incoming QBO payload and calls
`update_by_public_id(..., field=value, ...)` passing ONLY those fields.
App-owned fields are omitted from the argument list. The stored
procedures (task #7) use `CASE WHEN @Param IS NULL THEN [Column] ELSE
@Param END` guards, so unspecified fields are provably preserved in the
DB — the guarantee isn't a code review, it's the DB.

**Push path (`sync_to_qbo_*` in connectors):** the connector builds QBO
payloads (e.g., `QboBillCreate`) from a narrow set of local fields.
App-owned fields are never in the payload builder, so they can't leak
to QBO.

This implicit approach has worked well but is fragile to new-code drift.
The registry in `field_ownership.py` makes the contract explicit for
review and for task #20's conflict-resolution logic.

## Per-entity rules

### Bill

**QBO-owned:**
- `vendor_id`
- `bill_date`, `due_date`
- `bill_number` (QBO: `DocNumber`)
- `total_amount`
- `memo` (QBO: `PrivateNote`)
- `payment_term_id` (QBO: `SalesTermRef`)
- All line-item fields: `description`, `quantity`, `rate`, `amount`,
  `sub_cost_code_id`, `project_id`, `markup`, `is_billable`, `is_billed`

**App-owned:**
- `is_draft` — the completion lifecycle gate. QBO has no concept of
  "draft bill"; finalization is an app-level state.
- `review_status_id` — local review workflow.
- Attachment links (Bill ↔ BillLineItemAttachment). QBO attachments are
  a separate sync (Attachable entity), not fields on Bill itself.

### Invoice

**QBO-owned:**
- `customer_ref_value`
- `invoice_date`, `due_date`
- `invoice_number` (QBO: `DocNumber`)
- `total_amount`
- `memo` (QBO: `CustomerMemo`)
- Line-item fields (via InvoiceLineItem connector)

**App-owned:**
- `is_draft`
- Invoice workflow state — approval pipeline is entirely app-driven.

### Expense (QBO: Purchase)

**QBO-owned:**
- `vendor_id`, `expense_date`, `payment_type`, `account_id`,
  `total_amount`, `memo`
- All line-item fields

**App-owned:**
- `is_draft`, `review_status_id`

### BillCredit (QBO: VendorCredit)

**QBO-owned:**
- `vendor_id`, `credit_date`, `credit_number`, `total_amount`, `memo`
- Line-item fields

**App-owned:**
- `is_draft`

## Rationale

### Why is QBO mostly the source of truth?

Your context: single-realm, single-user, accountants/bookkeepers edit
daily in QuickBooks (confirmed in Round 0 Q4 of the integration plan).
Given that, QBO is the primary surface for financial-record edits. The
app's job is to stay in sync.

### Why are `is_draft`, `review_status_id` app-owned?

These represent **in-app workflow state** that QBO doesn't know about:
whether the user has finished preparing the bill, whether someone has
reviewed it internally, etc. Pushing these to QBO would pollute QBO's
data model; overwriting them on pull would break the workflow.

### What if an accountant changes `vendor_id` in QBO after the app has
finalized the bill?

Pull overwrites. The app's view of the bill now reflects QBO's view.
This is the correct behavior: QBO is authoritative, and the app adapts.

### What if a user changes `memo` in the app UI on a bill that's already
in QBO?

Two cases:
- **Before the scheduled push:** the local edit sits in the outbox
  debounce window (Policy C), then pushes to QBO. QBO now reflects the
  app's value. Next pull brings the same value back.
- **Concurrently with a QBO edit:** see task #20 (sync-token conflict
  resolution). The outbox push will hit a SyncToken mismatch; the
  conflict handler pulls fresh QBO state, applies only the app-owned
  fields on top, and retries.

## Using the registry programmatically

```python
from integrations.intuit.qbo.base.field_ownership import for_entity, QBO_OWNED, APP_OWNED

rules = for_entity("Bill")

# Is a specific field app-owned?
assert rules.ownership_of("is_draft") == APP_OWNED
assert rules.ownership_of("vendor_id") == QBO_OWNED

# Iterate:
for field in rules.qbo_owned:
    ...
```

## When to update this doc + registry

- Adding a new entity to the bidirectional sync → add a new
  `FieldOwnership` entry + doc section.
- Changing ownership of an existing field → update both files in lockstep
  AND audit the sync_from_qbo / sync_to_qbo methods for consistency.
- Adding a "both-editable" field → ensure conflict-resolution logic
  (task #20) handles it correctly.
