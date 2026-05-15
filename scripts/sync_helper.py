# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports


def assert_cli_system_admin() -> None:
    """
    CLI sync scripts span all users by design; declare system intent so the
    per-row access guards in shared/access.py bypass for these reads.
    Mirrors what `_require_drain_secret` does for HTTP-triggered drains.

    Call this as the first statement under `if __name__ == "__main__":` in
    every sync script. Safe to call when the script is imported (it just
    sets a ContextVar) but should only be reached when the script is the
    program entry point.
    """
    from shared.authz.context import set_authz_context
    set_authz_context(user_id=None, company_id=None, is_system_admin=True)


def _normalize_last_sync(last_sync: Optional[str]) -> Optional[str]:
    if not last_sync:
        return None
    # QBO accepts 'YYYY-MM-DDTHH:MM:SSZ' or '...+00:00'. Ensure Z form for safety.
    if last_sync.endswith("Z"):
        return last_sync
    if last_sync.endswith("+00:00"):
        return last_sync[:-6] + "Z"
    # If naive or other offset, coerce to Z (assumes stored in UTC)
    if "T" in last_sync:
        return last_sync.split(".")[0] + "Z"
    return last_sync
