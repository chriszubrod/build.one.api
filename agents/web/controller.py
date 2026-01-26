# Python Standard Library Imports
from datetime import datetime
from typing import Optional, Any, Dict

# Third-party Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Local Imports
from modules.auth.business.service import get_current_user_web
from agents.admin import WorkflowAdmin
from agents.entity_registry import get_entity_config, EntityConfig
from agents.persistence.repo import WorkflowRepository, WorkflowEventRepository

router = APIRouter(prefix="/agent", tags=["web", "agent"])
templates = Jinja2Templates(directory="templates")


def _format_workflow_for_list(wf):
    """Format a workflow for the list view."""
    ctx = wf.context or {}
    classification = ctx.get("classification", {})
    email_info = ctx.get("email", {})
    
    # Get entity type info
    entity_type = ctx.get("entity_type") or classification.get("entity_type", "other")
    entity_label = ctx.get("entity_label", entity_type.replace("_", " ").title() if entity_type else "Unknown")
    
    # Calculate days ago
    days_ago = None
    if wf.created_at:
        try:
            created = wf.created_at if isinstance(wf.created_at, datetime) else datetime.fromisoformat(str(wf.created_at).replace('Z', '+00:00'))
            delta = datetime.utcnow() - created.replace(tzinfo=None)
            days_ago = delta.days
        except:
            pass
    
    return {
        "public_id": wf.public_id,
        "state": wf.state,
        "subject": email_info.get("subject", "No subject"),
        "from_address": email_info.get("from_address"),
        "from_name": email_info.get("from_name"),
        "entity_type": entity_type,
        "entity_label": entity_label,
        "confidence": classification.get("confidence"),
        "created_at": wf.created_at,
        "days_ago": days_ago,
        "has_attachments": bool(ctx.get("attachment_blob_urls") or ctx.get("attachments")),
        "reminder_count": ctx.get("reminder_count", 0),
        "needs_attention": wf.state == "needs_review",
    }


def _resolve_field_value(field_source: str, source_key: str, sources: Dict[str, Any]) -> Any:
    """Resolve a field value from the appropriate source."""
    source_data = sources.get(field_source, {})
    if not source_data:
        return None
    
    # Handle nested keys like "vendor.name"
    keys = source_key.split(".")
    value = source_data
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None
        if value is None:
            return None
    return value


def _format_entity_fields(entity_config: EntityConfig, sources: Dict[str, Any]) -> list:
    """Build the dynamic field list for the entity."""
    fields = []
    for field in entity_config.fields:
        value = _resolve_field_value(field.source, field.source_key, sources)
        fields.append({
            "key": field.key,
            "label": field.label,
            "value": value,
            "format": field.format,
        })
    return fields


def _format_workflow_for_detail(wf, events=None):
    """Format a workflow for the detail view."""
    ctx = wf.context or {}
    classification = ctx.get("classification", {})
    vendor_match = ctx.get("vendor_match", {})
    project_match = ctx.get("project_match", {})
    email_info = ctx.get("email", {})
    approval_request = ctx.get("approval_request", {})
    approval_response = ctx.get("approval_response", {})
    draft_entity = ctx.get("draft_entity", {})
    
    # Get entity configuration
    entity_type = ctx.get("entity_type", "other")
    entity_config = get_entity_config(entity_type)
    
    # Build sources dict for field resolution
    sources = {
        "classification": classification,
        "vendor_match": vendor_match,
        "project_match": project_match,
        "email": email_info,
        "draft": draft_entity,
        "customer_match": ctx.get("customer_match", {}),
    }
    
    # Build dynamic entity fields
    entity_fields = _format_entity_fields(entity_config, sources)
    
    return {
        "public_id": wf.public_id,
        "state": wf.state,
        "workflow_type": wf.workflow_type,
        "created_at": wf.created_at,
        "updated_at": wf.updated_at,
        
        # Entity info (dynamic)
        "entity_type": entity_type,
        "entity_label": entity_config.label,
        "entity_details_label": entity_config.details_label,
        "entity_icon": entity_config.icon,
        "entity_color": entity_config.color,
        "entity_fields": entity_fields,
        
        # Keep legacy fields for backwards compatibility
        "vendor_name": vendor_match.get("vendor", {}).get("name", "Unknown"),
        "vendor_confidence": vendor_match.get("confidence"),
        "vendor_match_type": vendor_match.get("match_type"),
        "invoice_number": classification.get("invoice_number"),
        "amount": classification.get("amount"),
        "invoice_date": classification.get("invoice_date"),
        "category": classification.get("category") or classification.get("entity_type"),
        "classification_confidence": classification.get("confidence"),
        "classification_reasoning": classification.get("reasoning"),
        "confirmed_entity_type": ctx.get("confirmed_entity_type"),
        "project_name": project_match.get("project", {}).get("name"),
        "project_confidence": project_match.get("confidence"),
        
        # Email info
        "email_subject": email_info.get("subject"),
        "email_from": email_info.get("from_address"),
        "email_body": email_info.get("body"),
        "email_body_type": email_info.get("body_type", "text"),
        "email_received": email_info.get("received_at"),
        
        # Attachments
        "attachments": ctx.get("attachments", []),
        "attachment_count": len(ctx.get("attachments", [])) or len(ctx.get("attachment_blob_urls", [])),
        
        # Conversation thread
        "conversation": ctx.get("conversation", []),
        "conversation_id": ctx.get("conversation_id") or email_info.get("conversation_id"),
        "message_count": ctx.get("message_count", len(ctx.get("conversation", []))),
        
        # Approval info
        "approval_sent_to": approval_request.get("sent_to"),
        "approval_sent_at": approval_request.get("sent_at"),
        "approval_decision": approval_response.get("decision"),
        "approval_notes": approval_response.get("notes"),
        "cost_code": approval_response.get("cost_code"),
        
        # Status info
        "reminder_count": ctx.get("reminder_count", 0),
        "qbo_sync": ctx.get("qbo_sync"),
        "created_bill_id": ctx.get("created_bill_id"),
        
        # Errors
        "triage_error": ctx.get("triage_error"),
        "parse_error": ctx.get("parse_error"),
        "entity_error": ctx.get("entity_error"),
        
        # Events timeline
        "events": [
            {
                "type": e.event_type,
                "from_state": e.from_state,
                "to_state": e.to_state,
                "step_name": e.step_name,
                "created_at": e.created_at,
                "created_by": e.created_by,
            }
            for e in (events or [])
        ],
    }


@router.get("/browse")
async def browse_inbox(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
):
    """
    Browse emails from inbox (view only, no workflow creation).
    """
    return templates.TemplateResponse(
        "agent/browse.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/list")
async def list_workflows(
    request: Request,
    current_user: dict = Depends(get_current_user_web),
    state: Optional[str] = None,
):
    """
    List all workflows in an inbox-style view.
    """
    tenant_id = current_user.get("tenant_id", 1)
    repo = WorkflowRepository()
    admin = WorkflowAdmin()
    
    # Get workflows
    workflows = repo.read_by_tenant_and_state(tenant_id=tenant_id, state=state)
    
    # Format for display
    workflow_list = [_format_workflow_for_list(wf) for wf in workflows]
    
    # Get counts for filter badges
    all_workflows = repo.read_by_tenant_and_state(tenant_id=tenant_id, state=None)
    state_counts = {}
    for wf in all_workflows:
        state_counts[wf.state] = state_counts.get(wf.state, 0) + 1
    
    # Get metrics
    metrics = admin.get_workflow_metrics(tenant_id, days=30)
    
    return templates.TemplateResponse(
        "agent/list.html",
        {
            "request": request,
            "workflows": workflow_list,
            "current_user": current_user,
            "current_path": request.url.path,
            "selected_state": state,
            "state_counts": state_counts,
            "metrics": metrics,
        },
    )


@router.get("/{public_id}")
async def view_workflow(
    request: Request,
    public_id: str,
    current_user: dict = Depends(get_current_user_web),
):
    """
    View a single workflow's details.
    """
    tenant_id = current_user.get("tenant_id", 1)
    repo = WorkflowRepository()
    event_repo = WorkflowEventRepository()
    
    # Get the workflow
    workflow = repo.read_by_public_id(public_id)
    if not workflow:
        return templates.TemplateResponse(
            "agent/not_found.html",
            {
                "request": request,
                "current_user": current_user,
                "current_path": request.url.path,
                "public_id": public_id,
            },
            status_code=404,
        )
    
    # Get events
    events = event_repo.read_by_workflow_id(workflow.id)
    
    # Get other workflows for the sidebar
    all_workflows = repo.read_by_tenant_and_state(tenant_id=tenant_id, state=None)
    workflow_list = [_format_workflow_for_list(wf) for wf in all_workflows[:20]]
    
    # Format workflow detail
    workflow_detail = _format_workflow_for_detail(workflow, events)
    
    return templates.TemplateResponse(
        "agent/view.html",
        {
            "request": request,
            "workflow": workflow_detail,
            "workflows": workflow_list,
            "current_user": current_user,
            "current_path": request.url.path,
            "selected_id": public_id,
        },
    )
