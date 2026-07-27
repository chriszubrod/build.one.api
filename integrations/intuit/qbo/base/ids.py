# Python Standard Library Imports
from typing import Optional


def normalize_qbo_id(value) -> Optional[str]:
    """Canonical string form of a QBO record id. QBO returns Id as str or int;
    [QboId] is NVARCHAR(50) in SQL. Both sides of a reconcile diff MUST key
    through this one function or a type mismatch makes every mapped id look absent."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized if normalized else None
