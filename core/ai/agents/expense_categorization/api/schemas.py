# Python Standard Library Imports
from typing import Optional, List, Dict

# Third-party Imports
from pydantic import BaseModel, Field


# --- Suggest ---

class SuggestBatchRequest(BaseModel):
    realm_id: Optional[str] = None


class SuggestionItem(BaseModel):
    sub_cost_code_id: Optional[int] = None
    sub_cost_code_number: Optional[str] = None
    sub_cost_code_name: Optional[str] = None
    project_id: Optional[int] = None
    project_name: Optional[str] = None
    project_abbreviation: Optional[str] = None
    confidence: float = 0.0
    source: str = "unknown"
    reasoning: Optional[str] = None


class LineSuggestionItem(BaseModel):
    qbo_purchase_line_id: int
    vendor_name: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[float] = None
    suggestions: List[SuggestionItem] = Field(default_factory=list)


class SuggestBatchResponse(BaseModel):
    status_code: int = 200
    message: str = "OK"
    suggestions: Dict[str, LineSuggestionItem] = Field(default_factory=dict)


# --- Apply ---

class LineCategorizationInput(BaseModel):
    qbo_purchase_id: int
    qbo_purchase_line_id: int
    sub_cost_code_id: int
    project_public_id: Optional[str] = None


class ApplyBatchRequest(BaseModel):
    categorizations: List[LineCategorizationInput]
    push_to_qbo: bool = True
    realm_id: str


class ApplyBatchResponse(BaseModel):
    status_code: int = 200
    message: str = "OK"
    applied_count: int = 0
    errors: List[dict] = Field(default_factory=list)
    expense_public_ids: List[str] = Field(default_factory=list)
    qbo_push_results: List[dict] = Field(default_factory=list)


# --- Receipt Matching ---

class MatchReceiptsRequest(BaseModel):
    realm_id: Optional[str] = None


class ReceiptMatchItem(BaseModel):
    qbo_purchase_line_id: int
    match_type: str = "qbo_attachable"  # "qbo_attachable" or "inbox_email"
    message_id: Optional[str] = None
    subject: Optional[str] = None
    attachment_id: Optional[int] = None
    filename: Optional[str] = None
    confidence: float = 0.0
    match_signals: Optional[dict] = None


class MatchReceiptsResponse(BaseModel):
    status_code: int = 200
    message: str = "OK"
    matches: List[ReceiptMatchItem] = Field(default_factory=list)


class ConfirmReceiptMatchRequest(BaseModel):
    qbo_purchase_line_id: int
    attachment_id: Optional[int] = None
    message_id: Optional[str] = None


# --- Link Purchase ---

class LinkPurchaseRequest(BaseModel):
    expense_public_id: str
    qbo_purchase_id: int
    realm_id: str


class LinkPurchaseResponse(BaseModel):
    status_code: int = 200
    message: str = "OK"
    expense_public_id: Optional[str] = None
    has_qbo_purchase_mapping: bool = False
    qbo_push_result: Optional[dict] = None
