# Python Standard Library Imports
import base64
from typing import Optional

# Third-party Imports
from fastapi import APIRouter, Depends, Query, HTTPException

# Local Imports
from shared.rbac import require_module_api
from shared.rbac_constants import Modules
from integrations.ms.mail.message.business.service import MsMessageService
from integrations.ms.mail.message.api.schemas import (
    SendMessageRequest,
    CreateDraftRequest,
    UpdateDraftRequest,
    ReplyRequest,
    ForwardRequest,
    MoveMessageRequest,
    LinkAttachmentRequest,
)

router = APIRouter(prefix="/ms/mail", tags=["MS Mail"])

service = MsMessageService()


# =============================================================================
# Mail Folder Endpoints
# =============================================================================

@router.get("/folders")
async def list_mail_folders(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    List all mail folders for the current user.
    """
    result = service.list_folders()
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.get("/folders/{folder_id}")
async def get_mail_folder(folder_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Get a specific mail folder.
    Use well-known names: inbox, drafts, sentitems, deleteditems, or folder ID.
    """
    result = service.get_folder(folder_id)
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


# =============================================================================
# Message List & Read Endpoints (Pass-through to Graph)
# =============================================================================

@router.get("/messages")
async def list_messages(
    folder: str = Query(default="inbox", description="Folder ID or well-known name"),
    top: int = Query(default=25, ge=1, le=50, description="Number of messages to retrieve"),
    skip: int = Query(default=0, ge=0, description="Number of messages to skip"),
    filter_query: Optional[str] = Query(default=None, alias="filter", description="OData filter"),
    search: Optional[str] = Query(default=None, description="Search query"),
    order_by: str = Query(default="receivedDateTime desc", description="Sort order"),
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC)),
):
    """
    List messages from a mail folder.
    Pass-through to Graph API, not stored locally.
    """
    result = service.list_messages(
        folder=folder,
        top=top,
        skip=skip,
        filter_query=filter_query,
        search=search,
        order_by=order_by
    )
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.get("/messages/{message_id}")
async def get_message(
    message_id: str,
    include_body: bool = Query(default=True, description="Include full body content"),
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC)),
):
    """
    Get a specific message from Graph API.
    Pass-through, not stored locally.
    """
    result = service.get_message(message_id, include_body=include_body)
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.patch("/messages/{message_id}/read")
async def mark_message_read(
    message_id: str,
    is_read: bool = Query(default=True, description="Mark as read or unread"),
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_update")),
):
    """
    Mark a message as read or unread.
    """
    result = service.mark_message_read(message_id, is_read=is_read)
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.get("/messages/{message_id}/attachments")
async def list_message_attachments(message_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    List attachments for a message.
    """
    result = service.list_attachments(message_id)
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.get("/messages/{message_id}/attachments/{attachment_id}")
async def download_attachment(
    message_id: str, attachment_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))
):
    """
    Download an attachment from a message.
    Returns attachment metadata and base64-encoded content.
    """
    result = service.download_attachment(message_id, attachment_id)
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    
    # Don't return raw bytes via API - encode for JSON
    content = result.get("content")
    if content:
        result["content"] = base64.b64encode(content).decode("utf-8")
    
    return result


# =============================================================================
# Send & Draft Endpoints
# =============================================================================

@router.post("/messages/send")
async def send_message(request: SendMessageRequest, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Send a new email message.
    """
    result = service.send_message(
        to_recipients=[r.model_dump() for r in request.to_recipients],
        subject=request.subject,
        body=request.body,
        body_type=request.body_type,
        cc_recipients=[r.model_dump() for r in request.cc_recipients] if request.cc_recipients else None,
        bcc_recipients=[r.model_dump() for r in request.bcc_recipients] if request.bcc_recipients else None,
        importance=request.importance,
        save_to_sent_items=request.save_to_sent_items
    )
    if result.get("status_code") not in [200, 202]:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.post("/messages/drafts")
async def create_draft(request: CreateDraftRequest, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Create a new draft message.
    """
    result = service.create_draft(
        to_recipients=[r.model_dump() for r in request.to_recipients] if request.to_recipients else None,
        subject=request.subject,
        body=request.body,
        body_type=request.body_type,
        cc_recipients=[r.model_dump() for r in request.cc_recipients] if request.cc_recipients else None,
        bcc_recipients=[r.model_dump() for r in request.bcc_recipients] if request.bcc_recipients else None,
        importance=request.importance
    )
    if result.get("status_code") != 201:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.patch("/messages/drafts/{message_id}")
async def update_draft(
    message_id: str, request: UpdateDraftRequest, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_update"))
):
    """
    Update an existing draft message.
    """
    result = service.update_draft(
        message_id=message_id,
        to_recipients=[r.model_dump() for r in request.to_recipients] if request.to_recipients else None,
        subject=request.subject,
        body=request.body,
        body_type=request.body_type,
        cc_recipients=[r.model_dump() for r in request.cc_recipients] if request.cc_recipients else None,
        bcc_recipients=[r.model_dump() for r in request.bcc_recipients] if request.bcc_recipients else None,
        importance=request.importance
    )
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.post("/messages/drafts/{message_id}/send")
async def send_draft(message_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Send an existing draft message.
    """
    result = service.send_draft(message_id)
    if result.get("status_code") not in [200, 202]:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


# =============================================================================
# Reply & Forward Endpoints
# =============================================================================

@router.post("/messages/{message_id}/reply")
async def reply_to_message(
    message_id: str, request: ReplyRequest, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))
):
    """
    Reply to a message.
    """
    result = service.reply_to_message(
        message_id=message_id,
        body=request.body,
        body_type=request.body_type,
        reply_all=request.reply_all
    )
    if result.get("status_code") not in [200, 202]:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.post("/messages/{message_id}/reply/draft")
async def create_reply_draft(
    message_id: str,
    reply_all: bool = Query(default=False, description="Reply to all recipients"),
    current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create")),
):
    """
    Create a reply draft for more control before sending.
    """
    result = service.create_reply_draft(message_id, reply_all=reply_all)
    if result.get("status_code") != 201:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.post("/messages/{message_id}/forward")
async def forward_message(
    message_id: str, request: ForwardRequest, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))
):
    """
    Forward a message to recipients.
    """
    result = service.forward_message(
        message_id=message_id,
        to_recipients=[r.model_dump() for r in request.to_recipients],
        comment=request.comment
    )
    if result.get("status_code") not in [200, 202]:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.post("/messages/{message_id}/forward/draft")
async def create_forward_draft(message_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Create a forward draft for more control before sending.
    """
    result = service.create_forward_draft(message_id)
    if result.get("status_code") != 201:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


# =============================================================================
# Message Management Endpoints
# =============================================================================

@router.post("/messages/{message_id}/move")
async def move_message(
    message_id: str, request: MoveMessageRequest, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_update"))
):
    """
    Move a message to a different folder.
    """
    result = service.move_message(message_id, request.destination_folder_id)
    if result.get("status_code") != 201:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.delete("/messages/{message_id}")
async def delete_message(message_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_delete"))):
    """
    Delete a message from Graph API (moves to Deleted Items).
    """
    result = service.delete_message_from_graph(message_id)
    if result.get("status_code") != 204:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return {"message": "Message deleted successfully", "status_code": 204}


# =============================================================================
# Linked Message Endpoints (Local Storage)
# =============================================================================

@router.post("/messages/{message_id}/link")
async def link_message(message_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))):
    """
    Link a message by storing it locally.
    Fetches from Graph API and stores in database.
    """
    result = service.link_message(message_id)
    if result.get("status_code") not in [200, 201]:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.get("/linked")
async def list_linked_messages(current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    List all linked messages from local storage.
    """
    messages = service.read_all_linked()
    return {
        "message": f"Found {len(messages)} linked messages",
        "status_code": 200,
        "messages": [m.to_dict() for m in messages]
    }


@router.get("/linked/{public_id}")
async def get_linked_message(public_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Get a linked message with full details.
    """
    result = service.get_linked_message_full(public_id)
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.get("/linked/by-conversation/{conversation_id}")
async def get_linked_by_conversation(
    conversation_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))
):
    """
    Get all linked messages in a conversation thread.
    """
    messages = service.read_linked_by_conversation_id(conversation_id)
    return {
        "message": f"Found {len(messages)} messages in conversation",
        "status_code": 200,
        "messages": [m.to_dict() for m in messages]
    }


@router.get("/linked/by-sender/{from_email}")
async def get_linked_by_sender(from_email: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))):
    """
    Get all linked messages from a specific sender.
    """
    messages = service.read_linked_by_from_email(from_email)
    return {
        "message": f"Found {len(messages)} messages from {from_email}",
        "status_code": 200,
        "messages": [m.to_dict() for m in messages]
    }


@router.delete("/linked/{public_id}")
async def unlink_message(public_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_delete"))):
    """
    Unlink a message by removing it from local storage.
    Does not delete from Graph API.
    """
    result = service.unlink_message(public_id)
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


# =============================================================================
# Linked Attachment Endpoints
# =============================================================================

@router.post("/linked/{public_id}/attachments")
async def link_attachment(
    public_id: str, request: LinkAttachmentRequest, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC, "can_create"))
):
    """
    Link an attachment by downloading and optionally storing in Azure Blob.
    """
    result = service.link_message_attachment(
        message_public_id=public_id,
        graph_attachment_id=request.attachment_id,
        upload_to_blob=request.upload_to_blob
    )
    if result.get("status_code") != 201:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result


@router.get("/linked/{public_id}/attachments")
async def list_linked_attachments(
    public_id: str, current_user: dict = Depends(require_module_api(Modules.QBO_SYNC))
):
    """
    List attachments for a linked message.
    """
    result = service.read_linked_attachments(public_id)
    if result.get("status_code") != 200:
        raise HTTPException(
            status_code=result.get("status_code", 500),
            detail=result.get("message")
        )
    return result
