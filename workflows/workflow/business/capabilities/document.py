# Python Standard Library Imports
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Local Imports
from workflows.workflow.business.capabilities.base import Capability, CapabilityResult, with_retry

logger = logging.getLogger(__name__)


@dataclass
class ExtractedDocument:
    """Result of document extraction."""
    text: str
    pages: int
    tables: List[Dict] = field(default_factory=list)
    key_value_pairs: Dict[str, str] = field(default_factory=dict)
    confidence: float = 0.0
    raw_result: Optional[Any] = None


class DocumentCapabilities(Capability):
    """
    Document extraction capabilities using Azure Document Intelligence.
    
    Provides OCR and structured extraction from documents.
    """
    
    @property
    def name(self) -> str:
        return "document"
    
    def __init__(self):
        self._client = None
    
    def _get_client(self):
        """Lazy load the Document Intelligence client."""
        if self._client is None:
            from integrations.azure.ai.document_intelligence import AzureDocumentIntelligence
            self._client = AzureDocumentIntelligence()
        return self._client
    
    @with_retry(max_attempts=3, base_delay=2.0)
    def extract_from_bytes(
        self,
        file_content: bytes,
        content_type: str = "application/pdf",
    ) -> CapabilityResult:
        """
        Extract text and structure from document bytes.
        
        Args:
            file_content: Raw file bytes
            content_type: MIME type of the document
            
        Returns:
            CapabilityResult with ExtractedDocument data
        """
        self._log_call(
            "extract_from_bytes",
            content_length=len(file_content),
            content_type=content_type,
        )
        
        try:
            client = self._get_client()
            # extract_document returns ExtractionResult on success, raises on failure
            extraction = client.extract_document(
                file_content=file_content,
                content_type=content_type,
            )
            
            extracted = ExtractedDocument(
                text=extraction.content or "",
                pages=len(extraction.pages) if extraction.pages else 1,
                tables=extraction.tables or [],
                key_value_pairs={kv.get("key", ""): kv.get("value", "") for kv in (extraction.key_value_pairs or [])},
                confidence=0.9,  # Document Intelligence doesn't return overall confidence
                raw_result=extraction,
            )
            
            result = CapabilityResult.ok(data=extracted)
            self._log_result("extract_from_bytes", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "extract_from_bytes")
    
    @with_retry(max_attempts=3, base_delay=2.0)
    def extract_from_url(
        self,
        document_url: str,
    ) -> CapabilityResult:
        """
        Extract text and structure from a document URL.
        
        Args:
            document_url: URL to the document (e.g., blob storage URL)
            
        Returns:
            CapabilityResult with ExtractedDocument data
        """
        self._log_call("extract_from_url", document_url=document_url)
        
        try:
            client = self._get_client()
            extraction_result = client.extract_document_from_url(document_url=document_url)
            
            if not extraction_result.success:
                return CapabilityResult.fail(
                    error=extraction_result.error or "Document extraction failed",
                )
            
            extracted = ExtractedDocument(
                text=extraction_result.text or "",
                pages=extraction_result.pages or 1,
                tables=extraction_result.tables or [],
                key_value_pairs=extraction_result.key_value_pairs or {},
                confidence=extraction_result.confidence or 0.0,
                raw_result=extraction_result,
            )
            
            result = CapabilityResult.ok(data=extracted)
            self._log_result("extract_from_url", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "extract_from_url")
    
    def extract_invoice_fields(
        self,
        file_content: bytes,
        content_type: str = "application/pdf",
    ) -> CapabilityResult:
        """
        Extract invoice-specific fields from a document.
        
        Uses a specialized invoice model if available.
        Note: No retry decorator since it calls extract_from_bytes which has retry.
        
        Args:
            file_content: Raw file bytes
            content_type: MIME type of the document
            
        Returns:
            CapabilityResult with extracted invoice fields
        """
        self._log_call(
            "extract_invoice_fields",
            content_length=len(file_content),
            content_type=content_type,
        )
        
        try:
            # First, do basic extraction (has retry logic)
            extract_result = self.extract_from_bytes(file_content, content_type)
            if not extract_result.success:
                return extract_result
            
            extracted: ExtractedDocument = extract_result.data
            
            # Look for invoice-specific fields in key-value pairs
            invoice_fields = {
                "vendor_name": None,
                "invoice_number": None,
                "invoice_date": None,
                "due_date": None,
                "total_amount": None,
                "line_items": [],
                "raw_text": extracted.text,
            }
            
            # Map common key-value pairs to invoice fields
            kv = extracted.key_value_pairs
            field_mappings = {
                "vendor": ["vendor", "from", "bill from", "company"],
                "invoice_number": ["invoice", "inv", "invoice #", "invoice no", "bill #"],
                "invoice_date": ["date", "invoice date", "bill date"],
                "due_date": ["due", "due date", "payment due"],
                "total_amount": ["total", "amount due", "balance due", "grand total"],
            }
            
            for field, keywords in field_mappings.items():
                for key, value in kv.items():
                    if any(kw in key.lower() for kw in keywords):
                        if field == "vendor_name":
                            invoice_fields["vendor_name"] = value
                        elif field == "invoice_number":
                            invoice_fields["invoice_number"] = value
                        elif field == "invoice_date":
                            invoice_fields["invoice_date"] = value
                        elif field == "due_date":
                            invoice_fields["due_date"] = value
                        elif field == "total_amount":
                            # Try to parse as number
                            try:
                                amount_str = value.replace("$", "").replace(",", "").strip()
                                invoice_fields["total_amount"] = float(amount_str)
                            except ValueError:
                                invoice_fields["total_amount"] = value
                        break
            
            # Extract line items from tables
            if extracted.tables:
                for table in extracted.tables:
                    # Simple heuristic: tables with description/amount columns are line items
                    invoice_fields["line_items"].extend(table.get("rows", []))
            
            result = CapabilityResult.ok(
                data=invoice_fields,
                pages=extracted.pages,
                confidence=extracted.confidence,
            )
            self._log_result("extract_invoice_fields", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "extract_invoice_fields")
