# Python Standard Library Imports
from dataclasses import dataclass, asdict
from typing import Optional

# Third-party Imports

# Local Imports


@dataclass
class ResolvedRecipient:
    user_id: int
    firstname: Optional[str]
    lastname: Optional[str]
    email: Optional[str]
    role_name: str
    project_id: int

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.firstname, self.lastname) if p]
        if parts:
            return " ".join(parts)
        if self.email:
            return self.email
        return f"User {self.user_id}"

    def to_dict(self) -> dict:
        return asdict(self)
