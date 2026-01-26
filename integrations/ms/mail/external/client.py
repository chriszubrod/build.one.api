# Python Standard Library Imports
import json
import logging
from typing import Optional

# Third-party Imports
import requests

# Local Imports
from integrations.ms.auth.business.service import MsAuthService

logger = logging.getLogger(__name__)

# Microsoft Graph API base URL
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"


def _get_auth_headers() -> Optional[dict]:
    """
    Get authorization headers with a valid access token.
    Returns None if no valid token is available.
    """
    ms_auth_service = MsAuthService()
    ms_auth = ms_auth_service.ensure_valid_token()
    
    if not ms_auth or not ms_auth.access_token:
        logger.error("No valid MS access token available")
        return None
    
    return {
        "Authorization": f"Bearer {ms_auth.access_token}",
        "Content-Type": "application/json"
    }


# =============================================================================
# Mail Folder Operations
# =============================================================================


def list_mail_folders() -> dict:
    """
    List all mail folders for the current user.
    
    Returns:
        Dict with status_code, message, and folders list
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "folders": []
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/mailFolders"
        
        logger.info("Listing mail folders")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            folders = data.get("value", [])
            
            formatted_folders = []
            for folder in folders:
                formatted_folders.append({
                    "folder_id": folder.get("id"),
                    "display_name": folder.get("displayName"),
                    "parent_folder_id": folder.get("parentFolderId"),
                    "child_folder_count": folder.get("childFolderCount", 0),
                    "unread_item_count": folder.get("unreadItemCount", 0),
                    "total_item_count": folder.get("totalItemCount", 0),
                })
            
            return {
                "message": f"Found {len(formatted_folders)} mail folders",
                "status_code": 200,
                "folders": formatted_folders
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "folders": []
            }
        else:
            logger.error(f"Graph API list mail folders failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "folders": []
            }
    except Exception as e:
        logger.exception("Error listing mail folders")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "folders": []
        }


def get_mail_folder(folder_id: str) -> dict:
    """
    Get a specific mail folder by ID.
    
    Args:
        folder_id: The folder ID or well-known name (inbox, drafts, sentitems, deleteditems)
    
    Returns:
        Dict with status_code, message, and folder data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "folder": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/mailFolders/{folder_id}"
        
        logger.info(f"Getting mail folder: {folder_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            folder = resp.json()
            
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
                }
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "folder": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Folder not found: {folder_id}",
                "status_code": 404,
                "folder": None
            }
        else:
            logger.error(f"Graph API get mail folder failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "folder": None
            }
    except Exception as e:
        logger.exception("Error getting mail folder")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "folder": None
        }


# =============================================================================
# Message List & Read Operations
# =============================================================================


def _format_message(msg: dict, include_body: bool = False) -> dict:
    """
    Format a message from MS Graph API response.
    
    Args:
        msg: Raw message dict from Graph API
        include_body: Whether to include the full body content
    
    Returns:
        Formatted message dict
    """
    from_email = msg.get("from", {}).get("emailAddress", {})
    
    # Format recipients
    to_recipients = []
    for recipient in msg.get("toRecipients", []):
        email_addr = recipient.get("emailAddress", {})
        to_recipients.append({
            "name": email_addr.get("name"),
            "email": email_addr.get("address")
        })
    
    cc_recipients = []
    for recipient in msg.get("ccRecipients", []):
        email_addr = recipient.get("emailAddress", {})
        cc_recipients.append({
            "name": email_addr.get("name"),
            "email": email_addr.get("address")
        })
    
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
        "body_preview": msg.get("bodyPreview", "")[:200] if msg.get("bodyPreview") else "",
        "web_link": msg.get("webLink"),
        "flag": msg.get("flag"),  # Include flag for flagged status
    }
    
    if include_body:
        body = msg.get("body", {})
        formatted["body_content"] = body.get("content")
        formatted["body_content_type"] = body.get("contentType")
    
    # Include attachments if they're in the response (from $expand=attachments)
    if "attachments" in msg:
        formatted["attachments"] = [
            {
                "id": att.get("id"),
                "name": att.get("name"),
                "contentType": att.get("contentType"),
                "size": att.get("size"),
                "isInline": att.get("isInline", False),
            }
            for att in msg.get("attachments", [])
        ]
    
    return formatted


def list_messages(
    folder: str = "inbox",
    top: int = 25,
    skip: int = 0,
    filter_query: Optional[str] = None,
    search: Optional[str] = None,
    order_by: str = "receivedDateTime desc"
) -> dict:
    """
    List messages from a mail folder with automatic pagination.
    
    Args:
        folder: Folder ID or well-known name (inbox, drafts, sentitems, deleteditems)
        top: Total number of messages to retrieve (will page automatically)
        skip: Number of messages to skip for pagination
        filter_query: OData filter query (e.g., "isRead eq false")
        search: Search query string
        order_by: Sort order (default: receivedDateTime desc)
    
    Returns:
        Dict with status_code, message, messages list, and pagination info
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "messages": [],
                "total_count": 0
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/mailFolders/{folder}/messages"
        
        # MS Graph caps at 50 per request, so we'll page through
        page_size = 50
        all_messages = []
        total_count = 0
        pages_fetched = 0
        max_pages = (top // page_size) + 2  # Fetch enough pages to get 'top' messages
        
        params = {
            "$top": page_size,
            "$select": "id,conversationId,internetMessageId,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,isRead,isDraft,hasAttachments,importance,bodyPreview,webLink,flag",
            "$orderby": order_by,
            "$count": "true"
        }
        
        # $skip is not supported with $search
        if search:
            params["$search"] = f'"{search}"'
        else:
            params["$skip"] = skip
        
        if filter_query:
            params["$filter"] = filter_query
        
        logger.info(f"Listing messages from folder: {folder}, top: {top} (will page)")
        
        # Need to add header for $count
        headers_with_count = headers.copy()
        headers_with_count["ConsistencyLevel"] = "eventual"
        
        next_link = None
        
        while pages_fetched < max_pages and len(all_messages) < top:
            if next_link:
                resp = requests.get(url=next_link, headers=headers_with_count)
            else:
                resp = requests.get(url=endpoint, headers=headers_with_count, params=params)
            
            pages_fetched += 1
            
            if resp.status_code == 200:
                data = resp.json()
                messages = data.get("value", [])
                total_count = data.get("@odata.count", total_count or len(messages))
                next_link = data.get("@odata.nextLink")
                
                all_messages.extend(messages)
                logger.info(f"Page {pages_fetched}: fetched {len(messages)} messages (total: {len(all_messages)})")
                
                # Stop if no more pages
                if not next_link:
                    break
            elif resp.status_code == 401:
                return {
                    "message": "Access token expired or invalid. Try refreshing the token.",
                    "status_code": 401,
                    "messages": [],
                    "total_count": 0
                }
            elif resp.status_code == 404:
                return {
                    "message": f"Folder not found: {folder}",
                    "status_code": 404,
                    "messages": [],
                    "total_count": 0
                }
            else:
                logger.error(f"Graph API list messages failed: {resp.text}")
                return {
                    "message": f"Graph API call failed: {resp.text}",
                    "status_code": resp.status_code,
                    "messages": [],
                    "total_count": 0
                }
        
        # Limit to requested amount
        all_messages = all_messages[:top]
        formatted_messages = [_format_message(msg) for msg in all_messages]
        
        logger.info(f"Retrieved {len(formatted_messages)} messages total from {pages_fetched} pages")
        
        return {
            "message": f"Retrieved {len(formatted_messages)} messages",
            "status_code": 200,
            "messages": formatted_messages,
            "total_count": total_count,
            "has_more": next_link is not None or len(all_messages) >= top
        }
    except Exception as e:
        logger.exception("Error listing messages")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "messages": [],
            "total_count": 0
        }


def search_all_messages(
    conversation_id: str,
    top: int = 50,
) -> dict:
    """
    Search all messages by conversation ID.
    
    Uses a two-phase approach:
    1. First tries direct $filter (fast for recent conversations)
    2. Falls back to paging if filter fails or returns no results
    
    Args:
        conversation_id: The conversation ID to search for
        top: Maximum number of messages to return
        
    Returns:
        Dict with status_code, message, and messages list
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available.",
                "status_code": 401,
                "messages": [],
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages"
        select_fields = "id,conversationId,internetMessageId,subject,body,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,isRead,isDraft,hasAttachments,importance,bodyPreview,webLink,flag"
        
        # Phase 1: Try direct $filter (fast path for recent conversations)
        # Note: This may fail with InefficientFilter for some queries
        logger.info(f"Trying direct filter for conversation: {conversation_id[:50]}...")
        
        try:
            filter_params = {
                "$top": top,
                "$filter": f"conversationId eq '{conversation_id}'",
                "$select": select_fields,
                "$expand": "attachments($select=id,name,contentType,size,isInline)",
            }
            
            resp = requests.get(url=endpoint, headers=headers, params=filter_params, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                matching = data.get("value", [])
                
                if matching:
                    logger.info(f"Direct filter found {len(matching)} messages")
                    matching.sort(key=lambda m: m.get("receivedDateTime", ""))
                    formatted_messages = [_format_message(msg, include_body=True) for msg in matching[:top]]
                    return {
                        "message": f"Found {len(formatted_messages)} messages in conversation",
                        "status_code": 200,
                        "messages": formatted_messages,
                    }
                # If no results, fall through to paging approach
                logger.info("Direct filter returned no results, trying paging approach")
            else:
                logger.info(f"Direct filter failed ({resp.status_code}), trying paging approach")
        except requests.Timeout:
            logger.info("Direct filter timed out, trying paging approach")
        except Exception as e:
            logger.info(f"Direct filter error: {e}, trying paging approach")
        
        # Phase 2: Fall back to paging with client-side filtering
        params = {
            "$top": 250,  # Fetch in batches
            "$orderby": "receivedDateTime desc",
            "$select": select_fields,
            "$expand": "attachments($select=id,name,contentType,size,isInline)",
        }
        
        logger.info(f"Paging through messages to find conversation...")
        
        matching = []
        pages_searched = 0
        max_pages = 4  # Search up to 1000 messages (4 pages * 250)
        next_link = None
        
        while pages_searched < max_pages:
            if next_link:
                resp = requests.get(url=next_link, headers=headers)
            else:
                resp = requests.get(url=endpoint, headers=headers, params=params)
            
            if resp.status_code != 200:
                logger.error(f"Graph API search failed: {resp.text}")
                break
            
            data = resp.json()
            all_messages = data.get("value", [])
            pages_searched += 1
            
            # Filter client-side by conversation ID
            page_matches = [m for m in all_messages if m.get("conversationId") == conversation_id]
            matching.extend(page_matches)
            
            logger.info(f"Page {pages_searched}: Found {len(page_matches)} matches (total: {len(matching)}, searched: {len(all_messages)})")
            
            # Check if we have enough or no more pages
            next_link = data.get("@odata.nextLink")
            if not next_link:
                break
            
            # Early exit if we found messages and this page had none (conversation is probably complete)
            if matching and len(page_matches) == 0:
                break
            
            if len(matching) >= top:
                break
        
        # Sort by received date (oldest first) for thread processing
        matching.sort(key=lambda m: m.get("receivedDateTime", ""))
        
        # Include body content for thread messages
        formatted_messages = [_format_message(msg, include_body=True) for msg in matching[:top]]
        
        logger.info(f"Found {len(formatted_messages)} messages in conversation (searched {pages_searched} pages)")
        
        return {
            "message": f"Found {len(formatted_messages)} messages in conversation",
            "status_code": 200,
            "messages": formatted_messages,
        }
    except Exception as e:
        logger.exception("Error searching messages")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "messages": [],
        }


def get_message(message_id: str, include_body: bool = True) -> dict:
    """
    Get a specific message by ID with full details.
    
    Args:
        message_id: The message ID
        include_body: Whether to include the full body content
    
    Returns:
        Dict with status_code, message data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "email": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}"
        
        select_fields = "id,conversationId,internetMessageId,subject,from,toRecipients,ccRecipients,bccRecipients,receivedDateTime,sentDateTime,isRead,isDraft,hasAttachments,importance,bodyPreview,webLink,flag"
        if include_body:
            select_fields += ",body"
        
        params = {
            "$select": select_fields,
            "$expand": "attachments"
        }
        
        logger.info(f"Getting message: {message_id}")
        resp = requests.get(url=endpoint, headers=headers, params=params)
        
        if resp.status_code == 200:
            msg = resp.json()
            formatted = _format_message(msg, include_body=include_body)
            
            # Add BCC for complete message view
            bcc_recipients = []
            for recipient in msg.get("bccRecipients", []):
                email_addr = recipient.get("emailAddress", {})
                bcc_recipients.append({
                    "name": email_addr.get("name"),
                    "email": email_addr.get("address")
                })
            formatted["bcc_recipients"] = bcc_recipients
            
            # Add attachments
            attachments = []
            for att in msg.get("attachments", []):
                attachments.append({
                    "id": att.get("id"),
                    "name": att.get("name"),
                    "content_type": att.get("contentType"),
                    "size": att.get("size"),
                    "is_inline": att.get("isInline", False),
                })
            formatted["attachments"] = attachments
            
            return {
                "message": "Message retrieved successfully",
                "status_code": 200,
                "email": formatted
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "email": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message not found: {message_id}",
                "status_code": 404,
                "email": None
            }
        else:
            logger.error(f"Graph API get message failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "email": None
            }
    except Exception as e:
        logger.exception("Error getting message")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "email": None
        }


def mark_message_read(message_id: str, is_read: bool = True) -> dict:
    """
    Mark a message as read or unread.
    
    Args:
        message_id: The message ID
        is_read: True to mark as read, False to mark as unread
    
    Returns:
        Dict with status_code and message
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}"
        
        payload = {
            "isRead": is_read
        }
        
        logger.info(f"Marking message {message_id} as {'read' if is_read else 'unread'}")
        resp = requests.patch(url=endpoint, headers=headers, json=payload)
        
        if resp.status_code == 200:
            return {
                "message": f"Message marked as {'read' if is_read else 'unread'}",
                "status_code": 200
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message not found: {message_id}",
                "status_code": 404
            }
        else:
            logger.error(f"Graph API mark message read failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code
            }
    except Exception as e:
        logger.exception("Error marking message read")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


def unflag_message(message_id: str) -> dict:
    """
    Remove the flag from a message.
    
    Args:
        message_id: The message ID
    
    Returns:
        Dict with status_code and message
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}"
        
        payload = {
            "flag": {
                "flagStatus": "notFlagged"
            }
        }
        
        logger.info(f"Removing flag from message {message_id}")
        resp = requests.patch(url=endpoint, headers=headers, json=payload)
        
        if resp.status_code == 200:
            return {
                "message": "Flag removed from message",
                "status_code": 200
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message not found: {message_id}",
                "status_code": 404
            }
        else:
            logger.error(f"Graph API unflag message failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code
            }
    except Exception as e:
        logger.exception("Error removing flag from message")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


# =============================================================================
# Attachment Operations
# =============================================================================


def list_message_attachments(message_id: str) -> dict:
    """
    List all attachments for a message.
    
    Args:
        message_id: The message ID
    
    Returns:
        Dict with status_code, message, and attachments list
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "attachments": []
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}/attachments"
        
        logger.info(f"Listing attachments for message: {message_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            attachments = data.get("value", [])
            
            formatted_attachments = []
            for att in attachments:
                formatted_attachments.append({
                    "attachment_id": att.get("id"),
                    "name": att.get("name"),
                    "content_type": att.get("contentType"),
                    "size": att.get("size"),
                    "is_inline": att.get("isInline", False),
                    "attachment_type": att.get("@odata.type", "").replace("#microsoft.graph.", ""),
                })
            
            return {
                "message": f"Found {len(formatted_attachments)} attachments",
                "status_code": 200,
                "attachments": formatted_attachments
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "attachments": []
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message not found: {message_id}",
                "status_code": 404,
                "attachments": []
            }
        else:
            logger.error(f"Graph API list attachments failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "attachments": []
            }
    except Exception as e:
        logger.exception("Error listing message attachments")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "attachments": []
        }


def download_attachment(message_id: str, attachment_id: str) -> dict:
    """
    Download an attachment's content.
    
    Args:
        message_id: The message ID
        attachment_id: The attachment ID
    
    Returns:
        Dict with status_code, content (bytes), content_type, and filename
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "content": None,
                "content_type": None,
                "filename": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}/attachments/{attachment_id}"
        
        logger.info(f"Downloading attachment: {attachment_id} from message: {message_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            attachment = resp.json()
            
            # Handle file attachment (base64 encoded content)
            import base64
            content_bytes = None
            if "contentBytes" in attachment:
                content_bytes = base64.b64decode(attachment.get("contentBytes"))
            
            return {
                "message": "Attachment downloaded successfully",
                "status_code": 200,
                "content": content_bytes,
                "content_type": attachment.get("contentType", "application/octet-stream"),
                "filename": attachment.get("name"),
                "size": attachment.get("size")
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "content": None,
                "content_type": None,
                "filename": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Attachment not found: {attachment_id}",
                "status_code": 404,
                "content": None,
                "content_type": None,
                "filename": None
            }
        else:
            logger.error(f"Graph API download attachment failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "content": None,
                "content_type": None,
                "filename": None
            }
    except Exception as e:
        logger.exception("Error downloading attachment")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "content": None,
            "content_type": None,
            "filename": None
        }


# =============================================================================
# Send & Draft Operations
# =============================================================================


def _build_recipient_list(recipients: list[dict]) -> list[dict]:
    """
    Build recipient list in Graph API format.
    
    Args:
        recipients: List of dicts with 'email' and optional 'name' keys
    
    Returns:
        List in Graph API emailAddress format
    """
    result = []
    for r in recipients:
        result.append({
            "emailAddress": {
                "address": r.get("email"),
                "name": r.get("name", r.get("email"))
            }
        })
    return result


def send_message(
    to_recipients: list[dict],
    subject: str,
    body: str,
    body_type: str = "HTML",
    cc_recipients: Optional[list[dict]] = None,
    bcc_recipients: Optional[list[dict]] = None,
    attachments: Optional[list[dict]] = None,
    importance: str = "normal",
    save_to_sent_items: bool = True
) -> dict:
    """
    Send a new email message.
    
    Args:
        to_recipients: List of dicts with 'email' and optional 'name'
        subject: Email subject
        body: Email body content
        body_type: "HTML" or "Text"
        cc_recipients: Optional CC recipients
        bcc_recipients: Optional BCC recipients
        attachments: Optional list of dicts with 'name', 'content_type', 'content_bytes' (base64)
        importance: "low", "normal", or "high"
        save_to_sent_items: Whether to save in Sent Items folder
    
    Returns:
        Dict with status_code and message
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/sendMail"
        
        message = {
            "subject": subject,
            "body": {
                "contentType": body_type,
                "content": body
            },
            "toRecipients": _build_recipient_list(to_recipients),
            "importance": importance
        }
        
        if cc_recipients:
            message["ccRecipients"] = _build_recipient_list(cc_recipients)
        
        if bcc_recipients:
            message["bccRecipients"] = _build_recipient_list(bcc_recipients)
        
        if attachments:
            message["attachments"] = []
            for att in attachments:
                message["attachments"].append({
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": att.get("name"),
                    "contentType": att.get("content_type", "application/octet-stream"),
                    "contentBytes": att.get("content_bytes")  # Already base64 encoded
                })
        
        payload = {
            "message": message,
            "saveToSentItems": save_to_sent_items
        }
        
        logger.info(f"Sending email to {len(to_recipients)} recipients, subject: {subject[:50]}")
        resp = requests.post(url=endpoint, headers=headers, json=payload)
        
        if resp.status_code == 202:
            return {
                "message": "Email sent successfully",
                "status_code": 202
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401
            }
        else:
            logger.error(f"Graph API send message failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code
            }
    except Exception as e:
        logger.exception("Error sending message")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


def create_draft(
    to_recipients: Optional[list[dict]] = None,
    subject: str = "",
    body: str = "",
    body_type: str = "HTML",
    cc_recipients: Optional[list[dict]] = None,
    bcc_recipients: Optional[list[dict]] = None,
    attachments: Optional[list[dict]] = None,
    importance: str = "normal"
) -> dict:
    """
    Create a draft message in the Drafts folder.
    
    Args:
        to_recipients: Optional list of dicts with 'email' and optional 'name'
        subject: Email subject
        body: Email body content
        body_type: "HTML" or "Text"
        cc_recipients: Optional CC recipients
        bcc_recipients: Optional BCC recipients
        attachments: Optional list of dicts with 'name', 'content_type', 'content_bytes'
        importance: "low", "normal", or "high"
    
    Returns:
        Dict with status_code, message, and created draft data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "draft": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages"
        
        message = {
            "subject": subject,
            "body": {
                "contentType": body_type,
                "content": body
            },
            "importance": importance
        }
        
        if to_recipients:
            message["toRecipients"] = _build_recipient_list(to_recipients)
        
        if cc_recipients:
            message["ccRecipients"] = _build_recipient_list(cc_recipients)
        
        if bcc_recipients:
            message["bccRecipients"] = _build_recipient_list(bcc_recipients)
        
        logger.info(f"Creating draft, subject: {subject[:50] if subject else '(empty)'}")
        resp = requests.post(url=endpoint, headers=headers, json=message)
        
        if resp.status_code == 201:
            draft = resp.json()
            
            # If attachments provided, add them separately
            if attachments:
                draft_id = draft.get("id")
                for att in attachments:
                    add_attachment_result = add_attachment_to_message(
                        message_id=draft_id,
                        name=att.get("name"),
                        content_type=att.get("content_type", "application/octet-stream"),
                        content_bytes=att.get("content_bytes")
                    )
                    if add_attachment_result.get("status_code") != 201:
                        logger.warning(f"Failed to add attachment: {add_attachment_result.get('message')}")
            
            return {
                "message": "Draft created successfully",
                "status_code": 201,
                "draft": _format_message(draft, include_body=True)
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "draft": None
            }
        else:
            logger.error(f"Graph API create draft failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "draft": None
            }
    except Exception as e:
        logger.exception("Error creating draft")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "draft": None
        }


def update_draft(
    message_id: str,
    to_recipients: Optional[list[dict]] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    body_type: str = "HTML",
    cc_recipients: Optional[list[dict]] = None,
    bcc_recipients: Optional[list[dict]] = None,
    importance: Optional[str] = None
) -> dict:
    """
    Update an existing draft message.
    
    Args:
        message_id: The draft message ID
        to_recipients: Optional list of recipients to update
        subject: Optional new subject
        body: Optional new body content
        body_type: "HTML" or "Text"
        cc_recipients: Optional CC recipients
        bcc_recipients: Optional BCC recipients
        importance: Optional importance level
    
    Returns:
        Dict with status_code, message, and updated draft data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "draft": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}"
        
        message = {}
        
        if subject is not None:
            message["subject"] = subject
        
        if body is not None:
            message["body"] = {
                "contentType": body_type,
                "content": body
            }
        
        if to_recipients is not None:
            message["toRecipients"] = _build_recipient_list(to_recipients)
        
        if cc_recipients is not None:
            message["ccRecipients"] = _build_recipient_list(cc_recipients)
        
        if bcc_recipients is not None:
            message["bccRecipients"] = _build_recipient_list(bcc_recipients)
        
        if importance is not None:
            message["importance"] = importance
        
        logger.info(f"Updating draft: {message_id}")
        resp = requests.patch(url=endpoint, headers=headers, json=message)
        
        if resp.status_code == 200:
            draft = resp.json()
            return {
                "message": "Draft updated successfully",
                "status_code": 200,
                "draft": _format_message(draft, include_body=True)
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "draft": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Draft not found: {message_id}",
                "status_code": 404,
                "draft": None
            }
        else:
            logger.error(f"Graph API update draft failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "draft": None
            }
    except Exception as e:
        logger.exception("Error updating draft")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "draft": None
        }


def send_draft(message_id: str) -> dict:
    """
    Send an existing draft message.
    
    Args:
        message_id: The draft message ID
    
    Returns:
        Dict with status_code and message
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}/send"
        
        logger.info(f"Sending draft: {message_id}")
        resp = requests.post(url=endpoint, headers=headers)
        
        if resp.status_code == 202:
            return {
                "message": "Draft sent successfully",
                "status_code": 202
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401
            }
        elif resp.status_code == 404:
            return {
                "message": f"Draft not found: {message_id}",
                "status_code": 404
            }
        else:
            logger.error(f"Graph API send draft failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code
            }
    except Exception as e:
        logger.exception("Error sending draft")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


def add_attachment_to_message(
    message_id: str,
    name: str,
    content_type: str,
    content_bytes: str
) -> dict:
    """
    Add an attachment to a draft message.
    
    Args:
        message_id: The message ID (must be a draft)
        name: Attachment filename
        content_type: MIME type
        content_bytes: Base64-encoded content
    
    Returns:
        Dict with status_code, message, and attachment data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "attachment": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}/attachments"
        
        payload = {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": name,
            "contentType": content_type,
            "contentBytes": content_bytes
        }
        
        logger.info(f"Adding attachment '{name}' to message: {message_id}")
        resp = requests.post(url=endpoint, headers=headers, json=payload)
        
        if resp.status_code == 201:
            attachment = resp.json()
            return {
                "message": "Attachment added successfully",
                "status_code": 201,
                "attachment": {
                    "attachment_id": attachment.get("id"),
                    "name": attachment.get("name"),
                    "content_type": attachment.get("contentType"),
                    "size": attachment.get("size")
                }
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "attachment": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message not found: {message_id}",
                "status_code": 404,
                "attachment": None
            }
        else:
            logger.error(f"Graph API add attachment failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "attachment": None
            }
    except Exception as e:
        logger.exception("Error adding attachment")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "attachment": None
        }


# =============================================================================
# Reply & Forward Operations
# =============================================================================


def reply_to_message(
    message_id: str,
    body: str,
    body_type: str = "HTML",
    reply_all: bool = False
) -> dict:
    """
    Reply to a message.
    
    Args:
        message_id: The message ID to reply to
        body: Reply body content
        body_type: "HTML" or "Text"
        reply_all: If True, reply to all recipients
    
    Returns:
        Dict with status_code and message
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401
            }
        
        action = "replyAll" if reply_all else "reply"
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}/{action}"
        
        payload = {
            "comment": body
        }
        
        # Note: Graph API uses 'comment' for reply body in the simple reply endpoint
        # For more control, use createReply to create a draft, then edit and send
        
        logger.info(f"{'Reply all' if reply_all else 'Reply'} to message: {message_id}")
        resp = requests.post(url=endpoint, headers=headers, json=payload)
        
        if resp.status_code == 202:
            return {
                "message": f"{'Reply all' if reply_all else 'Reply'} sent successfully",
                "status_code": 202
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message not found: {message_id}",
                "status_code": 404
            }
        else:
            logger.error(f"Graph API reply failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code
            }
    except Exception as e:
        logger.exception("Error replying to message")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


def create_reply_draft(message_id: str, reply_all: bool = False) -> dict:
    """
    Create a reply draft for a message (for more control before sending).
    
    Args:
        message_id: The message ID to reply to
        reply_all: If True, create reply-all draft
    
    Returns:
        Dict with status_code, message, and draft data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "draft": None
            }
        
        action = "createReplyAll" if reply_all else "createReply"
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}/{action}"
        
        logger.info(f"Creating {'reply all' if reply_all else 'reply'} draft for message: {message_id}")
        resp = requests.post(url=endpoint, headers=headers)
        
        if resp.status_code == 201:
            draft = resp.json()
            return {
                "message": f"{'Reply all' if reply_all else 'Reply'} draft created successfully",
                "status_code": 201,
                "draft": _format_message(draft, include_body=True)
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "draft": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message not found: {message_id}",
                "status_code": 404,
                "draft": None
            }
        else:
            logger.error(f"Graph API create reply draft failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "draft": None
            }
    except Exception as e:
        logger.exception("Error creating reply draft")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "draft": None
        }


def forward_message(
    message_id: str,
    to_recipients: list[dict],
    comment: Optional[str] = None
) -> dict:
    """
    Forward a message to recipients.
    
    Args:
        message_id: The message ID to forward
        to_recipients: List of dicts with 'email' and optional 'name'
        comment: Optional comment to add above forwarded content
    
    Returns:
        Dict with status_code and message
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}/forward"
        
        payload = {
            "toRecipients": _build_recipient_list(to_recipients)
        }
        
        if comment:
            payload["comment"] = comment
        
        logger.info(f"Forwarding message {message_id} to {len(to_recipients)} recipients")
        resp = requests.post(url=endpoint, headers=headers, json=payload)
        
        if resp.status_code == 202:
            return {
                "message": "Message forwarded successfully",
                "status_code": 202
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message not found: {message_id}",
                "status_code": 404
            }
        else:
            logger.error(f"Graph API forward failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code
            }
    except Exception as e:
        logger.exception("Error forwarding message")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }


def create_forward_draft(message_id: str) -> dict:
    """
    Create a forward draft for a message (for more control before sending).
    
    Args:
        message_id: The message ID to forward
    
    Returns:
        Dict with status_code, message, and draft data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "draft": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}/createForward"
        
        logger.info(f"Creating forward draft for message: {message_id}")
        resp = requests.post(url=endpoint, headers=headers)
        
        if resp.status_code == 201:
            draft = resp.json()
            return {
                "message": "Forward draft created successfully",
                "status_code": 201,
                "draft": _format_message(draft, include_body=True)
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "draft": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message not found: {message_id}",
                "status_code": 404,
                "draft": None
            }
        else:
            logger.error(f"Graph API create forward draft failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "draft": None
            }
    except Exception as e:
        logger.exception("Error creating forward draft")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "draft": None
        }


# =============================================================================
# Message Management Operations
# =============================================================================


def move_message(message_id: str, destination_folder_id: str) -> dict:
    """
    Move a message to a different folder.
    
    Args:
        message_id: The message ID
        destination_folder_id: Target folder ID or well-known name
    
    Returns:
        Dict with status_code, message, and moved message data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "email": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}/move"
        
        payload = {
            "destinationId": destination_folder_id
        }
        
        logger.info(f"Moving message {message_id} to folder: {destination_folder_id}")
        resp = requests.post(url=endpoint, headers=headers, json=payload)
        
        if resp.status_code == 201:
            msg = resp.json()
            return {
                "message": "Message moved successfully",
                "status_code": 201,
                "email": _format_message(msg)
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "email": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message or folder not found",
                "status_code": 404,
                "email": None
            }
        else:
            logger.error(f"Graph API move message failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "email": None
            }
    except Exception as e:
        logger.exception("Error moving message")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "email": None
        }


def delete_message(message_id: str) -> dict:
    """
    Delete a message (moves to Deleted Items).
    
    Args:
        message_id: The message ID
    
    Returns:
        Dict with status_code and message
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/messages/{message_id}"
        
        logger.info(f"Deleting message: {message_id}")
        resp = requests.delete(url=endpoint, headers=headers)
        
        if resp.status_code == 204:
            return {
                "message": "Message deleted successfully",
                "status_code": 204
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401
            }
        elif resp.status_code == 404:
            return {
                "message": f"Message not found: {message_id}",
                "status_code": 404
            }
        else:
            logger.error(f"Graph API delete message failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code
            }
    except Exception as e:
        logger.exception("Error deleting message")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500
        }
