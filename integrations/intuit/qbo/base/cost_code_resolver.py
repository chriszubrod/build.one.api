"""
Shared QBO Item <-> dbo cost-code identity resolver (U-307a).

KEY FACT this module exists to act on: dbo.CostCode.QboId and dbo.SubCostCode.QboId
are already 100%-parity, live-maintained dbo-native identities (stamped by U-289's
Item Phase-4 repoint) -- ``entities/{sub_cost_code,cost_code}/business/service.py``'s
``read_by_qbo_identity`` already exist and already back ``ReadSubCostCodeByQboIdAndRealmId``
/ ``ReadCostCodeByQboIdAndRealmId``, but had zero callers before this unit. Every
consumer that previously hopped ``qbo.Item -> qbo.ItemSubCostCode``/``qbo.ItemCostCode``
staging tables to resolve a QBO Item reference now calls into this module instead --
one place, not ~14 hand-copied chains. Resolution is always by ID; never by parsing an
Item's display name/hierarchy.

Forward (QBO Item ref -> dbo): ``resolve_dbo_sub_cost_code`` / ``resolve_dbo_cost_code_direct``.
Each tries its dbo-native column first, falling back to the legacy
``qbo.Item -> qbo.ItemSubCostCode``/``qbo.ItemCostCode`` staging hop on a miss -- the
same "dbo-native first, legacy hop on miss" shape as every other reference-resolver in
this program (project ref / vendor ref, U-283b / U-284v), so a QBO Item created between
its QboId stamp and the dbo backfill still resolves. Neither function raises on a
dangling/unresolvable link at any hop -- both degrade to ``None`` so a caller falls back
to its own "no SubCostCode" / "Uncoded" handling, exactly as the staging-chain callers
did before this repoint.

The two forward functions are independent (SubCostCode-level vs. CostCode-level-only,
for a QBO Item with no SubCostCode granularity, e.g. "Initial Deposit"). A caller that
wants the two-tier "prefer SubCostCode-level, fall back to CostCode-level" behavior
composes them itself -- only the caller knows whether its SubCostCode-level result was
actually usable (e.g. ``QboInvoiceService``'s numeric-cost-code-only filter, which must
keep trying the CostCode-level fallback even when a SubCostCode-level mapping ROW
exists but resolves to a non-numeric QBO-admin pseudo-code like "Hours"/"Sales").

Reverse (dbo SubCostCode -> QBO Item ref): ``resolve_qbo_item_ref``, built now per the
Gate-1 decision to build both directions together, resolving straight off
``dbo.SubCostCode.QboId`` with no ``qbo.Item`` hop. Not yet called by any consumer --
U-307b repoints the 3 push consumers onto it as its own unit.

Every dependency (the dbo-native services, the legacy-hop repos) is an optional
constructor-style parameter defaulting to a real instance -- matching every connector
in this codebase's DI convention -- so callers (and their tests) can inject fakes
without this module ever reaching for a real DB connection on its own. A caller that
processes many lines sharing a QBO Item reference is responsible for its own result
caching (e.g. ``QboInvoiceService`` amortizes across an entire project's draw rollup);
this module does no caching of its own.
"""

# Python Standard Library Imports
import logging
from dataclasses import dataclass
from typing import Optional

# Local Imports
from entities.sub_cost_code.business.model import SubCostCode
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.cost_code.business.model import CostCode
from entities.cost_code.business.service import CostCodeService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QboItemRef:
    """A QBO Item reference resolved from a dbo SubCostCode (the reverse direction's
    return shape) -- deliberately not one of the per-integration ``QboReferenceType``
    pydantic models (bill/purchase/vendorcredit/invoice each define their own,
    external-schema-local by this codebase's convention); callers adapt this into
    their own integration's type."""
    value: str
    name: Optional[str]


def resolve_dbo_sub_cost_code(
    qbo_item_ref_value: Optional[str],
    realm_id: Optional[str] = None,
    *,
    sub_cost_code_service: Optional[SubCostCodeService] = None,
    qbo_item_repo=None,
    item_sub_cost_code_repo=None,
) -> Optional[SubCostCode]:
    """SubCostCode-level resolution for a QBO Item reference (e.g.
    ``QboBillLine.item_ref_value``). dbo-native first (``SubCostCode.QboId``); legacy
    ``qbo.Item -> qbo.ItemSubCostCode`` hop on a miss. ``None`` -- never raises -- on no
    ref, no match, or a dangling link at any hop.

    ``qbo_item_repo`` / ``item_sub_cost_code_repo`` only back the legacy-hop fallback --
    a caller with no interest in injecting them (the overwhelming majority, since the
    dbo-native path is the one at 100% live parity) can omit both."""
    if not qbo_item_ref_value:
        return None

    scc_service = sub_cost_code_service or SubCostCodeService()
    direct = scc_service.read_by_qbo_identity(qbo_item_ref_value, realm_id)
    if direct is not None:
        return direct

    return _legacy_resolve_sub_cost_code(
        qbo_item_ref_value,
        sub_cost_code_service=scc_service,
        qbo_item_repo=qbo_item_repo,
        item_sub_cost_code_repo=item_sub_cost_code_repo,
    )


def resolve_dbo_cost_code_direct(
    qbo_item_ref_value: Optional[str],
    realm_id: Optional[str] = None,
    *,
    cost_code_service: Optional[CostCodeService] = None,
    qbo_item_repo=None,
    item_cost_code_repo=None,
) -> Optional[CostCode]:
    """CostCode-LEVEL-ONLY resolution, for a QBO Item with no SubCostCode granularity
    mapped straight to a CostCode. dbo-native first (``CostCode.QboId``); legacy
    ``qbo.Item -> qbo.ItemCostCode`` hop on a miss. ``None`` -- never raises -- on no
    ref, no match, or a dangling link at any hop. Independent of
    ``resolve_dbo_sub_cost_code`` -- see module docstring for why."""
    if not qbo_item_ref_value:
        return None

    cc_service = cost_code_service or CostCodeService()
    direct = cc_service.read_by_qbo_identity(qbo_item_ref_value, realm_id)
    if direct is not None:
        return direct

    return _legacy_resolve_cost_code(
        qbo_item_ref_value,
        cost_code_service=cc_service,
        qbo_item_repo=qbo_item_repo,
        item_cost_code_repo=item_cost_code_repo,
    )


def resolve_qbo_item_ref(
    sub_cost_code_id: Optional[int],
    realm_id: Optional[str] = None,
    *,
    sub_cost_code_service: Optional[SubCostCodeService] = None,
) -> Optional[QboItemRef]:
    """Reverse: dbo ``SubCostCode.Id`` -> QBO Item reference, resolved directly off
    ``dbo.SubCostCode.QboId`` -- no ``qbo.Item`` hop. Built for U-307b's push repoint;
    not yet called by any consumer in this unit. ``realm_id``, when given, must match
    the SubCostCode's own stamped realm or the reference is treated as unresolved --
    including a SubCostCode with a QboId but a NULL RealmId (a partial/legacy stamp),
    which is not trusted as a match for any specific requested realm rather than
    silently passed through (a cross-realm or incompletely-stamped SubCostCode is
    not a valid Item ref for this realm's push)."""
    if not sub_cost_code_id:
        return None

    scc_service = sub_cost_code_service or SubCostCodeService()
    sub_cost_code = scc_service.read_by_id(sub_cost_code_id)
    if not sub_cost_code or not sub_cost_code.qbo_id:
        return None
    if realm_id and sub_cost_code.realm_id != realm_id:
        return None

    return QboItemRef(value=sub_cost_code.qbo_id, name=sub_cost_code.name)


# ---------------------------------------------------------------------------
# Legacy qbo.Item staging hop -- fallback only, reached on a dbo-native miss.
# Deliberately realm-blind, matching every hand-copied chain this replaces
# (none of them threaded realm_id through QboItemRepository.read_by_qbo_id) --
# preserving that exactly keeps this a pure repoint, not a behavior change.
# ---------------------------------------------------------------------------

def _legacy_resolve_sub_cost_code(
    qbo_item_ref_value: str,
    *,
    sub_cost_code_service: SubCostCodeService,
    qbo_item_repo=None,
    item_sub_cost_code_repo=None,
) -> Optional[SubCostCode]:
    from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
    from integrations.intuit.qbo.item.connector.sub_cost_code.persistence.repo import (
        ItemSubCostCodeRepository,
    )

    qbo_item = (qbo_item_repo or QboItemRepository()).read_by_qbo_id(qbo_item_ref_value)
    if not qbo_item:
        return None
    mapping = (item_sub_cost_code_repo or ItemSubCostCodeRepository()).read_by_qbo_item_id(qbo_item.id)
    if not mapping:
        return None
    return sub_cost_code_service.read_by_id(mapping.sub_cost_code_id)


def _legacy_resolve_cost_code(
    qbo_item_ref_value: str,
    *,
    cost_code_service: CostCodeService,
    qbo_item_repo=None,
    item_cost_code_repo=None,
) -> Optional[CostCode]:
    from integrations.intuit.qbo.item.persistence.repo import QboItemRepository
    from integrations.intuit.qbo.item.connector.cost_code.persistence.repo import (
        ItemCostCodeRepository,
    )

    qbo_item = (qbo_item_repo or QboItemRepository()).read_by_qbo_id(qbo_item_ref_value)
    if not qbo_item:
        return None
    mapping = (item_cost_code_repo or ItemCostCodeRepository()).read_by_qbo_item_id(qbo_item.id)
    if not mapping:
        return None
    return cost_code_service.read_by_id(mapping.cost_code_id)
