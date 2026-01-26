# Python Standard Library Imports
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

# Local Imports
from agents.capabilities.base import Capability, CapabilityResult

logger = logging.getLogger(__name__)


@dataclass
class MatchCandidate:
    """A potential entity match with confidence score."""
    id: int
    public_id: str
    name: str
    confidence: float
    match_type: str  # 'exact', 'fuzzy', 'embedding'


class EntityCapabilities(Capability):
    """
    Entity management capabilities.
    
    Provides operations for Bill, Vendor, Project entities
    including fuzzy matching using embeddings.
    """
    
    @property
    def name(self) -> str:
        return "entity"
    
    def __init__(self):
        self._bill_service = None
        self._vendor_service = None
        self._project_service = None
        self._embedding_service = None
    
    def _get_bill_service(self):
        if self._bill_service is None:
            from modules.bill.business.service import BillService
            self._bill_service = BillService()
        return self._bill_service
    
    def _get_vendor_service(self):
        if self._vendor_service is None:
            from modules.vendor.business.service import VendorService
            self._vendor_service = VendorService()
        return self._vendor_service
    
    def _get_project_service(self):
        if self._project_service is None:
            from modules.project.business.service import ProjectService
            self._project_service = ProjectService()
        return self._project_service
    
    def _get_embedding_service(self):
        if self._embedding_service is None:
            from shared.ai.embeddings import get_embedding_service
            self._embedding_service = get_embedding_service()
        return self._embedding_service
    
    def match_vendor(
        self,
        name: Optional[str] = None,
        email: Optional[str] = None,
        tenant_id: Optional[int] = None,
    ) -> CapabilityResult:
        """
        Match a vendor by name or email.
        
        Uses exact match first, then fuzzy matching with embeddings.
        
        Args:
            name: Vendor name to match
            email: Vendor email to match
            tenant_id: Tenant ID for filtering
            
        Returns:
            CapabilityResult with Vendor or list of MatchCandidate
        """
        self._log_call("match_vendor", name=name, email=email)
        
        try:
            service = self._get_vendor_service()
            
            # Try exact match by name first
            if name:
                vendor = service.read_by_name(name)
                if vendor:
                    return CapabilityResult.ok(
                        data={
                            "id": vendor.id,
                            "public_id": vendor.public_id,
                            "name": vendor.name,
                        },
                        match_type="exact_name",
                        confidence=1.0,
                    )
            
            # Fall back to fuzzy matching with embeddings
            if name:
                candidates = self._fuzzy_match_vendors(name, tenant_id)
                if candidates:
                    return CapabilityResult.ok(
                        data=candidates,
                        match_type="fuzzy",
                    )
            
            return CapabilityResult.ok(
                data=None,
                match_type="no_match",
            )
            
        except Exception as e:
            logger.exception("Error in match_vendor")
            return CapabilityResult.fail(error=str(e))
    
    def _fuzzy_match_vendors(
        self,
        name: str,
        tenant_id: Optional[int],
        top_k: int = 5,
    ) -> List[MatchCandidate]:
        """Fuzzy match vendors using embeddings."""
        try:
            service = self._get_vendor_service()
            embedding_service = self._get_embedding_service()
            
            # Get all vendors
            vendors = service.read_all()
            if not vendors:
                return []
            
            # Generate embeddings
            query_embedding = embedding_service.generate_embedding(name)
            vendor_names = [v.name or "" for v in vendors]
            vendor_embeddings = embedding_service.generate_embeddings_batch(vendor_names)
            
            # Find similar
            similar = embedding_service.find_most_similar(
                query_embedding,
                vendor_embeddings,
                top_k=top_k,
            )
            
            candidates = []
            for idx, score in similar:
                if score > 0.5:  # Minimum threshold
                    vendor = vendors[idx]
                    candidates.append(MatchCandidate(
                        id=vendor.id,
                        public_id=vendor.public_id,
                        name=vendor.name,
                        confidence=score,
                        match_type="embedding",
                    ))
            
            return candidates
            
        except Exception as e:
            logger.warning(f"Fuzzy matching failed: {e}")
            return []
    
    def match_project(
        self,
        name: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        tenant_id: Optional[int] = None,
    ) -> CapabilityResult:
        """
        Match a project by name or keywords.
        
        Args:
            name: Project name to match
            keywords: Keywords to search for
            tenant_id: Tenant ID for filtering
            
        Returns:
            CapabilityResult with Project or list of MatchCandidate
        """
        self._log_call("match_project", name=name, keywords=keywords)
        
        try:
            service = self._get_project_service()
            
            # Try exact match by name
            if name:
                project = service.read_by_name(name)
                if project:
                    return CapabilityResult.ok(
                        data={
                            "id": project.id,
                            "public_id": project.public_id,
                            "name": project.name,
                        },
                        match_type="exact_name",
                        confidence=1.0,
                    )
            
            # Fall back to fuzzy matching
            search_text = name or " ".join(keywords or [])
            if search_text:
                candidates = self._fuzzy_match_projects(search_text, tenant_id)
                if candidates:
                    return CapabilityResult.ok(
                        data=candidates,
                        match_type="fuzzy",
                    )
            
            return CapabilityResult.ok(
                data=None,
                match_type="no_match",
            )
            
        except Exception as e:
            logger.exception("Error in match_project")
            return CapabilityResult.fail(error=str(e))
    
    def _fuzzy_match_projects(
        self,
        search_text: str,
        tenant_id: Optional[int],
        top_k: int = 5,
    ) -> List[MatchCandidate]:
        """Fuzzy match projects using embeddings."""
        try:
            service = self._get_project_service()
            embedding_service = self._get_embedding_service()
            
            # Get all projects
            projects = service.read_all()
            if not projects:
                return []
            
            query_embedding = embedding_service.generate_embedding(search_text)
            project_names = [p.name or "" for p in projects]
            project_embeddings = embedding_service.generate_embeddings_batch(project_names)
            
            similar = embedding_service.find_most_similar(
                query_embedding,
                project_embeddings,
                top_k=top_k,
            )
            
            candidates = []
            for idx, score in similar:
                if score > 0.5:
                    project = projects[idx]
                    candidates.append(MatchCandidate(
                        id=project.id,
                        public_id=project.public_id,
                        name=project.name,
                        confidence=score,
                        match_type="embedding",
                    ))
            
            return candidates
            
        except Exception as e:
            logger.warning(f"Fuzzy matching failed: {e}")
            return []
    
    def create_bill(
        self,
        tenant_id: int,
        vendor_id: Optional[int] = None,
        amount: Optional[float] = None,
        invoice_number: Optional[str] = None,
        invoice_date: Optional[str] = None,
        due_date: Optional[str] = None,
        description: Optional[str] = None,
        line_items: Optional[List[Dict]] = None,
        is_draft: bool = True,
        payment_term_public_id: Optional[str] = None,
        project_public_id: Optional[str] = None,
        sub_cost_code_id: Optional[int] = None,
        is_billable: bool = True,
    ) -> CapabilityResult:
        """
        Create a new bill with line items.
        
        This operation is idempotent - if a bill with the same
        invoice number exists for this vendor, it returns the existing bill.
        
        Args:
            tenant_id: Tenant ID
            vendor_id: Vendor ID (required)
            amount: Bill amount
            invoice_number: Invoice number (for idempotency)
            invoice_date: Invoice date (YYYY-MM-DD format)
            due_date: Due date (YYYY-MM-DD format)
            description: Bill description/memo
            line_items: Bill line items (each can have description, quantity, rate, amount)
            is_draft: Whether this is a draft bill (default True)
            payment_term_public_id: Payment term public ID (default "Due on receipt")
            project_public_id: Project public ID for line items
            sub_cost_code_id: Sub cost code ID for line items
            is_billable: Whether line items are billable (default True)
            
        Returns:
            CapabilityResult with created/existing Bill
        """
        self._log_call(
            "create_bill",
            vendor_id=vendor_id,
            amount=amount,
            invoice_number=invoice_number,
            is_draft=is_draft,
        )
        
        try:
            service = self._get_bill_service()
            vendor_service = self._get_vendor_service()
            
            # Vendor is required - get vendor_public_id from vendor_id
            if not vendor_id:
                return CapabilityResult.fail(
                    error="Vendor is required to create a bill. No vendor was matched.",
                )
            
            vendor = vendor_service.read_by_id(vendor_id)
            if not vendor:
                return CapabilityResult.fail(
                    error=f"Vendor with ID {vendor_id} not found.",
                )
            
            vendor_public_id = vendor.public_id
            
            # Generate bill number if not provided
            from datetime import datetime
            import uuid
            if not invoice_number:
                # Generate a unique bill number
                invoice_number = f"DRAFT-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
            
            # Use current date if invoice_date not provided
            if not invoice_date:
                invoice_date = datetime.utcnow().strftime("%Y-%m-%d")
            
            # Set due_date to invoice_date if not provided
            if not due_date:
                due_date = invoice_date
            
            # Check for existing bill (idempotency) - by bill number and vendor
            try:
                existing = service.read_by_bill_number_and_vendor_public_id(
                    bill_number=invoice_number,
                    vendor_public_id=vendor_public_id,
                )
                if existing:
                    return CapabilityResult.ok(
                        data={
                            "id": existing.id,
                            "public_id": existing.public_id,
                            "bill_number": existing.bill_number,
                            "vendor_id": existing.vendor_id,
                            "total_amount": float(existing.total_amount) if existing.total_amount else None,
                            "is_draft": existing.is_draft,
                        },
                        already_existed=True,
                    )
            except Exception:
                pass  # No existing bill, continue to create
            
            # Create new bill
            from decimal import Decimal
            total_amount = Decimal(str(amount)) if amount is not None else None
            
            bill = service.create(
                vendor_public_id=vendor_public_id,
                payment_term_public_id=payment_term_public_id,
                bill_date=invoice_date,
                due_date=due_date,
                bill_number=invoice_number,
                total_amount=total_amount,
                memo=description,
                is_draft=is_draft,
            )
            
            bill_data = {
                "id": bill.id,
                "public_id": bill.public_id,
                "bill_number": bill.bill_number,
                "vendor_id": bill.vendor_id,
                "total_amount": float(bill.total_amount) if bill.total_amount else None,
                "is_draft": bill.is_draft,
            }
            
            # Create line items if provided
            first_line_item_public_id = None
            if line_items and bill.id:
                first_line_item_public_id = self._create_bill_line_items(
                    bill_id=bill.id,
                    bill_public_id=bill.public_id,
                    line_items=line_items,
                    project_public_id=project_public_id,
                    sub_cost_code_id=sub_cost_code_id,
                    is_billable=is_billable,
                )
            
            bill_data["first_line_item_public_id"] = first_line_item_public_id
            
            return CapabilityResult.ok(
                data=bill_data,
                already_existed=False,
            )
            
        except ValueError as e:
            # Handle validation errors from service
            logger.warning(f"Bill creation validation error: {e}")
            return CapabilityResult.fail(error=str(e))
        except Exception as e:
            logger.exception("Error in create_bill")
            return CapabilityResult.fail(error=str(e))
    
    def _create_bill_line_items(
        self,
        bill_id: int,
        bill_public_id: str,
        line_items: List[Dict],
        project_public_id: Optional[str] = None,
        sub_cost_code_id: Optional[int] = None,
        is_billable: bool = True,
    ) -> Optional[str]:
        """
        Create line items for a bill using the existing BillLineItemService.
        
        Uses the same service layer as the Bill module UI.
        
        Args:
            bill_id: Internal bill ID
            bill_public_id: Public ID of the bill
            line_items: List of line item dicts with description, quantity, rate, amount
            project_public_id: Project to assign to all line items
            sub_cost_code_id: Sub cost code to assign to all line items
            is_billable: Whether line items are billable (default True)
        
        Returns:
            The public_id of the first created line item (for attachment linking)
        """
        try:
            from modules.bill_line_item.business.service import BillLineItemService
            from decimal import Decimal
            
            line_item_service = BillLineItemService()
            first_line_item_public_id = None
            
            for idx, item in enumerate(line_items):
                description = item.get("description", "Line item")
                quantity = item.get("quantity", 1)
                rate = item.get("rate", 0)
                amount = item.get("amount", quantity * rate)
                
                # Use item-level overrides if provided, otherwise use defaults
                item_project = item.get("project_public_id") or project_public_id
                item_sub_cost_code = item.get("sub_cost_code_id") or sub_cost_code_id
                item_is_billable = item.get("is_billable", is_billable)
                
                created = line_item_service.create(
                    bill_public_id=bill_public_id,
                    description=description,
                    quantity=int(quantity) if quantity else 1,
                    rate=Decimal(str(rate)) if rate else Decimal("0"),
                    amount=Decimal(str(amount)) if amount else Decimal("0"),
                    project_public_id=item_project,
                    sub_cost_code_id=item_sub_cost_code,
                    is_billable=item_is_billable,
                    is_draft=True,
                )
                
                # Capture first line item for attachment linking
                if idx == 0 and created and created.public_id:
                    first_line_item_public_id = created.public_id
            
            return first_line_item_public_id
                
        except Exception as e:
            logger.warning(f"Error creating bill line items: {e}")
            return None
    
    def link_attachment_to_bill_line_item(
        self,
        bill_line_item_public_id: str,
        blob_url: str,
        filename: str,
        content_type: Optional[str] = None,
        file_size: Optional[int] = None,
    ) -> CapabilityResult:
        """
        Create an Attachment record from a blob URL and link it to a BillLineItem.
        
        Uses the existing AttachmentService and BillLineItemAttachmentService.
        
        Args:
            bill_line_item_public_id: The public_id of the bill line item
            blob_url: The Azure Blob Storage URL where the file is stored
            filename: Original filename
            content_type: MIME type of the file
            file_size: Size in bytes (optional)
            
        Returns:
            CapabilityResult with attachment data
        """
        self._log_call(
            "link_attachment_to_bill_line_item",
            bill_line_item_public_id=bill_line_item_public_id,
            filename=filename,
        )
        
        try:
            from modules.attachment.business.service import AttachmentService
            from modules.bill_line_item_attachment.business.service import BillLineItemAttachmentService
            import os
            
            attachment_service = AttachmentService()
            link_service = BillLineItemAttachmentService()
            
            # Extract file extension
            file_extension = AttachmentService.extract_extension(filename)
            
            # Create the Attachment record
            attachment = attachment_service.create(
                filename=filename,
                original_filename=filename,
                file_extension=file_extension,
                content_type=content_type or "application/octet-stream",
                file_size=file_size or 0,  # Default to 0 if not provided
                file_hash=None,  # Could calculate if needed
                blob_url=blob_url,
                description=f"Extracted from email workflow",
                category="bill",
                status="active",
                storage_tier="Hot",
            )
            
            if not attachment or not attachment.public_id:
                return CapabilityResult.fail(error="Failed to create attachment record")
            
            # Link the attachment to the bill line item
            link = link_service.create(
                bill_line_item_public_id=bill_line_item_public_id,
                attachment_public_id=attachment.public_id,
            )
            
            if not link:
                return CapabilityResult.fail(error="Failed to link attachment to bill line item")
            
            return CapabilityResult.ok(
                data={
                    "attachment_id": attachment.id,
                    "attachment_public_id": attachment.public_id,
                    "blob_url": blob_url,
                    "link_id": link.id,
                    "link_public_id": link.public_id,
                }
            )
            
        except Exception as e:
            logger.exception("Error in link_attachment_to_bill_line_item")
            return CapabilityResult.fail(error=str(e))
    
    def get_known_vendor_emails(self, tenant_id: int) -> CapabilityResult:
        """
        Get list of known vendor email addresses.
        
        Used for filtering incoming emails.
        """
        try:
            service = self._get_vendor_service()
            vendors = service.read_all()
            
            emails = [v.email for v in vendors if v.email]
            
            return CapabilityResult.ok(data=emails)
            
        except Exception as e:
            logger.exception("Error in get_known_vendor_emails")
            return CapabilityResult.fail(error=str(e))
    
    def get_known_vendor_names(self, tenant_id: int) -> CapabilityResult:
        """Get list of known vendor names."""
        try:
            service = self._get_vendor_service()
            vendors = service.read_all()
            
            names = [v.name for v in vendors if v.name]
            return CapabilityResult.ok(data=names)
            
        except Exception as e:
            logger.exception("Error in get_known_vendor_names")
            return CapabilityResult.fail(error=str(e))
    
    def get_known_project_names(self, tenant_id: int) -> CapabilityResult:
        """Get list of known project names."""
        try:
            service = self._get_project_service()
            projects = service.read_all()
            
            names = [p.name for p in projects if p.name]
            return CapabilityResult.ok(data=names)
            
        except Exception as e:
            logger.exception("Error in get_known_project_names")
            return CapabilityResult.fail(error=str(e))
    
    def get_projects_for_matching(self, tenant_id: int) -> CapabilityResult:
        """
        Get list of projects with details for LLM matching hints.
        
        Returns list of dicts with id, public_id, name, description.
        """
        try:
            service = self._get_project_service()
            projects = service.read_all()
            
            result = [
                {
                    "id": p.id,
                    "public_id": p.public_id,
                    "name": p.name,
                    "description": p.description,
                }
                for p in projects if p.name
            ]
            return CapabilityResult.ok(data=result)
            
        except Exception as e:
            logger.exception("Error in get_projects_for_matching")
            return CapabilityResult.fail(error=str(e))
    
    def get_sub_cost_codes(self, tenant_id: int) -> CapabilityResult:
        """
        Get list of sub cost codes for LLM matching.
        
        Returns list of dicts with id, public_id, number, name, description.
        """
        try:
            from modules.sub_cost_code.business.service import SubCostCodeService
            service = SubCostCodeService()
            codes = service.read_all()
            
            result = [
                {
                    "id": c.id,
                    "public_id": c.public_id,
                    "number": c.number,
                    "name": c.name,
                    "description": c.description,
                }
                for c in codes if c.number
            ]
            return CapabilityResult.ok(data=result)
            
        except Exception as e:
            logger.exception("Error in get_sub_cost_codes")
            return CapabilityResult.fail(error=str(e))
    
    def match_sub_cost_code(
        self,
        code_string: str,
        tenant_id: int,
    ) -> CapabilityResult:
        """
        Match a sub cost code string (e.g. "03-200") to a SubCostCode record.
        
        Args:
            code_string: The code extracted from email (e.g. "03-200", "6-100")
            tenant_id: Tenant ID
            
        Returns:
            CapabilityResult with matched SubCostCode or None
        """
        self._log_call("match_sub_cost_code", code_string=code_string)
        
        if not code_string:
            return CapabilityResult.ok(data=None)
        
        try:
            from modules.sub_cost_code.business.service import SubCostCodeService
            service = SubCostCodeService()
            
            # Normalize the code string (remove spaces, ensure consistent format)
            normalized = code_string.strip().upper()
            
            # Try exact match first
            codes = service.read_all()
            for code in codes:
                if code.number and code.number.strip().upper() == normalized:
                    return CapabilityResult.ok(
                        data={
                            "id": code.id,
                            "public_id": code.public_id,
                            "number": code.number,
                            "name": code.name,
                            "description": code.description,
                        },
                        match_type="exact",
                    )
            
            # Try partial match (code might be "03-200" but DB has "03-200.00")
            for code in codes:
                if code.number and (
                    code.number.strip().upper().startswith(normalized) or
                    normalized.startswith(code.number.strip().upper())
                ):
                    return CapabilityResult.ok(
                        data={
                            "id": code.id,
                            "public_id": code.public_id,
                            "number": code.number,
                            "name": code.name,
                            "description": code.description,
                        },
                        match_type="partial",
                    )
            
            # No match found
            return CapabilityResult.ok(data=None)
            
        except Exception as e:
            logger.exception("Error in match_sub_cost_code")
            return CapabilityResult.fail(error=str(e))
    
    def get_default_payment_term(self, tenant_id: int) -> CapabilityResult:
        """
        Get the default payment term ("Due on receipt").
        
        Returns the payment term record or None.
        """
        try:
            from modules.payment_term.business.service import PaymentTermService
            service = PaymentTermService()
            terms = service.read_all()
            
            # Look for "Due on receipt" or similar
            for term in terms:
                if term.name and "due on receipt" in term.name.lower():
                    return CapabilityResult.ok(
                        data={
                            "id": term.id,
                            "public_id": term.public_id,
                            "name": term.name,
                        }
                    )
            
            # Fallback to first term if no "Due on receipt"
            if terms:
                first = terms[0]
                return CapabilityResult.ok(
                    data={
                        "id": first.id,
                        "public_id": first.public_id,
                        "name": first.name,
                    }
                )
            
            return CapabilityResult.ok(data=None)
            
        except Exception as e:
            logger.exception("Error in get_default_payment_term")
            return CapabilityResult.fail(error=str(e))
