# Python Standard Library Imports
import base64
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

# Local Imports
from integrations.ms.base.client import DEFAULT_BASE_URL, MsGraphClient, writes_allowed
from integrations.ms.base.errors import MsGraphError, MsWriteRefusedError, build_error_envelope

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(e: MsGraphError, *, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Delegates to the shared envelope builder — see build_error_envelope's docstring."""
    return build_error_envelope(e, extra=extra)


def _mailbox_path(mailbox: Optional[str] = None) -> str:
    """
    Return the Graph API mailbox path segment.

    When ``mailbox`` is None (the default) requests target the authenticated
    user's own mailbox via ``/me``. When a mailbox email address is supplied,
    requests target that account via ``/users/{mailbox}`` (provided the
    authenticated user has delegated access).

    U-799: the address is URL-encoded as ONE path segment (`@` kept), so a character such as
    `/`, `?`, `#` or `'` can never split or redirect the request path. A plain address is
    unchanged (`invoice@rogersbuild.com` -> `users/invoice@rogersbuild.com`).
    """
    if mailbox:
        return f"users/{quote(mailbox, safe='@')}"
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


def _format_recipients(recipients: Any) -> List[dict]:
    """Map Graph to/cc/bcc recipient objects to {name, email} dicts."""
    return [
        {
            "name": r.get("emailAddress", {}).get("name"),
            "email": r.get("emailAddress", {}).get("address"),
        }
        for r in recipients
    ]


def _format_message(msg: dict, include_body: bool = False) -> dict:
    """Format a message from MS Graph API response."""
    from_email = msg.get("from", {}).get("emailAddress", {})

    to_recipients = _format_recipients(msg.get("toRecipients", []))
    cc_recipients = _format_recipients(msg.get("ccRecipients", []))

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
_ATTACHMENTS_EXPAND = "attachments($select=id,name,contentType,size,isInline)"

# U-799: with `$orderby`, Graph requires the ordered property to lead `$filter` (else 400
# "restriction or sort order is too complex"). An always-true `receivedDateTime ge <floor>`
# clause satisfies that without changing the result set; it matches the floor the
# email_message callers already hand-code.
_RECEIVED_FLOOR = "2000-01-01T00:00:00Z"
_LEADS_WITH_RECEIVED = re.compile(r"(\s*\()*\s*receivedDateTime\b", re.IGNORECASE)


def _received_order(order_by: Optional[str]) -> Optional[bool]:
    """None when the FIRST `$orderby` clause is not on receivedDateTime; else True if descending
    (`"receivedDateTime desc, subject asc"` -> True)."""
    parts = (order_by or "").split(",")[0].split()
    if not parts or parts[0].lower() != "receiveddatetime":
        return None
    return len(parts) > 1 and parts[1].lower() == "desc"


def _filter_compatible_with_order(filter_query: str, order_by: Optional[str]) -> str:
    """Prepend the always-true received clause when sorting on receivedDateTime and the
    filter does not already lead with it. Other sorts are not shape-corrected."""
    if (not filter_query.strip() or _received_order(order_by) is None
            or _LEADS_WITH_RECEIVED.match(filter_query)):
        return filter_query
    return f"receivedDateTime ge {_RECEIVED_FLOOR} and ({filter_query.strip()})"


def _normalize_search_term(search: str) -> str:
    """Wrap a `$search` term in exactly one pair of double quotes (a pre-quoted term used to
    become `""term""`, which Graph rejects); inner double quotes are dropped."""
    term = search.strip()
    while len(term) >= 2 and term[0] == '"' and term[-1] == '"':
        term = term[1:-1].strip()
    return '"' + term.replace('"', "") + '"'


def _sort_by_received(messages: List[dict], order_by: Optional[str]) -> List[dict]:
    """Client-side stand-in for `$orderby` on the `$search` path, where Graph cannot sort."""
    descending = _received_order(order_by)
    if descending is None:
        return messages
    return sorted(messages, key=lambda m: m.get("receivedDateTime") or "", reverse=descending)


def _bad_request(message: str, **extra: Any) -> Dict[str, Any]:
    """A locally-refused call, shaped like `_error_response` so callers check one field."""
    return {"message": message, "status_code": 400, **extra}


# A Graph message/attachment id is base64-ish (`AAMkAG…-_=`). Anything carrying a path, query
# or fragment character is refused before it can be interpolated into a request path.
_GRAPH_ID_RE = re.compile(r"[A-Za-z0-9_\-=+]+")
# One `@`, no whitespace/control characters and none of `/ \ ? #` — anything RFC-legal beyond that
# (apostrophes, `!`, `&` …) is accepted; `_mailbox_path` URL-encodes it as a single segment.
_MAILBOX_RE = re.compile(r"[^\s@/\\?#\x00-\x1f]+@[^\s@/\\?#\x00-\x1f]+")


def _is_graph_id(value: Any) -> bool:
    return isinstance(value, str) and _GRAPH_ID_RE.fullmatch(value) is not None


def _is_mailbox(value: Optional[str]) -> bool:
    """None (= `/me`) or a single address; never a path fragment like `x@y/../`."""
    return value is None or (isinstance(value, str) and _MAILBOX_RE.fullmatch(value) is not None)


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

    ⚠️ On a Graph error this returns the error envelope (`status_code` != 200) with
    `messages: []` — check `status_code`; an empty list alone does not mean "no mail".

    U-799 query-shape rules (each used to be a Graph 400 that read as "no mail"):
    `search` sends no `$orderby`/`$skip`; it returns Graph's top-`top` by RELEVANCE, re-sorted
    by `receivedDateTime` — use a `receivedDateTime` window for "newest". `search` with
    `filter_query` or a nonzero `skip` is refused locally (Graph rejects the first; it cannot
    offset a search). A filter not leading with `receivedDateTime` gets an always-true
    `receivedDateTime ge 2000-01-01` clause prepended (mail older than 2000 is not matched).
    """
    if search and filter_query:
        return _bad_request(
            "search and filter_query cannot be combined (Graph rejects $filter with $search)",
            messages=[], total_count=0,
        )
    if search and skip:
        return _bad_request(
            "skip is not supported with search (Graph cannot offset a $search)",
            messages=[], total_count=0,
        )
    try:
        page_size = 50
        params: Dict[str, Any] = {
            "$top": page_size,
            "$select": _MESSAGE_SELECT_FIELDS,
            "$count": "true",
        }
        if search:
            params["$search"] = _normalize_search_term(search)
        else:
            params["$orderby"] = order_by
            params["$skip"] = skip
            if filter_query:
                params["$filter"] = _filter_compatible_with_order(filter_query, order_by)

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

        more_than_top = next_link is not None or len(all_messages) > top
        all_messages = all_messages[:top]
        if search:  # re-sort Graph's top-`top` relevance hits; never pull in lower-ranked ones
            all_messages = _sort_by_received(all_messages, order_by)
        formatted = [_format_message(m) for m in all_messages]
        return {
            "message": f"Retrieved {len(formatted)} messages",
            "status_code": 200,
            "messages": formatted,
            "total_count": total_count,
            # A search cannot be offset (skip is refused), so never advertise a next page for it;
            # `search_truncated` says more matches existed beyond Graph's top-`top`.
            "has_more": False if search else (next_link is not None or len(all_messages) >= top),
            **({"search_truncated": more_than_top} if search else {}),
        }
    except MsGraphError as e:
        logger.error(
            f"Error listing messages in folder {folder} "
            f"(filter={params.get('$filter')!r} search={params.get('$search')!r}): {e}"
        )
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
                    "$expand": _ATTACHMENTS_EXPAND,
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
            "$expand": _ATTACHMENTS_EXPAND,
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
                params={
                    "$select": select_fields,
                    "$expand": _ATTACHMENTS_EXPAND,
                },
                operation_name="mail.get_message",
            )
        if not isinstance(msg, dict):
            logger.error(
                "Unexpected Graph get_message response shape for %s: %r",
                message_id,
                type(msg).__name__,
            )
            return {
                "status_code": 503,
                "message": "Unexpected Graph message response shape",
                "is_retryable": True,
                "email": None,
            }
        try:
            formatted = _format_message(msg, include_body=include_body)
            # Exposed on GET /mail/messages/{id} — not used in-repo after If-Match removal.
            formatted["odata_etag"] = msg.get("@odata.etag")
            formatted["bcc_recipients"] = _format_recipients(
                msg.get("bccRecipients", [])
            )
            formatted.setdefault("attachments", [])
        except (AttributeError, TypeError) as format_error:
            logger.error(
                "Failed to format Graph message %s: %s",
                message_id,
                format_error,
            )
            return {
                "status_code": 503,
                "message": f"Unexpected Graph message response shape: {format_error}",
                "is_retryable": True,
                "email": None,
            }
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
        if not isinstance(draft, dict):
            logger.error(
                "Unexpected Graph update_draft response shape for %s: %r",
                message_id,
                type(draft).__name__,
            )
            return {
                "status_code": 503,
                "message": "Unexpected Graph update_draft response shape",
                "is_retryable": True,
                "draft": None,
            }
        try:
            formatted_draft = _format_message(draft, include_body=True)
        except (AttributeError, TypeError) as format_error:
            logger.error(
                "Failed to format Graph update_draft response %s: %s",
                message_id,
                format_error,
            )
            return {
                "status_code": 503,
                "message": f"Unexpected Graph update_draft response shape: {format_error}",
                "is_retryable": True,
                "draft": None,
            }
        return {
            "message": "Draft updated successfully",
            "status_code": 200,
            "draft": formatted_draft,
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


def delete_attachment_from_message(
    message_id: str,
    attachment_id: str,
    mailbox: Optional[str] = None,
) -> dict:
    """
    Remove one attachment from a DRAFT message (U-799), e.g. to swap a wrong PDF on a
    `[Review]` draft without recreating it.

    ⛔ Refuses (409, nothing deleted) unless Graph reports `isDraft`: on a RECEIVED message
    this would destroy the vendor's original PDF. Write-gated (ALLOW_MS_WRITES), checked
    before any network call.

    ⛔ Both ids must be plain Graph ids (400 otherwise). The draft check reads
    `messages/{message_id}` but the DELETE targets `.../attachments/{attachment_id}`; an id
    carrying `../`, `/`, `?` or `#` would be normalised by the HTTP layer onto a DIFFERENT
    message (e.g. a received one) after the draft check passed.
    """
    if not (_is_graph_id(message_id) and _is_graph_id(attachment_id)) or not _is_mailbox(mailbox):
        return _bad_request("message_id, attachment_id and mailbox must be plain identifiers")
    base = _mailbox_path(mailbox)
    path = f"{base}/messages/{message_id}/attachments/{attachment_id}"
    try:
        if not writes_allowed():  # same refusal the client's DELETE would raise, minus the draft read
            logger.warning(
                "ms.http.request.blocked operation_name=mail.delete_attachment method=DELETE path=%s "
                "outcome=write_refused reason=ALLOW_MS_WRITES_not_true", path,
            )
            raise MsWriteRefusedError(
                f"MS write refused: ALLOW_MS_WRITES is not 'true' for DELETE {path}",
                request_method="DELETE", request_path=path,
            )
        with MsGraphClient() as client:
            msg = client.get(
                f"{base}/messages/{message_id}",
                params={"$select": "id,isDraft"},
                operation_name="mail.delete_attachment.check_draft",
            )
            if msg.get("isDraft") is not True:
                logger.warning(
                    "mail.delete_attachment.refused_not_draft message_id=%s attachment_id=%s",
                    message_id, attachment_id,
                )
                return {
                    "message": "Refused: attachments can only be removed from a draft message",
                    "status_code": 409,
                }
            client.delete(path, operation_name="mail.delete_attachment")
        return {"message": "Attachment deleted successfully", "status_code": 204}
    except MsGraphError as e:
        logger.error(f"Error deleting attachment {attachment_id} from {message_id}: {e}")
        return _error_response(e)


# Only what matching and identifying a hit need; Graph honours 250/page with $expand (verified).
_ATTACHMENT_SCAN_SELECT = (
    "id,conversationId,subject,from,receivedDateTime,isRead,isDraft,hasAttachments,webLink,parentFolderId"
)
_ATTACHMENT_SCAN_PAGE_SIZE = 250
_ATTACHMENT_SCAN_MAX_PAGES = 12  # 12 x 250 = 3,000 messages per call; beyond that `truncated` is True
# ISO-8601 UTC instant (`2026-10-12T05:00:00Z`, optional fraction); anything else is refused.
_UTC_INSTANT_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d{1,7})?Z")


def _is_utc_instant(value: Any) -> bool:
    """Exact shape (fullmatch — no trailing newline) AND a real calendar instant (no Feb 30)."""
    m = _UTC_INSTANT_RE.fullmatch(value) if isinstance(value, str) else None
    if not m:
        return False
    try:
        datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return False
    return True


def _scan_haystack(m: dict) -> str:
    sender = (m.get("from") or {}).get("emailAddress") or {}
    names = [a.get("name") or "" for a in m.get("attachments") or []]
    return " ".join([m.get("subject") or "", sender.get("address") or "", sender.get("name") or ""] + names).lower()


def _folder_names(client: MsGraphClient, base: str) -> Dict[str, str]:
    """Top-level folder id -> display name; best-effort ({} on failure)."""
    try:
        data = client.get(f"{base}/mailFolders", params={"$select": "id,displayName", "$top": 100},
                          operation_name="mail.find_by_attachment_name.folders")
        return {f.get("id"): f.get("displayName") for f in data.get("value", [])}
    except MsGraphError as e:
        logger.warning(f"Folder-name lookup failed; returning matches without folder_name: {e}")
        return {}


def find_messages_by_attachment_name(
    term: str,
    received_from: str,
    received_to: str,
    mailbox: Optional[str] = None,
    max_pages: int = _ATTACHMENT_SCAN_MAX_PAGES,
) -> dict:
    """
    Find messages in EVERY folder whose attachment names, subject or sender contain `term`
    (case-insensitive), received in `[received_from, received_to)` — ISO-8601 UTC instants (U-799).
    Outlook's search matches attachment filenames; `$search` and subject checks did not.

    Returns the envelope plus `scanned` and `truncated` (page cap hit: the window was NOT
    fully searched — narrow it). Each hit carries `folder_name` (None = a subfolder), since
    drafts and deleted copies are scanned too. Check `status_code` before trusting `messages`.
    """
    empty = {"messages": [], "scanned": 0, "truncated": False}
    needle = (term or "").strip().lower()
    if not needle:
        return _bad_request("term is required", **empty)
    for label, value in (("received_from", received_from), ("received_to", received_to)):
        if not _is_utc_instant(value):
            return _bad_request(f"{label} must be an ISO-8601 UTC instant like 2026-10-12T05:00:00Z", **empty)
    if not isinstance(max_pages, int) or max_pages < 1:
        return _bad_request("max_pages must be a positive integer", **empty)
    if not _is_mailbox(mailbox):
        return _bad_request("mailbox must be a plain address", **empty)
    base = _mailbox_path(mailbox)
    params: Dict[str, Any] = {
        "$top": _ATTACHMENT_SCAN_PAGE_SIZE,
        "$select": _ATTACHMENT_SCAN_SELECT,
        "$orderby": "receivedDateTime desc",
        "$filter": f"receivedDateTime ge {received_from} and receivedDateTime lt {received_to}",
        "$expand": _ATTACHMENTS_EXPAND,
    }
    matched: List[dict] = []
    scanned = 0
    next_link: Optional[str] = None
    try:
        with MsGraphClient() as client:
            for _ in range(max_pages):
                if next_link:
                    data = client.get(_strip_base(next_link), operation_name="mail.find_by_attachment_name.next")
                else:
                    data = client.get(f"{base}/messages", params=params, operation_name="mail.find_by_attachment_name")
                page = data.get("value", [])
                scanned += len(page)
                matched.extend(m for m in page if needle in _scan_haystack(m))
                next_link = data.get("@odata.nextLink")
                if not next_link:
                    break
            folder_names = _folder_names(client, base) if matched else {}
        formatted = [
            {**_format_message(m), "parent_folder_id": m.get("parentFolderId"),
             "folder_name": folder_names.get(m.get("parentFolderId"))}
            for m in matched
        ]
        return {
            "message": f"Found {len(formatted)} matching messages (scanned {scanned})",
            "status_code": 200,
            "messages": formatted,
            "scanned": scanned,
            "truncated": next_link is not None,
        }
    except MsGraphError as e:
        logger.error(f"Error scanning attachment names for {term!r}: {e}")
        return _error_response(e, extra={"messages": [], "scanned": scanned, "truncated": True})


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
    cc_recipients: Optional[List[dict]] = None,
    bcc_recipients: Optional[List[dict]] = None,
) -> dict:
    """Forward a message to recipients (immediate send via /forward).

    Graph's /forward endpoint accepts toRecipients + comment at the top
    level and additional message overrides (cc / bcc) under a `message`
    property. `comment` is plain text; HTML is not interpreted.
    """
    payload: Dict[str, Any] = {"toRecipients": _build_recipient_list(to_recipients)}
    if comment:
        payload["comment"] = comment
    if cc_recipients or bcc_recipients:
        message_overrides: Dict[str, Any] = {}
        if cc_recipients:
            message_overrides["ccRecipients"] = _build_recipient_list(cc_recipients)
        if bcc_recipients:
            message_overrides["bccRecipients"] = _build_recipient_list(bcc_recipients)
        payload["message"] = message_overrides
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


def create_forward_draft(
    message_id: str,
    comment: Optional[str] = None,
    html_preamble: Optional[str] = None,
    to_recipients: Optional[List[dict]] = None,
    cc_recipients: Optional[List[dict]] = None,
    bcc_recipients: Optional[List[dict]] = None,
) -> dict:
    """Create a forward draft for a message.

    Graph's /createForward endpoint accepts toRecipients + comment top-
    level, and cc / bcc inside `message`. The returned draft inherits the
    original subject (auto-prefixed `FW: `), body, and attachments.

    Two preamble modes:
      - `comment` (plain text): passed via Graph's `comment` field.
        Graph wraps it in a `<div>` and **strips newlines** — fields end
        up running together on one line. Cheap (single POST) but visually
        flat.
      - `html_preamble` (HTML): two-step POST createForward + PATCH
        body. The PATCH prepends the HTML preamble to the forwarded body
        content, preserving the original message AND giving Outlook real
        line breaks. Slightly heavier (2 Graph calls) but produces a
        properly formatted email.

    Pass either or neither; passing both raises ValueError.
    """
    if comment and html_preamble:
        raise ValueError("create_forward_draft: pass either comment or html_preamble, not both")

    payload: Dict[str, Any] = {}
    if comment:
        payload["comment"] = comment
    if to_recipients:
        payload["toRecipients"] = _build_recipient_list(to_recipients)
    if cc_recipients or bcc_recipients:
        message_overrides: Dict[str, Any] = {}
        if cc_recipients:
            message_overrides["ccRecipients"] = _build_recipient_list(cc_recipients)
        if bcc_recipients:
            message_overrides["bccRecipients"] = _build_recipient_list(bcc_recipients)
        payload["message"] = message_overrides
    try:
        with MsGraphClient() as client:
            draft = client.post(
                f"me/messages/{message_id}/createForward",
                json=payload if payload else None,
                operation_name="mail.create_forward_draft",
            )
            if html_preamble:
                draft_id = draft.get("id") if isinstance(draft, dict) else None
                if not draft_id:
                    raise MsGraphError(
                        message="createForward returned no draft id",
                        http_status=500,
                    )
                existing_body = (draft.get("body") or {}).get("content") or ""
                existing_content_type = (
                    (draft.get("body") or {}).get("contentType") or ""
                ).lower()

                if existing_content_type == "text":
                    # Plain-text vendor emails (Walker Lumber, etc.) come
                    # back from createForward as `contentType=text` with
                    # `\n` newlines. PATCHing with `contentType=HTML`
                    # would collapse those newlines visually, so we
                    # HTML-escape the existing content and convert
                    # newlines to `<br>` first, then prepend the preamble.
                    import html as _html
                    safe_existing = _html.escape(existing_body).replace("\n", "<br>")
                    new_body = html_preamble + safe_existing
                else:
                    # HTML body: inject the preamble inside the <body>
                    # tag so the existing <head>/<style> block (which
                    # styles the forwarded EmailQuote) and the original
                    # body markup stay untouched.
                    import re
                    body_open = re.search(r"<body[^>]*>", existing_body, flags=re.IGNORECASE)
                    if body_open:
                        insert_pos = body_open.end()
                        new_body = (
                            existing_body[:insert_pos]
                            + html_preamble
                            + existing_body[insert_pos:]
                        )
                    else:
                        # Fallback: no <body> tag → simple prepend.
                        new_body = html_preamble + existing_body

                try:
                    client.patch(
                        f"me/messages/{draft_id}",
                        json={
                            "body": {
                                "contentType": "HTML",
                                "content": new_body,
                            }
                        },
                        timeout_tier="B",
                        operation_name="mail.create_forward_draft.patch_body",
                    )
                except MsGraphError:
                    # PATCH failed — delete the orphan draft so the reviewer
                    # doesn't see a forwarded email with no bill context.
                    try:
                        client.delete(
                            f"me/messages/{draft_id}",
                            operation_name="mail.create_forward_draft.rollback_orphan",
                        )
                        logger.warning(
                            "Rolled back orphan forward draft %s after PATCH failure",
                            draft_id,
                        )
                    except MsGraphError as del_err:
                        logger.error(
                            "Failed to delete orphan forward draft %s: %s",
                            draft_id,
                            del_err,
                        )
                    raise
                # Re-fetch for the return shape so callers see the
                # updated body.
                draft = client.get(
                    f"me/messages/{draft_id}",
                    operation_name="mail.create_forward_draft.refetch",
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
