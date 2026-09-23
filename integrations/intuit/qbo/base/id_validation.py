"""Non-blank QBO `id` validation for external READ schemas (U-507 fix round 1).

WHY THIS EXISTS
---------------
Six watermarked pull families (bill, purchase, invoice, vendorcredit, account,
term) hold the watermark on a malformed QBO row purely by declaring
`id: str = Field(alias="Id")` REQUIRED on their external read schema. The client
parses a page as `[QboBill(**row) for row in page]`, so a bad row raises
ValidationError and aborts the WHOLE page before anything stages -- a harder hold
than an in-service guard, because `sync_from_qbo` never returns and
`WatermarkRun.commit` is never reached.

`required` is NOT that invariant. Pydantic's required rule rejects an ABSENT or
NULL `Id`; it accepts an EMPTY STRING. Worse, five of the six inherit
`str_strip_whitespace=True` from `_QboBaseModel`, so a whitespace-only `" "` was
silently stripped to `""` and accepted too. Such a row staged with QBO id `""`,
recorded SUCCESS, and ADVANCED the watermark past itself -- the exact silent-loss
shape U-507 exists to close, surviving in the six families the U-507 tally guard
declared safe. (vendorcredit does not strip, so it accepted `" "` VERBATIM: a
TRUTHY garbage id that sails through every downstream `if not qbo_id:` guard.)

WHY A SHARED VALIDATOR RATHER THAN SIX UPSERT-HEADER GUARDS
-----------------------------------------------------------
1. Enforcing at the SCHEMA preserves the mechanism the tally guard actually
   describes: the page aborts before anything stages. Six header guards inside
   the upserts would DOWNGRADE all six from "page aborts, commit unreachable" to
   "one row fails, the rest of the page stages and the hold marker is stamped" --
   a behavior change U-507 never asked for.
2. All six already carried a hand-copied `convert_id_to_string` before-validator
   doing the int->str coercion. This replaces six near-identical copies with one
   import, so the invariant has a single edit site and a greppable import list.

WHAT ABOUT `sync_token`, THE OTHER REQUIRED FIELD?
--------------------------------------------------
Deliberately NOT guarded. The invariant U-507 protects is IDENTITY. A falsy `id`
stages a row the filtered unique indexes (WHERE QboId IS NOT NULL...) let
duplicate on every subsequent pull and that the connectors can never repoint --
silent, permanent, self-compounding. Nothing dedupes, matches or repoints on
`sync_token`, and a blank one fails LOUDLY on the next push, where QBO rejects
the update outright. Guarding it would also mean touching the WRITE schemas
(`Qbo*Update` declares `sync_token`), which is outside this fix.

Not quite zero, though, and the honest statement is worth more than a tidy one:
`QboInvoiceService._upsert_invoice` short-circuits on
`existing.sync_token == qbo_invoice.sync_token` ("unchanged, skipping update"),
so an invoice that arrives with a blank SyncToken would CREATE correctly and then
freeze -- every later pull compares blank to blank and skips the update. That is
STALENESS of a correctly-identified row, not the loss-or-duplication class this
unit closes, and it needs QBO to return a record that has a real Id but no
SyncToken. Left for its own unit rather than folded in here.

USAGE (bind it, do not re-implement it):

    from integrations.intuit.qbo.base.id_validation import require_non_blank_qbo_id

    class QboBill(QboBillUpdate):
        _coerce_id = field_validator("id", mode="before")(require_non_blank_qbo_id)


    ⚠️ DEPLOY PREFLIGHT — scripts/migrations/u507_preflight_padded_qbo_ids.sql
    This validator does not only reject a blank id; it NORMALIZES a non-blank
    one, returning the stripped value. Five of the six families already stripped
    via `str_strip_whitespace`. VendorCredit did NOT, so it could historically
    store ' 900' verbatim -- and after this change that row is READ as '900',
    the exact (QboId, RealmId) lookup misses, and a second staging row plus a
    second downstream identity is minted. Deletion reconciliation normalizes
    both sides and will not clean the stale one up.

    Measured 0 padded rows across all six staging tables and all four dbo
    identity carriers on 2026-09-23 -- so this is a PREFLIGHT, not a migration
    (there is nothing to migrate). After deploy the case is unreachable, because
    ingestion strips. The only window is between that measurement and the
    deploy. Run the preflight; do not trust the number.
    """

# Python Standard Library Imports
from typing import Any

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.ids import normalize_qbo_id


# Message the six read schemas raise on a blank/whitespace-only QBO Id. Asserted
# by tests/test_u507_staging_skip_removal.py, so keep them in step.
BLANK_QBO_ID_ERROR = "QBO Id must be a non-blank string"


def require_non_blank_qbo_id(value: Any) -> Any:
    """`mode="before"` validator for a REQUIRED external-schema `id` field.

    Supersedes the per-family `convert_id_to_string` copies: same int->str
    coercion (QBO returns Id as either), but a blank or whitespace-only Id now
    RAISES instead of validating into a falsy PK.

    The `.strip()` (via `normalize_qbo_id`) is load-bearing, not cosmetic: a
    truthiness test alone would let vendorcredit's un-stripped `" "` through.
    Returns the NORMALIZED value so all six stage the same canonical id shape
    that `normalize_qbo_id` produces on the reconcile side.
    """
    if value is None:
        # Absent / NULL is already the required rule's job -- returning None here
        # yields pydantic's own "Field required" / "valid string" error rather
        # than masking it behind this one.
        return None
    normalized = normalize_qbo_id(value)
    if normalized is None:
        raise ValueError(BLANK_QBO_ID_ERROR)
    return normalized
