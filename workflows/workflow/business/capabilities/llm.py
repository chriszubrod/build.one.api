# Python Standard Library Imports
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# Local Imports
from workflows.workflow.business.capabilities.base import Capability, CapabilityResult, with_retry
from workflows.workflow.business.capabilities.llm_providers import get_llm_router, LlmProviderRouter
from workflows.workflow.business.capabilities.llm_providers.base import (
    TASK_CLASSIFICATION,
    TASK_EXTRACTION,
    TASK_REASONING,
    TASK_DEFAULT,
)

logger = logging.getLogger(__name__)


@dataclass
class Classification:
    """Result of email/document classification - entity type only."""
    entity_type: str  # 'bill', 'expense', 'invoice', 'contract', 'change_order', 'other'
    confidence: float  # 0.0 - 1.0
    reasoning: Optional[str] = None


@dataclass
class ParsedReply:
    """Result of parsing a human reply email."""
    decision: str  # 'approved', 'rejected', 'question', 'unclear'
    confidence: float
    project_name: Optional[str] = None
    project_id: Optional[int] = None
    cost_code: Optional[str] = None
    notes: Optional[str] = None
    question_text: Optional[str] = None


class LlmCapabilities(Capability):
    """
    LLM-based capabilities with provider routing.
    
    Routes requests to local (Ollama) or cloud (Azure OpenAI) providers
    based on task type and availability.
    
    Provides classification, extraction, and parsing functions
    for the agents framework.
    """
    
    @property
    def name(self) -> str:
        return "llm"
    
    def __init__(self):
        self._router: Optional[LlmProviderRouter] = None
    
    def _get_router(self) -> LlmProviderRouter:
        """Get the LLM provider router."""
        if self._router is None:
            self._router = get_llm_router()
        return self._router
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def classify_email(
        self,
        email_body: str,
        extracted_text: Optional[str] = None,
        historical_context: Optional[str] = None,
        known_vendors: Optional[List[str]] = None,
    ) -> CapabilityResult:
        """
        Classify an incoming email into an entity type.
        
        This is a simplified classification that ONLY determines the entity type.
        Field extraction (vendor, amount, etc.) happens in entity-specific workflows.
        
        Args:
            email_body: The email body text (full conversation)
            extracted_text: Text extracted from attachments (optional)
            historical_context: Historical data about the sender (optional)
            known_vendors: List of known vendor names to help identify bills
            
        Returns:
            CapabilityResult with Classification data (entity_type, confidence, reasoning)
        """
        self._log_call(
            "classify_email",
            email_body_len=len(email_body),
            has_attachment=extracted_text is not None,
            has_history=historical_context is not None,
        )
        
        try:
            router = self._get_router()
            
            # Build the prompt - entity type classification only
            system_prompt = """You are an email classification assistant for a construction company.
Analyze the email conversation and any attachments to determine the ENTITY TYPE.

Entity types:
- 'bill': Invoice or bill FROM a vendor TO the company (money owed by company)
- 'expense': Employee expense report, receipt, or reimbursement request
- 'invoice': Invoice FROM the company TO a customer (money owed to company)
- 'contract': Agreement, contract, or legal document
- 'change_order': Construction change order or modification request
- 'other': General correspondence, inquiries, or unclassified

CRITICAL DISTINCTION between 'bill' and 'invoice':
- 'bill': Sent BY a vendor/supplier TO us. We need to PAY them. Look for: vendor names in KNOWN VENDORS list, "Please remit payment", "Amount Due", invoices addressed to our company.
- 'invoice': Sent BY us TO a customer. They need to PAY us. Look for: our company letterhead, invoices we created for customers.

If the sender matches or resembles a name in KNOWN VENDORS, it is almost certainly a 'bill'.

USE HISTORICAL CONTEXT if provided:
- If sender has consistently sent bills before, this is likely a bill
- Consider sender patterns when determining entity type

Respond in JSON format:
{
    "entity_type": "bill|expense|invoice|contract|change_order|other",
    "confidence": 0.0-1.0,
    "reasoning": "brief explanation of why this entity type was chosen"
}"""

            user_content = f"EMAIL CONVERSATION:\n{email_body}\n"
            if extracted_text:
                user_content += f"\nATTACHMENT TEXT:\n{extracted_text[:4000]}\n"
            if historical_context:
                user_content += f"\n{historical_context}\n"
            if known_vendors:
                user_content += f"\nKNOWN VENDORS: {', '.join(known_vendors[:30])}\n"
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            
            # Use router for provider selection with classification task type
            response = router.chat_completion(
                messages=messages,
                task_type=TASK_CLASSIFICATION,
                json_mode=True,
            )
            
            # Parse the JSON response
            content = json.loads(response.content)
            classification = Classification(
                entity_type=content.get("entity_type", "other"),
                confidence=float(content.get("confidence", 0.5)),
                reasoning=content.get("reasoning"),
            )
            
            logger.info(f"Classification result: {classification.entity_type} ({classification.confidence:.0%})")
            result = CapabilityResult.ok(
                data=classification,
                provider=response.provider,
                model=response.model,
            )
            self._log_result("classify_email", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "classify_email")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def parse_approval_reply(
        self,
        reply_body: str,
        original_context: Optional[Dict] = None,
    ) -> CapabilityResult:
        """
        Parse a human reply to an approval request.
        
        Args:
            reply_body: The reply email body
            original_context: Context from the original approval request
            
        Returns:
            CapabilityResult with ParsedReply data
        """
        self._log_call("parse_approval_reply", reply_body_len=len(reply_body))
        
        try:
            router = self._get_router()
            
            system_prompt = """You are parsing an approval response email.
Determine:
1. Decision: 'approved', 'rejected', 'question' (asking for more info), or 'unclear'
2. If approved, extract: project name, cost code (format like "03-200")
3. If question, extract the question text
4. Extract any additional notes

Common approval phrases: "approved", "yes", "ok", "go ahead", "looks good"
Common rejection phrases: "rejected", "no", "deny", "not approved", "hold"

Respond in JSON:
{
    "decision": "approved|rejected|question|unclear",
    "confidence": 0.0-1.0,
    "project_name": "string or null",
    "cost_code": "string or null",
    "notes": "string or null",
    "question_text": "string or null if decision is question"
}"""

            user_content = f"REPLY EMAIL:\n{reply_body}\n"
            if original_context:
                user_content += f"\nORIGINAL CONTEXT: {json.dumps(original_context)}\n"
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            
            response = router.chat_completion(
                messages=messages,
                task_type=TASK_EXTRACTION,
                json_mode=True,
            )
            
            content = json.loads(response.content)
            parsed = ParsedReply(
                decision=content.get("decision", "unclear"),
                confidence=float(content.get("confidence", 0.5)),
                project_name=content.get("project_name"),
                cost_code=content.get("cost_code"),
                notes=content.get("notes"),
                question_text=content.get("question_text"),
            )
            
            result = CapabilityResult.ok(
                data=parsed,
                provider=response.provider,
                model=response.model,
                usage=response.usage,
            )
            self._log_result("parse_approval_reply", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "parse_approval_reply")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def match_orphan_email(
        self,
        email_body: str,
        open_workflows: List[Dict],
    ) -> CapabilityResult:
        """
        Try to match an orphan email to an existing workflow.
        
        Args:
            email_body: The email body that doesn't match by conversation ID
            open_workflows: List of open workflows with their context
            
        Returns:
            CapabilityResult with matched workflow public_id or None
        """
        self._log_call(
            "match_orphan_email",
            email_body_len=len(email_body),
            workflow_count=len(open_workflows),
        )
        
        try:
            router = self._get_router()
            
            system_prompt = """You are matching an email to existing workflows.
Given an email and a list of open workflows, determine if this email is a response
to any of them based on:
- Mentioned vendor names
- Mentioned project names
- Invoice numbers or amounts
- Context clues

Respond in JSON:
{
    "matched_workflow_id": "public_id string or null if no match",
    "confidence": 0.0-1.0,
    "reasoning": "why this matches or doesn't match"
}"""

            workflows_summary = json.dumps([
                {
                    "public_id": w.get("public_id"),
                    "vendor": w.get("vendor_name"),
                    "project": w.get("project_name"),
                    "amount": w.get("amount"),
                    "invoice": w.get("invoice_number"),
                }
                for w in open_workflows[:10]  # Limit to 10
            ], indent=2)
            
            user_content = f"EMAIL:\n{email_body}\n\nOPEN WORKFLOWS:\n{workflows_summary}"
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            
            response = router.chat_completion(
                messages=messages,
                task_type=TASK_REASONING,
                json_mode=True,
            )
            
            content = json.loads(response.content)
            result = CapabilityResult.ok(
                data={
                    "matched_workflow_id": content.get("matched_workflow_id"),
                    "confidence": float(content.get("confidence", 0)),
                    "reasoning": content.get("reasoning"),
                },
                provider=response.provider,
                model=response.model,
            )
            self._log_result("match_orphan_email", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "match_orphan_email")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def extract_bill_fields(
        self,
        document_text: str,
        conversation: Optional[List[Dict]] = None,
        email_subject: Optional[str] = None,
        email_from: Optional[str] = None,
        email_from_name: Optional[str] = None,
        known_projects: Optional[List[Dict]] = None,
        known_sub_cost_codes: Optional[List[Dict]] = None,
    ) -> CapabilityResult:
        """
        Extract structured bill fields from document text AND email conversation.
        
        Uses LLM to parse:
        - Bill attachment: vendor_name, invoice_number, bill_date, rate, project address
        - Email conversation (especially final/approval email): sub_cost_code, description, is_billable
        
        Args:
            document_text: Text content from the document (PDF, image)
            conversation: Full email conversation thread (list of message dicts)
            email_subject: Email subject for context
            email_from: Sender email address
            email_from_name: Sender display name
            known_projects: List of projects with addresses for matching
            known_sub_cost_codes: List of sub cost codes for matching
            
        Returns:
            CapabilityResult with extracted fields dict
        """
        self._log_call(
            "extract_bill_fields",
            document_text_len=len(document_text) if document_text else 0,
            conversation_count=len(conversation) if conversation else 0,
            has_projects=bool(known_projects),
            has_sub_cost_codes=bool(known_sub_cost_codes),
        )
        
        try:
            router = self._get_router()
            
            system_prompt = """You are a document extraction assistant for a construction company.
Extract structured information from bill/invoice documents AND the email conversation.

IMPORTANT: Data comes from TWO sources:
1. BILL ATTACHMENT TEXT - Contains: vendor name, invoice number, bill date, amounts, rates, project address
2. EMAIL CONVERSATION - Contains: approval info with sub_cost_code, description, is_billable (especially in the FINAL/most recent email)

Extract the following fields:

FROM BILL ATTACHMENT:
- vendor_name: Name of the vendor/supplier (from letterhead)
- invoice_number: Invoice or bill number (look for "Invoice #", "Bill #", "No.")
- bill_date: Date of the invoice (format as YYYY-MM-DD)
- total_amount: Total amount due (as a number, no currency symbols)
- rate: Unit rate/price for the work (for line item)
- project_address: Any address mentioned that could identify a project/jobsite

FROM EMAIL CONVERSATION (especially the FINAL/approval email):
- sub_cost_code: Cost code like "03-200" or "6-100" mentioned in approval
- line_description: Description of work from the approval email (may be combined with code like "Trim Labor - 44.0")
- is_billable: Default true, but set false if approval says "not billable" or "non-billable"
- memo: The approval text from the FINAL email - capture the key approval line/sentence for the bill memo

EXTRACTION TIPS:
- The FINAL email in the conversation often contains the approval with cost code and description
- Look for patterns like "Code: 03-200" or "Cost Code: 6-100" or just "03-200 - Description"
- Project addresses are usually street addresses in the bill text
- If no line items are detailed, use total_amount as the rate

Respond in JSON format:
{
    "vendor_name": "string",
    "invoice_number": "string or null",
    "bill_date": "YYYY-MM-DD or null",
    "total_amount": number or null,
    "project_address": "string or null",
    "sub_cost_code": "string or null (e.g. '03-200' or '44.0')",
    "line_description": "string or null (e.g. 'Trim Labor - 44.0')",
    "rate": number or null,
    "is_billable": true or false,
    "memo": "string or null - approval text from final email for bill memo",
    "extraction_notes": "any issues or uncertainties"
}"""

            # Build user content with both sources
            user_content = ""
            
            # Add bill attachment text
            if document_text:
                user_content += f"=== BILL ATTACHMENT TEXT ===\n{document_text[:6000]}\n\n"
            
            # Add email conversation (focus on final emails)
            if conversation:
                # Sort by date, most recent last
                sorted_msgs = sorted(
                    conversation,
                    key=lambda m: m.get("received_at", "") or ""
                )
                
                user_content += "=== EMAIL CONVERSATION ===\n"
                for i, msg in enumerate(sorted_msgs):
                    is_final = (i == len(sorted_msgs) - 1)
                    marker = "[FINAL/APPROVAL EMAIL]" if is_final else f"[Email {i+1}]"
                    
                    msg_from = msg.get("from_name") or msg.get("from_address") or "Unknown"
                    msg_date = msg.get("received_at", "")
                    msg_body = msg.get("body", "")[:1500]  # Limit each message
                    
                    user_content += f"\n{marker}\nFrom: {msg_from}\nDate: {msg_date}\n{msg_body}\n"
            else:
                # Fallback to basic email context
                if email_subject:
                    user_content += f"\nEMAIL SUBJECT: {email_subject}\n"
                if email_from_name or email_from:
                    user_content += f"SENDER: {email_from_name or ''} <{email_from or ''}>\n"
            
            # Add known projects for address matching hints
            if known_projects:
                project_hints = [f"- {p.get('name')}: {p.get('address')}" for p in known_projects[:20] if p.get('address')]
                if project_hints:
                    user_content += f"\n=== KNOWN PROJECTS (for address matching) ===\n" + "\n".join(project_hints) + "\n"
            
            # Add known sub cost codes for matching hints
            if known_sub_cost_codes:
                code_hints = [f"- {c.get('number')}: {c.get('description')}" for c in known_sub_cost_codes[:30]]
                if code_hints:
                    user_content += f"\n=== KNOWN SUB COST CODES ===\n" + "\n".join(code_hints) + "\n"
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]
            
            response = router.chat_completion(
                messages=messages,
                task_type=TASK_EXTRACTION,
                json_mode=True,
            )
            
            content = json.loads(response.content)
            
            # Validate and normalize the response
            extracted = {
                "vendor_name": content.get("vendor_name"),
                "invoice_number": content.get("invoice_number"),
                "bill_date": content.get("bill_date"),
                "total_amount": self._parse_amount(content.get("total_amount")),
                "project_address": content.get("project_address"),
                "sub_cost_code": content.get("sub_cost_code"),
                "line_description": content.get("line_description"),
                "rate": self._parse_amount(content.get("rate")),
                "is_billable": content.get("is_billable", True),
                "memo": content.get("memo"),
                "extraction_notes": content.get("extraction_notes"),
            }
            
            # Build line_items array for compatibility with bill creation
            line_items = []
            if extracted.get("line_description") or extracted.get("rate") or extracted.get("total_amount"):
                line_items.append({
                    "description": extracted.get("line_description") or "Services rendered",
                    "quantity": 1,
                    "rate": extracted.get("rate") or extracted.get("total_amount") or 0,
                    "amount": extracted.get("rate") or extracted.get("total_amount") or 0,
                })
            extracted["line_items"] = line_items
            
            logger.info(
                f"Extracted bill: vendor={extracted.get('vendor_name')}, "
                f"amount={extracted.get('total_amount')}, "
                f"sub_cost_code={extracted.get('sub_cost_code')}"
            )
            
            result = CapabilityResult.ok(
                data=extracted,
                confidence=0.85,
                provider=response.provider,
                model=response.model,
            )
            self._log_result("extract_bill_fields", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "extract_bill_fields")
    
    def _parse_amount(self, value) -> Optional[float]:
        """Parse a numeric amount from various formats."""
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            # Remove currency symbols, commas, whitespace
            cleaned = value.replace("$", "").replace(",", "").replace(" ", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def simple_completion(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
    ) -> CapabilityResult:
        """
        Simple text completion for ad-hoc prompts.
        
        Args:
            prompt: The user prompt
            system_prompt: Optional system prompt
            
        Returns:
            CapabilityResult with completion text
        """
        try:
            router = self._get_router()
            
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            
            response = router.chat_completion(
                messages=messages,
                task_type=TASK_DEFAULT,
                json_mode=False,
            )
            
            return CapabilityResult.ok(
                data=response.content,
                provider=response.provider,
                model=response.model,
            )
        except Exception as e:
            return self._handle_error(e, "simple_completion")
