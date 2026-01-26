# Python Standard Library Imports
import logging
import re
from typing import Dict, List, Optional

# Local Imports
from agents.capabilities.base import CapabilityResult
from agents.capabilities.email import EmailMessage
from agents.capabilities.llm import Classification
from agents.entity_registry import get_entity_config
from agents.runners.base import Agent, AgentContext, AgentResult

logger = logging.getLogger(__name__)


class EmailTriageAgent(Agent):
    """
    Agent that classifies incoming emails by entity type.
    
    This is a simplified classifier that ONLY determines what type of entity
    the email represents (bill, expense, invoice, etc.). It does NOT extract
    fields or match vendors/projects - that happens in entity-specific workflows.
    
    Responsibilities:
    1. Fetch email content and attachments
    2. Extract text from attachments using Document Intelligence
    3. Classify email entity type using LLM
    
    Returns entity_type and confidence for user confirmation.
    """
    
    @property
    def name(self) -> str:
        return "email_triage"
    
    @property
    def description(self) -> str:
        return "Classifies incoming emails by entity type"
    
    async def run(self, context: AgentContext) -> AgentResult:
        """
        Execute email classification.
        
        Expected context:
        - trigger_data.message_id: The email message ID to classify
        - conversation: Pre-fetched conversation thread (optional)
        - email: Pre-fetched email data (optional)
        - all_conversation_attachments: All attachments from conversation (optional)
        
        Returns:
        - classification: entity_type, confidence, reasoning
        - email: Basic email metadata
        - attachment_blob_urls: URLs of saved attachments
        """
        self._log_start(context)
        
        try:
            # Get the message ID from trigger data or context
            trigger_data = context.trigger_data or {}
            message_id = trigger_data.get("message_id") or (context.workflow_context or {}).get("trigger_message_id")
            
            if not message_id:
                return AgentResult.fail("No message_id provided in trigger_data")
            
            # Check if email data is pre-fetched in workflow context
            wf_context = context.workflow_context or {}
            pre_fetched_email = wf_context.get("email")
            pre_fetched_conversation = wf_context.get("conversation", [])
            pre_fetched_attachments = wf_context.get("all_conversation_attachments", [])
            
            if pre_fetched_email and pre_fetched_email.get("body"):
                # Use pre-fetched email data
                logger.info(f"Using pre-fetched email data for {message_id}")
                email = EmailMessage(
                    id=pre_fetched_email.get("message_id", message_id),
                    conversation_id=pre_fetched_email.get("conversation_id"),
                    subject=pre_fetched_email.get("subject", ""),
                    body=pre_fetched_email.get("body", ""),
                    body_type=pre_fetched_email.get("body_type", "text"),
                    from_address=pre_fetched_email.get("from_address", ""),
                    from_name=pre_fetched_email.get("from_name"),
                    to_recipients=[],
                    cc_recipients=[],
                    received_datetime=pre_fetched_email.get("received_at", ""),
                    has_attachments=len(pre_fetched_attachments) > 0,
                    is_read=True,
                    is_flagged=False,
                    importance="normal",
                    web_link=None,
                    body_preview=None,
                    attachments=wf_context.get("attachments", []),
                )
            else:
                # Fetch email from MS Graph
                logger.info(f"Fetching email from MS Graph: {message_id}")
                email_result = await self._fetch_email(context.access_token, message_id)
                if not email_result.success:
                    return AgentResult.fail(f"Failed to fetch email: {email_result.error}")
                email: EmailMessage = email_result.data
            
            # Step 2: Download and process attachments
            # Use pre-fetched conversation attachments if available, otherwise fetch
            attachment_texts = []
            attachment_blob_urls = []
            extraction_failed = False
            attachment_source_message_id = None
            
            # Check if we have pre-fetched attachments from the full conversation
            if pre_fetched_attachments:
                logger.info(f"Processing {len(pre_fetched_attachments)} pre-fetched attachments from conversation")
                seen_filenames = set()  # Track filenames to avoid duplicates
                for att in pre_fetched_attachments:
                    att_message_id = att.get("message_id")
                    att_id = att.get("id")
                    att_name = att.get("name", "")
                    att_content_type = att.get("contentType", "")
                    
                    # Skip inline images
                    if att.get("isInline"):
                        continue
                    
                    # Skip duplicates (same file forwarded in replies)
                    if att_name in seen_filenames:
                        logger.info(f"Skipping duplicate attachment: {att_name}")
                        continue
                    seen_filenames.add(att_name)
                    
                    # Download and process the attachment
                    if att_id and att_message_id:
                        attach_result = await self._process_attachments(
                            context.access_token,
                            att_message_id,
                            [att],
                            context.workflow_public_id,
                        )
                        if attach_result.get("texts"):
                            attachment_texts.extend(attach_result.get("texts", []))
                            attachment_blob_urls.extend(attach_result.get("blob_urls", []))
                            if not attachment_source_message_id:
                                attachment_source_message_id = att_message_id
                        if attach_result.get("extraction_failed"):
                            extraction_failed = True
            elif email.has_attachments:
                # Process attachments from the triggered email (fallback)
                attach_result = await self._process_attachments(
                    context.access_token,
                    message_id,
                    email.attachments,
                    context.workflow_public_id,
                )
                attachment_texts = attach_result.get("texts", [])
                attachment_blob_urls = attach_result.get("blob_urls", [])
                extraction_failed = attach_result.get("extraction_failed", False)
                attachment_source_message_id = message_id
            
            # If still no attachments, search conversation thread (last resort)
            if not attachment_texts and email.conversation_id and not pre_fetched_attachments:
                logger.info(f"No attachments found, searching conversation thread {email.conversation_id}")
                thread_result = await self._find_attachments_in_thread(
                    context.access_token,
                    email.conversation_id,
                    message_id,  # Exclude the triggered email
                    context.workflow_public_id,
                )
                if thread_result:
                    attachment_texts = thread_result.get("texts", [])
                    attachment_blob_urls = thread_result.get("blob_urls", [])
                    extraction_failed = thread_result.get("extraction_failed", False)
                    attachment_source_message_id = thread_result.get("source_message_id")
            
            # Step 3: Get historical context for the original sender (first email in thread)
            if pre_fetched_conversation:
                # Sort by date to find the original sender
                sorted_for_sender = sorted(
                    pre_fetched_conversation,
                    key=lambda m: m.get("received_at", "") or ""
                )
                first_msg = sorted_for_sender[0] if sorted_for_sender else None
                sender_email = first_msg.get("from_address") if first_msg else email.from_address
                logger.info(f"Using original sender from first email: {sender_email}")
            else:
                sender_email = email.from_address
            
            historical_context = await self._get_sender_history(
                tenant_id=context.tenant_id,
                sender_email=sender_email,
            )
            if historical_context:
                logger.info(f"Found historical context for sender {sender_email}")
            else:
                logger.info(f"No historical context for sender {sender_email}")
            
            # Step 3b: Get known vendors to help distinguish bill vs invoice
            known_vendors = await self._get_known_vendors(context.tenant_id)
            logger.info(f"Loaded {len(known_vendors)} known vendors for classification")
            
            # Step 4: Classify the email
            # Combine all conversation data for full context
            if pre_fetched_conversation:
                # Build full conversation text (oldest to newest)
                sorted_messages = sorted(
                    pre_fetched_conversation,
                    key=lambda m: m.get("received_at", "") or ""
                )
                conversation_parts = []
                for msg in sorted_messages:
                    msg_from = msg.get("from_name") or msg.get("from_address") or "Unknown"
                    msg_from_email = msg.get("from_address", "")
                    msg_to = ", ".join(msg.get("to_recipients", [])) or "Unknown"
                    msg_cc = ", ".join(msg.get("cc_recipients", []))
                    msg_date = msg.get("received_at", "")
                    msg_subject = msg.get("subject", "")
                    msg_body = msg.get("body", "")
                    msg_attachments = msg.get("attachments", [])
                    
                    # Build email header block
                    header_parts = [
                        f"From: {msg_from} <{msg_from_email}>",
                        f"To: {msg_to}",
                    ]
                    if msg_cc:
                        header_parts.append(f"CC: {msg_cc}")
                    header_parts.append(f"Date: {msg_date}")
                    header_parts.append(f"Subject: {msg_subject}")
                    if msg_attachments:
                        att_names = [a.get("name", "attachment") for a in msg_attachments]
                        header_parts.append(f"Attachments: {', '.join(att_names)}")
                    
                    email_block = "\n".join(header_parts)
                    if msg_body:
                        email_block += f"\n\n{msg_body}"
                    
                    conversation_parts.append(f"--- EMAIL MESSAGE ---\n{email_block}")
                
                full_email_body = "\n\n".join(conversation_parts)
                logger.info(f"Using full conversation ({len(sorted_messages)} messages) for classification")
            else:
                full_email_body = email.body
            
            combined_attachment_text = "\n\n---\n\n".join(attachment_texts) if attachment_texts else None
            
            classification_result = self.capabilities.llm.classify_email(
                email_body=full_email_body,
                extracted_text=combined_attachment_text,
                historical_context=historical_context,
                known_vendors=known_vendors,
            )
            
            if not classification_result.success:
                return AgentResult.fail(
                    f"Classification failed: {classification_result.error}"
                )
            
            classification: Classification = classification_result.data
            
            # Get entity config for the classified type
            entity_type = classification.entity_type
            entity_config = get_entity_config(entity_type)
            
            # Build simplified context updates (no vendor/project matching, no draft entity)
            context_updates = {
                "email": {
                    "message_id": email.id,
                    "conversation_id": email.conversation_id,
                    "subject": email.subject,
                    "from_address": email.from_address,
                    "from_name": email.from_name,
                    "received_at": email.received_datetime,
                    "body": email.body[:5000] if email.body else None,
                    "body_type": email.body_type,
                },
                "attachments": [
                    {
                        "id": a.get("id"),
                        "name": a.get("name"),
                        "content_type": a.get("contentType") or a.get("content_type"),
                        "size": a.get("size"),
                    }
                    for a in (email.attachments or [])
                ],
                "classification": {
                    "entity_type": classification.entity_type,
                    "confidence": classification.confidence,
                    "reasoning": classification.reasoning,
                },
                "entity_type": entity_type,
                "entity_label": entity_config.label,
                "entity_details_label": entity_config.details_label,
                "module": entity_config.module,
                "attachment_blob_urls": attachment_blob_urls,
                "attachment_source_message_id": attachment_source_message_id,
                "extraction_failed": extraction_failed,
            }
            
            # Determine next trigger - always go to awaiting_confirmation for user to confirm type
            if classification.confidence < 0.7:
                # Low confidence - needs review
                result = AgentResult.needs_human_input(
                    reason=f"Low confidence classification ({classification.confidence:.0%}). Please confirm entity type.",
                    data=classification,
                    context_updates=context_updates,
                )
            else:
                # High confidence - still go to confirmation but auto-suggest
                result = AgentResult.ok(
                    data=classification,
                    context_updates=context_updates,
                    next_trigger="classification_complete",
                )
            
            self._log_complete(result)
            return result
            
        except Exception as e:
            logger.exception(f"Error in {self.name}")
            return AgentResult.fail(str(e))
    
    async def _fetch_email(
        self,
        access_token: str,
        message_id: str,
    ) -> CapabilityResult:
        """Fetch email content."""
        return self.capabilities.email.get_message(
            access_token=access_token,
            message_id=message_id,
            include_attachments=True,
        )
    
    async def _process_attachments(
        self,
        access_token: str,
        message_id: str,
        attachments: List[Dict],
        workflow_public_id: Optional[str],
    ) -> Dict:
        """
        Process email attachments:
        1. Download each attachment
        2. Save to blob storage
        3. Extract text using Document Intelligence
        """
        texts = []
        blob_urls = []
        extraction_failed = False
        
        for attachment in attachments:
            attachment_id = attachment.get("id")
            if not attachment_id:
                continue
            
            # Skip inline images (email signatures, embedded images)
            if attachment.get("isInline"):
                continue
            
            content_type = attachment.get("contentType", "")
            name = attachment.get("name", "attachment")
            
            # Skip non-document attachments (only PDFs accepted)
            if not self._is_processable_attachment(content_type, name):
                continue
            
            # Download attachment
            download_result = self.capabilities.email.download_attachment(
                access_token=access_token,
                message_id=message_id,
                attachment_id=attachment_id,
            )
            
            if not download_result.success:
                logger.warning(f"Failed to download attachment {name}")
                continue
            
            content = download_result.data.get("content")
            if not content:
                continue
            
            # Save to blob storage
            if workflow_public_id:
                save_result = self.capabilities.storage.save_workflow_attachment(
                    workflow_public_id=workflow_public_id,
                    file_content=content,
                    filename=name,
                    content_type=content_type,
                )
                if save_result.success:
                    blob_urls.append(save_result.data.get("blob_url"))
            
            # Extract text
            extract_result = self.capabilities.document.extract_from_bytes(
                file_content=content,
                content_type=content_type,
            )
            
            if extract_result.success and extract_result.data:
                texts.append(extract_result.data.text)
            else:
                logger.warning(f"Failed to extract text from {name}")
                extraction_failed = True
        
        return {
            "texts": texts,
            "blob_urls": blob_urls,
            "extraction_failed": extraction_failed and not texts,  # Only fail if ALL extractions failed
        }
    
    def _is_processable_attachment(self, content_type: str, filename: str) -> bool:
        """Check if an attachment can be processed. Only PDFs are accepted."""
        # Only accept PDF files
        if "application/pdf" in content_type.lower():
            return True
        
        if filename.lower().endswith(".pdf"):
            return True
        
        return False
    
    async def _find_attachments_in_thread(
        self,
        access_token: str,
        conversation_id: str,
        exclude_message_id: str,
        workflow_public_id: Optional[str],
    ) -> Optional[Dict]:
        """
        Search conversation thread for attachments from prior emails.
        
        This handles the case where the triggered email (e.g., an approval reply)
        doesn't have attachments, but the original email in the thread does.
        
        Args:
            access_token: MS Graph access token
            conversation_id: The conversation thread ID
            exclude_message_id: Message ID to exclude (the triggered email)
            workflow_public_id: Workflow ID for blob storage
            
        Returns:
            Dict with texts, blob_urls, extraction_failed, source_message_id or None
        """
        # Fetch all messages in the conversation thread
        thread_result = self.capabilities.email.get_thread_messages(
            access_token=access_token,
            conversation_id=conversation_id,
        )
        
        if not thread_result.success:
            logger.warning(f"Failed to fetch conversation thread: {thread_result.error}")
            return None
        
        thread_messages: List[EmailMessage] = thread_result.data or []
        
        # Sort by received date (oldest first) to find the original email
        thread_messages.sort(key=lambda m: m.received_datetime or "")
        
        # Look for messages with attachments (excluding the triggered email)
        for msg in thread_messages:
            if msg.id == exclude_message_id:
                continue
            
            if not msg.has_attachments:
                continue
            
            logger.info(f"Found attachments in thread message {msg.id} (subject: {msg.subject})")
            
            # Fetch full message to get attachment details
            full_msg_result = self.capabilities.email.get_message(
                access_token=access_token,
                message_id=msg.id,
                include_attachments=True,
            )
            
            if not full_msg_result.success:
                logger.warning(f"Failed to fetch full message {msg.id}")
                continue
            
            full_msg: EmailMessage = full_msg_result.data
            
            if not full_msg.attachments:
                continue
            
            # Process attachments from this message
            attach_result = await self._process_attachments(
                access_token,
                msg.id,
                full_msg.attachments,
                workflow_public_id,
            )
            
            if attach_result.get("texts"):
                # Found processable attachments - return them
                return {
                    "texts": attach_result.get("texts", []),
                    "blob_urls": attach_result.get("blob_urls", []),
                    "extraction_failed": attach_result.get("extraction_failed", False),
                    "source_message_id": msg.id,
                }
        
        logger.info(f"No attachments found in conversation thread {conversation_id}")
        return None
    
    async def _get_known_vendors(self, tenant_id: int) -> List[str]:
        """Get list of known vendor names."""
        result = self.capabilities.entity.get_known_vendor_names(tenant_id)
        return result.data if result.success else []
    
    async def _get_known_projects(self, tenant_id: int) -> List[str]:
        """Get list of known project names."""
        result = self.capabilities.entity.get_known_project_names(tenant_id)
        return result.data if result.success else []
    
    async def _get_sender_history(
        self,
        tenant_id: int,
        sender_email: str,
    ) -> Optional[str]:
        """
        Get historical context for a sender email address.
        
        Looks up past workflows and bills from this sender to provide
        context that helps with classification.
        
        Args:
            tenant_id: Tenant ID
            sender_email: Email address of the sender
            
        Returns:
            Formatted string with historical context, or None if no history
        """
        if not sender_email:
            return None
        
        try:
            from agents.persistence.repo import WorkflowRepository
            from shared.database import get_connection
            
            # Query past workflows where email.from_address matches
            history_parts = []
            
            with get_connection() as conn:
                cursor = conn.cursor()
                
                # Get workflows from this sender
                cursor.execute("""
                    SELECT TOP 10
                        w.WorkflowType,
                        w.State,
                        w.Context,
                        w.CreatedAt
                    FROM agents.Workflow w
                    WHERE w.TenantId = ?
                    AND JSON_VALUE(w.Context, '$.email.from_address') = ?
                    ORDER BY w.CreatedAt DESC
                """, (tenant_id, sender_email))
                
                rows = cursor.fetchall()
                
                if rows:
                    # Analyze past workflows
                    categories = {}
                    vendors_matched = set()
                    projects_matched = set()
                    
                    for row in rows:
                        workflow_type = row[0]
                        state = row[1]
                        context_json = row[2]
                        created_at = row[3]
                        
                        try:
                            import json
                            ctx = json.loads(context_json) if context_json else {}
                            
                            # Count categories
                            classification = ctx.get("classification", {})
                            category = classification.get("category", "unknown")
                            categories[category] = categories.get(category, 0) + 1
                            
                            # Collect matched vendors
                            vendor_match = ctx.get("vendor_match", {})
                            if vendor_match.get("matched"):
                                vendor_name = vendor_match.get("vendor", {}).get("name")
                                if vendor_name:
                                    vendors_matched.add(vendor_name)
                            
                            # Collect matched projects
                            project_match = ctx.get("project_match", {})
                            if project_match.get("matched"):
                                project_name = project_match.get("project", {}).get("name")
                                if project_name:
                                    projects_matched.add(project_name)
                        except:
                            pass
                    
                    history_parts.append(f"SENDER HISTORY ({sender_email}):")
                    history_parts.append(f"- {len(rows)} previous emails processed")
                    
                    if categories:
                        cat_summary = ", ".join([f"{k}: {v}" for k, v in sorted(categories.items(), key=lambda x: -x[1])])
                        history_parts.append(f"- Classifications: {cat_summary}")
                    
                    if vendors_matched:
                        history_parts.append(f"- Matched vendors: {', '.join(list(vendors_matched)[:5])}")
                    
                    if projects_matched:
                        history_parts.append(f"- Matched projects: {', '.join(list(projects_matched)[:5])}")
                
                # Also get bill history if vendor was matched before
                if vendors_matched:
                    cursor.execute("""
                        SELECT TOP 5
                            b.BillNumber,
                            b.TotalAmount,
                            b.BillDate,
                            v.Name as VendorName
                        FROM bill.Bill b
                        JOIN vendor.Vendor v ON b.VendorId = v.Id
                        WHERE v.TenantId = ?
                        AND v.Name IN ({})
                        ORDER BY b.BillDate DESC
                    """.format(",".join(["?"] * len(vendors_matched))), 
                    (tenant_id, *list(vendors_matched)))
                    
                    bill_rows = cursor.fetchall()
                    if bill_rows:
                        history_parts.append(f"\nRECENT BILLS FROM THIS SENDER:")
                        for bill_row in bill_rows:
                            bill_num = bill_row[0] or "N/A"
                            amount = bill_row[1] or 0
                            date = bill_row[2] or "N/A"
                            vendor = bill_row[3] or "Unknown"
                            history_parts.append(f"- {vendor}: ${amount:,.2f} (#{bill_num}, {date})")
            
            if history_parts:
                return "\n".join(history_parts)
            return None
            
        except Exception as e:
            logger.warning(f"Failed to get sender history: {e}")
            return None
    
    async def _match_vendor(
        self,
        vendor_guess: Optional[str],
        from_address: str,
        tenant_id: int,
        email_subject: Optional[str] = None,
    ) -> Dict:
        """Match vendor by name or email."""
        # If no vendor guess from LLM, try to extract from email subject
        search_name = vendor_guess
        if not search_name and email_subject:
            # Simple extraction: look for common patterns like "VENDOR NAME INVOICE"
            match = re.search(r'^(?:Re:\s*)?(.+?)\s*(?:INVOICE|Invoice|INV|Bill|Statement)', email_subject, re.IGNORECASE)
            if match:
                search_name = match.group(1).strip()
                logger.info(f"Extracted vendor name from subject: {search_name}")
        
        if not search_name and not from_address:
            return {"matched": False, "candidates": []}
        
        result = self.capabilities.entity.match_vendor(
            name=search_name,
            email=from_address,
            tenant_id=tenant_id,
        )
        
        if not result.success:
            return {"matched": False, "error": result.error}
        
        data = result.data
        if data is None:
            return {"matched": False, "candidates": []}
        
        # Check if it's a direct match (dict) or candidates (list)
        if isinstance(data, dict):
            return {
                "matched": True,
                "vendor": data,
                "match_type": result.metadata.get("match_type"),
                "confidence": result.metadata.get("confidence", 1.0),
            }
        elif isinstance(data, list):
            candidates = [
                {
                    "id": c.id,
                    "public_id": c.public_id,
                    "name": c.name,
                    "confidence": c.confidence,
                }
                for c in data
            ]
            
            # Auto-match if top candidate has high confidence (>= 85%)
            if candidates and candidates[0]["confidence"] >= 0.85:
                top = candidates[0]
                return {
                    "matched": True,
                    "vendor": {
                        "id": top["id"],
                        "public_id": top["public_id"],
                        "name": top["name"],
                    },
                    "match_type": "fuzzy_high_confidence",
                    "confidence": top["confidence"],
                }
            
            return {
                "matched": False,
                "candidates": candidates,
            }
        
        return {"matched": False, "candidates": []}
    
    async def _match_project(
        self,
        project_guess: Optional[str],
        tenant_id: int,
    ) -> Dict:
        """Match project by name."""
        if not project_guess:
            return {"matched": False, "candidates": []}
        
        result = self.capabilities.entity.match_project(
            name=project_guess,
            tenant_id=tenant_id,
        )
        
        if not result.success:
            return {"matched": False, "error": result.error}
        
        data = result.data
        if data is None:
            return {"matched": False, "candidates": []}
        
        if isinstance(data, dict):
            return {
                "matched": True,
                "project": data,
                "match_type": result.metadata.get("match_type"),
                "confidence": result.metadata.get("confidence", 1.0),
            }
        elif isinstance(data, list):
            return {
                "matched": False,
                "candidates": [
                    {
                        "id": c.id,
                        "public_id": c.public_id,
                        "name": c.name,
                        "confidence": c.confidence,
                    }
                    for c in data
                ],
            }
        
        return {"matched": False, "candidates": []}
