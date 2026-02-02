# Python Standard Library Imports
import logging
from typing import Dict, List, Optional, TYPE_CHECKING

# Local Imports
from workflows.capabilities.base import CapabilityResult
from workflows.agents.base import Agent, AgentContext, AgentResult
from workflows.agents.registry import get_agent_registry

logger = logging.getLogger(__name__)


class BillExtractionAgent(Agent):
    """
    Agent that extracts bill-specific fields from documents AND email conversation.
    
    This agent runs as part of the bill_processing workflow, after
    email_intake has classified the email as a bill.
    
    Extraction sources:
    - BILL ATTACHMENT: vendor_name, invoice_number, bill_date, rate, project address
    - EMAIL CONVERSATION (especially final/approval email): sub_cost_code, description, is_billable
    
    The agent also:
    - Matches extracted sub_cost_code to SubCostCode database records
    - Matches project (if address found) to Project database records
    - Applies defaults: PaymentTerms="Due on receipt", DueDate=BillDate, Quantity=1
    """
    
    @property
    def name(self) -> str:
        return "bill_extraction"
    
    @property
    def description(self) -> str:
        return "Extracts bill fields from documents and email conversation"
    
    async def run(self, context: AgentContext) -> AgentResult:
        """
        Execute bill field extraction from both attachment and conversation.
        
        Expected context:
        - attachment_blob_urls: URLs to saved attachments
        - conversation: Full email conversation thread
        - email: Email metadata
        
        Returns:
        - extracted: Structured bill fields with matched entities
        """
        self._log_start(context)
        
        try:
            wf_context = context.workflow_context or {}
            
            # Get document text from attachments
            document_text = await self._get_document_text(context)
            
            if not document_text:
                logger.warning("No document text available, will rely on email conversation only")
            
            # Get email conversation for approval/cost code extraction
            conversation = wf_context.get("conversation", [])
            
            # Get email context for additional hints
            email_context = wf_context.get("email", {})
            email_subject = email_context.get("subject", "")
            email_from = email_context.get("from_address", "")
            email_from_name = email_context.get("from_name", "")
            
            # Get known entities for LLM matching hints
            projects = self._get_projects_for_hints(context.tenant_id)
            sub_cost_codes = self._get_sub_cost_codes_for_hints(context.tenant_id)
            
            # Use LLM to extract bill fields from BOTH sources
            extraction_result = self.capabilities.llm.extract_bill_fields(
                document_text=document_text or "",
                conversation=conversation,
                email_subject=email_subject,
                email_from=email_from,
                email_from_name=email_from_name,
                known_projects=projects,
                known_sub_cost_codes=sub_cost_codes,
            )
            
            if not extraction_result.success:
                return AgentResult.fail(
                    f"Bill field extraction failed: {extraction_result.error}"
                )
            
            extracted_fields = extraction_result.data
            
            # Match sub_cost_code to database record
            sub_cost_code_match = self._match_sub_cost_code(
                extracted_fields.get("sub_cost_code"),
                context.tenant_id,
            )
            if sub_cost_code_match:
                extracted_fields["matched_sub_cost_code"] = sub_cost_code_match
                logger.info(f"Matched sub_cost_code: {sub_cost_code_match.get('number')}")
            
            # Match project if possible
            project_match = await self._match_project(
                extracted_fields.get("project_address"),
                context.tenant_id,
            )
            if project_match:
                extracted_fields["matched_project"] = project_match
                logger.info(f"Matched project: {project_match.get('name')}")
            
            # Get default payment term
            default_payment_term = self._get_default_payment_term(context.tenant_id)
            if default_payment_term:
                extracted_fields["payment_term"] = default_payment_term
            
            # Apply defaults
            extracted_fields = self._apply_defaults(extracted_fields)
            
            # Build line_items array from flat extracted data
            # Most bills will have a single line item initially
            extracted_fields = self._build_line_items(extracted_fields)
            
            # Validate required fields
            validation = self._validate_extraction(extracted_fields)
            if not validation["valid"]:
                logger.warning(f"Extraction incomplete: {validation['missing']}")
            
            # Build context updates
            context_updates = {
                "extracted": extracted_fields,
                "extraction_confidence": extraction_result.metadata.get("confidence", 0.8),
                "extraction_source": "llm_multi_source",
                "extraction_valid": validation["valid"],
                "extraction_missing_fields": validation.get("missing", []),
            }
            
            # Determine next trigger
            if validation["valid"]:
                next_trigger = "extraction_complete"
            else:
                next_trigger = "extraction_complete"
                context_updates["needs_review_reason"] = f"Missing fields: {', '.join(validation['missing'])}"
            
            result = AgentResult.ok(
                data=extracted_fields,
                context_updates=context_updates,
                next_trigger=next_trigger,
            )
            
            self._log_complete(result)
            return result
            
        except Exception as e:
            logger.exception(f"Error in {self.name}")
            return AgentResult.fail(str(e))
    
    def _get_projects_for_hints(self, tenant_id: int) -> List[Dict]:
        """Get projects for LLM matching hints."""
        try:
            result = self.capabilities.entity.get_projects_for_matching(tenant_id)
            return result.data if result.success else []
        except Exception:
            return []
    
    def _get_sub_cost_codes_for_hints(self, tenant_id: int) -> List[Dict]:
        """Get sub cost codes for LLM matching hints."""
        try:
            result = self.capabilities.entity.get_sub_cost_codes(tenant_id)
            return result.data if result.success else []
        except Exception:
            return []
    
    def _match_sub_cost_code(self, code_string: Optional[str], tenant_id: int) -> Optional[Dict]:
        """Match extracted sub_cost_code to database record."""
        if not code_string:
            return None
        try:
            result = self.capabilities.entity.match_sub_cost_code(code_string, tenant_id)
            return result.data if result.success else None
        except Exception:
            return None
    
    async def _match_project(self, project_hint: Optional[str], tenant_id: int) -> Optional[Dict]:
        """
        Match project by name or address hint.
        
        Delegates to ProjectMatchAgent for consistent matching logic.
        """
        if not project_hint:
            return None
        try:
            # Delegate to ProjectMatchAgent
            agents = get_agent_registry()
            result = await agents.project_match.match(
                project_hint=project_hint,
                tenant_id=tenant_id,
                threshold=0.70,  # BillExtractionAgent uses 70% threshold
            )
            
            if result.get("matched") and result.get("project"):
                return result["project"]
            return None
        except Exception:
            return None
    
    def _get_default_payment_term(self, tenant_id: int) -> Optional[Dict]:
        """Get default payment term (Due on receipt)."""
        try:
            result = self.capabilities.entity.get_default_payment_term(tenant_id)
            return result.data if result.success else None
        except Exception:
            return None
    
    def _apply_defaults(self, extracted: Dict) -> Dict:
        """
        Apply default values per business rules.
        
        - DueDate defaults to BillDate
        - Quantity defaults to 1
        - IsBillable defaults to True
        - Markup defaults to None
        """
        # DueDate defaults to BillDate
        if not extracted.get("due_date") and extracted.get("bill_date"):
            extracted["due_date"] = extracted["bill_date"]
        
        # Ensure line items have defaults
        for item in extracted.get("line_items", []):
            if not item.get("quantity"):
                item["quantity"] = 1
            if item.get("is_billable") is None:
                item["is_billable"] = extracted.get("is_billable", True)
            if "markup" not in item:
                item["markup"] = None
        
        return extracted
    
    def _build_line_items(self, extracted: Dict) -> Dict:
        """
        Build line_items array from flat extracted data.
        
        Business rules:
        - Rate = total_amount from invoice attachment
        - Amount = Quantity × Rate
        - Bill.TotalAmount = sum of line item Amounts
        
        Parse approval text for sub_cost_code and description:
        - Pattern: "Description - Code" (e.g., "Trim Labor - 44.0")
        - Code goes to sub_cost_code, Description goes to line_description
        """
        # If line_items already exists, just return
        if extracted.get("line_items"):
            return extracted
        
        # Parse line_description for "Description - Code" pattern
        description, sub_cost_code = self._parse_approval_text(extracted)
        
        # Rate = total_amount from invoice
        total_amount = extracted.get("total_amount") or 0
        quantity = 1
        rate = total_amount
        amount = quantity * rate
        
        # Build single line item
        line_item = {
            "description": description,
            "quantity": quantity,
            "rate": rate,
            "amount": amount,
            "is_billable": extracted.get("is_billable", True),
            "markup": None,
        }
        
        # Update extracted with parsed values
        if sub_cost_code and not extracted.get("sub_cost_code"):
            extracted["sub_cost_code"] = sub_cost_code
        
        extracted["line_items"] = [line_item]
        
        logger.info(f"Built line_items: description='{description}', sub_cost_code='{sub_cost_code}', quantity={quantity}, rate={rate}, amount={amount}")
        
        return extracted
    
    def _parse_approval_text(self, extracted: Dict) -> tuple:
        """
        Parse approval text for description and sub_cost_code.
        
        Patterns to detect:
        - "Description - Code" (e.g., "Trim Labor - 44.0")
        - "Code - Description" (e.g., "44.0 - Trim Labor")
        
        Returns:
            (description, sub_cost_code) tuple
        """
        line_description = extracted.get("line_description") or ""
        existing_code = extracted.get("sub_cost_code")
        
        # If we already have both, use them
        if existing_code and line_description:
            # Check if description contains the code - if so, strip it
            if existing_code in line_description:
                # Remove the code and separator from description
                desc = line_description.replace(f" - {existing_code}", "")
                desc = desc.replace(f"{existing_code} - ", "")
                desc = desc.replace(existing_code, "").strip(" -")
                return (desc.strip() or "Bill line item", existing_code)
            return (line_description.strip(), existing_code)
        
        # Try to parse "Description - Code" pattern
        if " - " in line_description:
            parts = line_description.split(" - ")
            
            if len(parts) == 2:
                left = parts[0].strip()
                right = parts[1].strip()
                
                # Check which side looks like a code (numeric or pattern like "03-200")
                left_is_code = self._looks_like_cost_code(left)
                right_is_code = self._looks_like_cost_code(right)
                
                if right_is_code and not left_is_code:
                    # "Description - Code" pattern
                    return (left, right)
                elif left_is_code and not right_is_code:
                    # "Code - Description" pattern
                    return (right, left)
        
        # No pattern found - return as-is
        return (line_description.strip() or "Bill line item", existing_code)
    
    def _looks_like_cost_code(self, text: str) -> bool:
        """
        Check if text looks like a cost code.
        
        Cost codes are typically:
        - Numeric with optional decimal (e.g., "44.0", "6.100")
        - Pattern with dash (e.g., "03-200", "6-100")
        """
        import re
        
        text = text.strip()
        
        # Check for numeric pattern (e.g., "44.0", "6.100")
        if re.match(r"^\d+(\.\d+)?$", text):
            return True
        
        # Check for code pattern (e.g., "03-200", "6-100")
        if re.match(r"^\d+-\d+$", text):
            return True
        
        return False
    
    async def _get_document_text(self, context: AgentContext) -> Optional[str]:
        """
        Get document text for extraction.
        
        Checks multiple sources:
        1. Direct document_text in context
        2. Attachment blob URLs (download and extract)
        3. Parent workflow context
        """
        wf_context = context.workflow_context or {}
        
        # Check for pre-extracted text
        if wf_context.get("document_text"):
            logger.info("Using pre-extracted document text from context")
            return wf_context["document_text"]
        
        # Check for extracted attachment texts (from email_triage)
        if wf_context.get("attachment_texts"):
            texts = wf_context["attachment_texts"]
            if texts:
                logger.info(f"Using {len(texts)} attachment texts from context")
                return "\n\n---\n\n".join(texts)
        
        # Try to extract from blob storage
        attachment_urls = wf_context.get("attachment_blob_urls", [])
        if attachment_urls:
            logger.info(f"Extracting text from {len(attachment_urls)} blob attachments")
            texts = []
            
            for url in attachment_urls:
                # Download from blob storage
                download_result = self.capabilities.storage.download_blob(url)
                if not download_result.success:
                    logger.warning(f"Failed to download blob: {url}")
                    continue
                
                content = download_result.data.get("content")
                content_type = download_result.data.get("content_type", "application/pdf")
                
                if not content:
                    continue
                
                # Extract text
                extract_result = self.capabilities.document.extract_from_bytes(
                    file_content=content,
                    content_type=content_type,
                )
                
                if extract_result.success and extract_result.data:
                    texts.append(extract_result.data.text)
            
            if texts:
                return "\n\n---\n\n".join(texts)
        
        # Try email body as fallback
        email = wf_context.get("email", {})
        if email.get("body"):
            logger.info("Using email body as document text (no attachments)")
            return email["body"]
        
        return None
    
    def _validate_extraction(self, extracted: Dict) -> Dict:
        """
        Validate that required fields were extracted.
        
        Returns:
            Dict with 'valid' boolean and 'missing' list
        """
        required_fields = ["vendor_name", "total_amount"]
        recommended_fields = ["invoice_number", "bill_date", "sub_cost_code"]
        
        missing = []
        
        for field in required_fields:
            if not extracted.get(field):
                missing.append(field)
        
        # Check recommended fields
        for field in recommended_fields:
            if not extracted.get(field):
                missing.append(f"{field} (recommended)")
        
        return {
            "valid": all(extracted.get(f) for f in required_fields),
            "missing": missing,
        }
