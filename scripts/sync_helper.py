# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports


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
