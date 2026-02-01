# Python Standard Library Imports
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Dict, Any


class AnomalyType(str, Enum):
    """Types of anomalies that can be detected."""
    EXACT_DUPLICATE = "exact_duplicate"      # Same file hash
    NEAR_DUPLICATE = "near_duplicate"        # High semantic similarity
    SIMILAR_CONTENT = "similar_content"      # Moderate semantic similarity
    # Future anomaly types (extensible)
    UNUSUAL_AMOUNT = "unusual_amount"
    TIMING_ANOMALY = "timing_anomaly"
    MISSING_INFO = "missing_info"
    SUSPICIOUS_PATTERN = "suspicious_pattern"


class AnomalySeverity(str, Enum):
    """Severity levels for anomalies."""
    LOW = "low"           # Informational, no action required
    MEDIUM = "medium"     # Should be reviewed
    HIGH = "high"         # Requires immediate attention, may be blocked


@dataclass
class RelatedDocument:
    """A document related to an anomaly."""
    public_id: str
    filename: Optional[str]
    category: Optional[str]
    similarity_score: Optional[float] = None
    match_reason: Optional[str] = None


@dataclass
class AnomalyResult:
    """
    Result of anomaly detection for an attachment.
    
    Attributes:
        has_anomaly: Whether any anomaly was detected
        anomaly_type: Type of anomaly detected
        severity: Severity level of the anomaly
        blocked: Whether the attachment should be blocked from processing
        flagged: Whether the attachment should be flagged for review
        notification_required: Whether user notification is needed
        message: Human-readable description of the anomaly
        details: Additional details about the anomaly
        related_documents: List of related documents (e.g., duplicates)
    """
    has_anomaly: bool = False
    anomaly_type: Optional[AnomalyType] = None
    severity: Optional[AnomalySeverity] = None
    blocked: bool = False
    flagged: bool = False
    notification_required: bool = False
    message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    related_documents: List[RelatedDocument] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "has_anomaly": self.has_anomaly,
            "anomaly_type": self.anomaly_type.value if self.anomaly_type else None,
            "severity": self.severity.value if self.severity else None,
            "blocked": self.blocked,
            "flagged": self.flagged,
            "notification_required": self.notification_required,
            "message": self.message,
            "details": self.details,
            "related_documents": [
                {
                    "public_id": rd.public_id,
                    "filename": rd.filename,
                    "category": rd.category,
                    "similarity_score": rd.similarity_score,
                    "match_reason": rd.match_reason,
                }
                for rd in self.related_documents
            ],
        }

    @classmethod
    def no_anomaly(cls) -> "AnomalyResult":
        """Create a result indicating no anomaly was detected."""
        return cls(
            has_anomaly=False,
            message="No anomalies detected",
        )

    @classmethod
    def exact_duplicate(cls, related: List[RelatedDocument]) -> "AnomalyResult":
        """Create a result for an exact duplicate detection."""
        return cls(
            has_anomaly=True,
            anomaly_type=AnomalyType.EXACT_DUPLICATE,
            severity=AnomalySeverity.HIGH,
            blocked=True,
            flagged=True,
            notification_required=True,
            message=f"Exact duplicate detected. This file matches {len(related)} existing document(s).",
            details={"match_count": len(related)},
            related_documents=related,
        )

    @classmethod
    def near_duplicate(
        cls, 
        related: List[RelatedDocument], 
        max_similarity: float
    ) -> "AnomalyResult":
        """Create a result for a near duplicate detection."""
        return cls(
            has_anomaly=True,
            anomaly_type=AnomalyType.NEAR_DUPLICATE,
            severity=AnomalySeverity.MEDIUM,
            blocked=False,
            flagged=True,
            notification_required=True,
            message=f"Near duplicate detected ({max_similarity:.0%} similar). Review recommended.",
            details={
                "match_count": len(related),
                "max_similarity": max_similarity,
            },
            related_documents=related,
        )

    @classmethod
    def similar_content(
        cls,
        related: List[RelatedDocument],
        max_similarity: float
    ) -> "AnomalyResult":
        """Create a result for similar content detection (informational)."""
        return cls(
            has_anomaly=True,
            anomaly_type=AnomalyType.SIMILAR_CONTENT,
            severity=AnomalySeverity.LOW,
            blocked=False,
            flagged=False,
            notification_required=False,
            message=f"Similar content found ({max_similarity:.0%} similar).",
            details={
                "match_count": len(related),
                "max_similarity": max_similarity,
            },
            related_documents=related,
        )
