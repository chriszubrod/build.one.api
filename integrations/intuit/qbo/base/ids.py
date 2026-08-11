# Python Standard Library Imports
from typing import Optional, Union


def normalize_qbo_id(value) -> Optional[str]:
    """Canonical string form of a QBO record id. QBO returns Id as str or int;
    [QboId] is NVARCHAR(50) in SQL. Both sides of a reconcile diff MUST key
    through this one function or a type mismatch makes every mapped id look absent."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized if normalized else None


def coerce_id(value: Union[int, str]) -> int:
    """Canonical local-PK form (→int). QBO-side ids use `normalize_qbo_id` (→str)."""
    return int(value) if isinstance(value, str) else value
