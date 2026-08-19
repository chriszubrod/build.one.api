"""
Verify a dbo-native QBO identity against the qbo.* mapping table before an
outbound push trusts it as a QBO reference (U-276, round-4 review finding).

Background: as families are repointed off qbo.* staging (Phase 4), push
helpers start reading dbo-native QboId columns (e.g. dbo.Project.QboId)
directly instead of hopping through the qbo.*Customer mapping table. dbo
identity alone only guarantees dbo-internal uniqueness — Set*QboIdentity's
own theft-detection UPDATE enforces that no two local rows share an external
id at any moment — but it does NOT guarantee the mapping table has caught up
to the latest holder. CustomerProjectConnector/CustomerCustomerConnector's
own pull-side fast path treats a disagreement between the two as a
"conflict" and falls back to the mapping table as authoritative (never
guessing which side is right); an outbound push needs the same discipline,
or a stale/"stolen" dbo identity can push a live financial document (Bill/
Expense/Invoice) under the WRONG QBO customer's books.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


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
    if not project or not project.qbo_id:
        return None
    mapping = customer_project_repo.read_by_project_id(project.id)
    if not mapping:
        return project.qbo_id
    mapped_qbo_customer = qbo_customer_repo.read_by_id(mapping.qbo_customer_id)
    if mapped_qbo_customer and mapped_qbo_customer.qbo_id and mapped_qbo_customer.qbo_id != project.qbo_id:
        logger.error(
            f"Project {project.id}'s dbo QboId ({project.qbo_id}) disagrees with its own "
            f"CustomerProject mapping's QboCustomer ({mapped_qbo_customer.qbo_id}) — refusing "
            f"to push under a possibly-wrong CustomerRef."
        )
        return None
    return project.qbo_id
