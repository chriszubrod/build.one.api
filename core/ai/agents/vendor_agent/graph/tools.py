# Python Standard Library Imports
import json
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
from langchain_core.tools import tool

# Local Imports
from core.ai.agents.vendor_agent.business.service import VendorAgentService
from core.ai.agents.vendor_agent.config import (
    CONFIDENCE_THRESHOLDS,
    DEFAULT_BATCH_LIMIT,
    MAX_BATCH_LIMIT,
    DEFAULT_BILL_LIMIT,
    DEFAULT_EXPENSE_LIMIT,
)
from entities.vendor.business.service import VendorService
from entities.vendor_type.business.service import VendorTypeService
from entities.bill.business.service import BillService
from entities.bill_line_item.business.service import BillLineItemService

logger = logging.getLogger(__name__)


# =============================================================================
# Context Holder (set by agent before tool execution)
# =============================================================================

class ToolContext:
    """
    Holds context that tools need but shouldn't be passed as parameters.
    Set by the agent runner before executing tools.
    """
    tenant_id: int = 1
    agent_run_id: Optional[int] = None
    user_id: Optional[str] = None

    @classmethod
    def set(cls, tenant_id: int, agent_run_id: int = None, user_id: str = None):
        cls.tenant_id = tenant_id
        cls.agent_run_id = agent_run_id
        cls.user_id = user_id


# =============================================================================
# Discovery Tools
# =============================================================================

@tool
def get_vendors_missing_type(
    limit: int = DEFAULT_BATCH_LIMIT,
    exclude_with_pending: bool = True,
) -> dict:
    """
    Get vendors that do not have a VendorType assigned.

    Use this tool to find vendors that need VendorType classification.
    Returns a batch of vendors for processing.

    Args:
        limit: Maximum number of vendors to return (default 50, max 200)
        exclude_with_pending: If True, skip vendors that already have pending proposals

    Returns:
        Dict with 'vendors' list and 'total_missing' count
    """
    try:
        limit = min(limit, MAX_BATCH_LIMIT)

        vendor_service = VendorService()
        agent_service = VendorAgentService()

        # Get all vendors
        all_vendors = vendor_service.read_all()

        # Filter to those without vendor_type_id
        missing_type = [v for v in all_vendors if v.vendor_type_id is None]

        result_vendors = []
        for vendor in missing_type[:limit * 2]:  # Get extra to account for filtering
            if len(result_vendors) >= limit:
                break

            has_pending = False
            if exclude_with_pending:
                # Check for pending proposals
                pending = agent_service.get_proposals_for_vendor(
                    vendor_id=vendor.id,
                    status="pending",
                    include_fields=False,
                )
                # Check specifically for vendor_type proposals
                for p in pending:
                    fields = agent_service.repo.read_proposal_fields(p.id)
                    if any(f.field_name == "vendor_type_id" for f in fields):
                        has_pending = True
                        break

            if exclude_with_pending and has_pending:
                continue

            result_vendors.append({
                "id": vendor.id,
                "public_id": vendor.public_id,
                "name": vendor.name,
                "abbreviation": vendor.abbreviation,
                "has_pending_proposal": has_pending,
                "created_datetime": vendor.created_datetime,
            })

        return {
            "vendors": result_vendors,
            "total_missing": len(missing_type),
            "returned_count": len(result_vendors),
        }

    except Exception as e:
        logger.error(f"Error in get_vendors_missing_type: {e}")
        return {
            "error": str(e),
            "vendors": [],
            "total_missing": 0,
            "returned_count": 0,
        }


# =============================================================================
# Vendor Context Tools
# =============================================================================

@tool
def get_vendor_details(vendor_id: int) -> dict:
    """
    Get detailed information about a specific vendor.

    Use this tool to retrieve core vendor information for analysis.

    Args:
        vendor_id: The database ID of the vendor

    Returns:
        Dict with vendor details including name, abbreviation, current type, and taxpayer info
    """
    try:
        vendor_service = VendorService()
        vendor = vendor_service.read_by_id(id=vendor_id)

        if not vendor:
            return {"error": f"Vendor not found: {vendor_id}"}

        # Get current vendor type if assigned
        current_type = None
        if vendor.vendor_type_id:
            vendor_type_service = VendorTypeService()
            vt = vendor_type_service.read_by_id(id=vendor.vendor_type_id)
            if vt:
                current_type = {
                    "public_id": vt.public_id,
                    "name": vt.name,
                    "description": vt.description,
                }

        # Get taxpayer info if linked
        taxpayer_info = None
        if vendor.taxpayer_id:
            try:
                from entities.taxpayer.business.service import TaxpayerService
                taxpayer_service = TaxpayerService()
                taxpayer = taxpayer_service.read_by_id(id=vendor.taxpayer_id)
                if taxpayer:
                    # Mask the ID number for safety
                    id_masked = None
                    if taxpayer.id_number:
                        id_masked = f"***-**-{taxpayer.id_number[-4:]}" if len(taxpayer.id_number) >= 4 else "***"
                    taxpayer_info = {
                        "name": taxpayer.name,
                        "id_number_masked": id_masked,
                        "taxpayer_type": taxpayer.taxpayer_type,
                    }
            except Exception as e:
                logger.warning(f"Could not load taxpayer info: {e}")

        return {
            "vendor": {
                "id": vendor.id,
                "public_id": vendor.public_id,
                "name": vendor.name,
                "abbreviation": vendor.abbreviation,
                "current_vendor_type": current_type,
                "taxpayer": taxpayer_info,
                "is_draft": vendor.is_draft,
                "created_datetime": vendor.created_datetime,
            }
        }

    except Exception as e:
        logger.error(f"Error in get_vendor_details: {e}")
        return {"error": str(e)}


@tool
def get_vendor_pending_proposals(vendor_id: int) -> dict:
    """
    Get pending proposals for a vendor.

    Use this tool to check if a vendor already has proposals awaiting review.

    Args:
        vendor_id: The database ID of the vendor

    Returns:
        Dict with 'pending_proposals' list and 'has_pending' boolean
    """
    try:
        agent_service = VendorAgentService()
        proposals = agent_service.get_proposals_for_vendor(
            vendor_id=vendor_id,
            status="pending",
            include_fields=True,
        )

        result = []
        for p in proposals:
            field_changes = []
            for f in p.fields:
                field_changes.append({
                    "field_name": f.field_name,
                    "proposed_value": f.new_display_value or f.new_value,
                })

            result.append({
                "public_id": p.public_id,
                "reasoning": p.reasoning,
                "confidence": float(p.confidence) if p.confidence else None,
                "field_changes": field_changes,
                "created_datetime": p.created_datetime,
            })

        return {
            "pending_proposals": result,
            "has_pending": len(result) > 0,
        }

    except Exception as e:
        logger.error(f"Error in get_vendor_pending_proposals: {e}")
        return {"error": str(e), "pending_proposals": [], "has_pending": False}


@tool
def get_vendor_rejection_history(vendor_id: int) -> dict:
    """
    Get history of rejected proposals for a vendor.

    IMPORTANT: Always check rejection history before making a proposal.
    Learn from past rejections to avoid repeating the same mistakes.

    Args:
        vendor_id: The database ID of the vendor

    Returns:
        Dict with 'rejections' list containing past rejected proposals and their reasons
    """
    try:
        agent_service = VendorAgentService()
        rejected = agent_service.get_rejected_proposals_for_learning(
            vendor_id=vendor_id,
            include_fields=True,
        )

        result = []
        for p in rejected:
            for f in p.fields:
                result.append({
                    "proposal_id": p.public_id,
                    "field_name": f.field_name,
                    "proposed_value": f.new_display_value or f.new_value,
                    "rejection_reason": p.rejection_reason,
                    "rejected_by": p.responded_by,
                    "rejected_at": p.responded_datetime,
                    "original_reasoning": p.reasoning,
                })

        return {
            "rejections": result,
            "rejection_count": len(result),
        }

    except Exception as e:
        logger.error(f"Error in get_vendor_rejection_history: {e}")
        return {"error": str(e), "rejections": [], "rejection_count": 0}


@tool
def get_vendor_bills(vendor_id: int, limit: int = DEFAULT_BILL_LIMIT) -> dict:
    """
    Get historical bills for a vendor with line item coding details.

    Use this tool to understand how the vendor's bills have been coded.
    The coding_summary shows how line items have been categorized,
    which can help determine the vendor type.

    Args:
        vendor_id: The database ID of the vendor
        limit: Maximum number of bills to return (default 20)

    Returns:
        Dict with 'bills' list, 'bill_count', and 'coding_summary'
    """
    try:
        bill_service = BillService()
        line_item_service = BillLineItemService()

        # Get bills for this vendor (read_paginated returns list[Bill], not a dict)
        bills_result = bill_service.read_paginated(
            vendor_id=vendor_id,
            page_number=1,
            page_size=limit,
            sort_by="BillDate",
            sort_direction="DESC",
        )
        if isinstance(bills_result, list):
            bills = bills_result
            total_count = len(bills)
        else:
            bills = bills_result.get("items", [])
            total_count = bills_result.get("total_count", 0)

        # Track coding across all bills
        coding_counts = {}

        result_bills = []
        for bill in bills:
            # Get line items for this bill
            line_items = line_item_service.read_by_bill_id(bill_id=bill.id)

            line_item_data = []
            for li in line_items:
                # Get cost code name if available
                account_name = None
                account_code = None
                if li.sub_cost_code_id:
                    try:
                        from entities.sub_cost_code.business.service import SubCostCodeService
                        scc_service = SubCostCodeService()
                        scc = scc_service.read_by_id(id=li.sub_cost_code_id)
                        if scc:
                            account_code = scc.code
                            account_name = scc.name
                            # Track coding
                            coding_counts[account_name] = coding_counts.get(account_name, 0) + 1
                    except Exception:
                        pass

                # Get project name if available
                project_name = None
                if li.project_id:
                    try:
                        from entities.project.business.service import ProjectService
                        project_service = ProjectService()
                        project = project_service.read_by_id(id=li.project_id)
                        if project:
                            project_name = project.name
                    except Exception:
                        pass

                line_item_data.append({
                    "description": li.description,
                    "amount": float(li.amount) if li.amount else None,
                    "account_code": account_code,
                    "account_name": account_name,
                    "project_name": project_name,
                })

            result_bills.append({
                "public_id": bill.public_id,
                "bill_number": bill.bill_number,
                "total_amount": float(bill.total_amount) if bill.total_amount else None,
                "bill_date": bill.bill_date,
                "line_items": line_item_data,
            })

        return {
            "bills": result_bills,
            "bill_count": total_count,
            "coding_summary": coding_counts,
        }

    except Exception as e:
        logger.error(f"Error in get_vendor_bills: {e}")
        return {"error": str(e), "bills": [], "bill_count": 0, "coding_summary": {}}


@tool
def get_vendor_expenses(vendor_id: int, limit: int = DEFAULT_EXPENSE_LIMIT) -> dict:
    """
    Get historical expenses for a vendor with line item coding details.

    Use this tool to understand how the vendor's expenses have been coded.
    Some vendors only have expenses (not bills), so this complements get_vendor_bills.
    The coding_summary shows how line items have been categorized,
    which can help determine the vendor type.

    Args:
        vendor_id: The database ID of the vendor
        limit: Maximum number of expenses to return (default 20)

    Returns:
        Dict with 'expenses' list, 'expense_count', and 'coding_summary'
    """
    try:
        from entities.expense.business.service import ExpenseService
        from entities.expense_line_item.business.service import ExpenseLineItemService

        expense_service = ExpenseService()
        line_item_service = ExpenseLineItemService()

        expenses_result = expense_service.read_paginated(
            vendor_id=vendor_id,
            page_number=1,
            page_size=limit,
            sort_by="ExpenseDate",
            sort_direction="DESC",
        )
        if isinstance(expenses_result, list):
            expenses = expenses_result
            total_count = len(expenses)
        else:
            expenses = expenses_result.get("items", [])
            total_count = expenses_result.get("total_count", 0)

        coding_counts = {}

        result_expenses = []
        for expense in expenses:
            line_items = line_item_service.read_by_expense_id(expense_id=expense.id)

            line_item_data = []
            for li in line_items:
                account_name = None
                account_code = None
                if li.sub_cost_code_id:
                    try:
                        from entities.sub_cost_code.business.service import SubCostCodeService
                        scc_service = SubCostCodeService()
                        scc = scc_service.read_by_id(id=li.sub_cost_code_id)
                        if scc:
                            account_code = scc.code
                            account_name = scc.name
                            coding_counts[account_name] = coding_counts.get(account_name, 0) + 1
                    except Exception:
                        pass

                project_name = None
                if li.project_id:
                    try:
                        from entities.project.business.service import ProjectService
                        project_service = ProjectService()
                        project = project_service.read_by_id(id=li.project_id)
                        if project:
                            project_name = project.name
                    except Exception:
                        pass

                line_item_data.append({
                    "description": li.description,
                    "amount": float(li.amount) if li.amount else None,
                    "account_code": account_code,
                    "account_name": account_name,
                    "project_name": project_name,
                })

            result_expenses.append({
                "public_id": expense.public_id,
                "reference_number": expense.reference_number,
                "total_amount": float(expense.total_amount) if expense.total_amount else None,
                "expense_date": expense.expense_date,
                "line_items": line_item_data,
            })

        return {
            "expenses": result_expenses,
            "expense_count": total_count,
            "coding_summary": coding_counts,
        }

    except Exception as e:
        logger.error(f"Error in get_vendor_expenses: {e}")
        return {"error": str(e), "expenses": [], "expense_count": 0, "coding_summary": {}}


@tool
def get_vendor_documents(vendor_id: int) -> dict:
    """
    Get system attachments for a vendor (stored in Azure Blob Storage).

    Use this tool to see what documents have been uploaded for this vendor.
    Document names may provide hints about the vendor type.

    Args:
        vendor_id: The database ID of the vendor

    Returns:
        Dict with 'documents' list and 'document_count'
    """
    try:
        # Get the vendor to find taxpayer_id (attachments are linked via taxpayer)
        vendor_service = VendorService()
        vendor = vendor_service.read_by_id(id=vendor_id)

        if not vendor:
            return {"error": f"Vendor not found: {vendor_id}", "documents": [], "document_count": 0}

        documents = []

        if vendor.taxpayer_id:
            try:
                from entities.taxpayer.business.service import TaxpayerService
                from entities.taxpayer_attachment.business.service import TaxpayerAttachmentService
                from entities.attachment.business.service import AttachmentService

                taxpayer_service = TaxpayerService()
                taxpayer = taxpayer_service.read_by_id(id=vendor.taxpayer_id)

                if taxpayer and taxpayer.public_id:
                    ta_service = TaxpayerAttachmentService()
                    attachment_service = AttachmentService()

                    taxpayer_attachments = ta_service.read_by_taxpayer_id(
                        taxpayer_public_id=taxpayer.public_id
                    )

                    for ta in taxpayer_attachments:
                        if ta.attachment_id:
                            attachment = attachment_service.read_by_id(id=ta.attachment_id)
                            if attachment:
                                documents.append({
                                    "public_id": attachment.public_id,
                                    "filename": attachment.original_filename,
                                    "file_type": attachment.file_extension,
                                    "content_type": attachment.content_type,
                                    "uploaded_datetime": attachment.created_datetime,
                                })
            except Exception as e:
                logger.warning(f"Could not load taxpayer attachments: {e}")

        return {
            "documents": documents,
            "document_count": len(documents),
        }

    except Exception as e:
        logger.error(f"Error in get_vendor_documents: {e}")
        return {"error": str(e), "documents": [], "document_count": 0}


@tool
def get_vendor_sharepoint_folder(vendor_id: int) -> dict:
    """
    Get the contents of the vendor's linked SharePoint folder.

    Use this tool to see what documents exist in the vendor's SharePoint folder.
    File names and types may provide hints about the vendor type.

    Args:
        vendor_id: The database ID of the vendor

    Returns:
        Dict with 'sharepoint_linked' boolean, 'folder_path', and 'files' list
    """
    try:
        vendor_service = VendorService()
        vendor = vendor_service.read_by_id(id=vendor_id)

        if not vendor:
            return {"error": f"Vendor not found: {vendor_id}"}

        try:
            from integrations.ms.sharepoint.driveitem.connector.vendor.business.service import DriveItemVendorConnector
            from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
            from integrations.ms.sharepoint.drive.business.service import MsDriveService

            connector = DriveItemVendorConnector()
            driveitem = connector.get_driveitem_for_vendor(vendor_id=vendor.id)

            if not driveitem:
                return {
                    "sharepoint_linked": False,
                    "folder_path": None,
                    "files": [],
                    "file_count": 0,
                }

            # Get the drive to get its public_id
            drive_service = MsDriveService()
            drive = drive_service.read_by_id(id=driveitem.get("ms_drive_id"))

            if not drive:
                return {
                    "sharepoint_linked": True,
                    "folder_path": driveitem.get("name"),
                    "error": "Could not load drive information",
                    "files": [],
                    "file_count": 0,
                }

            # Browse the folder contents
            driveitem_service = MsDriveItemService()
            browse_result = driveitem_service.browse_folder(
                drive_public_id=drive.public_id,
                item_id=driveitem.get("item_id"),
            )

            files = []
            if browse_result.get("status_code") == 200:
                for item in browse_result.get("items", []):
                    files.append({
                        "name": item.get("name"),
                        "item_type": item.get("item_type"),
                        "size_kb": round(item.get("size", 0) / 1024, 1) if item.get("size") else None,
                        "mime_type": item.get("mime_type"),
                        "modified_datetime": item.get("graph_modified_datetime"),
                    })

            return {
                "sharepoint_linked": True,
                "folder_path": driveitem.get("name"),
                "web_url": driveitem.get("web_url"),
                "files": files,
                "file_count": len(files),
            }

        except ImportError:
            return {
                "sharepoint_linked": False,
                "error": "SharePoint integration not available",
                "files": [],
                "file_count": 0,
            }

    except Exception as e:
        logger.error(f"Error in get_vendor_sharepoint_folder: {e}")
        return {"error": str(e), "sharepoint_linked": False, "files": [], "file_count": 0}


# =============================================================================
# Reference Tools
# =============================================================================

@tool
def get_available_vendor_types() -> dict:
    """
    Get all available VendorType options.

    Use this tool to see what vendor types are available for assignment.
    Review the descriptions to understand what each type means.

    Returns:
        Dict with 'vendor_types' list containing all available types
    """
    try:
        vendor_type_service = VendorTypeService()
        vendor_types = vendor_type_service.read_all()

        result = []
        for vt in vendor_types:
            result.append({
                "public_id": vt.public_id,
                "name": vt.name,
                "description": vt.description,
            })

        return {
            "vendor_types": result,
        }

    except Exception as e:
        logger.error(f"Error in get_available_vendor_types: {e}")
        return {"error": str(e), "vendor_types": []}


# =============================================================================
# Action Tools
# =============================================================================

@tool
def create_vendor_type_proposal(
    vendor_id: int,
    vendor_type_public_id: str,
    reasoning: str,
    confidence: float,
    evidence_summary: str = None,
) -> dict:
    """
    Create a proposal to assign a VendorType to a vendor.

    IMPORTANT:
    - Check rejection history first to avoid repeating rejected proposals
    - Confidence must be >= 0.50 to create a proposal
    - If confidence < 0.50, use skip_vendor instead

    Args:
        vendor_id: The database ID of the vendor
        vendor_type_public_id: The public UUID of the VendorType to propose
        reasoning: Detailed explanation of why this type fits the vendor
        confidence: Confidence score from 0.0 to 1.0
        evidence_summary: Optional summary of evidence used in decision

    Returns:
        Dict with 'success' boolean and 'proposal' details
    """
    try:
        # Validate confidence threshold
        if confidence < CONFIDENCE_THRESHOLDS["skip"]:
            return {
                "success": False,
                "error": f"Confidence {confidence} is below minimum threshold {CONFIDENCE_THRESHOLDS['skip']}. Use skip_vendor instead.",
            }

        # Get the vendor
        vendor_service = VendorService()
        vendor = vendor_service.read_by_id(id=vendor_id)
        if not vendor:
            return {"success": False, "error": f"Vendor not found: {vendor_id}"}

        # Get the vendor type
        vendor_type_service = VendorTypeService()
        vendor_type = vendor_type_service.read_by_public_id(public_id=vendor_type_public_id)
        if not vendor_type:
            return {"success": False, "error": f"VendorType not found: {vendor_type_public_id}"}

        # Check for existing pending proposal for vendor_type_id
        agent_service = VendorAgentService()
        existing_proposals = agent_service.get_proposals_for_vendor(
            vendor_id=vendor_id,
            status="pending",
            include_fields=True,
        )
        for p in existing_proposals:
            if any(f.field_name == "vendor_type_id" for f in p.fields):
                return {
                    "success": False,
                    "error": "Vendor already has a pending VendorType proposal",
                    "existing_proposal_id": p.public_id,
                }

        # Determine confidence tier
        confidence_tier = "normal"
        if confidence < CONFIDENCE_THRESHOLDS["auto_propose"]:
            confidence_tier = "low_confidence"

        # Build context
        context = {}
        if evidence_summary:
            context["evidence_summary"] = evidence_summary
        context["confidence_tier"] = confidence_tier

        # Get current vendor type display value
        old_display = "(none)"
        old_value = None
        if vendor.vendor_type_id:
            current_type = vendor_type_service.read_by_id(id=vendor.vendor_type_id)
            if current_type:
                old_display = current_type.name
                old_value = current_type.public_id

        # Create the proposal
        proposal = agent_service.create_proposal(
            tenant_id=ToolContext.tenant_id,
            vendor_id=vendor_id,
            agent_run_id=ToolContext.agent_run_id,
            reasoning=reasoning,
            field_changes=[{
                "field_name": "vendor_type_id",
                "old_value": old_value,
                "new_value": vendor_type_public_id,
                "old_display_value": old_display,
                "new_display_value": vendor_type.name,
            }],
            confidence=confidence,
            context=context,
        )

        # Log to conversation
        agent_service.add_agent_message(
            tenant_id=ToolContext.tenant_id,
            vendor_id=vendor_id,
            content=f"Proposed VendorType: {vendor_type.name}\n\nReasoning: {reasoning}",
            message_type="proposal",
            agent_run_id=ToolContext.agent_run_id,
            proposal_id=proposal.id,
        )

        return {
            "success": True,
            "proposal": {
                "public_id": proposal.public_id,
                "status": "pending",
                "confidence_tier": confidence_tier,
                "vendor_name": vendor.name,
                "proposed_type": vendor_type.name,
                "reasoning": reasoning,
                "confidence": confidence,
            },
        }

    except Exception as e:
        logger.error(f"Error in create_vendor_type_proposal: {e}")
        return {"success": False, "error": str(e)}


@tool
def skip_vendor(
    vendor_id: int,
    reason: str,
    reason_code: str = "insufficient_info",
) -> dict:
    """
    Skip a vendor and log the reason.

    Use this when:
    - Confidence is below 0.50
    - There's conflicting information
    - The vendor needs human review
    - There's insufficient information to make a decision

    Args:
        vendor_id: The database ID of the vendor
        reason: Detailed explanation of why the vendor was skipped
        reason_code: One of 'insufficient_info', 'needs_human_review', 'conflicting_signals'

    Returns:
        Dict with 'success' boolean and confirmation message
    """
    try:
        valid_codes = ["insufficient_info", "needs_human_review", "conflicting_signals"]
        if reason_code not in valid_codes:
            reason_code = "insufficient_info"

        # Get vendor for logging
        vendor_service = VendorService()
        vendor = vendor_service.read_by_id(id=vendor_id)
        if not vendor:
            return {"success": False, "error": f"Vendor not found: {vendor_id}"}

        # Log to conversation
        agent_service = VendorAgentService()
        agent_service.add_agent_message(
            tenant_id=ToolContext.tenant_id,
            vendor_id=vendor_id,
            content=f"Skipped vendor (reason: {reason_code})\n\n{reason}",
            message_type="skip",
            agent_run_id=ToolContext.agent_run_id,
            metadata={"reason_code": reason_code},
        )

        return {
            "success": True,
            "logged": True,
            "vendor_name": vendor.name,
            "reason_code": reason_code,
            "message": f"Vendor '{vendor.name}' skipped and logged to conversation",
        }

    except Exception as e:
        logger.error(f"Error in skip_vendor: {e}")
        return {"success": False, "error": str(e)}


# =============================================================================
# Tool Registry
# =============================================================================

# All tools available to the VendorAgent
VENDOR_AGENT_TOOLS = [
    # Discovery
    #get_vendors_missing_type,
    # Vendor Context
    get_vendor_details,
    get_vendor_pending_proposals,
    get_vendor_rejection_history,
    get_vendor_bills,
    get_vendor_expenses,
    get_vendor_documents,
    get_vendor_sharepoint_folder,
    # Reference
    get_available_vendor_types,
    # Actions
    create_vendor_type_proposal,
    skip_vendor,
]
