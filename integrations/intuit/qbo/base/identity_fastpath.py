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

A dbo-only sibling (U-300a)
----------------------------
`run_identity_fastpath` above assumes every family still has a mapping table to
cross-check against. `run_identity_fastpath_dbo_only` (below, near the bottom of this
module) is for a family that has RETIRED its second store (the Wave-5 "trust dbo
alone" decision — attachable/`dbo.Attachment` is the pilot) and therefore has no
mapping table left to detect drift against — see that function's own docstring for why
this is a new function rather than a mode flag here. Everything above this point is
unaffected: no family still on a mapping table changes behavior until it separately
migrates.

A dbo-only LINE sibling (U-361)
--------------------------------
`run_line_identity_fastpath` (below) is the with-mapping helper for line-item
families; `run_line_identity_fastpath_dbo_only` (at the bottom of this module) is
its mapping-free analog for a line family that has retired its `qbo.<Parent>Line
<Entity>LineItem` mapping table (the U-349 retirement program's line-item wave —
vendorcredit_line_item first, then invoice/bill/expense line items clone it). It is
to `run_line_identity_fastpath` what `run_identity_fastpath_dbo_only` is to
`run_identity_fastpath`: same parent-scoped `(parent_local_id, qbo_line_id)` key, no
second store left to cross-check, a create lock around the MISS branch, and — new
at this layer — a helper-level guarantee that a failed identity stamp never strands
an unstamped orphan line. See its own docstring.
"""

# Python Standard Library Imports
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Tuple

# Local Imports
from integrations.intuit.qbo.base.ids import coerce_id
from integrations.intuit.qbo.base.locking import qbo_app_lock

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


def create_race_lock(mapping_label: str, external_id: int, timeout_ms: int = 15000):
    """
    The shared sp_getapplock critical section guarding the create/rollback race for a
    single (mapping_label, external_id) pair (U-304).

    Must be acquired by BOTH:
      (a) run_identity_fastpath's MISSING-branch self-heal (apply_fields'
          header UPDATE + the mapping-row insert that binds it) — opt in via
          that function's `race_lock_mapping_label` param, and
      (b) guard_create_mapping_rollback below (the connector's own explicit
          create-header-then-map tail's failure/rollback path).

    Same resource name -> sp_getapplock serializes the two: whichever acquires
    first completes its ENTIRE decide-or-mutate step (write+bind, or recheck-
    then-conditionally-delete) before the other can even read state. Without
    this, a self-heal write/bind landing in the window between a rollback's
    point-in-time recheck and its actual DELETE call could be destroyed by
    that DELETE — a real, silent, unrecoverable loss of a legitimately-
    completed record. This was U-298's and U-302's shared residual (both
    connectors hand-copied a recheck-before-rollback that closed the common
    case but left this narrower window open); closed here in one shared place
    instead of a third hand-copy. The lock must cover self-heal's apply_fields
    UPDATE too, not just its final insert — see run_identity_fastpath's
    `race_lock_mapping_label` docstring for why locking only the insert still
    leaves a real window open.

    Keyed on `external_id` (the QBO-side staging row), not local_id: that is the
    one thing every racer for the same QBO record shares — each racer mints its
    own, different local header, so a local_id-keyed lock would not serialize
    them against each other at all.

    Deadlock note: never combined with mapping_cleanup.py's `qbo_mapping_delete:*`
    lock in the same call stack (disjoint resource namespace, disjoint trigger —
    pull-time create-race here vs. API-driven entity-delete there) and never
    acquired while already holding one of its own — every call site in this
    module does a single, non-nested acquire, so no lock-ordering cycle is
    possible.
    """
    return qbo_app_lock(f"qbo_mapping_create:{mapping_label}:{external_id}", timeout_ms=timeout_ms)


@dataclass(frozen=True)
class RollbackGuardOutcome:
    """
    Result of guard_create_mapping_rollback.

    `state` is one of CONSISTENT / CONFLICT / MISSING (see resolve_mapping_state).
    `by_local` / `by_external` are that recheck's own mapping-row reads. `delete_exc`
    carries the compensating delete's exception when it failed, or None when it
    succeeded (or was never attempted). Callers use `delete_exc` to log their own
    distinct success/failure message exactly as they did before this helper existed
    — this dataclass deliberately does not swallow that detail into a single bool.
    """

    state: str
    by_local: Any
    by_external: Any
    delete_exc: Optional[BaseException] = None

    @property
    def delete_succeeded(self) -> Optional[bool]:
        """None when state is CONSISTENT (delete_header was never called — the
        racer's row is valid, nothing to roll back); True/False otherwise,
        derived from `delete_exc`. A property, not a stored field, so it can never
        drift out of sync with `state`/`delete_exc` the way three hand-written
        return statements could."""
        if self.state == CONSISTENT:
            return None
        return self.delete_exc is None


def guard_create_mapping_rollback(
    *,
    mapping_label: str,
    external_id: int,
    local_id: int,
    read_by_local_id: Callable[[int], Any],
    read_by_external_id: Callable[[int], Any],
    external_id_attr: str,
    record_conflict_issue: Callable[[Any, Any], None],
    delete_header: Callable[[], None],
    entity_label: str,
    lock_timeout_ms: int = 15000,
) -> RollbackGuardOutcome:
    """
    Call from a connector's `except` block immediately AFTER its own explicit
    create_mapping() attempt has failed for a brand-new header (U-304).

    Re-runs resolve_mapping_state UNDER create_race_lock (shared with the
    self-heal mapping-insert create_race_lock also guards) so no racer can bind
    to this header in the window between this recheck and delete_header() —
    closing the point-in-time-snapshot gap U-298/U-302 each left behind (a later
    racer landing in that window could still be destroyed by this function's own
    delete).

    Returns a RollbackGuardOutcome so each connector keeps its own distinct
    exception wrapping / log message / severity on the final outcome exactly as
    before this unit — this helper owns only the racy mechanics (the lock, the
    recheck, and — when warranted — the delete + conflict-issue recording),
    never the caller's final raise:
      CONSISTENT — a racer already validly bound this exact pair. Caller should
                   return the racer's current row; delete_header() was NOT called
                   (delete_succeeded is None).
      CONFLICT   — the mapping table disagrees with a DIFFERENT row.
                   record_conflict_issue has been called AND delete_header has
                   ALREADY been attempted (a genuine orphan on this row).
      MISSING    — no self-resolve, no conflict. delete_header has ALREADY been
                   attempted (a genuine orphan; the original failure was not a
                   resolved race).

    FAIL CLOSED on a lock-acquire timeout: raises RuntimeError WITHOUT ever
    calling resolve_mapping_state or delete_header — never deletes under
    uncertainty. The just-created header is left mapped-pending, not destroyed;
    a future retry's identity fast path will discover and heal it. RuntimeError
    (not ValueError) so record_projection_error's rule 3 holds this for retry
    instead of treating it as a permanent skip — the same transient-vs-permanent
    split raise_concurrent_write_race already relies on.
    """
    with create_race_lock(mapping_label, external_id, timeout_ms=lock_timeout_ms) as got_lock:
        if not got_lock:
            raise RuntimeError(
                f"Could not acquire create/rollback lock for {entity_label} {local_id} "
                f"({mapping_label} external_id={external_id}) within {lock_timeout_ms}ms "
                f"- holding for retry without deleting."
            )

        state, by_local, by_external = resolve_mapping_state(
            local_id=local_id,
            external_id=external_id,
            read_by_local_id=read_by_local_id,
            read_by_external_id=read_by_external_id,
            external_id_attr=external_id_attr,
        )

        if state == CONSISTENT:
            return RollbackGuardOutcome(state=state, by_local=by_local, by_external=by_external)

        if state == CONFLICT:
            record_conflict_issue(by_local, by_external)

        try:
            delete_header()
        except Exception as del_e:
            return RollbackGuardOutcome(
                state=state, by_local=by_local, by_external=by_external, delete_exc=del_e,
            )

        return RollbackGuardOutcome(state=state, by_local=by_local, by_external=by_external)


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
    race_lock_mapping_label: Optional[str] = None,
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
        race_lock_mapping_label: opt-in (U-304). When set, a MISSING-state hit
            (see below) runs `apply_fields` AND `create_mapping` INSIDE
            `create_race_lock(race_lock_mapping_label, external_id)` — the same
            lock a caller's `guard_create_mapping_rollback` acquires around its
            own recheck-then-delete. Omit (default None) to keep today's
            behavior EXACTLY (no lock at all) — every family besides the ones
            that opt in is unaffected byte-for-byte. Why the whole apply_fields
            call must be inside the lock, not just create_mapping: apply_fields
            is what WRITES onto `direct` (the header a caller's rollback might
            be about to delete) — locking only the later create_mapping call
            still leaves a window where apply_fields's UPDATE could land on a
            header a concurrent rollback is mid-delete-decision on. With the
            lock covering both, whichever side (self-heal vs. rollback) gets it
            first finishes its ENTIRE decide-or-mutate step before the other
            can act: if rollback's delete lands first, apply_fields's own
            UPDATE affects 0 rows and returns None (the ordinary "ROWVERSION
            race / concurrent delete" path just below, already handled); if
            self-heal's UPDATE lands first, the rollback's own re-check inside
            guard_create_mapping_rollback sees the fresh mapping row (once
            create_mapping commits) as CONSISTENT and does not delete.

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

    def _apply_and_maybe_self_heal() -> FastPathOutcome:
        """
        Write the QBO-derived fields onto `direct`, then — only on a MISSING
        state — self-heal the missing mapping row. Factored out so the
        MISSING+race_lock_mapping_label case (below) can run this EXACT body
        inside create_race_lock instead of a hand-copied duplicate that could
        drift from the unlocked version every other caller still runs.
        """
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
            # ROWVERSION race or a concurrent delete — including, for a
            # lock-guarded MISSING self-heal, a concurrent rollback that won the
            # lock first and deleted `direct` out from under this UPDATE; U-304
            # relies on exactly this branch to make that outcome safe). This
            # check used to live inside `if state == MISSING`, so it only ever
            # fired on the rare first-mapping self-heal window; on the far more
            # common "consistent" steady-state resync of an already-mapped
            # record — where a race is, if anything, MORE likely — a None here
            # fell straight through to `return FastPathOutcome(hit=True,
            # entity=None)` below with no callback, no exception, nothing
            # (U-291). Checking here, before the state branch, makes the
            # callback fire on every apply-returned-None outcome regardless of
            # mapping state. Callers that must not let this pass silently
            # supply the callback and raise from it — see
            # CustomerCustomerConnector._on_update_empty for why a silent
            # return here can wrongly advance a watermark.
            if on_apply_returned_none is not None:
                on_apply_returned_none(direct)
            return FastPathOutcome(hit=True, entity=None)

        if state == MISSING:
            try:
                create_mapping(coerce_id(updated.id))
            except Exception as e:
                # A concurrent sync may have raced this exact staging row between
                # the "missing" pre-check and this create. Without
                # race_lock_mapping_label this is a pre-existing, un-serialized
                # gap (TODO.md's U-238a follow-ups); with it, this create ran
                # under create_race_lock and a genuine collision here means a
                # DIFFERENT external_id's mapping (this lock is scoped per
                # external_id) or a non-race DB error — either way, re-check
                # rather than assume: if it is now a real conflict, record it
                # properly instead of leaving a bare warning (U-276 round-4
                # finding).
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

    if state == MISSING and race_lock_mapping_label is not None:
        with create_race_lock(race_lock_mapping_label, external_id) as got_lock:
            if not got_lock:
                raise RuntimeError(
                    f"Could not acquire create/rollback lock for {entity_label} self-heal "
                    f"(local_id={coerce_id(direct.id)}, {race_lock_mapping_label} "
                    f"external_id={external_id}) - holding for retry without writing."
                )
            return _apply_and_maybe_self_heal()

    return _apply_and_maybe_self_heal()


def _apply_dbo_only_hit(
    row: Any,
    *,
    apply_fields: Optional[Callable[[Any], Any]],
    on_apply_returned_none: Optional[Callable[[Any], None]],
    entity_label: str,
    external_label: str,
    log_suffix: str,
    path_label: str,
) -> FastPathOutcome:
    """
    Shared HIT-branch body for every dbo-only fast path (header
    `run_identity_fastpath_dbo_only`, line `run_line_identity_fastpath_dbo_only`):
    write `apply_fields` onto an already-resolved row, and unconditionally raise
    via `raise_concurrent_write_race` on a `None` return (U-316's contract — a
    caller can never receive a silent `hit=True, entity=None`).

    Factored out (code-review finding, 2026-09-01) after this exact body was
    hand-copied once already between the header and line siblings — the class
    of drift U-316 itself exists to prevent, now closed at its source instead
    of per-copy. `log_suffix` and `path_label` are the only two things that
    ever differed between the copies; everything else is identical by
    construction now, not by convention.
    """
    if apply_fields is None:
        updated = row
    else:
        logger.info(f"Updating existing {entity_label} {row.id} from {external_label} {log_suffix}")
        updated = apply_fields(row)
    if updated is None:
        if on_apply_returned_none is not None:
            on_apply_returned_none(row)
        raise_concurrent_write_race(entity_label=entity_label, entity_id=row.id, path_label=path_label)
    return FastPathOutcome(hit=True, entity=updated)


def run_identity_fastpath_dbo_only(
    *,
    qbo_id: Optional[str],
    realm_id: Optional[str],
    entity_label: str,
    external_label: str,
    lock_resource_label: str,
    read_direct_by_qbo_identity: Callable[[Optional[str], Optional[str]], Any],
    resolve_candidate: Callable[[], Any],
    stamp_identity: Callable[[Any], Any],
    apply_fields: Optional[Callable[[Any], Any]] = None,
    on_apply_returned_none: Optional[Callable[[Any], None]] = None,
    lock_timeout_ms: int = 15000,
) -> FastPathOutcome:
    """
    Run the dbo-native identity fast path for a family with NO mapping table
    (U-300a — the Wave-5 "trust dbo alone" pilot, `attachable`/`dbo.Attachment`
    first). See the module docstring's "A dbo-only sibling" section for how
    this relates to `run_identity_fastpath` above.

    Why this is a separate function, not a mode flag on `run_identity_fastpath`
    ------------------------------------------------------------------------
    `run_identity_fastpath`'s CONSISTENT/MISSING/CONFLICT machinery exists to
    catch DRIFT between dbo-native identity and an independently-writable
    mapping table — that class of bug is exactly what caused the 2026-08-20
    live-prod P0 documented above. Once a family's mapping table is retired,
    there is no second store left to drift from: `dbo.<Entity>`'s own
    filtered unique index (e.g. `UQ_Attachment_QboId_RealmId` —
    `(QboId, RealmId) WHERE QboId IS NOT NULL`) plus `Set<Entity>
    QboIdentity`'s theft-clear UPDATE already guarantee, at the database
    engine level, that at most one row holds a given identity at any instant.
    A direct hit against that index needs no cross-check — it unambiguously
    IS the current single holder. Threading a `dbo_only=True` branch through
    the existing function would blur two genuinely different algorithms
    (compare-against-a-second-store vs. nothing-left-to-compare-against)
    into one body; this sibling keeps them honest instead — the same
    reasoning `run_line_identity_fastpath` gives for staying separate from
    the header helper it sits beside.

    The one residual risk, and how this closes it
    -----------------------------------------------
    A MISS here means no dbo row currently holds this identity — safe to
    mint one, EXCEPT that two concurrent syncs of the same external object
    can both observe MISS and both try to become the holder. The unique
    index + theft-clear resolve that race correctly (exactly one winner) but
    SILENTLY: the loser's own `Set<Entity>QboIdentity` call fires its own
    theft-clear branch against itself, orphaning whichever row it just
    created with no error and no reconciliation record. So a MISS runs the
    caller's candidate-resolution + stamp INSIDE a dedicated app lock, with
    one re-read of `read_direct_by_qbo_identity` under that lock first — a
    racer who already won is discovered here and adopted, instead of a
    second row being minted and immediately orphaned.

    Deliberately a NEW lock namespace (`qbo_dbo_identity_create:...`), not a
    reuse of `create_race_lock`'s `qbo_mapping_create:...` prefix: that lock
    is keyed on the STAGING row's PK for a fundamentally different critical
    section (U-304's create-vs-rollback race over a mapping row). This one
    is keyed on the entity's own `(qbo_id, realm_id)` — the only stable key
    every racer for the same external identity shares once there is no
    mapping-table PK left to key on instead. Disjoint prefix, disjoint
    trigger, never acquired nested — same deadlock-avoidance shape
    `create_race_lock` documents for itself.

    What the caller still owns
    ---------------------------
    `resolve_candidate` — find-or-create the local row to bind (hash-dedup,
    a brand-new row, whatever the family's own CREATE path already does);
    called ONLY on a genuine (lock-confirmed) miss. `stamp_identity` — call
    the family's own `Set<Entity>QboIdentity` (or equivalent) on that
    candidate and return the refreshed row; also called only on a genuine
    miss. Neither runs on a hit (direct or race-discovered) — an existing
    holder is never re-stamped or re-created.

    A race-resolved hit logs at INFO rather than raising or recording a
    `ReconciliationIssue`: it is dbo-only mode's NORMAL outcome for this
    scenario (correct and safe by construction), not a data-integrity event
    the way `run_identity_fastpath`'s CONFLICT is — worth watching in
    telemetry if it fires far more than expected, not worth escalating.

    Args mirror `run_identity_fastpath` where the concept is shared
    (`qbo_id`/`realm_id`/`entity_label`/`external_label`/`apply_fields`/
    `on_apply_returned_none`); see that function's docstring for those.
    `lock_resource_label` names this family's lock namespace (e.g.
    "Attachment") — analogous to `mapping_label` there, but there is no
    mapping table to log against, only the reconciliation-free race branch.

    Returns:
        FastPathOutcome. `hit=False` ONLY when `qbo_id` is falsy — unlike
        `run_identity_fastpath`, a dbo-only caller has no separate legacy
        mapping-table path to fall back to, so every other outcome (direct
        hit, race-resolved hit, or a genuine miss resolved via
        `resolve_candidate`/`stamp_identity`) reports `hit=True`. As of
        U-316, `hit=True` NEVER carries `entity=None` — an `apply_fields`
        or `stamp_identity` call that returns `None` (a ROWVERSION race or
        a concurrent delete) raises instead of being handed back to the
        caller as a silent miss; see Raises below.

    Raises:
        RuntimeError: on a lock-acquire timeout — FAILS CLOSED, never
            proceeds to create-or-stamp under uncertainty (mirrors
            `guard_create_mapping_rollback`'s own fail-closed contract).
            Not raised for a race-resolved hit (a racer discovered under the
            lock) — that path is handled, not an error (see above). Also
            raised, via `raise_concurrent_write_race` (U-316), when
            `apply_fields` or `stamp_identity` returns `None` — the caller's
            `on_apply_returned_none` (if wired) fires first, then this raise
            is unconditional; a caller can no longer silently receive
            `entity=None` by omitting or under-implementing that callback.
    """
    if not qbo_id:
        return FastPathOutcome(hit=False)

    def _apply(row: Any) -> FastPathOutcome:
        return _apply_dbo_only_hit(
            row,
            apply_fields=apply_fields,
            on_apply_returned_none=on_apply_returned_none,
            entity_label=entity_label,
            external_label=external_label,
            log_suffix="(direct dbo-only identity match)",
            path_label="dbo-only fast path",
        )

    direct = read_direct_by_qbo_identity(qbo_id, realm_id)
    if direct:
        return _apply(direct)

    lock_resource = f"qbo_dbo_identity_create:{lock_resource_label}:{qbo_id}:{realm_id or ''}"
    with qbo_app_lock(lock_resource, timeout_ms=lock_timeout_ms) as got_lock:
        if not got_lock:
            raise RuntimeError(
                f"Could not acquire dbo-only identity create lock for {entity_label} "
                f"({external_label} qbo_id={qbo_id}, realm_id={realm_id}) within "
                f"{lock_timeout_ms}ms - holding for retry without creating."
            )

        direct_under_lock = read_direct_by_qbo_identity(qbo_id, realm_id)
        if direct_under_lock:
            logger.info(
                f"{entity_label} identity race resolved: another sync already bound "
                f"{external_label} qbo_id={qbo_id} realm_id={realm_id} to "
                f"{entity_label} {direct_under_lock.id} - adopting it instead of "
                f"minting a second row."
            )
            return _apply(direct_under_lock)

        candidate = resolve_candidate()
        stamped = stamp_identity(candidate)
        if stamped is None:
            raise_concurrent_write_race(
                entity_label=entity_label, entity_id=candidate.id, path_label="dbo-only fast path (stamp)",
            )
        return FastPathOutcome(hit=True, entity=stamped)


def stamp_dbo_identity_with_lock(
    *,
    candidate_id: int,
    entity_label: str,
    qbo_id: str,
    realm_id: Optional[str],
    read_by_id: Callable[[int], Any],
    write_identity: Callable[[Any], None],
    apply_fields: Optional[Callable[[Any], Optional[Any]]] = None,
    on_conflict: Optional[Callable[[Any], None]] = None,
    lock_timeout_ms: int = 15000,
) -> Optional[Any]:
    """
    Stamp a QBO identity onto a candidate row under a row-scoped app lock
    (U-328/U-331 — `docs/design/stamp-lock-helper.md`), for a
    `run_identity_fastpath_dbo_only` MISS branch whose `resolve_candidate`
    binds by a side-channel business key (hash, number, name) rather than by
    `qbo_id`.

    Why a SECOND lock, nested inside `run_identity_fastpath_dbo_only`'s own
    `qbo_dbo_identity_create:*` lock: that outer lock is keyed on the
    INCOMING (qbo_id, realm_id) being resolved, so it only serializes two
    racers for the SAME external record. Two DIFFERENT incoming QBO records
    (different qbo_ids — no contention on the outer lock) can still resolve
    to the SAME local candidate row via `resolve_candidate`'s side-channel
    key. This lock, keyed on the candidate row itself, is what serializes
    those two racers against each other.

    Mechanically extracted from six hand-copies (design doc §1/§3):
      1. Acquire `qbo_dbo_identity_stamp:{entity_label}:{candidate_id}`;
         fail closed (RuntimeError) on a timeout — never stamps under
         uncertainty.
      2. Re-read the candidate via `read_by_id`.
      3. `current is None` → return None (a concurrent delete stole the row
         out from under `resolve_candidate`) — the caller's own
         `stamped is None` check turns this into
         `raise_concurrent_write_race`, same as every other MISS-path None.
      4. Theft-guard: refuse to overwrite a DIFFERENT existing identity,
         calling `on_conflict(current)` first if provided, then raising
         ValueError. Checking QboId alone would miss a same-QboId-different-
         realm collision (QBO ids are only unique WITHIN a realm).
      5. `apply_fields(current)` if provided — the family's own QBO-derived
         field write, deferred to HERE (after the theft-guard, inside the
         lock) so two racers landing on the same candidate can't each write
         their own incoming values before either wins the identity stamp.
         A `None` return (a ROWVERSION race / concurrent delete on THIS
         update) raises via `raise_concurrent_write_race` — the guard
         Customer/Project already hand-rolled here, now also closing the
         same gap for CostCode/SubCostCode, whose hand-copies discarded
         `update_by_id`'s return value entirely (a previously-silent race
         now raises and holds for retry instead).
      6. `write_identity(current)` — the family's own `set_qbo_identity`
         call, plus any same-lock side effect (Project's/Vendor's own
         `_sync_addresses`) the closure chooses to bundle in.
      7. Re-read and return the refreshed row.

    Args:
        candidate_id: the row to stamp, already coerced to `int` by the
            caller (mirrors every hand-copy's own `coerce_id(candidate.id)`).
        entity_label: log/lock-namespace noun, e.g. "CostCode".
        qbo_id / realm_id: the incoming identity to stamp.
        read_by_id: the family's own `read_by_id` closure.
        write_identity: `(current) -> None`, the family's `set_qbo_identity`
            call. Kwargs are NOT uniform across families (e.g. `active=`
            only applies to a family with a `QboActive` mirror), so this
            stays a plain callable rather than a fixed helper param.
        apply_fields: `(current) -> Optional[Any]`, the family's QBO-derived
            field write. Omit for a family with nothing to write here
            (Attachment, Vendor).
        on_conflict: `(current) -> None`, called before the theft-guard's
            raise, for a family that wants a `ReconciliationIssue` recorded
            on a stamp-time race. Optional so a family need not grow one
            just to adopt this helper.
        lock_timeout_ms: forwarded to `qbo_app_lock`.

    Returns:
        The refreshed row (post `read_by_id`), or None when `current is
        None` at step 3.

    Raises:
        RuntimeError: on a lock-acquire timeout, or via
            `raise_concurrent_write_race` when `apply_fields` returns None.
        ValueError: on a detected theft-guard conflict, after `on_conflict`
            (when provided) has recorded it.
    """
    lock_resource = f"qbo_dbo_identity_stamp:{entity_label}:{candidate_id}"
    with qbo_app_lock(lock_resource, timeout_ms=lock_timeout_ms) as got_lock:
        if not got_lock:
            raise RuntimeError(
                f"Could not acquire identity-stamp lock for {entity_label} {candidate_id} "
                f"(qbo_id={qbo_id}, realm_id={realm_id}) — holding for retry without stamping."
            )

        current = read_by_id(candidate_id)
        if current is None:
            return None

        existing_qbo_id = getattr(current, "qbo_id", None)
        if existing_qbo_id and not (
            existing_qbo_id == qbo_id and (getattr(current, "realm_id", None) or "") == (realm_id or "")
        ):
            if on_conflict is not None:
                on_conflict(current)
            raise ValueError(
                f"{entity_label} {candidate_id} already carries QBO identity {existing_qbo_id} "
                f"(realm {getattr(current, 'realm_id', None)}) — refusing to overwrite it with "
                f"qbo_id={qbo_id} realm_id={realm_id}"
            )

        if apply_fields is not None:
            updated = apply_fields(current)
            if updated is None:
                raise_concurrent_write_race(
                    entity_label=entity_label, entity_id=candidate_id, path_label="identity stamp",
                )

        write_identity(current)
        return read_by_id(candidate_id)


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


def run_line_identity_fastpath_dbo_only(
    *,
    parent_local_id: int,
    qbo_line_id: Optional[str],
    entity_label: str,
    external_label: str,
    lock_resource_label: str,
    read_direct_by_parent_and_qbo_line_id: Callable[[int, str], Any],
    resolve_candidate: Callable[[], Any],
    stamp_identity: Callable[[Any], Any],
    apply_fields: Optional[Callable[[Any], Any]] = None,
    on_apply_returned_none: Optional[Callable[[Any], None]] = None,
    rollback_candidate: Optional[Callable[[Any], None]] = None,
    lock_timeout_ms: int = 15000,
) -> FastPathOutcome:
    """
    Run the dbo-native identity fast path for a LINE-ITEM entity whose family
    has NO mapping table left (U-361 — the U-349 retirement program's first
    line-item family, `vendorcredit_line_item`/`dbo.BillCreditLineItem`;
    `docs/design/u361-line-item-dbo-only-fastpath.md`). This is the dbo-only
    analog of `run_line_identity_fastpath` above and the line-scoped analog of
    `run_identity_fastpath_dbo_only` — a deliberate third sibling, not a mode
    flag on either, for the same reasons each of those gives for being separate:

      * vs. `run_identity_fastpath_dbo_only`: a line's identity is PARENT-SCOPED.
        QBO line ids are per-transaction sequence numbers ("1", "2", "3"…)
        reused across every parent, so the direct-read key is
        `(parent_local_id, qbo_line_id)` against the family's own
        `UQ_<Entity>LineItem_<Parent>Id_QboId` index — never `(qbo_id,
        realm_id)`. There is no `realm_id` parameter at all: the parent header
        pins the realm (resolved by the caller before any line runs), exactly
        as `run_line_identity_fastpath` documents.
      * vs. `run_line_identity_fastpath`: that helper's CONSISTENT/MISSING/
        CONFLICT machinery exists to catch drift between dbo identity and an
        independently-writable mapping table. Once the family's mapping table
        is retired there is no second store to disagree with, so the five
        mapping-specific parameters (`read_by_local_id`, `read_by_external_id`,
        `external_id_attr`, `record_conflict_issue`, `conflict_message`) vanish
        with it, and a direct hit is trusted outright — the filtered unique
        index plus `Set<Entity>LineItemQboIdentity`'s theft-clear UPDATE already
        guarantee at most one holder per `(parent, qbo_line_id)` at any instant.

    The two decisions that shape the MISS branch (design §3/§4, approved)
    ---------------------------------------------------------------------
    1. Create lock (Option A, parent+line-scoped). Two concurrent syncs of the
       same parent can both observe a MISS for the same line and both mint a
       row; the unique index + theft-clear resolve that SILENTLY (the loser
       orphans its own fresh row with no error). So a MISS re-reads and then
       creates+stamps INSIDE `qbo_dbo_line_identity_create:{lock_resource_label}:
       {parent_local_id}:{qbo_line_id}`. This is a NEW namespace, disjoint from
       both `qbo_dbo_identity_create:*` (the header helper's, keyed on the
       header's own QBO identity) and `qbo_mapping_create:*` (U-304's mapping-row
       race). Nesting order is fixed and one-directional: a header connector's
       CREATE path syncs lines from inside its own header create lock, so this
       lock is acquired NESTED INSIDE `qbo_dbo_identity_create:*` — never the
       reverse (this helper never touches a header lock), so no lock-ordering
       cycle is possible. It is genuinely reachable on its own, not just
       defense-in-depth: a header's HIT/update path syncs lines with NO header
       lock held, and reconciliation's missing-locally autofix projects a
       parent with no pull-level lock either — proven by test, not asserted
       (tests/test_u361_bill_credit_line_item_mapping_retire.py).
    2. Create-only, no adopt. A line has no independent adopt key: it is always
       created fresh under an already-resolved parent, so `resolve_candidate`
       for a line is "create it" — no side-channel business key two syncs could
       both resolve to, hence no `stamp_dbo_identity_with_lock`-style second
       lock and no `_check_no_conflicting` guard (Term's shape, U-352). The
       with-mapping helper's "never self-heal a MISSING line" rule (U-293's
       stale-orphan P1) does not carry over: that rule guarded against binding
       a mapping row to a stale orphan whose recycled id merely LOOKED like a
       new pairing. Here a direct hit on such an orphan is simply the in-place
       update the family's upsert-by-stable-line-id snapshot layer already
       performs whenever the id persists — the row follows QBO's current line.

    The helper-level guarantee (U-354/U-355's identity-stamp rollback race fix,
    now structural)
    -----------------------------------------------------------------------
    `resolve_candidate` has CREATED a row by the time `stamp_identity` runs. If
    the stamp then fails, that row is an UNSTAMPED ORPHAN: invisible to every
    future `read_direct_by_parent_and_qbo_line_id` (there is no mapping row to
    find it by any more), so the next pull MISSes again and mints a duplicate —
    silently, every pull, forever. The header connectors each hand-rolled a
    compensating delete for this (U-354 expense, U-355 bill); here it lives in
    the helper so a clone cannot forget it:
      * `stamp_identity` RAISES  → `rollback_candidate(candidate)` runs (best-
        effort, its own failure is logged and never masks the original), then
        the ORIGINAL exception re-raises so the caller's watermark holds.
      * `stamp_identity` returns a row whose `qbo_id` is not `qbo_line_id` (a
        stamp that silently did not land — e.g. `Set<Entity>LineItemQboIdentity`'s
        own atomic-pair guard declining to write QboId without a RealmId) →
        same rollback, then RuntimeError. The row we hand back MUST carry the
        identity we just resolved, or it is the orphan described above.
      * `stamp_identity` returns None → `raise_concurrent_write_race` with NO
        rollback: None means the candidate row is already gone (a concurrent
        delete between create and stamp), so there is nothing to compensate —
        the same reading `run_identity_fastpath_dbo_only` gives it.
      * `resolve_candidate` returns None → RuntimeError (transient hold): nothing
        was created, nothing to roll back, and no row means no identity to bind.
    `rollback_candidate` is optional only so a caller that has genuinely nothing
    to compensate can omit it; a caller whose `resolve_candidate` creates a row
    should ALWAYS wire it (a missing one is logged at ERROR when it would have
    run).

    What the caller still owns
    ---------------------------
    `read_direct_by_parent_and_qbo_line_id` — the entity service's own parent-
    scoped `read_by_qbo_identity(parent_id, qbo_id)`. `resolve_candidate` — the
    family's CREATE (fail closed BEFORE creating if the realm needed for the
    stamp is unavailable). `stamp_identity` — the bare `Set<Entity>LineItem
    QboIdentity` call on the candidate, returning a RE-READ of the row (the
    stamp is a void DB write that never mutates the in-memory candidate).
    `rollback_candidate` — the compensating delete (+ whatever the family records
    when even that fails). `apply_fields` / `on_apply_returned_none` — as in every
    sibling: the QBO-derived field write for an existing hit; a None return
    fires the callback and then ALWAYS raises via `raise_concurrent_write_race`
    (U-316's contract, so no caller can under-implement the callback into a
    silent `entity=None`).

    Returns:
        FastPathOutcome. `hit=False` ONLY when `qbo_line_id` is falsy (a dbo-only
        caller has no legacy path to fall back to); every other outcome — direct
        hit, race-resolved hit, or a genuine miss resolved via
        `resolve_candidate`/`stamp_identity` — reports `hit=True` and never
        carries `entity=None`.

    Raises:
        RuntimeError: lock-acquire timeout (FAILS CLOSED — never creates under
            uncertainty); `apply_fields`/`stamp_identity` returning None (via
            `raise_concurrent_write_race`); a stamp that did not land; a
            `resolve_candidate` that returned None. Always RuntimeError, never
            ValueError, so `record_projection_error` holds the record for retry
            instead of skipping it permanently.
        Whatever `resolve_candidate` or `stamp_identity` raise: re-raised
            unchanged (after the rollback, for a stamp failure).
    """
    if not qbo_line_id:
        return FastPathOutcome(hit=False)

    def _apply(row: Any) -> FastPathOutcome:
        return _apply_dbo_only_hit(
            row,
            apply_fields=apply_fields,
            on_apply_returned_none=on_apply_returned_none,
            entity_label=entity_label,
            external_label=external_label,
            log_suffix=(
                f"(direct parent-scoped dbo-only identity match: parent {parent_local_id}, "
                f"qbo_line_id={qbo_line_id})"
            ),
            path_label="dbo-only line fast path",
        )

    def _rollback(candidate: Any, cause: BaseException) -> None:
        """Best-effort compensating rollback of the just-created candidate. Never
        raises: the ORIGINAL failure (`cause`) is what the caller must see."""
        if rollback_candidate is None:
            logger.error(
                f"{entity_label} {getattr(candidate, 'id', None)} identity stamp failed "
                f"({cause}) and no rollback_candidate is wired - the row is left as an "
                f"UNSTAMPED ORPHAN under parent {parent_local_id} (qbo_line_id={qbo_line_id})."
            )
            return
        try:
            rollback_candidate(candidate)
        except Exception as rollback_exc:
            logger.error(
                f"Compensating rollback of {entity_label} {getattr(candidate, 'id', None)} "
                f"failed: {rollback_exc} (original failure: {cause})"
            )

    direct = read_direct_by_parent_and_qbo_line_id(parent_local_id, qbo_line_id)
    if direct:
        return _apply(direct)

    lock_resource = (
        f"qbo_dbo_line_identity_create:{lock_resource_label}:{parent_local_id}:{qbo_line_id}"
    )
    with qbo_app_lock(lock_resource, timeout_ms=lock_timeout_ms) as got_lock:
        if not got_lock:
            raise RuntimeError(
                f"Could not acquire dbo-only line identity create lock for {entity_label} "
                f"({external_label} qbo_line_id={qbo_line_id} under parent {parent_local_id}) "
                f"within {lock_timeout_ms}ms - holding for retry without creating."
            )

        direct_under_lock = read_direct_by_parent_and_qbo_line_id(parent_local_id, qbo_line_id)
        if direct_under_lock:
            logger.info(
                f"{entity_label} line identity race resolved: another sync already bound "
                f"{external_label} qbo_line_id={qbo_line_id} under parent {parent_local_id} to "
                f"{entity_label} {direct_under_lock.id} - adopting it instead of minting a "
                f"second row."
            )
            return _apply(direct_under_lock)

        candidate = resolve_candidate()
        if candidate is None:
            raise RuntimeError(
                f"resolve_candidate returned None for {entity_label} ({external_label} "
                f"qbo_line_id={qbo_line_id} under parent {parent_local_id}) - nothing was "
                f"created; holding for retry."
            )

        try:
            stamped = stamp_identity(candidate)
        except Exception as stamp_exc:
            _rollback(candidate, stamp_exc)
            raise

        if stamped is None:
            raise_concurrent_write_race(
                entity_label=entity_label,
                entity_id=candidate.id,
                path_label="dbo-only line fast path (stamp)",
            )
        if getattr(stamped, "qbo_id", None) != qbo_line_id:
            unstamped = RuntimeError(
                f"{entity_label} {candidate.id} identity stamp did not land: the re-read "
                f"carries qbo_id={getattr(stamped, 'qbo_id', None)!r}, expected "
                f"{qbo_line_id!r} (parent {parent_local_id}) - rolled back rather than "
                f"leave an unstamped orphan; holding for retry."
            )
            _rollback(candidate, unstamped)
            raise unstamped
        return FastPathOutcome(hit=True, entity=stamped)
