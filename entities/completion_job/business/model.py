# Python Standard Library Imports
from dataclasses import dataclass
from typing import Optional


@dataclass
class CompletionJob:
    id: int
    entity_type: str
    entity_public_id: str
    status: str
    attempts: int
    max_attempts: int
    claimed_at: Optional[str]
    last_error: Optional[str]
    public_id: Optional[str] = None
    was_created: bool = False
