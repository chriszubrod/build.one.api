# Python Standard Library Imports
import logging
from typing import Optional

# Third-party Imports

# Local Imports
from integrations.intuit.qbo.base.field_ownership import (
    preserve_human_edited_name,
    raise_if_inactive_unmapped,
)
from integrations.intuit.qbo.base.identity_fastpath import run_identity_fastpath_dbo_only
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.locking import qbo_app_lock
from integrations.intuit.qbo.base.reconciliation_recorder import record_mapping_issue
from integrations.intuit.qbo.item.business.model import QboItem
from integrations.intuit.qbo.reconciliation.persistence.repo import ReconciliationIssueRepository
from entities.cost_code.business.service import CostCodeService
from entities.cost_code.business.model import CostCode

logger = logging.getLogger(__name__)


class ItemCostCodeConnector:
    """
    Connector service for synchronization between QboItem and CostCode modules.
    Handles parent QBO Items (no ParentRef) mapping to CostCode.

    U-307c: dbo-only identity resolution via `run_identity_fastpath_dbo_only` --
    no `qbo.ItemCostCode` mapping-table read/write of any kind (mirrors U-300b's
    `AttachableAttachmentConnector`). `dbo.CostCode.QboId`/`RealmId` (U-289) is
    the sole identity store; `dbo.CostCode`'s own filtered unique index +
    `SetCostCodeQboIdentity`'s theft-clear UPDATE guarantee at most one row
    holds a given identity, so a direct hit needs no cross-check and the old
    heal/adopt/dedup branch structure (driven by a second, independently-
    writable mapping table) no longer has anything to drift from.
    """

    def __init__(
        self,
        cost_code_service: Optional[CostCodeService] = None,
        reconciliation_repo: Optional[ReconciliationIssueRepository] = None,
    ):
        """Initialize the ItemCostCodeConnector."""
        self.cost_code_service = cost_code_service or CostCodeService()
        self.reconciliation_repo = reconciliation_repo or ReconciliationIssueRepository()

    def sync_from_qbo_item(self, qbo_item: QboItem) -> CostCode:
        """
        Sync data from QboItem to CostCode module.

        Args:
            qbo_item: QboItem record (must be a parent item with no ParentRef).
                U-307c: this is a transient (never persisted) object built by
                `QboItemService._upsert_item` -- `.id` is always None; use
                `.qbo_id` for identity/logging.

        Returns:
            CostCode: The synced CostCode record

        Raises:
            ValueError: If the item has a ParentRef (is not a parent item)
        """
        if qbo_item.is_child:
            raise ValueError(f"QboItem {qbo_item.qbo_id} has a ParentRef and is not a parent item")

        # Parse the name to get number and name
        number, name = qbo_item.parse_name()
        description = qbo_item.description

        outcome = run_identity_fastpath_dbo_only(
            qbo_id=qbo_item.qbo_id,
            realm_id=qbo_item.realm_id,
            entity_label="CostCode",
            external_label="QboItem",
            lock_resource_label="CostCode",
            read_direct_by_qbo_identity=self.cost_code_service.read_by_qbo_identity,
            apply_fields=lambda entity: self._apply_cost_code_fields_and_sync(
                entity,
                number=number,
                incoming_name=name,
                description=description,
            ),
            resolve_candidate=lambda: self._resolve_cost_code_candidate(
                qbo_item, number=number, name=name, description=description,
            ),
            stamp_identity=lambda candidate: self._stamp_cost_code_identity(
                candidate, qbo_item, name=name, description=description,
            ),
        )
        if outcome.entity is None:
            # U-316: no longer race-reachable (see run_identity_fastpath_
            # dbo_only's Raises docstring) — kept as a backstop for a
            # directly-invoked falsy qbo_item.qbo_id (this public method has
            # no guard of its own; pinned by test_cost_code_no_qbo_id_raises).
            # The production pull path already guards this upstream via
            # QboItemService._upsert_item.
            raise RuntimeError(
                f"Failed to resolve CostCode for QboItem qbo_id={qbo_item.qbo_id} "
                f"via the dbo-only identity fast path"
            )
        return outcome.entity

    def _resolve_cost_code_candidate(
        self,
        qbo_item: QboItem,
        *,
        number: str,
        name: Optional[str],
        description,
    ) -> CostCode:
        """
        `resolve_candidate` for the dbo-only fast path's MISS branch (U-307c):
        called only under `run_identity_fastpath_dbo_only`'s create lock, once
        a genuine miss is confirmed (no dbo.CostCode currently holds this
        identity, including the re-read under lock). Reproduces today's
        adopt-by-number-then-create logic using `read_by_number` directly --
        no mapping-table check, since there is no mapping table left.
        """
        raise_if_inactive_unmapped(
            qbo_item.active, qbo_label="QboItem", qbo_id=qbo_item.qbo_id, target="CostCode"
        )

        existing = self.cost_code_service.read_by_number(number)
        if existing is None:
            logger.info(
                f"Creating new CostCode from QboItem qbo_id={qbo_item.qbo_id}: "
                f"number={number}, name={name}"
            )
            return self.cost_code_service.create(number=number, name=name, description=description)

        # The number-matched row must be re-checked for an existing, DIFFERENT
        # (QboId, RealmId) before being returned as the candidate -- the
        # dbo-only equivalent of today's mapping-table duplicate check
        # (Decision 2). `stamp_identity`'s Set<Entity>QboIdentity theft-clear
        # only protects the INCOMING (qbo_id, realm_id) pair's uniqueness, not
        # this row's PRIOR identity -- it would not stop a silent re-point
        # here. Checking QboId alone would miss a same-QboId-different-realm
        # collision (QBO ids are only unique WITHIN a realm) -- this must
        # match _stamp_cost_code_identity's own (qbo_id AND realm_id) check
        # exactly, or a cross-realm row would get its fields overwritten here
        # before that later guard ever runs (Codex round-1 P1).
        existing_qbo_id = getattr(existing, "qbo_id", None)
        if existing_qbo_id and not (
            existing_qbo_id == qbo_item.qbo_id
            and (getattr(existing, "realm_id", None) or "") == (qbo_item.realm_id or "")
        ):
            self._raise_duplicate_qbo_item_issue(
                qbo_item=qbo_item,
                local_cost_code=existing,
                existing_qbo_id=existing_qbo_id,
            )
            raise ValueError(
                f"QboItem qbo_id={qbo_item.qbo_id} realm_id={qbo_item.realm_id} number-matches "
                f"local CostCode {existing.id} which already carries a DIFFERENT identity "
                f"(QboId={existing_qbo_id}, RealmId={getattr(existing, 'realm_id', None)})."
            )

        logger.info(
            f"Binding existing local CostCode {existing.id} ({number}) to QboItem "
            f"qbo_id={qbo_item.qbo_id} by number match"
        )
        # Field write deliberately deferred to _stamp_cost_code_identity, which
        # applies it atomically with the identity stamp under the candidate's
        # own lock (Codex round-2 P1) -- see that method's docstring.
        return existing

    def _stamp_cost_code_identity(
        self, candidate: CostCode, qbo_item: QboItem, *, name: Optional[str], description
    ) -> Optional[CostCode]:
        """
        `stamp_identity` for the dbo-only fast path's MISS branch (U-307c).

        Runs under its own app lock keyed on the CANDIDATE's cost_code_id --
        NOT `run_identity_fastpath_dbo_only`'s own create lock, which is keyed
        on the qbo_id/realm_id being resolved. `resolve_candidate` binds by
        NUMBER (a side-channel business key, the same shape as Attachment's
        hash-dedupe), so two different QboItems (different qbo_ids -- no
        contention on the qbo_id-keyed lock upstream) could number-match onto
        the SAME local CostCode concurrently. Re-reads immediately before
        stamping and refuses to overwrite a DIFFERENT existing identity.
        Mirrors `AttachableAttachmentConnector._stamp_pulled_identity`
        (U-300b) -- same side-channel-candidate race, same fix, per that
        method's own "next adopter" TODO.

        The QBO-derived field write (name/description) also happens HERE,
        not in `resolve_candidate` -- applying it there would let two
        concurrent QboItems that both number-match the SAME candidate (two
        different qbo_ids, so no contention on the create lock upstream) each
        write their own incoming values to the row BEFORE either acquires
        this lock, corrupting whichever one loses the identity stamp below
        (Codex round-2 P1: the round-1 realm-aware duplicate guard closed the
        already-stamped case but not this pre-stamp mutation race). Applying
        the field write inside this lock, after the theft-guard confirms the
        row is still genuinely unclaimed (or already this exact identity),
        makes the read-guard-write-stamp sequence atomic per candidate row --
        the loser raises before ever touching the row's fields. A freshly
        `create()`d candidate already carries correct fields; re-applying the
        same values here is a harmless no-op.
        """
        candidate_id = coerce_id(candidate.id)
        lock_resource = f"qbo_dbo_identity_stamp:CostCode:{candidate_id}"
        with qbo_app_lock(lock_resource) as got_lock:
            if not got_lock:
                raise RuntimeError(
                    f"Could not acquire identity-stamp lock for CostCode {candidate_id} "
                    f"(qbo_id={qbo_item.qbo_id}, realm_id={qbo_item.realm_id}) — holding for "
                    f"retry without stamping."
                )
            current = self.cost_code_service.read_by_id(str(candidate_id))
            current_qbo_id = getattr(current, "qbo_id", None) if current else None
            if current and current_qbo_id and not (
                current_qbo_id == qbo_item.qbo_id
                and (getattr(current, "realm_id", None) or "") == (qbo_item.realm_id or "")
            ):
                raise ValueError(
                    f"CostCode {candidate_id} already carries QBO identity {current_qbo_id} "
                    f"(realm {getattr(current, 'realm_id', None)}) — refusing to overwrite it "
                    f"with qbo_id={qbo_item.qbo_id} realm_id={qbo_item.realm_id}"
                )
            if current is not None:
                # U-219: adopt-by-number deliberately assigns name RAW, bypassing preserve_human_edited_name.
                current.name = name
                current.description = description
                self.cost_code_service.repo.update_by_id(current)
            self.cost_code_service.repo.set_qbo_identity(
                id=candidate_id, qbo_id=qbo_item.qbo_id, realm_id=qbo_item.realm_id,
            )
            return self.cost_code_service.read_by_id(str(candidate_id))

    def _apply_cost_code_fields_and_sync(
        self,
        cost_code: CostCode,
        *,
        number: str,
        incoming_name: Optional[str],
        description,
    ) -> CostCode:
        """
        Write QboItem-derived fields onto an existing CostCode and persist.
        The single field-write path for a dbo-identity hit (direct or
        race-discovered) -- the pre-U-307c heal-in-place repoint path that
        also called this is gone (nothing left to heal).
        """
        cost_code.number = number
        cost_code.name = preserve_human_edited_name(cost_code.name, incoming_name)
        cost_code.description = description
        return self.cost_code_service.repo.update_by_id(cost_code)

    def _raise_duplicate_qbo_item_issue(
        self,
        *,
        qbo_item: QboItem,
        local_cost_code: CostCode,
        existing_qbo_id: str,
    ) -> None:
        details = (
            f"Duplicate QBO item detected. QboItem qbo_id={qbo_item.qbo_id} "
            f"(Name='{qbo_item.name}') number-matches local CostCode {local_cost_code.id} "
            f"which already carries a DIFFERENT QboId {existing_qbo_id}. Resolve by merging or "
            f"renaming one of the QBO items."
        )
        record_mapping_issue(
            self.reconciliation_repo,
            drift_type="duplicate_qbo_item",
            entity_type="CostCode",
            entity_public_id=str(local_cost_code.public_id) if local_cost_code.public_id else None,
            qbo_id=str(qbo_item.qbo_id) if qbo_item.qbo_id else None,
            realm_id=qbo_item.realm_id or "",
            details=details,
        )
