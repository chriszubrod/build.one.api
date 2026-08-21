"""
Shared dbo-native identity fast path for QBO pull connectors (U-287).

Every Phase-4 repoint (U-276 customer/project, U-277 company_info/physical_address,
U-278 vendorcredit, U-279 attachable) resolves a QBO record against the entity's own
`dbo.<Entity>.QboId`/`RealmId` FIRST, and only falls back to the `qbo.<Family>`
mapping-table hop when that misses. Before U-287 that recipe was hand-copied into six
connectors; this module is its single home.

Why it must be shared, not copied
---------------------------------
The recipe protects a fragile invariant, and hand-copying it is how a fix to that
invariant goes unnoticed. It already did, twice:

  * U-276's pilot recorded a mapping-vs-dbo identity **conflict** and then fell
    through to the legacy mapping-table path anyway. That path either updates a
    DIFFERENT local row and calls `set_qbo_identity` on it — and every
    `Set<Entity>QboIdentity` sproc's theft-clear UPDATE applies to ANY row carrying
    that `(QboId, RealmId)` pair, so it silently NULLs the conflicted row's identity —
    or mints a DUPLICATE via the CREATE path. Identity theft + duplicate mint, live in
    prod from 2026-08-19 until the 2026-08-20 hotfix, which had to patch TWO copies by
    hand.
  * `company_info` and `physical_address` (U-277) shipped one day before that hotfix
    with the same fall-through, mitigated only by a `protected_<entity>_id` guard that
    covers the re-resolves-to-the-same-row case and nothing else. They were repointed
    onto this helper by U-287, which is what finally closed it for them.

Hence: **`conflict` raises here, unconditionally, and there is no parameter to opt out.**
A caller cannot reintroduce the fall-through without deleting this module's code.

What the caller still owns
--------------------------
Only the genuinely family-specific bits, passed in as callbacks:
  * the reconciliation-issue message (each family names different downstream readers),
  * the `ValueError` text,
  * the field-write itself (`apply_fields`),
  * the mapping-row `create(...)` kwargs.
The control flow — check-before-write ordering, conflict hard-stop, and the self-heal
create-race re-check — lives here and only here.
"""

# Python Standard Library Imports
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

# Local Imports
from integrations.intuit.qbo.base.ids import coerce_id

logger = logging.getLogger(__name__)

# Mapping-table states. `CONFLICT` is terminal — see run_identity_fastpath.
CONSISTENT = "consistent"
MISSING = "missing"
CONFLICT = "conflict"


def resolve_mapping_state(
    *,
    local_id: int,
    external_id: int,
    read_by_local_id: Callable[[int], Any],
    read_by_external_id: Callable[[int], Any],
    external_id_attr: str,
) -> Tuple[str, Any, Any]:
    """
    Read-only check of a connector's mapping table against a dbo-identity match,
    BEFORE any field is written.

    Ordering is the point. Writing to the dbo-identity-matched row first and detecting
    a conflict afterward corrupts that row's data in the case where the mapping table —
    not dbo identity — was still the correct side (U-276 round-3 finding; getting this
    backwards cost three review rounds).

    Checks BOTH directions, mirroring every `create_mapping`'s own 1:1 guards. A
    local-id-only check would miss a stale mapping still binding this external id to a
    DIFFERENT local row, left behind by an earlier identity "theft" —
    `Set<Entity>QboIdentity`'s theft-clear UPDATE nulls the losing row's identity but
    does not touch the mapping table.

    Returns `(state, by_local, by_external)`:
      "consistent" — a mapping row exists and agrees; caller writes freely.
      "missing"    — no mapping row on either side; caller writes and creates one.
      "conflict"   — the two sides disagree (one or both directions); the caller must
                     NOT write to the dbo-identity-matched row. Terminal.

    Only reads `read_by_external_id` when `by_local` doesn't already settle it: the
    external id is unique on every one of these mapping tables, so a `by_local` row
    whose external id matches IS the row `read_by_external_id` would return. Skipping
    it saves a round trip on the steady-state path this whole fast path exists to keep
    cheap.
    """
    by_local = read_by_local_id(local_id)
    if by_local and getattr(by_local, external_id_attr) == external_id:
        return CONSISTENT, by_local, by_local
    by_external = read_by_external_id(external_id)
    if not by_local and not by_external:
        return MISSING, by_local, by_external
    return CONFLICT, by_local, by_external


@dataclass(frozen=True)
class FastPathOutcome:
    """
    Result of the fast path.

    `hit=False` means the fast path did not resolve (no `qbo_id`, or no dbo row carries
    that identity) and the caller should continue to its legacy mapping-table path.
    `hit=True` means it resolved; `entity` is the post-`apply_fields` row, which may be
    None when the caller's own update returned None (a ROWVERSION race).
    """

    hit: bool
    entity: Any = None


def run_identity_fastpath(
    *,
    qbo_id: Optional[str],
    realm_id: Optional[str],
    external_id: int,
    entity_label: str,
    external_label: str,
    mapping_label: str,
    read_direct_by_qbo_identity: Callable[[Optional[str], Optional[str]], Any],
    read_by_local_id: Callable[[int], Any],
    read_by_external_id: Callable[[int], Any],
    external_id_attr: str,
    record_conflict_issue: Callable[[Any, Any, Any], None],
    conflict_message: Callable[[Any], str],
    create_mapping: Callable[[int], Any],
    apply_fields: Optional[Callable[[Any], Any]] = None,
    on_apply_returned_none: Optional[Callable[[Any], None]] = None,
) -> FastPathOutcome:
    """
    Run the dbo-native identity fast path.

    Args:
        qbo_id / realm_id: the QBO identity to resolve. A falsy `qbo_id` short-circuits
            to `hit=False` without a read (every connector already guarded this way).
        external_id: the staging-row PK (e.g. `qbo_customer.id`) this sync is for.
        entity_label / external_label / mapping_label: log-message nouns, e.g.
            "Customer" / "QboCustomer" / "CustomerCustomer". NB all three already
            exist per-family in `base/identity_drift.py`'s `FlatEntitySpec` registry
            (`.label`, `"Qbo" + .staging_table`, `.mapping_table`) — the self-declared
            single source of truth for this topology, and what the backfill/drift
            scripts key on. They are passed literally here only because binding them
            from the registry would change this contract; a seventh family should
            prefer deriving them so an operator's log trail and the drift checker
            cannot name the same mapping table differently. Booked in TODO.md.
        read_direct_by_qbo_identity: `(qbo_id, realm_id) -> Optional[entity]`, called
            positionally — the entity service's own `read_by_qbo_identity`.
        read_by_local_id / read_by_external_id / external_id_attr: the family's mapping
            repo accessors, as for `resolve_mapping_state`.
        record_conflict_issue: `(entity, by_local, by_external) -> None` — the family's
            own `_raise_identity_mapping_conflict_issue`. Called with the row the
            conflict is about: `direct` on the pre-write check, the post-write row on
            the self-heal re-check.
        conflict_message: `(direct) -> str`, the ValueError text for the hard stop.
        create_mapping: `(local_id) -> Any`, creates the mapping row directly (NOT via
            the family's `create_mapping()`, which would also re-stamp identity —
            redundant here, since dbo identity is already correct by construction).
        apply_fields: `(direct) -> Optional[entity]` writing the QBO-derived fields.
            Omit for a caller that only wants identity resolution and does its own
            downstream work (attachable).
        on_apply_returned_none: called with `direct` when `apply_fields` returned None
            on the "missing" path — there is no row to map or stamp.

    Returns:
        FastPathOutcome.

    Raises:
        ValueError: on a detected `conflict`, ALWAYS, after recording the issue.
            This is a hard stop, never a fall-through — see the module docstring.
            A human resolves which identity source is correct; the recorded
            reconciliation issue is the durable follow-up.
    """
    if not qbo_id:
        return FastPathOutcome(hit=False)

    direct = read_direct_by_qbo_identity(qbo_id, realm_id)
    if not direct:
        return FastPathOutcome(hit=False)

    def check_mapping(local_id: int) -> Tuple[str, Any, Any]:
        """`resolve_mapping_state` with this run's accessors bound. Guarantees the
        pre-write check and the self-heal re-check ask the identical question, by
        construction rather than by eyeball — they differ only in which row they ask
        about (pre-write `direct` vs post-write `updated`)."""
        return resolve_mapping_state(
            local_id=local_id,
            external_id=external_id,
            read_by_local_id=read_by_local_id,
            read_by_external_id=read_by_external_id,
            external_id_attr=external_id_attr,
        )

    state, by_local, by_external = check_mapping(coerce_id(direct.id))

    if state == CONFLICT:
        record_conflict_issue(direct, by_local, by_external)
        # HARD STOP. Never fall through to the legacy mapping-table path: it would
        # either write to a DIFFERENT row and call set_qbo_identity — whose theft-clear
        # UPDATE would silently NULL `direct`'s identity — or mint a duplicate via its
        # CREATE path. That exact fall-through was the live-prod P0 of 2026-08-20.
        raise ValueError(conflict_message(direct))

    if apply_fields is None:
        updated = direct
    else:
        logger.info(
            f"Updating existing {entity_label} {direct.id} from {external_label} "
            f"{external_id} (direct dbo identity match)"
        )
        updated = apply_fields(direct)

    if state == MISSING:
        if updated is None:
            # Nothing to map or stamp — the caller's update found the row gone.
            # Callers that must not let this pass silently supply the callback and
            # raise from it; see CustomerCustomerConnector._on_update_empty for why
            # a silent return here can wrongly advance a watermark.
            if on_apply_returned_none is not None:
                on_apply_returned_none(direct)
            return FastPathOutcome(hit=True, entity=None)

        try:
            create_mapping(coerce_id(updated.id))
        except Exception as e:
            # A concurrent sync may have raced this exact staging row between the
            # "missing" pre-check and this create — no sp_getapplock serializes
            # these call sites (pre-existing gap, TODO.md's U-238a follow-ups).
            # Re-check rather than assume: if it is now a real conflict, record it
            # properly instead of leaving a bare warning (U-276 round-4 finding).
            logger.error(
                f"{mapping_label} mapping create failed for {entity_label} "
                f"{updated.id} after a 'missing' pre-check: {e}"
            )
            recheck_state, recheck_by_local, recheck_by_external = check_mapping(
                coerce_id(updated.id)
            )
            if recheck_state == CONFLICT:
                record_conflict_issue(updated, recheck_by_local, recheck_by_external)

    return FastPathOutcome(hit=True, entity=updated)
