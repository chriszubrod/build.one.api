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
from typing import Dict, List, Optional


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
# Pull-side value preservation (the "rule of three" document-number decision)
# ---------------------------------------------------------------------------
#
# Several human-editable document-number fields are re-derived from QBO on every
# pull (Bill.bill_number, BillCredit.credit_number, Invoice.invoice_number,
# Expense.reference_number — all `qbo.doc_number or f"QBO-{qbo_id}"`). Passing
# that derived value unconditionally into the UPDATE path reverts any local human
# correction within a scheduler tick (KI-42). These pure helpers centralize both
# the mint (`qbo_ref_or_placeholder`) and the preserve/upgrade decision
# (`preserve_human_edited_ref`) so every connector shares one implementation and
# the placeholder format lives in exactly one place.


def _qbo_placeholder(qbo_id) -> str:
    """The single source of the ``QBO-<qbo_id>`` placeholder format.

    Both the minter (`qbo_ref_or_placeholder`) and the recognizer
    (`is_qbo_placeholder_ref`) derive the placeholder from here, so they can never
    drift apart.
    """
    return f"QBO-{qbo_id}"


def qbo_ref_or_placeholder(doc_number: Optional[str], qbo_id) -> str:
    """Derive the local document number from a QBO record.

    Returns the QBO DocNumber, or the ``QBO-<qbo_id>`` placeholder when QBO hasn't
    assigned one yet. This is the one mint the pull connectors call for
    Bill.bill_number / BillCredit.credit_number / Invoice.invoice_number /
    Expense.reference_number; `is_qbo_placeholder_ref` recognizes exactly what it
    mints. Pure; no I/O.
    """
    return doc_number or _qbo_placeholder(qbo_id)


def is_qbo_placeholder_ref(stored_value: Optional[str], qbo_id) -> bool:
    """True when ``stored_value`` is exactly the placeholder that
    `qbo_ref_or_placeholder` mints for a QBO record with no DocNumber yet.

    A stored placeholder is a not-yet-real number that SHOULD upgrade to a genuine
    doc_number once QBO supplies one. Pure; no I/O.
    """
    return stored_value == _qbo_placeholder(qbo_id)


def preserve_human_edited_ref(
    stored_value: Optional[str], incoming_value: Optional[str], qbo_id
) -> Optional[str]:
    """Decide the document number to write on a QBO re-pull UPDATE.

    Preserve the locally stored value (a possible human correction) UNLESS it is
    empty/None or the ``QBO-<qbo_id>`` placeholder — in which case take the incoming
    QBO-derived value (so an empty field fills in, and a placeholder upgrades to a
    real doc_number). Pure; no I/O.

    ACCEPTED RESIDUAL: if QBO's doc_number legitimately CHANGES after the initial
    sync AND the local value is neither empty nor the placeholder, the QBO change is
    ignored (rare) — the correct tradeoff, since we must never clobber a manual
    correction. This is the shared "rule of three" decision for Bill.bill_number,
    BillCredit.credit_number, Invoice.invoice_number, and Expense.reference_number.
    """
    if not stored_value or is_qbo_placeholder_ref(stored_value, qbo_id):
        return incoming_value
    return stored_value


def preserve_human_edited_name(
    stored_value: Optional[str], incoming_value: Optional[str]
) -> Optional[str]:
    """Reference-entity NAME sibling of `preserve_human_edited_ref`: keep a non-blank
    stored value on re-pull UPDATE, else take the incoming QBO-derived name. Pure; no I/O.

    Not `preserve_human_edited_ref` because names have no ``QBO-<id>`` placeholder —
    a ``qbo_id`` param would encode a dead upgrade branch.

    First caller is QBO Vendor pull: Vendor.Name is W-9/DBA curated locally, push to
    QBO is a stub, so DisplayName is guaranteed to diverge — curation must win.

    Blankness differs from `preserve_human_edited_ref` in this module: that helper
    treats blank as falsy (`not stored_value`), so whitespace-only stored values are
    preserved; here ``stored_value.strip()`` decides, so whitespace-only is replaced.

    ACCEPTED RESIDUAL: once a local name exists, a legitimate QBO-side rename is
    ignored (same tradeoff as document numbers — never clobber curation).
    """
    if stored_value and stored_value.strip():
        return stored_value
    return incoming_value


def _raise_if_inactive(
    active: Optional[bool],
    *,
    qbo_label: str,
    qbo_id,
    message: str,
) -> None:
    if active is False:
        raise ValueError(message)


def raise_if_inactive_unmapped(
    active: Optional[bool],
    *,
    qbo_label: str,
    qbo_id,
    target: str,
) -> None:
    """Refuse to bind a QBO record that is inactive and has no local mapping.

    The reference pulls query `Active IN (true, false)`, so deactivations and
    merges now reach the connectors. A record we ALREADY map must still update —
    that is the whole point of widening the query. But a record with no mapping
    must not be bound to a local row AT ALL, neither adopted nor created: QBO
    renames deactivated records with a " (deleted)" suffix, so binding one either
    mints a name-variant duplicate or (where the connector adopts by parsed
    NUMBER, which survives that suffix) hijacks and renames a LIVE local row.

    `active is False` deliberately, NOT a falsy check: None means QBO did not
    report the field and must never suppress.

    Raises ValueError, which `SyncOutcome.record_projection_error` classifies as
    a permanent skip that advances the watermark — the correct bucket, since an
    inactive record will not become bindable on a retry.

    CALL SITE ORDERING IS LOAD-BEARING and cannot be enforced here. Two
    sanctioned positions:

    1. Unmapped adopt/create path — AFTER the mapping lookup (no mapping found)
       and BEFORE any adopt-by-name/number lookup (so an unmapped inactive record
       cannot hijack a live row).

    2. Mapped happy path — do NOT call here; inactive mapped records still update.

    For the heal branch (mapping exists but bound entity reads empty), use
    `raise_if_inactive_orphaned_mapping` instead.
    """
    _raise_if_inactive(
        active,
        qbo_label=qbo_label,
        qbo_id=qbo_id,
        message=(
            f"{qbo_label} {qbo_id} is inactive in QBO and has no local "
            f"{target} mapping; skipping (deactivated records are never bound "
            f"to a local row)."
        ),
    )


def raise_if_inactive_orphaned_mapping(
    active: Optional[bool],
    *,
    qbo_label: str,
    qbo_id,
    target: str,
) -> None:
    """Heal-branch guard: refuse repoint/heal for an inactive QBO record with an orphaned mapping.

    CALL AFTER the mapping lookup AND AFTER the bound-entity read (mapping exists but
    local row is missing), BEFORE any re-resolve by number.
    """
    _raise_if_inactive(
        active,
        qbo_label=qbo_label,
        qbo_id=qbo_id,
        message=(
            f"{qbo_label} {qbo_id} is inactive in QBO; local {target} mapping "
            f"exists but its bound row is missing and cannot be safely repointed "
            f"for a deactivated record; skipping."
        ),
    )


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
        # DocNumber in QBO, but a human may correct it locally. Pull resolves the
        # conflict via preserve_human_edited_ref (keep the local edit unless empty
        # or the QBO-<id> placeholder) rather than clobbering it (KI-42 / U-027).
        "bill_number",
    ],
)


INVOICE = FieldOwnership(
    entity="Invoice",
    qbo_owned=[
        "customer_ref_value",
        "invoice_date",
        "due_date",
        "total_amount",
        "memo",                # CustomerMemo
        "line_items",          # managed via InvoiceLineItem connector
    ],
    app_owned=[
        "is_draft",
        # Invoice workflow state is entirely app-driven; QBO doesn't track
        # the invoice review/approval pipeline.
    ],
    both_editable=[
        # DocNumber in QBO; human-editable locally. Pull resolves the conflict via
        # preserve_human_edited_ref (keep the local edit unless empty or the QBO-<id>
        # placeholder), same as Bill/BillCredit/Expense (KI-42 / U-027 / U-034). Safe
        # because the lost-mapping adopt path no longer keys ONLY on the QBO-derived
        # number — a header fingerprint (total + txn_date + project) re-adopts a
        # human-renamed invoice, so a preserved divergent number no longer reintroduces
        # the phantom-duplicate bug.
        "invoice_number",
    ],
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
    both_editable=[
        # DocNumber in QBO (reference_number locally); human-editable. Pull resolves
        # the conflict via preserve_human_edited_ref, not by clobbering (KI-42 / U-024).
        "reference_number",
        # Expense LINE fields. A QBO amount-only line (Ramp card spend on the 58999
        # placeholder) carries no Qty/UnitPrice/MarkupInfo at all, so the pull defaults
        # 1 x amount / markup 0 — but only to FILL a hole. Once a value exists locally
        # (coding-queue backfill), the pull preserves it instead of clobbering, so
        # these are no longer qbo_owned (U-098). When QBO does supply a value it still
        # wins. See default_amount_only_line / preserve_stored_value in
        # purchase/connector/expense_line_item/business/service.py.
        "quantity",
        "rate",
        "markup",
    ],
)


VENDOR = FieldOwnership(
    entity="Vendor",
    qbo_owned=[
        # Billing-address projection lands on VendorAddress/Address (via
        # PhysicalAddressAddressConnector), not on dbo.Vendor columns.
        "bill_addr",
    ],
    app_owned=[
        "abbreviation",
        "vendor_type_id",
        "taxpayer_id",
        "is_draft",
        "is_deleted",
        "is_contract_labor",
        "notes",
        "hourly_rate",
        "markup",
        "track_compliance",
    ],
    both_editable=[
        # DisplayName in QBO, curated locally; the pull resolves via
        # preserve_human_edited_name (U-214 / audit P1-09).
        "name",
    ],
)


COST_CODE = FieldOwnership(
    entity="CostCode",
    qbo_owned=["number", "description"],
    app_owned=[],
    both_editable=["name"],
)


SUB_COST_CODE = FieldOwnership(
    entity="SubCostCode",
    qbo_owned=["number", "description", "cost_code_id"],
    app_owned=["aliases"],
    both_editable=["name"],
)


VENDOR_CREDIT = FieldOwnership(
    entity="BillCredit",   # local entity name; corresponds to QBO VendorCredit
    qbo_owned=[
        "vendor_id",
        "credit_date",         # TxnDate
        "total_amount",
        "memo",
        "line_items",
    ],
    app_owned=[
        "is_draft",
    ],
    both_editable=[
        # DocNumber in QBO; human-editable locally. Pull resolves the conflict via
        # preserve_human_edited_ref, not by clobbering (KI-42 / U-027).
        "credit_number",
    ],
)


# Lookup by entity name (both local name and QBO name for convenience).
_REGISTRY: Dict[str, FieldOwnership] = {
    # Local entity name → ownership rules.
    "Bill": BILL,
    "Invoice": INVOICE,
    "Expense": PURCHASE,
    "BillCredit": VENDOR_CREDIT,
    "Vendor": VENDOR,
    "CostCode": COST_CODE,
    "SubCostCode": SUB_COST_CODE,
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
