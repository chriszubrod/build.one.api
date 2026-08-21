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
