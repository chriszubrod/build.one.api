# Python Standard Library Imports
from typing import Optional, Tuple

# Local Imports
from shared.api.responses import ENTRY_NOT_OWNER_PREFIX
from shared.authz import current_user_id, current_is_system_admin, current_can_view_team, current_is_system_context

TIME_TRACKING_MODULE = "Time Tracking"


def actor_scope() -> Tuple[Optional[int], Optional[bool], bool]:
    """
    Read the current request actor from ContextVars.

    Returns `(user_id, is_system_admin, can_view_team)` — the single
    source for actor scoping across the time_entry services. The
    2026-05-26 CanViewTeam migration threaded @ActorCanViewTeam through
    the sprocs; every service-layer repo call must pass all three
    components or PM/Controller team visibility silently collapses
    (the exact bug fixed 2026-06-10).
    """
    return (
        current_user_id.get(),
        current_is_system_admin.get(),
        current_can_view_team(TIME_TRACKING_MODULE),
    )


def assert_actor_owns_entry(
    entry_user_id: Optional[int],
    *,
    actor_user_id: Optional[int],
    actor_is_system_admin: Optional[bool],
) -> None:
    """
    U-766. Raise unless the actor is the entry's own worker (or exempt).

    CanViewTeam opens a teammate's day for READ and APPROVE/REJECT only.
    Edit, delete, submit and time-log writes call this with the entry's
    CURRENT owner. System admins and system context (workers, scheduler,
    CLI) are exempt. A missing actor id is refused. The caller passes the
    actor it already resolved for its scoped read, so both decisions use
    one reading of the request's actor.
    """
    if actor_is_system_admin or current_is_system_context.get():
        return
    if actor_user_id is None or entry_user_id != actor_user_id:
        raise ValueError(
            ENTRY_NOT_OWNER_PREFIX
            + "a teammate's day can be viewed and approved, but only its worker can edit, "
            "delete, submit or change its time logs."
        )
