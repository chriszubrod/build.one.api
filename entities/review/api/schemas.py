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
    confirm_not_duplicate: bool = Field(
        default=False,
        description="U-723: acknowledge visible possible-duplicate matches on bill submit.",
    )
    confirmed_duplicate_public_ids: list[str] = Field(
        default_factory=list,
        description="U-723: public ids of possible duplicates the caller reviewed.",
    )


class ReviewAdvanceRequest(BaseModel):
    comments: Optional[str] = Field(
        default=None,
        description="Optional comments to record on the advance entry.",
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


class EmailedReviewerDecisionRequestBase(BaseModel):
    """Shared body for the emailed POST /{bill,expense}/{public_id}/apply-reviewer-decision.

    U-762: ONE field set for both entity request models so they cannot drift.
    Field descriptions are kept byte-identical to the original expense model
    so the expense route's OpenAPI shape is unchanged.
    """

    decision: str = Field(
        description="Review outcome: 'approved' or 'rejected'."
    )
    reviewer_email: str = Field(
        description=(
            "Email of the PM/Owner who replied; must match the expense's "
            "notification recipient envelope."
        )
    )
    sub_cost_code_public_id: Optional[str] = Field(
        default=None,
        description="Required when decision is 'approved'."
    )
    description: Optional[str] = Field(
        default=None,
        description="Optional line description update on approval."
    )
    raw_reply_text: Optional[str] = Field(
        default=None,
        description="PM reply body persisted to Review.Comments when non-empty."
    )
    reviewer_email_message_public_id: str = Field(
        ...,
        description="Required inbound reply EmailMessage public id; the server refuses without provenance binding.",
    )


class ReviewDecisionApplyRequestBase(BaseModel):
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
    expected_review_public_id: Optional[str] = Field(
        default=None,
        description="Latest review the client knew about, or null when none.",
    )


class BillReviewDecisionApplyRequest(ReviewDecisionApplyRequestBase):
    line_row_version: str = Field(
        description="ROWVERSION of the sole bill line item being recoded."
    )


class ExpenseReviewDecisionApplyRequest(ReviewDecisionApplyRequestBase):
    line_row_version: str = Field(
        description="ROWVERSION of the sole expense line item being recoded."
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


class ExpenseReviewDecisionApplyResponse(BaseModel):
    decision_applied: str
    review_status: Optional[str] = None
    reviewer_user_id: int
    is_draft: bool
    expense_public_id: str
    review_public_id: str
    replayed: bool = False


class ExpenseReviewDecisionApplyEnvelope(BaseModel):
    data: ExpenseReviewDecisionApplyResponse
