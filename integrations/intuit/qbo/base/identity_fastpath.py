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


def raise_concurrent_write_race(*, entity_label: str, entity_id, path_label: str = "fast path") -> None:
    """
    Raise the standard "apply_fields returned None" exception (U-291).

    Always RuntimeError, deliberately NOT ValueError: a ROWVERSION race
    (concurrent edit) or a concurrent delete between the identity read and the
    write is transient, not a permanent data problem. `record_projection_error`'s
    rule 2 classifies a plain ValueError as a permanent SKIP, which would
    advance the watermark past this record anyway — the exact outcome this
    raise exists to prevent. Rule 3 sends everything else (including
    RuntimeError) to failure/hold, which is correct here.

    `record_projection_error` classifies purely by exception type, not message
    text, so the message itself carries no functional weight — this exists only
    to give every family ONE call instead of a hand-copied raise, each with its
    own near-identical rationale comment.

    Call this from inside a family's shared apply-fields write helper (the
    function passed as `apply_fields=` below, e.g. `_apply_bill_fields`), not
    from a per-call-site guard — so every caller of that helper (fast path,
    legacy mapping-table path, any self-heal/repoint path) gets the guard for
    free and cannot forget to check for a None return. `path_label` names which
    caller hit it, for the log trail; the ONE guard still lives in ONE place.
    """
    raise RuntimeError(
        f"Failed to update {entity_label} {entity_id} via {path_label} - update "
        f"returned None (concurrent write race); holding for retry."
    )


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
        on_apply_returned_none: called with `direct` whenever `apply_fields` returns
            None — a ROWVERSION race (concurrent edit) or a concurrent delete.
            Fires regardless of mapping state (U-291): a race is at least as likely
            on the "consistent" steady-state resync of an already-mapped record as
            on the rarer "missing" self-heal window this used to be scoped to, and
            in the "consistent" case there is no create_mapping step to fall back
            on to catch it, so a caller that cares must raise from this callback.

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

    if updated is None:
        # Nothing to map or stamp — the caller's update found the row gone (a
        # ROWVERSION race or a concurrent delete). This check used to live inside
        # `if state == MISSING`, so it only ever fired on the rare first-mapping
        # self-heal window; on the far more common "consistent" steady-state
        # resync of an already-mapped record — where a race is, if anything, MORE
        # likely — a None here fell straight through to `return
        # FastPathOutcome(hit=True, entity=None)` below with no callback, no
        # exception, nothing (U-291). Checking here, before the state branch,
        # makes the callback fire on every apply-returned-None outcome regardless
        # of mapping state. Callers that must not let this pass silently supply
        # the callback and raise from it — see CustomerCustomerConnector
        # ._on_update_empty for why a silent return here can wrongly advance a
        # watermark.
        if on_apply_returned_none is not None:
            on_apply_returned_none(direct)
        return FastPathOutcome(hit=True, entity=None)

    if state == MISSING:
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
            elif recheck_state == MISSING:
                # Not a race that resolved itself (recheck == CONSISTENT, a
                # concurrent create already succeeded — genuinely benign, nothing
                # to record) and not an escalated conflict (handled above). The
                # create failed for its own reason (transient DB/network) and the
                # mapping row still does not exist anywhere. The field write
                # already landed, but treating that as full success would advance
                # the watermark past a still-genuinely-unmapped entity with zero
                # durable trace (U-291 P2) — this record won't be re-pulled again
                # until QBO sees another change to it, so "next tick" would never
                # come. Raise so record_projection_error holds it for retry
                # instead: a redundant idempotent re-pull, not a lost record.
                raise RuntimeError(
                    f"{mapping_label} mapping create failed for {entity_label} "
                    f"{updated.id} and the retry-check still shows no mapping on "
                    f"either side (not a self-resolved race, not an escalated "
                    f"conflict): {e}"
                ) from e

    return FastPathOutcome(hit=True, entity=updated)


def run_line_identity_fastpath(
    *,
    parent_local_id: int,
    qbo_line_id: Optional[str],
    external_id: int,
    entity_label: str,
    external_label: str,
    read_direct_by_parent_and_qbo_line_id: Callable[[int, str], Any],
    read_by_local_id: Callable[[int], Any],
    read_by_external_id: Callable[[int], Any],
    external_id_attr: str,
    record_conflict_issue: Callable[[Any, Any, Any], None],
    conflict_message: Callable[[Any], str],
    apply_fields: Optional[Callable[[Any], Any]] = None,
    on_apply_returned_none: Optional[Callable[[Any], None]] = None,
) -> FastPathOutcome:
    """
    Run the dbo-native identity fast path for a LINE-ITEM entity (U-293).

    A line's QBO identity is parent-scoped, not globally unique: QBO line ids
    are small per-transaction sequence numbers ("1", "2", "3", ...) reused
    across every parent transaction — unlike header identity, where a QBO
    transaction id IS globally unique. Every dbo line table's own live unique
    index reflects this (`UQ_<Entity>LineItem_<Parent>Id_QboId` on
    `(ParentId, QboId)`, not a bare `(QboId, RealmId)` like the header
    tables) — confirmed against live prod for all 4 line families at U-293's
    Gate-1: real duplicate QboId values ARE reused across different parents
    in every family. So the direct-read key here is
    `(parent_local_id, qbo_line_id)`, not `(qbo_id, realm_id)` —
    `run_identity_fastpath` above cannot be reused as-is without repurposing
    its parameters to secretly mean something else; this sibling function
    keeps the two identity shapes honest instead of forking the header
    helper's own contract (the failure mode U-287's closing instruction
    warns against — this is a deliberate new shape, not a fork of an
    existing one).

    Note there is no `realm_id` parameter: unlike the header helper, realm
    is not part of this function's direct-read key at all. A line's parent
    already pins the realm (a parent header belongs to exactly one RealmId,
    resolved by the caller before this ever runs), so a redundant RealmId
    check here would be dead weight — matching the live
    `(ParentId, QboId)` unique index, which likewise carries no RealmId
    column. Callers still have `realm_id` in scope for their own
    `apply_fields`/identity-stamp step; it just never reaches this function.

    Once the parent is resolved, the conflict handling mirrors the header
    fast path exactly — same hard-stop-on-conflict guarantee, same
    `resolve_mapping_state` call (it doesn't care whether its key is
    globally or parent-scoped, only about the mapping table's own
    local-id/external-id relationship).

    MISSING does NOT self-heal here, unlike `run_identity_fastpath` — this
    is the one deliberate divergence from the header helper's control flow,
    not an oversight. A header's QBO id is never reused once minted, so
    "direct hit, no mapping either side" can only mean a genuinely new
    pairing, safe to bind. A line's QBO id is a small per-parent sequence
    number QBO actively RECYCLES: editing a bill's lines deletes the old
    `qbo.<Line>` staging row and its mapping (see the line connector's own
    stale-line cleanup), but nothing clears the now-orphaned dbo row's own
    QboId stamp. If a later, genuinely different new line reuses that same
    freed-up id, `read_direct_by_parent_and_qbo_line_id` returns that stale
    orphan, the mapping table confirms nothing on either side (MISSING, not
    CONFLICT — there is no mapping row to disagree), and blind self-heal
    would silently overwrite the orphan's real content with the new line's
    — a confirmed, adversarially-verified P1 (U-293 Gate-2, executable PoC).
    So MISSING is treated as a plain miss (`hit=False`) here: nothing is
    read further, nothing is written, no mapping is minted. The caller's
    existing content-fingerprint fallback (every line connector already has
    one — built for this exact "QBO renumbers a bill's lines on edit"
    scenario) is what may safely re-adopt the orphan, because unlike this
    function it can check the new line's CONTENT actually matches before
    binding, not just its coincidentally-recycled id.
    """
    if not qbo_line_id:
        return FastPathOutcome(hit=False)

    direct = read_direct_by_parent_and_qbo_line_id(parent_local_id, qbo_line_id)
    if not direct:
        return FastPathOutcome(hit=False)

    state, by_local, by_external = resolve_mapping_state(
        local_id=coerce_id(direct.id),
        external_id=external_id,
        read_by_local_id=read_by_local_id,
        read_by_external_id=read_by_external_id,
        external_id_attr=external_id_attr,
    )

    if state == CONFLICT:
        record_conflict_issue(direct, by_local, by_external)
        # HARD STOP — same guarantee as run_identity_fastpath: never fall through
        # to the legacy mapping-table path on a conflict. See that function's
        # docstring for why (the 2026-08-20 header P0 this class of bug caused).
        raise ValueError(conflict_message(direct))

    if state == MISSING:
        # See the docstring: a direct hit with no mapping on either side is
        # ambiguous for a line (stale orphan vs. genuinely new pairing) in a
        # way it never is for a header. Never self-heal it here — fall
        # through so the caller's own content-fingerprint check decides.
        return FastPathOutcome(hit=False)

    # Only CONSISTENT reaches here: a real mapping row already confirms this
    # direct hit, so it's safe to write.
    if apply_fields is None:
        updated = direct
    else:
        logger.info(
            f"Updating existing {entity_label} {direct.id} from {external_label} "
            f"{external_id} (direct parent-scoped dbo identity match)"
        )
        updated = apply_fields(direct)

    if updated is None:
        # ROWVERSION race or concurrent delete between the identity read and the
        # write — see run_identity_fastpath's identical branch for the full
        # rationale (U-291). Callers must raise RuntimeError (never ValueError)
        # from on_apply_returned_none.
        if on_apply_returned_none is not None:
            on_apply_returned_none(direct)
        return FastPathOutcome(hit=True, entity=None)

    return FastPathOutcome(hit=True, entity=updated)
