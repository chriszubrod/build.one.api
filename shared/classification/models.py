"""
Classification Models
=====================
Shared data structures for AI classification results.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List


class ClassificationType(Enum):
    BILL_DOCUMENT = "BILL_DOCUMENT"
    BILL_CREDIT_DOCUMENT = "BILL_CREDIT_DOCUMENT"
    EXPENSE = "EXPENSE"
    INQUIRY = "INQUIRY"
    STATEMENT = "STATEMENT"
    UNKNOWN = "UNKNOWN"


LABEL_MAP = {
    ClassificationType.BILL_DOCUMENT: "Bill",
    ClassificationType.BILL_CREDIT_DOCUMENT: "Bill Credit",
    ClassificationType.EXPENSE: "Expense",
    ClassificationType.INQUIRY: "Inquiry",
    ClassificationType.STATEMENT: "Statement",
    ClassificationType.UNKNOWN: "Unknown",
}


@dataclass
class ClassificationResult:
    message_type: ClassificationType = ClassificationType.UNKNOWN
    classification: str = "UNKNOWN"
    confidence: float = 0.0
    signals: List[str] = field(default_factory=list)
    suggested_label: str = "UNKNOWN"
