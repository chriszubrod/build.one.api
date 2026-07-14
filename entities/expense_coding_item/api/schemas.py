# Third-party Imports
from pydantic import BaseModel, Field


class FlagExpenseCodingItemRequest(BaseModel):
    reason: str = Field(..., min_length=1)
