"""
Shared QBO push-side reference resolvers (U-797c5a round 2, R2).

Bill's push (BillBillConnector) and BillCredit's push (VendorCreditBillCreditConnector) resolve
the same references: the vendor, the project's CustomerRef, the SubCostCode's ItemRef, and the
AP account. The bodies live here. Each connector keeps a thin `_get_qbo_*_ref` wrapper that
turns the neutral (value, name) into its own QboReferenceType, because tests patch those
wrappers by name.

Breadcrumb: the vendor and customer resolvers were hand-copied in both connectors. They are
shared now (U-005[reuse] / U-284v inventory, TODO.md). The pull-side resolvers are NOT shared.
"""

from typing import Any, Callable, Optional, Tuple

from integrations.intuit.qbo.account.business.service import AP_ACCOUNT_TYPE, select_ap_account
from integrations.intuit.qbo.base.cost_code_resolver import resolve_qbo_item_ref
from integrations.intuit.qbo.base.identity_consistency import verify_identity_dbo_only

NameValue = Tuple[str, Optional[str]]


def resolve_verified_dbo_ref(
    entity_id: Optional[int],
    *,
    read_by_id: Callable[[int], Any],
    read_by_qbo_identity: Callable[[Optional[str], Optional[str]], Any],
    require_qbo_id: bool = False,
    log=None,
    not_found_msg: Optional[str] = None,
    unverified_msg: Optional[str] = None,
    missing_qbo_id_msg: Optional[str] = None,
) -> Optional[NameValue]:
    """
    (QboId, Name) for a local vendor or project, re-verified against a fresh dbo-only read.
    None when the entity is absent, has no QboId (if required), or no longer resolves back to
    itself. Messages are optional `str.format` templates with `{entity_id}`.
    """
    if not entity_id:
        return None
    entity = read_by_id(entity_id)
    if not entity:
        if log is not None and not_found_msg:
            log.warning(not_found_msg.format(entity_id=entity_id))
        return None
    if require_qbo_id and not entity.qbo_id:
        if log is not None and missing_qbo_id_msg:
            log.debug(missing_qbo_id_msg.format(entity_id=entity_id))
        return None
    verified_qbo_id = verify_identity_dbo_only(entity, read_direct_by_qbo_identity=read_by_qbo_identity)
    if verified_qbo_id:
        return (verified_qbo_id, entity.name)
    if log is not None and unverified_msg:
        log.warning(unverified_msg.format(entity_id=entity_id))
    return None


def resolve_item_ref(
    sub_cost_code_id: int,
    realm_id: Optional[str],
    *,
    sub_cost_code_service,
    log=None,
) -> Optional[NameValue]:
    """(QboId, Name) for a SubCostCode via cost_code_resolver.resolve_qbo_item_ref (holding raises there)."""
    item_ref = resolve_qbo_item_ref(sub_cost_code_id, realm_id, sub_cost_code_service=sub_cost_code_service)
    if item_ref is None:
        if log is not None:
            log.warning(f"No QBO Item mapping resolved for sub_cost_code_id: {sub_cost_code_id}")
        return None
    return (item_ref.value, item_ref.name)


def resolve_ap_account_ref(
    realm_id: str,
    *,
    company_service,
    qbo_account_repo,
    log=None,
) -> Optional[NameValue]:
    """
    AP account for a realm: the cached dbo.Company AP fields first (U-281), then the live
    qbo.Account scan when the cache is empty for this realm (U-296). A transient cache read
    failure falls through to the scan rather than failing the push.
    """
    try:
        company = company_service.read_by_realm_id(realm_id)
    except Exception as e:
        if log is not None:
            log.warning(f"Failed to read cached AP account for realm_id {realm_id}: {e}")
        company = None

    if company and company.ap_account_qbo_id:
        return (company.ap_account_qbo_id, company.ap_account_name)

    accounts = qbo_account_repo.read_by_realm_id_and_account_type(realm_id, AP_ACCOUNT_TYPE)
    ap_account = select_ap_account(accounts)
    if ap_account:
        return (ap_account.qbo_id, ap_account.name)

    if log is not None:
        log.warning(f"No Accounts Payable account found for realm_id: {realm_id}")
    return None
