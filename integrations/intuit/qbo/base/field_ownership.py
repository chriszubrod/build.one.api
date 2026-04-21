"""
Field-level source-of-truth registry for bidirectional QBO sync (task #19).

For each entity that syncs both directions between the local DB and QBO,
this module declares which side owns each field. The pull-side sync
(`sync_from_qbo_*`) MUST NOT overwrite app-owned fields; the push-side
sync (`sync_to_qbo_*`) MUST NOT send QBO-owned fields that the app
shouldn't be influencing.

## The rules

Three ownership categories per field:

- `QBO_OWNED` — QBO is the source of truth. Pull overwrites the local
  value; push does not send this field.
- `APP_OWNED` — The local app is the source of truth. Push sends it;
  pull does not touch it.
- `BOTH_EDITABLE` — Either side can edit (rare). Requires explicit
  conflict resolution on push (e.g., sync-token mismatch → merge or flag).
  See task #20.

## How this is enforced today (implicitly)

The current connector code does the right thing by construction:

- Pull paths call `update_by_public_id(..., field=value, ...)` passing
  ONLY the fields it extracted from QBO. App-owned fields (not in the
  argument list) keep their existing values because the sproc ignores
  unspecified parameters (task #7 CASE WHEN guards make this
  well-defined).

- Push paths build QBO payloads from a narrow set of local fields — the
  QBO-owned ones. App-owned fields are never serialized into the QBO
  request because they're not in the payload builder.

## Why this registry exists

The implicit enforcement works until someone writes a new connector, or
edits an existing sync method without understanding the contract. The
registry is:

  1. A machine-readable record of what fields live on each side.
  2. A reference for code review (does this new sync method touch fields
     it shouldn't?).
  3. A foundation for task #20's conflict-resolution logic — when QBO
     rejects a push with SyncToken mismatch, the merge algorithm needs
     to know which fields to take from our side vs re-fetched QBO state.

Changing an entry here is a semantics-level decision. Update this file
and then update the corresponding connector sync methods in lockstep.
"""

from dataclasses import dataclass, field
from typing import Dict, List


QBO_OWNED = "qbo_owned"
APP_OWNED = "app_owned"
BOTH_EDITABLE = "both_editable"


@dataclass(frozen=True)
class FieldOwnership:
    """Per-entity field ownership declaration."""
    entity: str
    qbo_owned: List[str] = field(default_factory=list)
    app_owned: List[str] = field(default_factory=list)
    both_editable: List[str] = field(default_factory=list)

    def ownership_of(self, field_name: str) -> str:
        if field_name in self.qbo_owned:
            return QBO_OWNED
        if field_name in self.app_owned:
            return APP_OWNED
        if field_name in self.both_editable:
            return BOTH_EDITABLE
        # Unknown fields default to QBO_OWNED for safety: on a pull, preferring
        # QBO's value when we don't know is less risky than preserving a
        # potentially stale local value. The inverse on push is handled by
        # not including the field in payload builders.
        return QBO_OWNED


# ---------------------------------------------------------------------------
# Per-entity rules
# ---------------------------------------------------------------------------

BILL = FieldOwnership(
    entity="Bill",
    qbo_owned=[
        # These fields are authoritative in QBO. Accountants/bookkeepers edit
        # them in QuickBooks and the app pulls the latest on the next sync.
        "vendor_id",
        "bill_date",
        "due_date",
        "bill_number",         # DocNumber in QBO
        "total_amount",
        "memo",                # PrivateNote in QBO
        "payment_term_id",     # SalesTermRef in QBO
        # Line-item fields (managed via the line connector, listed for completeness):
        "description",
        "quantity",
        "rate",
        "amount",
        "sub_cost_code_id",    # derived from ItemRef
        "project_id",          # derived from CustomerRef
        "markup",
        "is_billable",
        "is_billed",
    ],
    app_owned=[
        # These only exist locally and are never sent to QBO:
        "is_draft",            # completion gate (Bill lifecycle)
        "review_status_id",    # local review workflow
        # Attachment links are app-side only; QBO attachments are a separate sync.
    ],
    both_editable=[
        # None. Pure source-of-truth per field.
    ],
)


INVOICE = FieldOwnership(
    entity="Invoice",
    qbo_owned=[
        "customer_ref_value",
        "invoice_date",
        "due_date",
        "invoice_number",      # DocNumber
        "total_amount",
        "memo",                # CustomerMemo
        "line_items",          # managed via InvoiceLineItem connector
    ],
    app_owned=[
        "is_draft",
        # Invoice workflow state is entirely app-driven; QBO doesn't track
        # the invoice review/approval pipeline.
    ],
    both_editable=[],
)


PURCHASE = FieldOwnership(
    entity="Expense",   # local entity name; corresponds to QBO Purchase
    qbo_owned=[
        "vendor_id",
        "expense_date",        # TxnDate
        "payment_type",
        "account_id",          # AccountRef
        "total_amount",
        "memo",
        # Line-item fields:
        "description",
        "quantity",
        "rate",
        "amount",
        "sub_cost_code_id",
        "project_id",
        "is_billable",
        "is_billed",
    ],
    app_owned=[
        "is_draft",
        "review_status_id",
    ],
    both_editable=[],
)


VENDOR_CREDIT = FieldOwnership(
    entity="BillCredit",   # local entity name; corresponds to QBO VendorCredit
    qbo_owned=[
        "vendor_id",
        "credit_date",         # TxnDate
        "credit_number",       # DocNumber
        "total_amount",
        "memo",
        "line_items",
    ],
    app_owned=[
        "is_draft",
    ],
    both_editable=[],
)


# Lookup by entity name (both local name and QBO name for convenience).
_REGISTRY: Dict[str, FieldOwnership] = {
    # Local entity name → ownership rules.
    "Bill": BILL,
    "Invoice": INVOICE,
    "Expense": PURCHASE,
    "BillCredit": VENDOR_CREDIT,
    # QBO entity name aliases.
    "Purchase": PURCHASE,
    "VendorCredit": VENDOR_CREDIT,
}


def for_entity(entity_name: str) -> FieldOwnership:
    """Look up the ownership rules for a local or QBO entity name."""
    rules = _REGISTRY.get(entity_name)
    if rules is None:
        raise KeyError(
            f"No field-ownership rules registered for entity '{entity_name}'. "
            f"Known: {sorted(_REGISTRY.keys())}"
        )
    return rules
