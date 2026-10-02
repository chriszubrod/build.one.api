# Python Standard Library Imports
from typing import Literal, Optional

# Third-party Imports
from pydantic import BaseModel, Field

# Local Imports


class ReviewSubmitRequest(BaseModel):
    comments: Optional[str] = Field(
        default=None,
        description="Optional comments to record on the submission entry.",
    )
    expected_row_version: Optional[str] = Field(
        default=None,
        description=("For a ContractLabor parent: the labor row_version the reviewer read. Refused if the "
                     "labor was rebuilt since; REQUIRED when its time entry carries reopened_after_submit (U-596)."),
    )


class ReviewAdvanceRequest(BaseModel):
    comments: Optional[str] = Field(
        default=None,
        description="Optional comments to record on the advance entry.",
    )
    expected_row_version: Optional[str] = Field(
        default=None,
        description=("For a ContractLabor parent: the labor row_version the reviewer read. Refused if the "
                     "labor was rebuilt since; REQUIRED when its time entry carries reopened_after_submit (U-596)."),
    )


class ReviewDeclineRequest(BaseModel):
    target_status_public_id: Optional[str] = Field(
        default=None,
        description=(
            "Optional public_id of a declined ReviewStatus. Required when more "
            "than one declined status is configured. If omitted and exactly "
            "one declined status exists, that one is used."
        ),
    )
    comments: Optional[str] = Field(
        default=None,
        description="Optional comments to record on the decline entry.",
    )
    expected_row_version: Optional[str] = Field(
        default=None,
        description=("For a ContractLabor parent: the labor row_version the reviewer read. Refused if the "
                     "labor was rebuilt since; REQUIRED when its time entry carries reopened_after_submit (U-596)."),
    )


class BillReviewDecisionApplyRequest(BaseModel):
    decision: Literal["approved", "rejected"] = Field(
        description="Terminal reviewer decision; approval carries the SCC coding."
    )
    sub_cost_code_public_id: Optional[str] = Field(
        default=None,
        description="Required when decision='approved'.",
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional summary-line description on approval.",
    )
    idempotency_key: str = Field(
        description="Client UUID for this queued gesture; reused on offline retries."
    )
    line_row_version: str = Field(
        description="ROWVERSION of the sole bill line item being recoded."
    )
    expected_review_public_id: Optional[str] = Field(
        default=None,
        description="Latest review the client knew about, or null when none.",
    )


class BillReviewDecisionApplyResponse(BaseModel):
    decision_applied: str
    review_status: Optional[str] = None
    reviewer_user_id: int
    is_draft: bool
    bill_public_id: str
    review_public_id: str
    replayed: bool = False


class BillReviewDecisionApplyEnvelope(BaseModel):
    data: BillReviewDecisionApplyResponse
