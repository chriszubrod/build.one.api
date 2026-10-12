# Third-party Imports
from pydantic import BaseModel, Field
from typing import Optional


class FlagCodingSuggestionRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class ConfirmCodingSuggestionRequest(BaseModel):
    project_public_id: str
    sub_cost_code_public_id: str
    description: Optional[str] = None
    was_overridden: bool = False
