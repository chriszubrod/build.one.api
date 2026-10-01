# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Any, Optional

# Third-party Imports

# Local Imports


@dataclass
class AdminAuditLog:
    id: Optional[int]
    public_id: Optional[str]
    created_datetime: Optional[str]
    actor_user_id: Optional[int]
    actor_is_system_admin: bool
    action: Optional[str]
    target_user_id: Optional[int]
    detail: Optional[dict[str, Any]]

    def to_dict(self) -> dict:
        return asdict(self)
