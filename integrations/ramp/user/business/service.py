# Python Standard Library Imports
from dataclasses import dataclass
from typing import Dict, List, Optional

# Local Imports
from integrations.ramp.user.external.client import RampUserExternalClient


ACTIVE_USER_STATUSES = frozenset({"USER_ACTIVE", "active"})


@dataclass(frozen=True)
class RampUserRosterEntry:
    ramp_user_id: str
    email: Optional[str]
    status: Optional[str]
    is_active: bool


class RampUserService:
    def __init__(self, external_client: RampUserExternalClient):
        self._client = external_client

    def build_roster(self) -> Dict[str, RampUserRosterEntry]:
        users = self._client.list_all_users()
        roster: Dict[str, RampUserRosterEntry] = {}
        for raw in users:
            user_id = raw.get("id")
            if user_id is None:
                continue
            status = raw.get("status")
            status_str = str(status) if status is not None else None
            is_active = status_str in ACTIVE_USER_STATUSES
            email_raw = raw.get("email")
            email = (str(email_raw).strip() or None) if email_raw else None
            roster[str(user_id)] = RampUserRosterEntry(
                ramp_user_id=str(user_id),
                email=email,
                status=status_str,
                is_active=is_active,
            )
        return roster
