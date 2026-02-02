# Python Standard Library Imports
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Local Imports
from workflows.capabilities.base import Capability, CapabilityResult, with_retry

logger = logging.getLogger(__name__)


@dataclass
class EmailMessage:
    """Represents an email message."""
    id: str
    conversation_id: Optional[str]
    subject: str
    body: str
    body_type: str  # 'text' or 'html'
    from_address: str
    from_name: Optional[str]
    to_recipients: List[str]
    cc_recipients: List[str]
    received_datetime: str
    has_attachments: bool
    is_read: bool
    is_flagged: bool
    importance: str
    web_link: Optional[str] = None
    body_preview: Optional[str] = None
    attachments: List[Dict] = field(default_factory=list)


class EmailCapabilities(Capability):
    """
    Email capabilities using MS Graph API.
    
    Provides email operations for the agents framework.
    """
    
    @property
    def name(self) -> str:
        return "email"
    
    def _parse_message(self, msg: Dict) -> EmailMessage:
        """Parse MS Graph message response to EmailMessage.
        
        Handles both raw Graph API format and formatted mail client format.
        """
        # Handle different key formats between raw API and mail client
        message_id = msg.get("message_id") or msg.get("id", "")
        conversation_id = msg.get("conversation_id") or msg.get("conversationId")
        
        # From address - mail client uses from_email/from_name, raw API uses from.emailAddress
        from_data = msg.get("from", {}).get("emailAddress", {})
        from_address = msg.get("from_email") or from_data.get("address", "")
        from_name = msg.get("from_name") or from_data.get("name")
        
        # Body - mail client uses body_content/body_content_type, raw API uses body.content
        body = msg.get("body_content") or msg.get("body", {}).get("content", "")
        body_type = msg.get("body_content_type") or msg.get("body", {}).get("contentType", "text")
        
        # Recipients - mail client formats differently
        to_raw = msg.get("toRecipients", [])
        to_formatted = msg.get("to_recipients", [])
        if to_formatted:
            to_recipients = [r.get("email", "") for r in to_formatted]
        else:
            to_recipients = [r.get("emailAddress", {}).get("address", "") for r in to_raw]
        
        cc_raw = msg.get("ccRecipients", [])
        cc_formatted = msg.get("cc_recipients", [])
        if cc_formatted:
            cc_recipients = [r.get("email", "") for r in cc_formatted]
        else:
            cc_recipients = [r.get("emailAddress", {}).get("address", "") for r in cc_raw]
        
        # Received datetime
        received_datetime = msg.get("received_datetime") or msg.get("receivedDateTime", "")
        
        # Boolean fields
        has_attachments = msg.get("has_attachments") if "has_attachments" in msg else msg.get("hasAttachments", False)
        is_read = msg.get("is_read") if "is_read" in msg else msg.get("isRead", False)
        
        # Flag status
        flag_status = msg.get("flag", {}).get("flagStatus", "notFlagged")
        
        # Other fields
        web_link = msg.get("web_link") or msg.get("webLink")
        body_preview = msg.get("body_preview") or msg.get("bodyPreview")
        
        return EmailMessage(
            id=message_id,
            conversation_id=conversation_id,
            subject=msg.get("subject", ""),
            body=body,
            body_type=body_type,
            from_address=from_address,
            from_name=from_name,
            to_recipients=to_recipients,
            cc_recipients=cc_recipients,
            received_datetime=received_datetime,
            has_attachments=has_attachments,
            is_read=is_read,
            is_flagged=flag_status == "flagged",
            importance=msg.get("importance", "normal"),
            web_link=web_link,
            body_preview=body_preview,
            attachments=msg.get("attachments", []),
        )
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def get_new_messages(
        self,
        access_token: str,
        folder: str = "inbox",
        since: Optional[str] = None,
        has_attachments: Optional[bool] = None,
        flagged: Optional[bool] = None,
        is_read: Optional[bool] = None,
        from_addresses: Optional[List[str]] = None,
        top: int = 50,
    ) -> CapabilityResult:
        """
        Get new messages from a mail folder.
        
        Args:
            access_token: MS Graph access token
            folder: Mail folder (inbox, sentitems, etc.)
            since: ISO datetime to filter messages after
            has_attachments: Filter for messages with attachments
            flagged: Filter for flagged (red flag) messages
            is_read: Filter for read/unread messages
            from_addresses: Filter for messages from specific addresses
            top: Maximum number of messages to return
            
        Returns:
            CapabilityResult with list of EmailMessage
        """
        self._log_call(
            "get_new_messages",
            folder=folder,
            since=since,
            has_attachments=has_attachments,
            flagged=flagged,
            is_read=is_read,
            top=top,
        )
        
        try:
            from integrations.ms.mail.external import client as mail_client
            
            # Build OData filter query
            # Note: hasAttachments and flag/flagStatus filters not supported on inbox with sort
            filter_parts = []
            
            if since:
                filter_parts.append(f"receivedDateTime ge {since}")
            
            # Server-side filter for read status (supported by MS Graph)
            if is_read is not None:
                filter_parts.append(f"isRead eq {str(is_read).lower()}")
            
            filter_query = " and ".join(filter_parts) if filter_parts else None
            
            # Note: mail_client handles token internally via MsAuthService
            # Fetch extra if client-side filtering is needed (hasAttachments, flagged)
            # Cap to avoid excessive API calls
            needs_client_filter = has_attachments is not None or flagged is not None
            fetch_top = min(top * 3, 300) if needs_client_filter else top
            
            response = mail_client.list_messages(
                folder=folder,
                top=fetch_top,
                filter_query=filter_query,
            )
            
            if response.get("status_code") != 200:
                return CapabilityResult.fail(
                    error=response.get("message", "Failed to list messages"),
                )
            
            messages = [
                self._parse_message(msg)
                for msg in response.get("messages", [])
            ]
            
            # Client-side filter for attachments (OData filter not reliable on inbox)
            if has_attachments is not None:
                messages = [m for m in messages if m.has_attachments == has_attachments]
            
            # Client-side filter for flagged status (OData filter not supported with sort)
            if flagged is not None:
                messages = [m for m in messages if m.is_flagged == flagged]
            
            # Don't limit here - let the caller decide the final limit
            # This ensures enough data for grouping and filtering by conversation
            
            # Filter by from addresses if specified
            if from_addresses:
                from_set = {addr.lower() for addr in from_addresses}
                messages = [
                    m for m in messages
                    if m.from_address.lower() in from_set
                ]
            
            result = CapabilityResult.ok(data=messages)
            self._log_result("get_new_messages", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "get_new_messages")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def get_message(
        self,
        access_token: str,
        message_id: str,
        include_attachments: bool = False,
    ) -> CapabilityResult:
        """
        Get a specific message by ID.
        
        Args:
            access_token: MS Graph access token
            message_id: The message ID
            include_attachments: Whether to include attachment metadata
            
        Returns:
            CapabilityResult with EmailMessage
        """
        self._log_call("get_message", message_id=message_id)
        
        try:
            from integrations.ms.mail.external import client as mail_client
            
            # Note: mail_client handles token internally
            response = mail_client.get_message(
                message_id=message_id,
                include_body=True,
            )
            
            if response.get("status_code") != 200:
                return CapabilityResult.fail(
                    error=response.get("message", "Failed to get message"),
                )
            
            # Mail client returns 'email' key, not 'message'
            message = self._parse_message(response.get("email", {}))
            
            result = CapabilityResult.ok(data=message)
            self._log_result("get_message", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "get_message")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def get_thread_messages(
        self,
        access_token: str,
        conversation_id: str,
    ) -> CapabilityResult:
        """
        Get all messages in a conversation thread.
        
        Args:
            access_token: MS Graph access token
            conversation_id: The conversation ID
            
        Returns:
            CapabilityResult with list of EmailMessage
        """
        self._log_call("get_thread_messages", conversation_id=conversation_id)
        
        try:
            from integrations.ms.mail.external import client as mail_client
            
            # Use search_all_messages to search across all mail (not folder-specific)
            # This avoids the InefficientFilter error with conversationId filter
            response = mail_client.search_all_messages(
                conversation_id=conversation_id,
            )
            
            if response.get("status_code") != 200:
                return CapabilityResult.fail(
                    error=response.get("message", "Failed to get thread"),
                )
            
            messages = [
                self._parse_message(msg)
                for msg in response.get("messages", [])
            ]
            
            result = CapabilityResult.ok(data=messages)
            self._log_result("get_thread_messages", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "get_thread_messages")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def send_as_user(
        self,
        access_token: str,
        to_recipients: List[str],
        subject: str,
        body: str,
        body_type: str = "html",
        cc_recipients: Optional[List[str]] = None,
        attachments: Optional[List[Dict]] = None,
    ) -> CapabilityResult:
        """
        Send an email as the authenticated user.
        
        Args:
            access_token: MS Graph access token (with delegated permissions)
            to_recipients: List of recipient email addresses
            subject: Email subject
            body: Email body
            body_type: 'text' or 'html'
            cc_recipients: Optional CC recipients
            attachments: Optional list of attachments
            
        Returns:
            CapabilityResult with sent message details
        """
        self._log_call(
            "send_as_user",
            to_recipients=to_recipients,
            subject=subject,
        )
        
        try:
            from integrations.ms.mail.external import client as mail_client
            
            # Convert email strings to recipient dicts
            to_dicts = [{"email": addr} for addr in to_recipients]
            cc_dicts = [{"email": addr} for addr in cc_recipients] if cc_recipients else None
            
            # Note: mail_client handles token internally
            response = mail_client.send_message(
                to_recipients=to_dicts,
                subject=subject,
                body=body,
                body_type=body_type.upper(),
                cc_recipients=cc_dicts,
                attachments=attachments,
            )
            
            if response.get("status_code") not in (200, 201, 202):
                return CapabilityResult.fail(
                    error=response.get("message", "Failed to send message"),
                )
            
            result = CapabilityResult.ok(
                data={
                    "message_id": response.get("message_id"),
                    "status": "sent",
                },
            )
            self._log_result("send_as_user", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "send_as_user")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def reply_to_message(
        self,
        access_token: str,
        message_id: str,
        body: str,
        body_type: str = "html",
        reply_all: bool = False,
    ) -> CapabilityResult:
        """
        Reply to an existing message.
        
        Args:
            access_token: MS Graph access token
            message_id: The message to reply to
            body: Reply body
            body_type: 'text' or 'html'
            reply_all: Whether to reply all
            
        Returns:
            CapabilityResult with reply details
        """
        self._log_call("reply_to_message", message_id=message_id, reply_all=reply_all)
        
        try:
            from integrations.ms.mail.external import client as mail_client
            
            # Note: mail_client handles token internally
            response = mail_client.reply_to_message(
                message_id=message_id,
                body=body,
                body_type=body_type.upper(),
                reply_all=reply_all,
            )
            
            if response.get("status_code") not in (200, 201, 202):
                return CapabilityResult.fail(
                    error=response.get("message", "Failed to send reply"),
                )
            
            result = CapabilityResult.ok(
                data={"status": "sent"},
            )
            self._log_result("reply_to_message", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "reply_to_message")
    
    @with_retry(max_attempts=3, base_delay=1.0)
    def download_attachment(
        self,
        access_token: str,
        message_id: str,
        attachment_id: str,
    ) -> CapabilityResult:
        """
        Download an attachment from a message.
        
        Args:
            access_token: MS Graph access token
            message_id: The message ID
            attachment_id: The attachment ID
            
        Returns:
            CapabilityResult with attachment bytes and metadata
        """
        self._log_call(
            "download_attachment",
            message_id=message_id,
            attachment_id=attachment_id,
        )
        
        try:
            from integrations.ms.mail.external import client as mail_client
            
            # Note: mail_client handles token internally
            response = mail_client.download_attachment(
                message_id=message_id,
                attachment_id=attachment_id,
            )
            
            if response.get("status_code") != 200:
                return CapabilityResult.fail(
                    error=response.get("message", "Failed to download attachment"),
                )
            
            result = CapabilityResult.ok(
                data={
                    "content": response.get("content"),
                    "content_type": response.get("content_type"),
                    "name": response.get("name"),
                    "size": response.get("size"),
                },
            )
            self._log_result("download_attachment", result)
            return result
            
        except Exception as e:
            return self._handle_error(e, "download_attachment")
