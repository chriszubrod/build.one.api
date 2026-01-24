# Python Standard Library Imports
import json
import logging
from typing import Optional, Dict, Any, List

# Third-party Imports

# Local Imports
from modules.categorization.business.model import (
    DocumentCategory,
    CategorizationResult,
    CategorizationStatus,
    ExtractedFields,
)
from modules.attachment.business.model import Attachment
from modules.attachment.business.service import AttachmentService
from modules.attachment.business.extraction_service import ExtractionService
from integrations.azure.ai import AzureOpenAIClient

logger = logging.getLogger(__name__)


# System prompt for document categorization
CATEGORIZATION_SYSTEM_PROMPT = """You are a document classification expert for a construction management system.

Your task is to:
1. Classify the document into one of the predefined categories
2. Extract relevant fields based on the category
3. Provide a confidence score (0.0 to 1.0) for your classification

CRITICAL DISTINCTION - Bill vs Invoice:
The user's company is a CONSTRUCTION CONTRACTOR. You must determine the DIRECTION of the document:

- bill: A document FROM a vendor/supplier TO the user's company requesting payment.
  Signs it's a BILL: 
  * Vendor/supplier letterhead or "From" field shows another company
  * "Bill To" or "Ship To" shows the user's company (a contractor)
  * Phrases like "Amount Due", "Please Remit", "Payment Due"
  * Materials, supplies, equipment, or subcontractor services being charged TO user
  
- invoice: A document FROM the user's company TO their customer/client.
  Signs it's an INVOICE:
  * User's company appears in "From" or header as the sender
  * A property owner, developer, or GC is in the "Bill To" field
  * Document shows work performed BY the user for someone else
  * Progress billing, applications for payment, or service charges TO a client

When in doubt: If the document is requesting payment FROM the user (they owe money), it's a BILL.
If the document is requesting payment TO the user (someone owes them), it's an INVOICE.

CATEGORIES:
- bill: A bill/invoice FROM a vendor TO us (we owe them money - accounts payable)
- invoice: An invoice FROM us TO a customer (they owe us money - accounts receivable)
- receipt: Proof of payment or transaction receipt
- purchase_order: A PO we issue authorizing purchase of goods/services from a vendor
- quote: A price quote or estimate from a vendor
- change_order: A change order modifying project scope/cost
- delivery_ticket: Proof of material delivery
- work_order: Authorization to perform specific work
- contract: A legal contract or agreement
- lien_waiver: A lien waiver or release document
- insurance_certificate: Certificate of insurance (COI)
- permit: Construction or building permit
- correspondence: Letters, emails, memos
- photo: Photographs or images
- drawing: Architectural or engineering drawings
- specification: Technical specifications
- other: Document that doesn't fit other categories
- unknown: Unable to determine document type

RESPOND WITH JSON ONLY in this exact format:
{
    "category": "category_name",
    "confidence": 0.85,
    "reasoning": "Brief explanation of why this category was chosen",
    "alternative_categories": [
        {"category": "other_category", "confidence": 0.10}
    ],
    "extracted_fields": {
        "vendor_name": "extracted vendor name or null",
        "document_date": "YYYY-MM-DD or null",
        "document_number": "invoice/PO number or null",
        "total_amount": 1234.56,
        "due_date": "YYYY-MM-DD or null",
        "project_name": "project name or null",
        "description": "brief description of document content"
    }
}

Be conservative with confidence scores:
- Only use 0.95+ when document clearly matches category with identifying headers/format
- Use 0.70-0.94 when document likely matches but some ambiguity exists
- Use below 0.70 when uncertain or document is unclear

For extracted_fields, only include fields that are clearly present in the document.
Use null for fields that cannot be determined."""


class CategorizationConfig:
    """Configuration for categorization thresholds."""
    HIGH_CONFIDENCE_THRESHOLD = 0.95   # Auto-assign
    MEDIUM_CONFIDENCE_THRESHOLD = 0.70  # Suggest
    # Below 0.70 = manual review required


class CategorizationService:
    """
    Service for automatically categorizing documents.
    
    Uses GPT-4o-mini to analyze extracted text and classify documents
    into appropriate categories with field extraction.
    """

    def __init__(
        self,
        attachment_service: Optional[AttachmentService] = None,
        extraction_service: Optional[ExtractionService] = None,
        openai_client: Optional[AzureOpenAIClient] = None,
        config: Optional[CategorizationConfig] = None,
    ):
        """Initialize the CategorizationService."""
        self._attachment_service = attachment_service
        self._extraction_service = extraction_service
        self._openai_client = openai_client
        self.config = config or CategorizationConfig()

    @property
    def attachment_service(self) -> AttachmentService:
        """Lazy load attachment service."""
        if self._attachment_service is None:
            self._attachment_service = AttachmentService()
        return self._attachment_service

    @property
    def extraction_service(self) -> ExtractionService:
        """Lazy load extraction service."""
        if self._extraction_service is None:
            self._extraction_service = ExtractionService()
        return self._extraction_service

    @property
    def openai_client(self) -> AzureOpenAIClient:
        """Lazy load OpenAI client."""
        if self._openai_client is None:
            self._openai_client = AzureOpenAIClient()
        return self._openai_client

    def categorize_attachment(
        self,
        attachment: Attachment,
        force: bool = False,
    ) -> Optional[CategorizationResult]:
        """
        Categorize an attachment.

        Args:
            attachment: The attachment to categorize.
            force: If True, re-categorize even if already categorized.

        Returns:
            CategorizationResult or None if categorization not possible.
        """
        logger.info(f"Categorizing attachment {attachment.id}")

        # Check if extraction is complete
        if attachment.extraction_status != "completed":
            logger.warning(
                f"Attachment {attachment.id} not extracted, cannot categorize"
            )
            return None

        # Get extracted text
        extracted_text = self.extraction_service.get_extracted_text(attachment)
        if not extracted_text:
            logger.warning(f"No extracted text for attachment {attachment.id}")
            return None

        # Truncate text if too long (keep first 8000 chars for context window)
        if len(extracted_text) > 8000:
            extracted_text = extracted_text[:8000] + "\n\n[Document truncated...]"

        # Build user prompt with document context
        user_prompt = self._build_categorization_prompt(attachment, extracted_text)

        # Call GPT-4o-mini for categorization
        try:
            response = self.openai_client.chat_completion_with_json(
                messages=[
                    {"role": "system", "content": CATEGORIZATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,  # Low temperature for consistent classification
            )
        except Exception as e:
            logger.error(f"Error calling OpenAI for categorization: {e}")
            return None

        # Parse response
        result = self._parse_categorization_response(response)
        
        if result:
            logger.info(
                f"Categorized attachment {attachment.id} as {result.category.value} "
                f"with {result.confidence:.0%} confidence ({result.status.value})"
            )

        return result

    def categorize_attachment_by_id(
        self,
        attachment_id: int,
        force: bool = False,
    ) -> Optional[CategorizationResult]:
        """Categorize an attachment by ID."""
        attachment = self.attachment_service.read_by_id(attachment_id)
        if not attachment:
            logger.warning(f"Attachment {attachment_id} not found")
            return None
        return self.categorize_attachment(attachment, force)

    def categorize_attachment_by_public_id(
        self,
        public_id: str,
        force: bool = False,
    ) -> Optional[CategorizationResult]:
        """Categorize an attachment by public ID."""
        attachment = self.attachment_service.read_by_public_id(public_id)
        if not attachment:
            logger.warning(f"Attachment {public_id} not found")
            return None
        return self.categorize_attachment(attachment, force)

    def categorize_text(self, text: str, filename: Optional[str] = None) -> Optional[CategorizationResult]:
        """
        Categorize raw text without an attachment.
        
        Useful for testing or pre-upload categorization.

        Args:
            text: The document text to categorize.
            filename: Optional filename for context.

        Returns:
            CategorizationResult or None if categorization fails.
        """
        if not text:
            return None

        # Truncate if needed
        if len(text) > 8000:
            text = text[:8000] + "\n\n[Document truncated...]"

        # Build prompt
        user_prompt = f"FILENAME: {filename or 'unknown'}\n\nDOCUMENT CONTENT:\n{text}"

        try:
            response = self.openai_client.chat_completion_with_json(
                messages=[
                    {"role": "system", "content": CATEGORIZATION_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
            )
        except Exception as e:
            logger.error(f"Error calling OpenAI for categorization: {e}")
            return None

        return self._parse_categorization_response(response)

    def _build_categorization_prompt(
        self,
        attachment: Attachment,
        extracted_text: str,
    ) -> str:
        """Build the user prompt for categorization."""
        prompt_parts = []

        # Add filename context
        filename = attachment.original_filename or attachment.filename or "unknown"
        prompt_parts.append(f"FILENAME: {filename}")

        # Add file type context
        if attachment.content_type:
            prompt_parts.append(f"FILE TYPE: {attachment.content_type}")

        # Add existing category if present (for context, not to bias)
        if attachment.category and attachment.category != "unknown":
            prompt_parts.append(f"CURRENT CATEGORY (may be incorrect): {attachment.category}")

        # Add the document content
        prompt_parts.append(f"\nDOCUMENT CONTENT:\n{extracted_text}")

        return "\n".join(prompt_parts)

    def _parse_categorization_response(
        self,
        response: Dict[str, Any],
    ) -> Optional[CategorizationResult]:
        """Parse the GPT response into a CategorizationResult."""
        try:
            # Get category
            category_str = response.get("category", "unknown").lower()
            try:
                category = DocumentCategory(category_str)
            except ValueError:
                category = DocumentCategory.UNKNOWN

            # Get confidence
            confidence = float(response.get("confidence", 0.0))
            confidence = max(0.0, min(1.0, confidence))  # Clamp to 0-1

            # Determine status based on confidence
            if confidence >= self.config.HIGH_CONFIDENCE_THRESHOLD:
                status = CategorizationStatus.AUTO_ASSIGNED
            elif confidence >= self.config.MEDIUM_CONFIDENCE_THRESHOLD:
                status = CategorizationStatus.SUGGESTED
            else:
                status = CategorizationStatus.PENDING

            # Parse extracted fields
            fields_data = response.get("extracted_fields", {})
            extracted_fields = self._parse_extracted_fields(fields_data)

            # Get alternatives
            alternatives = response.get("alternative_categories", [])

            return CategorizationResult(
                category=category,
                confidence=confidence,
                status=status,
                extracted_fields=extracted_fields,
                reasoning=response.get("reasoning"),
                alternative_categories=alternatives,
            )

        except Exception as e:
            logger.error(f"Error parsing categorization response: {e}")
            return None

    def _parse_extracted_fields(self, fields_data: Dict[str, Any]) -> ExtractedFields:
        """Parse extracted fields from GPT response."""
        fields = ExtractedFields()

        # Map response fields to ExtractedFields attributes
        field_mapping = {
            "document_date": "document_date",
            "document_number": "document_number",
            "vendor_name": "vendor_name",
            "vendor_address": "vendor_address",
            "customer_name": "customer_name",
            "amount": "amount",
            "subtotal": "subtotal",
            "tax_amount": "tax_amount",
            "total_amount": "total_amount",
            "due_date": "due_date",
            "payment_terms": "payment_terms",
            "project_name": "project_name",
            "project_number": "project_number",
            "job_number": "job_number",
            "effective_date": "effective_date",
            "expiration_date": "expiration_date",
            "policy_number": "policy_number",
            "coverage_amount": "coverage_amount",
            "description": "description",
            "notes": "notes",
        }

        for response_key, field_name in field_mapping.items():
            value = fields_data.get(response_key)
            if value is not None and value != "null":
                # Convert numeric strings to floats for amount fields
                if field_name in ["amount", "subtotal", "tax_amount", "total_amount", "coverage_amount"]:
                    try:
                        value = float(value) if isinstance(value, str) else value
                    except (ValueError, TypeError):
                        value = None
                setattr(fields, field_name, value)

        # Handle line items
        if "line_items" in fields_data and isinstance(fields_data["line_items"], list):
            fields.line_items = fields_data["line_items"]

        # Handle parties
        if "parties" in fields_data and isinstance(fields_data["parties"], list):
            fields.parties = fields_data["parties"]

        # Store raw fields for reference
        fields.raw_fields = fields_data

        return fields

    def get_category_actions(self, category: DocumentCategory) -> Dict[str, Any]:
        """
        Get available automation actions for a category.
        
        Returns suggested workflows based on document type.
        """
        actions = {
            DocumentCategory.BILL: {
                "can_create_bill": True,
                "can_link_vendor": True,
                "can_link_project": True,
                "suggested_action": "Create bill from document",
            },
            DocumentCategory.INVOICE: {
                "can_create_invoice": True,
                "can_link_customer": True,
                "can_link_project": True,
                "suggested_action": "Create invoice from document",
            },
            DocumentCategory.RECEIPT: {
                "can_attach_to_bill": True,
                "can_link_vendor": True,
                "suggested_action": "Attach to existing bill",
            },
            DocumentCategory.PURCHASE_ORDER: {
                "can_create_po": True,
                "can_link_vendor": True,
                "can_link_project": True,
                "suggested_action": "Create purchase order",
            },
            DocumentCategory.CHANGE_ORDER: {
                "can_create_change_order": True,
                "can_link_project": True,
                "suggested_action": "Create change order",
            },
            DocumentCategory.CONTRACT: {
                "can_create_contract": True,
                "can_link_vendor": True,
                "can_link_customer": True,
                "suggested_action": "Create contract record",
            },
            DocumentCategory.INSURANCE_CERTIFICATE: {
                "can_update_vendor_insurance": True,
                "can_set_expiration_alert": True,
                "suggested_action": "Update vendor insurance info",
            },
            DocumentCategory.LIEN_WAIVER: {
                "can_track_compliance": True,
                "can_link_vendor": True,
                "can_link_project": True,
                "suggested_action": "Track lien waiver compliance",
            },
        }
        
        return actions.get(category, {
            "suggested_action": "Review and file document",
        })


# Singleton instance
_categorization_service: Optional[CategorizationService] = None


def get_categorization_service() -> CategorizationService:
    """Get or create the singleton CategorizationService instance."""
    global _categorization_service
    if _categorization_service is None:
        _categorization_service = CategorizationService()
    return _categorization_service
