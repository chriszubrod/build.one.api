"""
Verify a dbo-native QBO identity against the qbo.* mapping table before an
outbound push trusts it as a QBO reference (U-276, round-4 review finding).

Background: as families are repointed off qbo.* staging (Phase 4), push
helpers start reading dbo-native QboId columns (e.g. dbo.Project.QboId)
directly instead of hopping through the qbo.*Customer mapping table. dbo
identity alone only guarantees dbo-internal uniqueness — Set*QboIdentity's
own theft-detection UPDATE enforces that no two local rows share an external
id at any moment — but it does NOT guarantee the mapping table has caught up
to the latest holder. The pull-side fast path (base/identity_fastpath.py)
treats a disagreement between the two as a "conflict" and HARD-STOPS —
recording a reconciliation issue and raising, never guessing which side is
right and never proceeding on either. An outbound push needs the same
discipline, or a stale/"stolen" dbo identity can push a live financial
document (Bill/Expense/Invoice) under the WRONG QBO customer's books.

NB (U-287): this module's original text said the pull side "falls back to the
mapping table as authoritative". That WAS true when this was written, and it
was the 2026-08-20 live-prod P0 — falling through let the legacy path
set_qbo_identity on a different row (theft-clearing the conflicted row's
identity) or mint a duplicate. Do not "restore symmetry" by softening the
push side back toward a fall-back; the discipline being mirrored is the hard
stop. Refusing to resolve (returning None here) is the push-side analogue.

NB (U-284v/U-297): two of the three wrappers are now ALSO used pull-side, as the
verify step of a dbo-first *reference resolver* (`BillLineItemConnector
._get_project_public_id`, `CustomerProjectConnector._resolve_parent_customer_id`).
There a None is advisory, not a veto: it means "don't trust the dbo-native
shortcut", and the caller falls through to the legacy qbo.* hop it was trying to
skip — it does NOT mean "stop". The hard stop above is the rule wherever there is
a WRITE to protect (the push helpers, the header identity fast path); a read-only
resolver has nothing to corrupt by taking the slower, already-trusted path. So do
not "restore symmetry" in that direction either — the two disciplines differ
because what is at stake differs.

NB (U-306): the verify engine used to run 2 reads per call (mapping-by-local-id,
then a second round trip for the mapped external row) and, when a family had no
mapping row at all, TRUSTED the dbo-stamped QboId unconditionally — LOCAL-SIDE
ONLY, blind to the mapping table already binding that same external id to a
DIFFERENT local row (booked as U-297's H1). Both are now closed by one change:
each family's `read_identity_check` callable is a single JOIN'd sproc
(`integrations/intuit/qbo/base/sql/identity_consistency_reads.sql`) that
returns the forward comparison AND the reverse-direction lookup in one round
trip — see `_verify_dbo_qbo_identity`'s docstring for the resulting logic. The
JOIN'd read makes the reverse check free (it already touches the staging
table), which is what makes closing H1 no longer "self-defeating" the way a
second round trip would have been.

NB (U-309): the four `verify_*_qbo_identity` wrappers above all read a family's
qbo.* mapping table — the right check while that table is still an
independently-writable second store this module exists to guard against
drifting from (see the top of this docstring). A family that has RETIRED its
mapping table (Wave 5's "trust dbo alone" plan, memory
`project_qbo_trust_dbo_identity_alone`, `docs/design/wave5.md`) has no second
store left to read, so it needs a structurally different check —
`verify_identity_dbo_only` below. See that function's own docstring for its
contract, why it needs no lock, and its current wiring status.
"""
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdentityCheckResult:
    """
    Result of a family's single JOIN'd identity-check read (U-306).

    `mapping_id` is None when this entity has no mapping row of its own yet —
    the ordinary not-fully-migrated-yet state. `forward_external_qbo_id` is
    the QboId the mapping row's own FK points at; only meaningful when
    `mapping_id` is set. `reverse_mapped_local_id` is the local id the mapping
    table binds the entity's `qbo_id` to, independent of whether the entity
    itself has a mapping row — it is what lets a forward-absent read still be
    verified instead of blindly trusted.
    """

    mapping_id: Optional[int]
    forward_external_qbo_id: Optional[str]
    reverse_mapped_local_id: Optional[int]


def _verify_dbo_qbo_identity(
    entity,
    *,
    entity_label: str,
    mapping_label: str,
    read_identity_check: Callable[..., IdentityCheckResult],
) -> Optional[str]:
    """
    Shared engine behind `verify_project_qbo_identity` / `verify_vendor_qbo_identity`
    (and any future family joining this pattern — see the module docstring
    for the discipline this enforces). Pure orchestration: `entity_label` and
    `mapping_label` only shape the log lines; `read_identity_check` is the
    family's own bound repo method (e.g.
    `customer_project_repo.read_identity_check`), called as
    `read_identity_check(local_id=entity.id, qbo_id=entity.qbo_id)` and
    returning one `IdentityCheckResult` from a single JOIN'd sproc — see
    `base/sql/identity_consistency_reads.sql`. Mirrors
    `base/identity_fastpath.py`'s own callback-based generalization of the
    sibling pull-side pattern — extend this one deliberately when a new
    family needs it rather than hand-copying another wrapper's body.

    Two independent checks, both answered by the one read, EITHER of which
    refuses:

    1. **Forward — this entity's own mapping row, if it has one** (`mapping_id`
       set): refuse if its external QboId disagrees with the dbo-stamped one.
       A disagreement means the mapping table still binds a DIFFERENT
       external id to this exact row.
    2. **Reverse — does the mapping table bind this entity's QboId to a
       DIFFERENT local row** (`reverse_mapped_local_id`), regardless of
       whether this entity has a forward mapping of its own — U-297's H1.
       That reverse binding is exactly what `identity_fastpath
       .resolve_mapping_state` checks on the pull side; before U-306 this
       engine could not see it without a second round trip, which made
       closing it here self-defeating for a reference resolver. The JOIN'd
       read makes the check free. Checking it unconditionally (not only when
       `mapping_id` is absent) matters when the qbo.* staging table itself
       holds more than one row for the same QboId (only possible when
       `RealmId` differs/is NULL, per the filtered UNIQUE(QboId, RealmId)
       index) — a forward mapping can agree with ONE of those staging rows
       while a DIFFERENT one is mapped elsewhere; codex round-2 review caught
       that the original nested-branch version skipped this check whenever a
       forward mapping already agreed. The reverse arm's own SQL already
       returns this entity's own local id (not a conflict) in the ordinary
       agreeing-and-unique case, so this check is a no-op there — the common
       (0-population) case stays unaffected.
    """
    if not entity or not entity.qbo_id:
        return None

    check = read_identity_check(local_id=entity.id, qbo_id=entity.qbo_id)

    if check.mapping_id is not None and check.forward_external_qbo_id and check.forward_external_qbo_id != entity.qbo_id:
        logger.error(
            f"{entity_label} {entity.id}'s dbo QboId ({entity.qbo_id}) disagrees with its own "
            f"{mapping_label} mapping's external QboId ({check.forward_external_qbo_id}) — refusing to "
            f"trust it."
        )
        return None

    if check.reverse_mapped_local_id is not None and check.reverse_mapped_local_id != entity.id:
        logger.error(
            f"{entity_label} {entity.id}'s dbo QboId ({entity.qbo_id}) is already bound by the "
            f"{mapping_label} mapping table to a DIFFERENT {entity_label} ({check.reverse_mapped_local_id}) — "
            f"refusing to trust it."
        )
        return None

    return entity.qbo_id


def verify_project_qbo_identity(
    project,
    *,
    customer_project_repo,
    qbo_customer_repo,
) -> Optional[str]:
    """
    Return `project.qbo_id` if it's safe to trust for an outbound push, else
    None. Safe means: the Project has no CustomerProject mapping row yet
    (the ordinary not-fully-migrated-yet state — nothing to disagree with),
    OR its mapping row's own QboCustomer external id matches `project.qbo_id`
    exactly. A mismatch means the mapping table still binds a DIFFERENT
    external customer to this Project — refuse rather than push under an
    unverified CustomerRef. When there is no mapping row of its own, also
    refuses if the mapping table already binds `project.qbo_id` to a
    DIFFERENT Project (U-297's H1, closed by U-306's JOIN'd read).

    `qbo_customer_repo` is accepted but unused — U-306 folded its job into
    `customer_project_repo.read_identity_check`'s single JOIN'd sproc. Kept
    in the signature so every existing caller's kwargs keep working unchanged.
    """
    return _verify_dbo_qbo_identity(
        project,
        entity_label="Project",
        mapping_label="CustomerProject",
        read_identity_check=customer_project_repo.read_identity_check,
    )


def verify_vendor_qbo_identity(
    vendor,
    *,
    vendor_vendor_repo,
    qbo_vendor_repo,
) -> Optional[str]:
    """
    Return `vendor.qbo_id` if it's safe to trust (for an outbound push, or for
    a pull-side reference resolution — see module docstring), else None.

    Safe means: the Vendor has no VendorVendor mapping row yet (the ordinary
    not-fully-migrated state — nothing to disagree with), OR its mapping
    row's own QboVendor external id matches `vendor.qbo_id` exactly. A
    mismatch means the mapping table still binds a DIFFERENT external vendor
    to this Vendor — refuse rather than trust an unverified VendorRef. When
    there is no mapping row of its own, also refuses if the mapping table
    already binds `vendor.qbo_id` to a DIFFERENT Vendor (U-297's H1, closed
    by U-306's JOIN'd read).

    `qbo_vendor_repo` is accepted but unused — U-306 folded its job into
    `vendor_vendor_repo.read_identity_check`'s single JOIN'd sproc. Kept
    in the signature so every existing caller's kwargs keep working unchanged.
    """
    return _verify_dbo_qbo_identity(
        vendor,
        entity_label="Vendor",
        mapping_label="VendorVendor",
        read_identity_check=vendor_vendor_repo.read_identity_check,
    )


def verify_bill_qbo_identity(
    bill,
    *,
    bill_bill_repo,
    qbo_bill_repo,
) -> Optional[str]:
    """
    Return `bill.qbo_id` if it's safe to trust for an outbox refresh mid-retry
    (U-301b: `outbox/business/worker.py::_refresh_bill`), else None.

    Safe means: the Bill has no BillBill mapping row yet (the ordinary
    not-fully-migrated state — nothing to disagree with), OR its mapping
    row's own QboBill external id matches `bill.qbo_id` exactly. A mismatch
    means the mapping table still binds a DIFFERENT external bill to this
    Bill — refuse rather than trust an unverified identity mid-retry. When
    there is no mapping row of its own, also refuses if the mapping table
    already binds `bill.qbo_id` to a DIFFERENT Bill (U-297's H1, closed by
    U-306's JOIN'd read).

    `qbo_bill_repo` is accepted but unused — U-306 folded its job into
    `bill_bill_repo.read_identity_check`'s single JOIN'd sproc. Kept in the
    signature so every existing caller's kwargs keep working unchanged.

    Caller note: this collapses two different reasons into one `None` —
    "bill.qbo_id is falsy" (nothing to check yet) and "mapping exists and
    disagrees" (a genuine conflict). `_refresh_bill` distinguishes them by
    checking `bill.qbo_id` truthiness itself *before* calling this, since the
    two cases need different handling (fall through to the legacy qbo.BillBill
    -> qbo.Bill lookup vs. hard-refuse and record a conflict issue).
    """
    return _verify_dbo_qbo_identity(
        bill,
        entity_label="Bill",
        mapping_label="BillBill",
        read_identity_check=bill_bill_repo.read_identity_check,
    )


def verify_customer_qbo_identity(
    customer,
    *,
    customer_customer_repo,
    qbo_customer_repo,
) -> Optional[str]:
    """
    Return `customer.qbo_id` if it's safe to trust (for a pull-side reference
    resolution — see module docstring), else None.

    Safe means: the Customer has no CustomerCustomer mapping row yet (the
    ordinary not-fully-migrated state — nothing to disagree with), OR its
    mapping row's own QboCustomer external id matches `customer.qbo_id`
    exactly. A mismatch means the mapping table still binds a DIFFERENT
    external customer to this Customer — refuse rather than trust an
    unverified parent CustomerRef.

    Added by U-297 for `CustomerProjectConnector._resolve_parent_customer_id`: a
    QBO job/sub-customer's ParentRef names its parent's QBO customer id, which
    that connector resolves to a local `dbo.Customer.Id` and writes to
    `dbo.Project.CustomerId`.

    When there is no mapping row of its own, also refuses if the mapping
    table already binds `customer.qbo_id` to a DIFFERENT Customer (U-297's
    H1, closed by U-306's JOIN'd read — was measurably zero-population at
    U-297 (2026-08-22): 0 stamped `dbo.Customer` rows lacked a
    `CustomerCustomer` row; revisit if that population count moves).

    `qbo_customer_repo` is accepted but unused — U-306 folded its job into
    `customer_customer_repo.read_identity_check`'s single JOIN'd sproc. Kept
    in the signature so every existing caller's kwargs keep working unchanged.
    """
    return _verify_dbo_qbo_identity(
        customer,
        entity_label="Customer",
        mapping_label="CustomerCustomer",
        read_identity_check=customer_customer_repo.read_identity_check,
    )


def verify_identity_dbo_only(
    entity,
    *,
    read_direct_by_qbo_identity: Callable[[Optional[str], Optional[str]], Any],
) -> Optional[str]:
    """
    Verify an already-resolved dbo-native QBO identity for a family that has
    NO qbo.* mapping table left to cross-check against (U-309 — the Wave-5
    "trust dbo alone" verify-side counterpart to
    `base/identity_fastpath.py::run_identity_fastpath_dbo_only`).

    Read `dbo.<Entity>` fresh by `(entity.qbo_id, entity.realm_id)` and return
    `entity.qbo_id` iff the fresh read's `.id` still equals `entity.id`, else
    `None`. This is exactly `run_identity_fastpath_dbo_only`'s unlocked direct
    read (`direct = read_direct_by_qbo_identity(...)`), extracted and given an
    id-match comparison so it answers a VERIFY question — "is this entity I
    already resolved still the current holder of its own identity?" — instead
    of that function's CREATE question, "who currently holds this identity,
    if anyone?"

    No lock. `run_identity_fastpath_dbo_only` takes one because a MISS there
    can lead to two concurrent callers both deciding to MINT a new row for the
    same identity — a real critical section to serialize. This function never
    mints anything, so there is no critical section to protect: the one race
    it could hit (the identity gets reassigned to a different row between the
    caller's original read and this verify call) is caught by the `.id`
    comparison itself, not by serializing around it — there's nothing to
    serialize, only a fact to re-check, exactly as `docs/design/wave5.md` §2
    lays out for Option A.

    `read_direct_by_qbo_identity` is the family's own direct-by-identity
    read (e.g. `<entity>_service.read_by_qbo_identity`), called as
    `read_direct_by_qbo_identity(entity.qbo_id, entity.realm_id)` — the same
    callable shape and parameter name `run_identity_fastpath_dbo_only`
    accepts, so a family that already has one wired for its create path can
    hand it straight to this one too.

    UNWIRED as of U-309 — no connector calls this yet. U-310/U-311/U-312 wire
    it into the 12 verify/reference-resolver call sites `docs/design/wave5.md`
    §4 enumerates (Customer/Project/Vendor), each replacing that family's
    `qbo.*`-mapping-table-reading `verify_*_qbo_identity` wrapper above once
    the family's own mapping table is retired.
    """
    if not entity or not entity.qbo_id:
        return None

    direct = read_direct_by_qbo_identity(entity.qbo_id, entity.realm_id)
    if direct is not None and direct.id == entity.id:
        return entity.qbo_id

    logger.error(
        f"{type(entity).__name__} {getattr(entity, 'id', None)}'s dbo QboId "
        f"({entity.qbo_id}) no longer resolves back to it on a fresh "
        f"dbo-only read (found local id: {getattr(direct, 'id', None)}) — "
        f"refusing to trust it."
    )
    return None
