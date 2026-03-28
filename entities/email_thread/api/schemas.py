from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, field_validator


class EmailThreadCorrectClassification(BaseModel):
    """
    Request body for POST /correct-classification/{public_id}.
    Corrects a misclassified EmailThread and resets it to RECEIVED
    so it re-processes under the correct process type.
    """
    row_version:             str   # base64 encoded — optimistic concurrency
    new_classification_type: str   # must be a valid email process registry key
    notes:                   Optional[str] = None  # max 500 — reason for correction

    @field_validator("new_classification_type")
    @classmethod
    def validate_classification_type(cls, v: str) -> str:
        valid = {
            "BILL_DOCUMENT",
            "BILL_CREDIT_DOCUMENT",
            "EXPENSE_DOCUMENT",
            "EXPENSE_REFUND_DOCUMENT",
            "UNKNOWN",
        }
        if v not in valid:
            raise ValueError(
                f"Invalid classification type '{v}'. "
                f"Must be one of: {sorted(valid)}"
            )
        return v

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v) > 500:
            raise ValueError("notes must be 500 characters or fewer.")
        return v


class EmailThreadResponse(BaseModel):
    """
    Standard response shape for EmailThread API endpoints.
    Mirrors the to_dict() output of the EmailThread dataclass.
    """
    public_id:                  str
    category:                   Optional[str]
    process_type:               Optional[str]
    current_stage:              Optional[str]
    is_reply:                   Optional[bool]
    is_forward:                 Optional[bool]
    internet_message_id:        Optional[str]
    subject:                    Optional[str]
    owner_user_id:              Optional[int]
    classification_confidence:  Optional[float]
    is_resolved:                Optional[bool]
    requires_action:            Optional[bool]
    created_datetime:           Optional[str]
    updated_datetime:           Optional[str]
