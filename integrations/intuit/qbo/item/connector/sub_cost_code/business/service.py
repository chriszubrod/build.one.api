# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_name,
    raise_if_inactive_unmapped,
)
from integrations.intuit.qbo.base.identity_fastpath import (
    run_identity_fastpath_dbo_only,
    stamp_dbo_identity_with_lock,
)
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.reconciliation_recorder import record_duplicate_identity_conflict
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.cost_code.business.service import CostCodeService
from entities.sub_cost_code.business.service import SubCostCodeService
from entities.sub_cost_code.business.model import SubCostCode

logger = logging.getLogger(__name__)


class ItemSubCostCodeConnector:
    """
    Connector service for synchronization between QboItem and SubCostCode modules.
    Handles child QBO Items (with ParentRef) mapping to SubCostCode.

    U-307c: dbo-only identity resolution via `run_identity_fastpath_dbo_only` --
    no `qbo.ItemSubCostCode` mapping-table read/write of any kind (mirrors
    U-300b's `AttachableAttachmentConnector`). `dbo.SubCostCode.QboId`/`RealmId`
    (U-289) is the sole identity store. The parent-CostCode lookup is also
    dbo-native now (`CostCodeService.read_by_qbo_identity`), closing the
    standing TODO.md item -- structurally required once `qbo.Item`/
    `qbo.ItemCostCode` stop being written, since the old 2-hop lookup would
    have nothing left to find.
    """

    def __init__(
        self,
        sub_cost_code_service: Optional[SubCostCodeService] = None,
        cost_code_service: Optional[CostCodeService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the ItemSubCostCodeConnector."""
        self.sub_cost_code_service = sub_cost_code_service or SubCostCodeService()
        self.cost_code_service = cost_code_service or CostCodeService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def _match_sub_cost_code_by_number_and_parent(
        self, number: str, cost_code_id: Optional[int]
    ) -> Optional[SubCostCode]:
        """Resolve a SubCostCode by number scoped to its parent CostCode."""
        if cost_code_id is None:
            return None
        siblings = self.sub_cost_code_service.repo.read_by_cost_code_id(cost_code_id)
        matches = [s for s in siblings if s.number == number]
        if not matches:
            return None
        if len(matches) > 1:
            matches.sort(key=lambda s: coerce_id(s.id))
            logger.warning(
                f"Multiple SubCostCodes numbered {number!r} under CostCode {cost_code_id}; "
                f"using lowest id {matches[0].id}"
            )
        return matches[0]

    def sync_from_qbo_item(self, qbo_item: QboItem) -> SubCostCode:
        """
        Sync data from QboItem to SubCostCode module.

        This method:
        1. Parses the Item.Name to extract number and name
        2. Finds the parent CostCode via ParentRef, resolved dbo-native
        3. Resolves identity dbo-only against dbo.SubCostCode.QboId/RealmId
        4. Creates or updates the SubCostCode accordingly

        Args:
            qbo_item: QboItem record (must be a child item with ParentRef).
                U-307c: this is a transient (never persisted) object built by
                `QboItemService._upsert_item` -- `.id` is always None; use
                `.qbo_id` for identity/logging.

        Returns:
            SubCostCode: The synced SubCostCode record

        Raises:
            ValueError: If the item has no ParentRef (is not a child item)
            ValueError: If the parent CostCode is not yet dbo-stamped
        """
        if qbo_item.is_parent:
            raise ValueError(f"QboItem {qbo_item.qbo_id} has no ParentRef and is not a child item")

        # Parse the name to get number and name
        number, name = qbo_item.parse_name()
        description = qbo_item.description

        # Find the parent CostCode dbo-natively by its own QBO identity
        # (U-307c). Preserves the existing "sync parent items before child
        # items" ordering contract already enforced by
        # scripts/sync_qbo_item.py::sync_qbo_to_local (parents processed
        # first in a separate loop) -- in steady state the parent's
        # dbo.CostCode.QboId is already stamped by the time a child is
        # processed in the same batch.
        parent_qbo_id = qbo_item.parent_ref_value
        parent_cost_code = self.cost_code_service.read_by_qbo_identity(
            parent_qbo_id, realm_id=qbo_item.realm_id
        )
        if not parent_cost_code:
            raise ValueError(
                f"Parent CostCode for QboItem qbo_id={qbo_item.qbo_id} "
                f"(ParentRef={parent_qbo_id}) not yet dbo-stamped"
            )
        cost_code_id = coerce_id(parent_cost_code.id)

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_item.qbo_id,
            realm_id=qbo_item.realm_id,
            entity_label="SubCostCode",
            external_label="QboItem",
            lock_resource_label="SubCostCode",
            read_direct_by_qbo_identity=self.sub_cost_code_service.read_by_qbo_identity,
            apply_fields=lambda entity: self._apply_sub_cost_code_fields_and_sync(
                entity,
                number=number,
                incoming_name=name,
                description=description,
                cost_code_id=cost_code_id,
            ),
            resolve_candidate=lambda: self._resolve_sub_cost_code_candidate(
                qbo_item,
                number=number,
                name=name,
                description=description,
                cost_code_id=cost_code_id,
            ),
            stamp_identity=lambda candidate: self._stamp_sub_cost_code_identity(
                candidate, qbo_item, name=name, description=description, cost_code_id=cost_code_id,
            ),
        )
        if outcome.entity is None:
            # U-316: no longer race-reachable (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a
            # directly-invoked falsy qbo_item.qbo_id (this public method has
            # no guard of its own; pinned by test_sub_cost_code_no_qbo_id_
            # raises). The production pull path already guards this
            # upstream via QboItemService._upsert_item.
            raise RuntimeError(
                f"Failed to resolve SubCostCode for QboItem qbo_id={qbo_item.qbo_id} "
                f"via the dbo-only identity fast path"
            )

        # QboActive is a dbo-native mirror (U-275) that must stay current
        # every sync tick even when identity itself hasn't changed (an item
        # can be deactivated in QBO without its QboId/RealmId moving) --
        # unlike CostCode, SubCostCode carries such a mirror. QboId/RealmId
        # are passed as None so the sproc's own CASE WHEN guards leave them
        # untouched (no redundant re-stamp, no theft-detection re-trigger);
        # only QboActive's CASE WHEN branch fires. On the MISS/create path
        # this is a harmless redundant re-set of the value _stamp_sub_cost_
        # code_identity already stamped -- kept unconditional to match the
        # pre-U-307c fast path's own identical shape (it refreshed Active
        # uniformly for every hit=True outcome, including its own
        # MISSING-self-heal branch).
        self.sub_cost_code_service.repo.set_qbo_identity(
            id=coerce_id(outcome.entity.id),
            qbo_id=None,
            realm_id=None,
            active=qbo_item.active,
        )
        return outcome.entity

    def _resolve_sub_cost_code_candidate(
        self,
        qbo_item: QboItem,
        *,
        number: str,
        name: Optional[str],
        description,
        cost_code_id: int,
    ) -> SubCostCode:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-307c):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once
        a genuine miss is confirmed (no dbo.SubCostCode currently holds this
        identity, including the re-read under lock). Reproduces today's
        adopt-by-number-then-create logic using `read_by_number` directly --
        no mapping-table check, since there is no mapping table left.
        """
        raise_if_inactive_unmapped(
            qbo_item.active, qbo_label="QboItem", qbo_id=qbo_item.qbo_id, target="SubCostCode"
        )

        existing = self._match_sub_cost_code_by_number_and_parent(number, cost_code_id)
        if existing is None:
            logger.info(
                f"Creating new SubCostCode from QboItem qbo_id={qbo_item.qbo_id}: "
                f"number={number}, name={name}"
            )
            return self.sub_cost_code_service.create(
                number=number, name=name, description=description, cost_code_id=cost_code_id,
            )

        # The number-matched row must be re-checked for an existing, DIFFERENT
        # (QboId, RealmId) before being returned as the candidate -- the
        # dbo-only equivalent of today's mapping-table duplicate check
        # (Decision 2). `stamp_identity`'s Set<Entity>QboIdentity theft-clear
        # only protects the INCOMING (qbo_id, realm_id) pair's uniqueness, not
        # this row's PRIOR identity -- it would not stop a silent re-point
        # here. Checking QboId alone would miss a same-QboId-different-realm
        # collision (QBO ids are only unique WITHIN a realm) -- this must
        # match _stamp_sub_cost_code_identity's own (qbo_id AND realm_id)
        # check exactly, or a cross-realm row would get its fields overwritten
        # here before that later guard ever runs (Codex round-1 P1).
        existing_qbo_id = getattr(existing, "qbo_id", None)
        if existing_qbo_id and not (
            existing_qbo_id == qbo_item.qbo_id
            and (getattr(existing, "realm_id", None) or "") == (qbo_item.realm_id or "")
        ):
            self._record_duplicate_qbo_item_issue(
                qbo_item=qbo_item,
                local_sub_cost_code=existing,
                existing_qbo_id=existing_qbo_id,
            )
            raise ValueError(
                f"QboItem qbo_id={qbo_item.qbo_id} realm_id={qbo_item.realm_id} number-matches "
                f"local SubCostCode {existing.id} which already carries a DIFFERENT identity "
                f"(QboId={existing_qbo_id}, RealmId={getattr(existing, 'realm_id', None)})."
            )

        logger.info(
            f"Binding existing local SubCostCode {existing.id} ({number}) to QboItem "
            f"qbo_id={qbo_item.qbo_id} by number match"
        )
        # Field write deliberately deferred to _stamp_sub_cost_code_identity,
        # which applies it atomically with the identity stamp under the
        # candidate's own lock (Codex round-2 P1) -- see that method's docstring.
        return existing

    def _stamp_sub_cost_code_identity(
        self,
        candidate: SubCostCode,
        qbo_item: QboItem,
        *,
        name: Optional[str],
        description,
        cost_code_id: int,
    ) -> Optional[SubCostCode]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-307c),
        delegating the row-scoped lock + theft-guard + write sequence to the
        shared `stamp_dbo_identity_with_lock` (U-328/U-331 —
        `docs/design/stamp-lock-helper.md`) — see that function's own
        docstring for why a SECOND lock, keyed on the CANDIDATE's
        sub_cost_code_id, is needed here: `resolve_candidate` binds by NUMBER
        (a side-channel business key), so two different QboItems (different
        qbo_ids — no contention on the qbo_id-keyed lock upstream) could
        number-match onto the SAME local SubCostCode concurrently.

        `apply_fields` writes the QBO-derived name/description/cost_code_id
        (U-219: name is raw, bypassing `preserve_human_edited_name`) and now
        feeds `update_by_id`'s return value into the shared helper's own
        None-guard — a previously DISCARDED return value, so a concurrent
        ROWVERSION race on this write used to succeed silently; it now
        raises and holds for retry (D1, U-328/U-331 — the one behavior
        change this migration makes here). `on_conflict` reuses
        `_record_duplicate_qbo_item_issue` (below) — the SAME
        `duplicate_qbo_item` DriftType `resolve_candidate`'s own number-match
        guard already records, since both are the identical comparison on
        the same row (a number-matched candidate already carrying a
        DIFFERENT identity); mirrors Customer/Project/Vendor's precedent of
        one shared recorder across both call sites (Decision 2).
        """

        def _apply_fields(c: SubCostCode) -> Optional[SubCostCode]:
            c.name = name
            c.description = description
            c.cost_code_id = cost_code_id
            return self.sub_cost_code_service.repo.update_by_id(c)

        candidate_id = coerce_id(candidate.id)
        return stamp_dbo_identity_with_lock(
            candidate_id=candidate_id,
            entity_label="SubCostCode",
            qbo_id=qbo_item.qbo_id,
            realm_id=qbo_item.realm_id,
            read_by_id=self.sub_cost_code_service.read_by_id,
            apply_fields=_apply_fields,
            write_identity=lambda c: self.sub_cost_code_service.repo.set_qbo_identity(
                id=c.id, qbo_id=qbo_item.qbo_id, realm_id=qbo_item.realm_id, active=qbo_item.active,
            ),
            on_conflict=lambda c: self._record_duplicate_qbo_item_issue(
                qbo_item=qbo_item, local_sub_cost_code=c, existing_qbo_id=c.qbo_id,
            ),
        )

    def _apply_sub_cost_code_fields_and_sync(
        self,
        sub_cost_code: SubCostCode,
        *,
        number: str,
        incoming_name: Optional[str],
        description,
        cost_code_id: int,
    ) -> SubCostCode:
        """
        Write QboItem-derived fields onto an existing SubCostCode and persist.
        The single field-write path for a dbo-identity hit (direct or
        race-discovered) -- the pre-U-307c heal-in-place repoint path that
        also called this is gone (nothing left to heal).
        """
        sub_cost_code.number = number
        sub_cost_code.name = preserve_human_edited_name(sub_cost_code.name, incoming_name)
        sub_cost_code.description = description
        sub_cost_code.cost_code_id = cost_code_id
        return self.sub_cost_code_service.repo.update_by_id(sub_cost_code)

    def _record_duplicate_qbo_item_issue(
        self,
        *,
        qbo_item: QboItem,
        local_sub_cost_code: SubCostCode,
        existing_qbo_id: str,
    ) -> None:
        """
        Number-match duplicate. Reuses `duplicate_qbo_item` — same DriftType as
        `resolve_candidate`'s guard and ItemCostCodeConnector's stamp-time check.
        """
        details = (
            f"Duplicate QBO item detected. QboItem qbo_id={qbo_item.qbo_id} "
            f"(Name='{qbo_item.name}') number-matches local SubCostCode {local_sub_cost_code.id} "
            f"which already carries a DIFFERENT QboId {existing_qbo_id}. Resolve by merging or "
            f"renaming one of the QBO items."
        )
        record_duplicate_identity_conflict(
            self.reconciliation_repo,
            drift_type="duplicate_qbo_item",
            entity_type="SubCostCode",
            entity_public_id=str(local_sub_cost_code.public_id) if local_sub_cost_code.public_id else None,
            qbo_id=str(qbo_item.qbo_id) if qbo_item.qbo_id else None,
            realm_id=qbo_item.realm_id or "",
            details=details,
        )
