# Python Standard Library Imports
import base64
import logging
from typing import Any, Dict, List, Optional

# Local Imports
from integrations.ms.base.client import DEFAULT_BASE_URL, MsGraphClient
from integrations.ms.base.errors import MsGraphError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(e: MsGraphError, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    status = e.http_status or 500
    base: Dict[str, Any] = {"status_code": status, "message": str(e)}
    if extra:
        base.update(extra)
    return base


def _mailbox_path(mailbox: Optional[str] = None) -> str:
    """
    Return the Graph API mailbox path segment.

    When ``mailbox`` is None (the default) requests target the authenticated
    user's own mailbox via ``/me``. When a mailbox email address is supplied,
    requests target that account via ``/users/{mailbox}`` (provided the
    authenticated user has delegated access).
    """
    if mailbox:
        return f"users/{mailbox}"
    return "me"


def _strip_base(absolute_url: str) -> str:
    """
    Strip the Graph base URL prefix from a @odata.nextLink so the result
    can be passed as a path to MsGraphClient.get().
    """
    prefix = DEFAULT_BASE_URL + "/"
    if absolute_url.startswith(prefix):
        return absolute_url[len(prefix):]
    return absolute_url  # fallback; base URL mismatch — let Graph reject it


def _format_message(msg: dict, include_body: bool = False) -> dict:
    """Format a message from MS Graph API response."""
    from_email = msg.get("from", {}).get("emailAddress", {})

    to_recipients = [
        {
            "name": r.get("emailAddress", {}).get("name"),
            "email": r.get("emailAddress", {}).get("address"),
        }
        for r in msg.get("toRecipients", [])
    ]
    cc_recipients = [
        {
            "name": r.get("emailAddress", {}).get("name"),
            "email": r.get("emailAddress", {}).get("address"),
        }
        for r in msg.get("ccRecipients", [])
    ]

    formatted = {
        "message_id": msg.get("id"),
        "conversation_id": msg.get("conversationId"),
        "internet_message_id": msg.get("internetMessageId"),
        "subject": msg.get("subject"),
        "from_name": from_email.get("name"),
        "from_email": from_email.get("address"),
        "to_recipients": to_recipients,
        "cc_recipients": cc_recipients,
        "received_datetime": msg.get("receivedDateTime"),
        "sent_datetime": msg.get("sentDateTime"),
        "is_read": msg.get("isRead"),
        "is_draft": msg.get("isDraft"),
        "has_attachments": msg.get("hasAttachments"),
        "importance": msg.get("importance"),
        "body_preview": (msg.get("bodyPreview") or "")[:200],
        "web_link": msg.get("webLink"),
        "flag": msg.get("flag"),
        "categories": msg.get("categories", []),
    }

    if include_body:
        body = msg.get("body", {})
        formatted["body_content"] = body.get("content")
        formatted["body_content_type"] = body.get("contentType")

    if "attachments" in msg:
        formatted["attachments"] = [
            {
                "id": att.get("id"),
                "name": att.get("name"),
                "content_type": att.get("contentType"),
                "size": att.get("size"),
                "is_inline": att.get("isInline", False),
            }
            for att in msg.get("attachments", [])
        ]

    return formatted


def _build_recipient_list(recipients: List[dict]) -> List[dict]:
    """Build recipient list in Graph API emailAddress format."""
    return [
        {
            "emailAddress": {
                "address": r.get("email"),
                "name": r.get("name", r.get("email")),
            }
        }
        for r in recipients
    ]


# ---------------------------------------------------------------------------
# Mail Folder Operations
# ---------------------------------------------------------------------------


def list_mail_folders(mailbox: Optional[str] = None) -> dict:
    """List all mail folders for the current user (or a delegated mailbox)."""
    try:
        with MsGraphClient() as client:
            data = client.get(
                f"{_mailbox_path(mailbox)}/mailFolders",
                operation_name="mail.list_folders",
            )
        folders = data.get("value", [])
        formatted = [
            {
                "folder_id": f.get("id"),
                "display_name": f.get("displayName"),
                "parent_folder_id": f.get("parentFolderId"),
                "child_folder_count": f.get("childFolderCount", 0),
                "unread_item_count": f.get("unreadItemCount", 0),
                "total_item_count": f.get("totalItemCount", 0),
            }
            for f in folders
        ]
        return {
            "message": f"Found {len(formatted)} mail folders",
            "status_code": 200,
            "folders": formatted,
        }
    except MsGraphError as e:
        logger.error(f"Error listing mail folders: {e}")
        return _error_response(e, extra={"folders": []})


def get_mail_folder(folder_id: str, mailbox: Optional[str] = None) -> dict:
    """Get a specific mail folder by ID."""
    try:
        with MsGraphClient() as client:
            folder = client.get(
                f"{_mailbox_path(mailbox)}/mailFolders/{folder_id}",
                operation_name="mail.get_folder",
            )
        return {
            "message": "Folder retrieved successfully",
            "status_code": 200,
            "folder": {
                "folder_id": folder.get("id"),
                "display_name": folder.get("displayName"),
                "parent_folder_id": folder.get("parentFolderId"),
                "child_folder_count": folder.get("childFolderCount", 0),
                "unread_item_count": folder.get("unreadItemCount", 0),
                "total_item_count": folder.get("totalItemCount", 0),
            },
        }
    except MsGraphError as e:
        logger.error(f"Error getting mail folder {folder_id}: {e}")
        return _error_response(e, extra={"folder": None})


# ---------------------------------------------------------------------------
# Message List & Read Operations
# ---------------------------------------------------------------------------


_MESSAGE_SELECT_FIELDS = (
    "id,conversationId,internetMessageId,subject,from,toRecipients,"
    "ccRecipients,receivedDateTime,sentDateTime,isRead,isDraft,"
    "hasAttachments,importance,bodyPreview,webLink,flag,categories"
)


def list_messages(
    folder: str = "inbox",
    top: int = 25,
    skip: int = 0,
    filter_query: Optional[str] = None,
    search: Optional[str] = None,
    order_by: str = "receivedDateTime desc",
    mailbox: Optional[str] = None,
) -> dict:
    """
    List messages from a mail folder with automatic pagination.
    MS Graph caps at 50 per request; we page through to fulfill `top`.
    """
    try:
        page_size = 50
        params: Dict[str, Any] = {
            "$top": page_size,
            "$select": _MESSAGE_SELECT_FIELDS,
            "$orderby": order_by,
            "$count": "true",
        }
        if search:
            # $skip is not supported with $search
            params["$search"] = f'"{search}"'
        else:
            params["$skip"] = skip
        if filter_query:
            params["$filter"] = filter_query

        endpoint_path = f"{_mailbox_path(mailbox)}/mailFolders/{folder}/messages"
        max_pages = (top // page_size) + 2
        all_messages: List[dict] = []
        total_count = 0
        pages_fetched = 0
        next_link: Optional[str] = None
        logger.info(f"Listing messages from folder: {folder}, top: {top} (will page)")

        with MsGraphClient() as client:
            while pages_fetched < max_pages and len(all_messages) < top:
                if next_link:
                    data = client.get(
                        _strip_base(next_link),
                        extra_headers={"ConsistencyLevel": "eventual"},
                        operation_name="mail.list_messages.next",
                    )
                else:
                    data = client.get(
                        endpoint_path,
                        params=params,
                        extra_headers={"ConsistencyLevel": "eventual"},
                        operation_name="mail.list_messages",
                    )

                pages_fetched += 1
                messages = data.get("value", [])
                total_count = data.get("@odata.count", total_count or len(messages))
                next_link = data.get("@odata.nextLink")
                all_messages.extend(messages)
                logger.info(
                    f"Page {pages_fetched}: fetched {len(messages)} messages "
                    f"(total: {len(all_messages)})"
                )
                if not next_link:
                    break

        all_messages = all_messages[:top]
        formatted = [_format_message(m) for m in all_messages]
        return {
            "message": f"Retrieved {len(formatted)} messages",
            "status_code": 200,
            "messages": formatted,
            "total_count": total_count,
            "has_more": next_link is not None or len(all_messages) >= top,
        }
    except MsGraphError as e:
        logger.error(f"Error listing messages in folder {folder}: {e}")
        return _error_response(e, extra={"messages": [], "total_count": 0})


def search_all_messages(
    conversation_id: str,
    top: int = 50,
    mailbox: Optional[str] = None,
) -> dict:
    """
    Find messages by conversation ID. Two-phase: direct $filter first, then
    page-and-client-filter fallback if the filter fails or returns empty.
    """
    endpoint_path = f"{_mailbox_path(mailbox)}/messages"
    select_fields = (
        "id,conversationId,internetMessageId,subject,body,from,toRecipients,"
        "ccRecipients,receivedDateTime,sentDateTime,isRead,isDraft,"
        "hasAttachments,importance,bodyPreview,webLink,flag,categories"
    )

    # Phase 1: direct $filter
    try:
        with MsGraphClient() as client:
            data = client.get(
                endpoint_path,
                params={
                    "$top": top,
                    "$filter": f"conversationId eq '{conversation_id}'",
                    "$select": select_fields,
                    "$expand": "attachments($select=id,name,contentType,size,isInline)",
                },
                operation_name="mail.search_by_conversation.filter",
            )
        matching = data.get("value", [])
        if matching:
            logger.info(f"Direct filter found {len(matching)} messages")
            matching.sort(key=lambda m: m.get("receivedDateTime", ""))
            formatted = [_format_message(m, include_body=True) for m in matching[:top]]
            return {
                "message": f"Found {len(formatted)} messages in conversation",
                "status_code": 200,
                "messages": formatted,
            }
        logger.info("Direct filter returned no results, trying paging approach")
    except MsGraphError as e:
        logger.info(f"Direct filter failed ({e.http_status}), trying paging: {e}")

    # Phase 2: paging with client-side filter
    try:
        matching: List[dict] = []
        pages_searched = 0
        max_pages = 4
        next_link: Optional[str] = None
        params = {
            "$top": 250,
            "$orderby": "receivedDateTime desc",
            "$select": select_fields,
            "$expand": "attachments($select=id,name,contentType,size,isInline)",
        }

        with MsGraphClient() as client:
            while pages_searched < max_pages:
                if next_link:
                    data = client.get(
                        _strip_base(next_link),
                        operation_name="mail.search_by_conversation.page",
                    )
                else:
                    data = client.get(
                        endpoint_path,
                        params=params,
                        operation_name="mail.search_by_conversation.page",
                    )
                pages_searched += 1
                page_messages = data.get("value", [])
                page_matches = [m for m in page_messages if m.get("conversationId") == conversation_id]
                matching.extend(page_matches)
                logger.info(
                    f"Page {pages_searched}: {len(page_matches)} matches "
                    f"(total: {len(matching)}, searched: {len(page_messages)})"
                )
                next_link = data.get("@odata.nextLink")
                if not next_link:
                    break
                if matching and len(page_matches) == 0:
                    break
                if len(matching) >= top:
                    break

        matching.sort(key=lambda m: m.get("receivedDateTime", ""))
        formatted = [_format_message(m, include_body=True) for m in matching[:top]]
        logger.info(
            f"Found {len(formatted)} messages in conversation "
            f"(searched {pages_searched} pages)"
        )
        return {
            "message": f"Found {len(formatted)} messages in conversation",
            "status_code": 200,
            "messages": formatted,
        }
    except MsGraphError as e:
        logger.error(f"Error searching messages by conversation: {e}")
        return _error_response(e, extra={"messages": []})


def get_message(
    message_id: str,
    include_body: bool = True,
    mailbox: Optional[str] = None,
) -> dict:
    """Get a specific message by ID with full details."""
    select_fields = (
        "id,conversationId,internetMessageId,subject,from,toRecipients,"
        "ccRecipients,bccRecipients,receivedDateTime,sentDateTime,isRead,"
        "isDraft,hasAttachments,importance,bodyPreview,webLink,flag"
    )
    if include_body:
        select_fields += ",body"

    try:
        with MsGraphClient() as client:
            msg = client.get(
                f"{_mailbox_path(mailbox)}/messages/{message_id}",
                params={"$select": select_fields, "$expand": "attachments"},
                operation_name="mail.get_message",
            )
        formatted = _format_message(msg, include_body=include_body)

        bcc_recipients = [
            {
                "name": r.get("emailAddress", {}).get("name"),
                "email": r.get("emailAddress", {}).get("address"),
            }
            for r in msg.get("bccRecipients", [])
        ]
        formatted["bcc_recipients"] = bcc_recipients
        formatted["attachments"] = [
            {
                "id": att.get("id"),
                "name": att.get("name"),
                "content_type": att.get("contentType"),
                "size": att.get("size"),
                "is_inline": att.get("isInline", False),
            }
            for att in msg.get("attachments", [])
        ]
        return {
            "message": "Message retrieved successfully",
            "status_code": 200,
            "email": formatted,
        }
    except MsGraphError as e:
        logger.error(f"Error getting message {message_id}: {e}")
        return _error_response(e, extra={"email": None})


def mark_message_read(
    message_id: str,
    is_read: bool = True,
    mailbox: Optional[str] = None,
) -> dict:
    """Mark a message as read or unread."""
    try:
        with MsGraphClient() as client:
            client.patch(
                f"{_mailbox_path(mailbox)}/messages/{message_id}",
                json={"isRead": is_read},
                operation_name="mail.mark_read",
            )
        return {
            "message": f"Message marked as {'read' if is_read else 'unread'}",
            "status_code": 200,
        }
    except MsGraphError as e:
        logger.error(f"Error marking message {message_id} read={is_read}: {e}")
        return _error_response(e)


def flag_message(
    message_id: str,
    flagged: bool = True,
    mailbox: Optional[str] = None,
) -> dict:
    """Flag or unflag a message."""
    try:
        with MsGraphClient() as client:
            client.patch(
                f"{_mailbox_path(mailbox)}/messages/{message_id}",
                json={"flag": {"flagStatus": "flagged" if flagged else "notFlagged"}},
                operation_name="mail.flag",
            )
        return {
            "message": f"Message {'flagged' if flagged else 'unflagged'}",
            "status_code": 200,
        }
    except MsGraphError as e:
        logger.error(f"Error flagging message {message_id}: {e}")
        return _error_response(e)


def unflag_message(message_id: str) -> dict:
    """Remove the flag from a message. Convenience wrapper."""
    return flag_message(message_id, flagged=False)


def set_categories(
    message_id: str,
    categories: List[str],
    mailbox: Optional[str] = None,
) -> dict:
    """Set the categories (color labels) on a message. Replaces existing."""
    try:
        with MsGraphClient() as client:
            client.patch(
                f"{_mailbox_path(mailbox)}/messages/{message_id}",
                json={"categories": categories},
                operation_name="mail.set_categories",
            )
        return {"message": "Categories updated", "status_code": 200}
    except MsGraphError as e:
        logger.error(f"Error setting categories on message {message_id}: {e}")
        return _error_response(e)


# ---------------------------------------------------------------------------
# Attachment Operations
# ---------------------------------------------------------------------------


def list_message_attachments(
    message_id: str,
    mailbox: Optional[str] = None,
) -> dict:
    """List all attachments for a message."""
    try:
        with MsGraphClient() as client:
            data = client.get(
                f"{_mailbox_path(mailbox)}/messages/{message_id}/attachments",
                operation_name="mail.list_attachments",
            )
        formatted = [
            {
                "attachment_id": att.get("id"),
                "name": att.get("name"),
                "content_type": att.get("contentType"),
                "size": att.get("size"),
                "is_inline": att.get("isInline", False),
                "attachment_type": att.get("@odata.type", "").replace(
                    "#microsoft.graph.", ""
                ),
            }
            for att in data.get("value", [])
        ]
        return {
            "message": f"Found {len(formatted)} attachments",
            "status_code": 200,
            "attachments": formatted,
        }
    except MsGraphError as e:
        logger.error(f"Error listing attachments for {message_id}: {e}")
        return _error_response(e, extra={"attachments": []})


def download_attachment(
    message_id: str,
    attachment_id: str,
    mailbox: Optional[str] = None,
) -> dict:
    """
    Download an attachment's content. Graph returns the attachment JSON
    with a base64-encoded `contentBytes` field; we decode it to bytes.
    """
    try:
        with MsGraphClient() as client:
            attachment = client.get(
                f"{_mailbox_path(mailbox)}/messages/{message_id}/attachments/{attachment_id}",
                timeout_tier="C",
                operation_name="mail.download_attachment",
            )
        content_bytes: Optional[bytes] = None
        if "contentBytes" in attachment:
            content_bytes = base64.b64decode(attachment.get("contentBytes"))
        return {
            "message": "Attachment downloaded successfully",
            "status_code": 200,
            "content": content_bytes,
            "content_type": attachment.get("contentType", "application/octet-stream"),
            "filename": attachment.get("name"),
            "size": attachment.get("size"),
        }
    except MsGraphError as e:
        logger.error(f"Error downloading attachment {attachment_id}: {e}")
        return _error_response(
            e,
            extra={"content": None, "content_type": None, "filename": None},
        )


# ---------------------------------------------------------------------------
# Send & Draft Operations
# ---------------------------------------------------------------------------


def send_message(
    to_recipients: List[dict],
    subject: str,
    body: str,
    body_type: str = "HTML",
    cc_recipients: Optional[List[dict]] = None,
    bcc_recipients: Optional[List[dict]] = None,
    attachments: Optional[List[dict]] = None,
    importance: str = "normal",
    save_to_sent_items: bool = True,
) -> dict:
    """Send a new email message via /me/sendMail (202 on success)."""
    message: Dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": body_type, "content": body},
        "toRecipients": _build_recipient_list(to_recipients),
        "importance": importance,
    }
    if cc_recipients:
        message["ccRecipients"] = _build_recipient_list(cc_recipients)
    if bcc_recipients:
        message["bccRecipients"] = _build_recipient_list(bcc_recipients)
    if attachments:
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": att.get("name"),
                "contentType": att.get("content_type", "application/octet-stream"),
                "contentBytes": att.get("content_bytes"),
            }
            for att in attachments
        ]
    payload = {"message": message, "saveToSentItems": save_to_sent_items}

    try:
        with MsGraphClient() as client:
            client.post(
                "me/sendMail",
                json=payload,
                timeout_tier="B",  # sendMail can be slow with many/large attachments
                operation_name="mail.send",
            )
        logger.info(
            f"Sent email to {len(to_recipients)} recipients, subject: {subject[:50]}"
        )
        return {"message": "Email sent successfully", "status_code": 202}
    except MsGraphError as e:
        logger.error(f"Error sending email (subject: {subject[:50]}): {e}")
        return _error_response(e)


def create_draft(
    to_recipients: Optional[List[dict]] = None,
    subject: str = "",
    body: str = "",
    body_type: str = "HTML",
    cc_recipients: Optional[List[dict]] = None,
    bcc_recipients: Optional[List[dict]] = None,
    attachments: Optional[List[dict]] = None,
    importance: str = "normal",
) -> dict:
    """Create a draft message in the Drafts folder."""
    message: Dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": body_type, "content": body},
        "importance": importance,
    }
    if to_recipients:
        message["toRecipients"] = _build_recipient_list(to_recipients)
    if cc_recipients:
        message["ccRecipients"] = _build_recipient_list(cc_recipients)
    if bcc_recipients:
        message["bccRecipients"] = _build_recipient_list(bcc_recipients)

    try:
        with MsGraphClient() as client:
            draft = client.post(
                "me/messages",
                json=message,
                operation_name="mail.create_draft",
            )
        draft_id = draft.get("id")
        if attachments and draft_id:
            for att in attachments:
                add_result = add_attachment_to_message(
                    message_id=draft_id,
                    name=att.get("name"),
                    content_type=att.get("content_type", "application/octet-stream"),
                    content_bytes=att.get("content_bytes"),
                )
                if add_result.get("status_code") != 201:
                    logger.warning(
                        f"Failed to add attachment: {add_result.get('message')}"
                    )
        return {
            "message": "Draft created successfully",
            "status_code": 201,
            "draft": _format_message(draft, include_body=True),
        }
    except MsGraphError as e:
        logger.error(f"Error creating draft (subject: {subject[:50]}): {e}")
        return _error_response(e, extra={"draft": None})


def update_draft(
    message_id: str,
    to_recipients: Optional[List[dict]] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    body_type: str = "HTML",
    cc_recipients: Optional[List[dict]] = None,
    bcc_recipients: Optional[List[dict]] = None,
    importance: Optional[str] = None,
) -> dict:
    """Update an existing draft message."""
    message: Dict[str, Any] = {}
    if subject is not None:
        message["subject"] = subject
    if body is not None:
        message["body"] = {"contentType": body_type, "content": body}
    if to_recipients is not None:
        message["toRecipients"] = _build_recipient_list(to_recipients)
    if cc_recipients is not None:
        message["ccRecipients"] = _build_recipient_list(cc_recipients)
    if bcc_recipients is not None:
        message["bccRecipients"] = _build_recipient_list(bcc_recipients)
    if importance is not None:
        message["importance"] = importance

    try:
        with MsGraphClient() as client:
            draft = client.patch(
                f"me/messages/{message_id}",
                json=message,
                operation_name="mail.update_draft",
            )
        return {
            "message": "Draft updated successfully",
            "status_code": 200,
            "draft": _format_message(draft, include_body=True),
        }
    except MsGraphError as e:
        logger.error(f"Error updating draft {message_id}: {e}")
        return _error_response(e, extra={"draft": None})


def send_draft(message_id: str) -> dict:
    """Send an existing draft message."""
    try:
        with MsGraphClient() as client:
            client.post(
                f"me/messages/{message_id}/send",
                timeout_tier="B",
                operation_name="mail.send_draft",
            )
        return {"message": "Draft sent successfully", "status_code": 202}
    except MsGraphError as e:
        logger.error(f"Error sending draft {message_id}: {e}")
        return _error_response(e)


def add_attachment_to_message(
    message_id: str,
    name: str,
    content_type: str,
    content_bytes: str,
) -> dict:
    """Add a file attachment to a draft message (content_bytes is base64)."""
    payload = {
        "@odata.type": "#microsoft.graph.fileAttachment",
        "name": name,
        "contentType": content_type,
        "contentBytes": content_bytes,
    }
    try:
        with MsGraphClient() as client:
            attachment = client.post(
                f"me/messages/{message_id}/attachments",
                json=payload,
                timeout_tier="C",
                operation_name="mail.add_attachment",
            )
        return {
            "message": "Attachment added successfully",
            "status_code": 201,
            "attachment": {
                "attachment_id": attachment.get("id"),
                "name": attachment.get("name"),
                "content_type": attachment.get("contentType"),
                "size": attachment.get("size"),
            },
        }
    except MsGraphError as e:
        logger.error(f"Error adding attachment '{name}' to {message_id}: {e}")
        return _error_response(e, extra={"attachment": None})


# ---------------------------------------------------------------------------
# Reply & Forward Operations
# ---------------------------------------------------------------------------


def reply_to_message(
    message_id: str,
    body: str,
    body_type: str = "HTML",  # retained for signature parity
    reply_all: bool = False,
) -> dict:
    """Reply (or reply-all) to a message. The simple reply endpoint uses the
    'comment' field for the reply body; for richer HTML bodies use
    `create_reply_draft` + edit + send."""
    del body_type  # the simple reply endpoint ignores body contentType
    action = "replyAll" if reply_all else "reply"
    try:
        with MsGraphClient() as client:
            client.post(
                f"me/messages/{message_id}/{action}",
                json={"comment": body},
                timeout_tier="B",
                operation_name=f"mail.{action}",
            )
        return {
            "message": f"{'Reply all' if reply_all else 'Reply'} sent successfully",
            "status_code": 202,
        }
    except MsGraphError as e:
        logger.error(f"Error replying to message {message_id}: {e}")
        return _error_response(e)


def create_reply_draft(message_id: str, reply_all: bool = False) -> dict:
    """Create a reply draft for a message (for more control before sending)."""
    action = "createReplyAll" if reply_all else "createReply"
    try:
        with MsGraphClient() as client:
            draft = client.post(
                f"me/messages/{message_id}/{action}",
                operation_name=f"mail.{action}",
            )
        return {
            "message": f"{'Reply all' if reply_all else 'Reply'} draft created successfully",
            "status_code": 201,
            "draft": _format_message(draft, include_body=True),
        }
    except MsGraphError as e:
        logger.error(f"Error creating reply draft for {message_id}: {e}")
        return _error_response(e, extra={"draft": None})


def forward_message(
    message_id: str,
    to_recipients: List[dict],
    comment: Optional[str] = None,
) -> dict:
    """Forward a message to recipients."""
    payload: Dict[str, Any] = {"toRecipients": _build_recipient_list(to_recipients)}
    if comment:
        payload["comment"] = comment
    try:
        with MsGraphClient() as client:
            client.post(
                f"me/messages/{message_id}/forward",
                json=payload,
                timeout_tier="B",
                operation_name="mail.forward",
            )
        return {"message": "Message forwarded successfully", "status_code": 202}
    except MsGraphError as e:
        logger.error(f"Error forwarding message {message_id}: {e}")
        return _error_response(e)


def create_forward_draft(message_id: str) -> dict:
    """Create a forward draft for a message (for more control before sending)."""
    try:
        with MsGraphClient() as client:
            draft = client.post(
                f"me/messages/{message_id}/createForward",
                operation_name="mail.create_forward_draft",
            )
        return {
            "message": "Forward draft created successfully",
            "status_code": 201,
            "draft": _format_message(draft, include_body=True),
        }
    except MsGraphError as e:
        logger.error(f"Error creating forward draft for {message_id}: {e}")
        return _error_response(e, extra={"draft": None})


# ---------------------------------------------------------------------------
# Message Management Operations
# ---------------------------------------------------------------------------


def move_message(message_id: str, destination_folder_id: str) -> dict:
    """Move a message to a different folder."""
    try:
        with MsGraphClient() as client:
            msg = client.post(
                f"me/messages/{message_id}/move",
                json={"destinationId": destination_folder_id},
                operation_name="mail.move",
            )
        return {
            "message": "Message moved successfully",
            "status_code": 201,
            "email": _format_message(msg),
        }
    except MsGraphError as e:
        logger.error(f"Error moving message {message_id}: {e}")
        return _error_response(e, extra={"email": None})


def delete_message(message_id: str) -> dict:
    """Delete a message (moves to Deleted Items)."""
    try:
        with MsGraphClient() as client:
            client.delete(
                f"me/messages/{message_id}",
                operation_name="mail.delete",
            )
        return {"message": "Message deleted successfully", "status_code": 204}
    except MsGraphError as e:
        logger.error(f"Error deleting message {message_id}: {e}")
        return _error_response(e)
