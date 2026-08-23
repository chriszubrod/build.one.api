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
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def _verify_dbo_qbo_identity(
    entity,
    *,
    entity_label: str,
    mapping_label: str,
    read_mapping_by_local_id,
    read_external_by_mapped_id,
    mapping_external_id_attr: str,
) -> Optional[str]:
    """
    Shared engine behind `verify_project_qbo_identity` / `verify_vendor_qbo_identity`
    (and any future family joining this pattern — see the module docstring
    for the discipline this enforces). Pure orchestration: `entity_label` and
    `mapping_label` only shape the log line; `read_mapping_by_local_id` and
    `read_external_by_mapped_id` are the family's own bound repo methods
    (e.g. `customer_project_repo.read_by_project_id`), and
    `mapping_external_id_attr` names the mapping row's FK to the external
    staging table (e.g. `"qbo_customer_id"`). Mirrors
    `base/identity_fastpath.py`'s own callback-based generalization of the
    sibling pull-side pattern — extend this one deliberately when a new
    family needs it rather than hand-copying another wrapper's body.

    KNOWN RESIDUAL, true of EVERY binding (booked in TODO.md as U-297's H1,
    which points here): this check is LOCAL-SIDE ONLY. It asks "does this dbo
    row's own mapping agree?" and TRUSTS when the row has no mapping at all
    (see the `if not mapping` branch below). It therefore cannot see the
    opposite direction — the mapping table still binding this external id to a
    DIFFERENT local row, which is what `identity_fastpath.resolve_mapping_state`
    checks. Closing that here is self-defeating for a reference resolver:
    reading the qbo-side requires the staging hop the resolver exists to skip,
    so a both-directions verify would cost strictly more than the legacy path it
    replaces. Each wrapper documents its own measured blind population.
    """
    if not entity or not entity.qbo_id:
        return None
    mapping = read_mapping_by_local_id(entity.id)
    if not mapping:
        return entity.qbo_id
    mapped_external = read_external_by_mapped_id(getattr(mapping, mapping_external_id_attr))
    if mapped_external and mapped_external.qbo_id and mapped_external.qbo_id != entity.qbo_id:
        logger.error(
            f"{entity_label} {entity.id}'s dbo QboId ({entity.qbo_id}) disagrees with its own "
            f"{mapping_label} mapping's external QboId ({mapped_external.qbo_id}) — refusing to "
            f"trust it."
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
    unverified CustomerRef.
    """
    return _verify_dbo_qbo_identity(
        project,
        entity_label="Project",
        mapping_label="CustomerProject",
        read_mapping_by_local_id=customer_project_repo.read_by_project_id,
        read_external_by_mapped_id=qbo_customer_repo.read_by_id,
        mapping_external_id_attr="qbo_customer_id",
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
    to this Vendor — refuse rather than trust an unverified VendorRef.
    """
    return _verify_dbo_qbo_identity(
        vendor,
        entity_label="Vendor",
        mapping_label="VendorVendor",
        read_mapping_by_local_id=vendor_vendor_repo.read_by_vendor_id,
        read_external_by_mapped_id=qbo_vendor_repo.read_by_id,
        mapping_external_id_attr="qbo_vendor_id",
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
    Bill — refuse rather than trust an unverified identity mid-retry.

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
        read_mapping_by_local_id=bill_bill_repo.read_by_bill_id,
        read_external_by_mapped_id=qbo_bill_repo.read_by_id,
        mapping_external_id_attr="qbo_bill_id",
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

    KNOWN RESIDUAL: the engine is LOCAL-SIDE ONLY — `_verify_dbo_qbo_identity`
    owns that caveat for all three wrappers (TODO.md, U-297's H1). Customer's
    blind population is measurably ZERO (0 stamped `dbo.Customer` rows lack a
    `CustomerCustomer` row, live 2026-08-22); revisit if that stops being true.
    """
    return _verify_dbo_qbo_identity(
        customer,
        entity_label="Customer",
        mapping_label="CustomerCustomer",
        read_mapping_by_local_id=customer_customer_repo.read_by_customer_id,
        read_external_by_mapped_id=qbo_customer_repo.read_by_id,
        mapping_external_id_attr="qbo_customer_id",
    )
