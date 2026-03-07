# Python Standard Library Imports
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class CategorizationSuggestion:
    """A single SubCostCode + Project suggestion for an uncategorized expense line."""
    sub_cost_code_id: Optional[int] = None
    sub_cost_code_number: Optional[str] = None
    sub_cost_code_name: Optional[str] = None
    project_id: Optional[int] = None
    project_name: Optional[str] = None
    project_abbreviation: Optional[str] = None
    confidence: float = 0.0
    source: str = "unknown"  # "vendor_history", "ai", "combined"
    reasoning: Optional[str] = None


@dataclass
class LineSuggestion:
    """Suggestions for a specific uncategorized QBO purchase line."""
    qbo_purchase_line_id: int = 0
    suggestions: List[CategorizationSuggestion] = field(default_factory=list)
    vendor_name: Optional[str] = None
    description: Optional[str] = None
    amount: Optional[float] = None
