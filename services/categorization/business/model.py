# Python Standard Library Imports
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any, List


class DocumentCategory(str, Enum):
    """Document categories for construction management."""
    # Financial Documents
    BILL = "bill"
    INVOICE = "invoice"
    RECEIPT = "receipt"
    PURCHASE_ORDER = "purchase_order"
    QUOTE = "quote"
    
    # Project Documents
    CHANGE_ORDER = "change_order"
    DELIVERY_TICKET = "delivery_ticket"
    WORK_ORDER = "work_order"
    
    # Legal/Compliance
    CONTRACT = "contract"
    LIEN_WAIVER = "lien_waiver"
    INSURANCE_CERTIFICATE = "insurance_certificate"
    PERMIT = "permit"
    
    # Other
    CORRESPONDENCE = "correspondence"
    PHOTO = "photo"
    DRAWING = "drawing"
    SPECIFICATION = "specification"
    OTHER = "other"
    UNKNOWN = "unknown"


class CategorizationStatus(str, Enum):
    """Status of categorization assignment."""
    PENDING = "pending"           # Not yet categorized
    AUTO_ASSIGNED = "auto_assigned"  # High confidence, auto-applied
    SUGGESTED = "suggested"       # Medium confidence, awaiting confirmation
    MANUAL = "manual"             # User manually assigned
    CONFIRMED = "confirmed"       # User confirmed AI suggestion
    REJECTED = "rejected"         # User rejected AI suggestion


@dataclass
class ExtractedFields:
    """
    Fields extracted from a document based on its category.
    
    Different categories will have different relevant fields populated.
    """
    # Common fields
    document_date: Optional[str] = None
    document_number: Optional[str] = None
    
    # Vendor/Party fields
    vendor_name: Optional[str] = None
    vendor_address: Optional[str] = None
    customer_name: Optional[str] = None
    
    # Financial fields
    amount: Optional[float] = None
    subtotal: Optional[float] = None
    tax_amount: Optional[float] = None
    total_amount: Optional[float] = None
    due_date: Optional[str] = None
    payment_terms: Optional[str] = None
    
    # Project fields
    project_name: Optional[str] = None
    project_number: Optional[str] = None
    job_number: Optional[str] = None
    
    # Line items (for invoices, POs, etc.)
    line_items: List[Dict[str, Any]] = field(default_factory=list)
    
    # Contract/Legal fields
    effective_date: Optional[str] = None
    expiration_date: Optional[str] = None
    parties: List[str] = field(default_factory=list)
    
    # Insurance/Compliance fields
    policy_number: Optional[str] = None
    coverage_amount: Optional[float] = None
    
    # Description/Notes
    description: Optional[str] = None
    notes: Optional[str] = None
    
    # Raw extraction data
    raw_fields: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary, excluding None values."""
        result = {}
        for key, value in self.__dict__.items():
            if value is not None and value != [] and value != {}:
                result[key] = value
        return result


@dataclass
class CategorizationResult:
    """
    Result of document categorization.
    
    Attributes:
        category: The determined document category
        confidence: Confidence score (0.0 to 1.0)
        status: Whether auto-assigned, suggested, or needs manual review
        extracted_fields: Category-specific extracted fields
        reasoning: Explanation of why this category was chosen
        alternative_categories: Other possible categories with scores
    """
    category: DocumentCategory
    confidence: float
    status: CategorizationStatus
    extracted_fields: Optional[ExtractedFields] = None
    reasoning: Optional[str] = None
    alternative_categories: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "category": self.category.value,
            "confidence": self.confidence,
            "status": self.status.value,
            "extracted_fields": self.extracted_fields.to_dict() if self.extracted_fields else None,
            "reasoning": self.reasoning,
            "alternative_categories": self.alternative_categories,
        }

    @property
    def is_high_confidence(self) -> bool:
        """Check if confidence is high enough for auto-assignment (>95%)."""
        return self.confidence >= 0.95

    @property
    def is_medium_confidence(self) -> bool:
        """Check if confidence is medium (70-95%)."""
        return 0.70 <= self.confidence < 0.95

    @property
    def is_low_confidence(self) -> bool:
        """Check if confidence is low (<70%)."""
        return self.confidence < 0.70
