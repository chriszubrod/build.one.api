# Python Standard Library Imports
from typing import Optional, Tuple

# Local Imports
from shared.authz import current_user_id, current_is_system_admin, current_can_view_team

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
