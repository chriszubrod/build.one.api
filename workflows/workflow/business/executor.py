# Python Standard Library Imports
import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional

# Third-party Imports
from jinja2 import Environment, FileSystemLoader

# Local Imports
from workflows.workflow.business.capabilities.registry import CapabilityRegistry, get_capability_registry
from workflows.workflow.business.definitions.base import WorkflowDefinition
from workflows.workflow.business.definitions.bill_intake import (
    BILL_INTAKE_WORKFLOW,
    EMAIL_INTAKE_WORKFLOW,
    EXPENSE_INTAKE_WORKFLOW,
    BILL_PROCESSING_WORKFLOW,
)
from workflows.workflow.business.exceptions import WorkflowStepError
from workflows.workflow.business.models import Workflow
from workflows.workflow.business.orchestrator import WorkflowOrchestrator
from workflows.workflow.persistence.repo import WorkflowRepository
from workflows.workflow_event.persistence.repo import WorkflowEventRepository
from workflows.workflow.business.agents.base import AgentContext, AgentResult
from workflows.workflow.business.agents.registry import AgentRegistry, get_agent_registry

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path(__file__).parent / "notifications" / "templates"


class BillIntakeExecutor:
    """
    Executor for the Bill Intake workflow.
    
    Coordinates the full workflow from email receipt to entity creation,
    including human approval steps.
    """
    
    def __init__(
        self,
        orchestrator: Optional[WorkflowOrchestrator] = None,
        capabilities: Optional[CapabilityRegistry] = None,
    ):
        self.orchestrator = orchestrator or WorkflowOrchestrator()
        self.capabilities = capabilities or get_capability_registry()
        
        # Register workflow definitions
        self.orchestrator.register_definition(BILL_INTAKE_WORKFLOW)
        self.orchestrator.register_definition(EMAIL_INTAKE_WORKFLOW)
        self.orchestrator.register_definition(EXPENSE_INTAKE_WORKFLOW)
        self.orchestrator.register_definition(BILL_PROCESSING_WORKFLOW)
        
        # Initialize agents via registry
        self.agents = get_agent_registry()
        
        # Initialize template engine
        if TEMPLATE_DIR.exists():
            self.jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))
        else:
            self.jinja_env = None
            logger.warning(f"Template directory not found: {TEMPLATE_DIR}")
    
    def _mark_conversation_read_and_unflag(
        self,
        conversation_id: Optional[str],
        conversation: Optional[List[Dict]],
    ) -> None:
        """
        Mark all messages in the conversation as read and remove flag.
        Used so the conversation drops out of Browse Inbox (flagged-only list).
        Sync helper; log failures, do not raise.
        """
        from integrations.ms.mail.external.client import (
            mark_message_read,
            unflag_message,
            search_all_messages,
        )
        message_ids: List[str] = []
        if conversation:
            for msg in conversation:
                mid = msg.get("message_id") or msg.get("id")
                if mid:
                    message_ids.append(mid)
        elif conversation_id:
            resp = search_all_messages(conversation_id=conversation_id, top=50)
            if resp.get("status_code") == 200:
                for m in resp.get("messages", []):
                    mid = m.get("message_id") or m.get("id")
                    if mid:
                        message_ids.append(mid)
        if not message_ids:
            return
        for message_id in message_ids:
            try:
                r = mark_message_read(message_id, True)
                if r.get("status_code") not in (200,):
                    logger.warning(
                        "mark_message_read failed for %s: %s",
                        message_id[:20] + "..." if len(message_id) > 20 else message_id,
                        r.get("message"),
                    )
            except Exception as e:
                logger.warning("mark_message_read error for message %s: %s", message_id, e)
            try:
                u = unflag_message(message_id)
                if u.get("status_code") not in (200, 204):
                    logger.warning(
                        "unflag_message failed for %s: %s",
                        message_id[:20] + "..." if len(message_id) > 20 else message_id,
                        u.get("message"),
                    )
            except Exception as e:
                logger.warning("unflag_message error for message %s: %s", message_id, e)
    
    async def start_from_email(
        self,
        tenant_id: int,
        access_token: str,
        message_id: str,
        conversation_id: Optional[str] = None,
        conversation: Optional[List[Dict]] = None,
        total_attachments: int = 0,
        workflow_type: str = "email_intake",
        preferred_module: Optional[str] = None,
    ) -> Workflow:
        """
        Start a new email intake workflow from a conversation.
        
        Args:
            tenant_id: Tenant ID
            access_token: MS Graph access token
            message_id: The selected/triggered email message ID
            conversation_id: Conversation ID for thread correlation
            conversation: Full conversation thread with all messages
            total_attachments: Total count of attachments across conversation
            workflow_type: email_intake (bill/invoice) or expense_intake
            
        Returns:
            Created workflow
        """
        print(f"[BillIntakeExecutor] start_from_email workflow_type={workflow_type} message_id={message_id[:24] if message_id else None}... tenant_id={tenant_id}")
        logger.info(f"Starting {workflow_type} workflow for message {message_id} with {len(conversation or [])} messages")
        
        # Build initial context with full conversation data
        initial_context = {
            "source": "email",
            "trigger_message_id": message_id,
            "conversation_id": conversation_id,
            "conversation": conversation or [],
            "message_count": len(conversation or []),
            "total_attachments": total_attachments,
        }
        if preferred_module == "bill":
            initial_context["confirmed_entity_type"] = "bill"
            initial_context["classification"] = {"entity_type": "bill"}
        
        # Extract the triggered email from conversation for quick access
        triggered_email = None
        for msg in (conversation or []):
            if msg.get("id") == message_id:
                triggered_email = msg
                break
        
        if triggered_email:
            initial_context["email"] = {
                "message_id": triggered_email.get("id"),
                "conversation_id": conversation_id,
                "subject": triggered_email.get("subject"),
                "from_address": triggered_email.get("from_address"),
                "from_name": triggered_email.get("from_name"),
                "received_at": triggered_email.get("received_at"),
                "body": triggered_email.get("body"),
                "body_type": triggered_email.get("body_type"),
            }
            initial_context["attachments"] = triggered_email.get("attachments", [])
        
        # Collect all attachments from conversation
        all_attachments = []
        for msg in (conversation or []):
            for att in msg.get("attachments", []):
                all_attachments.append({
                    "message_id": msg.get("id"),
                    "message_subject": msg.get("subject"),
                    **att,
                })
        initial_context["all_conversation_attachments"] = all_attachments
        
        # Create the workflow with selected type (email_intake or expense_intake)
        workflow, created = self.orchestrator.create_workflow(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            trigger_message_id=message_id,
            conversation_id=conversation_id,
            context=initial_context,
            created_by=f"{workflow_type}_executor",
        )
        # If INSERT...OUTPUT didn't return a row, workflow.id may be None; re-read by public_id
        if workflow.id is None and workflow.public_id:
            from workflows.workflow.persistence.repo import WorkflowRepository
            wf_repo = WorkflowRepository()
            refetched = wf_repo.read_by_public_id(workflow.public_id)
            if refetched:
                workflow = refetched
                print(f"[BillIntakeExecutor] re-read workflow by public_id id={workflow.id}")
        print(f"[BillIntakeExecutor] created workflow id={workflow.id} public_id={workflow.public_id} workflow_type={workflow.workflow_type} state={workflow.state} is_new={created}")
        # Create or update Task entry for list/detail UI (upsert handles duplicate workflow)
        task_created_or_updated = False
        task = None
        try:
            from entities.tasks.business.service import TaskService
            task = TaskService().upsert_task_for_workflow(
                workflow,
                source_type="email",
                source_id=workflow.conversation_id or (workflow.context or {}).get("conversation_id"),
            )
            if task:
                task_created_or_updated = True
                print(f"[BillIntakeExecutor] task created/updated task_id={task.public_id} workflow_id={workflow.public_id}")
                logger.info("Task %s linked to workflow %s", task.public_id, workflow.public_id)
            else:
                print(f"[BillIntakeExecutor] upsert_task_for_workflow returned None (workflow.id={workflow.id} workflow.tenant_id={workflow.tenant_id})")
        except Exception as e:
            print(f"[BillIntakeExecutor] Failed to create task for workflow {workflow.public_id}: {e}")
            logger.warning("Failed to create task for workflow %s: %s", workflow.public_id, e)
        # Mark conversation read and unflag so it drops out of Browse Inbox (fire-and-forget)
        if conversation_id or conversation:
            try:
                loop = asyncio.get_event_loop()
                loop.run_in_executor(
                    None,
                    lambda: self._mark_conversation_read_and_unflag(conversation_id, conversation),
                )
                logger.info("Marking conversation read and unflagging messages")
            except Exception as e:
                logger.warning("Failed to schedule mark read/unflag: %s", e)
        # Run triage in background only for newly created workflows (duplicate is already past triage)
        if created:
            asyncio.create_task(self._run_triage_background(workflow, access_token))
        # Duplicate workflow + user chose Bill but task has no bill yet -> ensure draft bill is created
        elif preferred_module == "bill" and task and not task.bill_id:
            if workflow.state == "received":
                # Triage never ran: set confirmed_entity_type and run triage so attachment_blob_urls get set, then spawn at end
                from workflows.workflow.persistence.repo import WorkflowRepository
                merged = dict(workflow.context or {})
                merged["confirmed_entity_type"] = "bill"
                merged["classification"] = {"entity_type": "bill"}
                wf_repo = WorkflowRepository()
                updated = wf_repo.update_context(workflow.public_id, merged)
                if updated:
                    logger.info("Duplicate workflow %s (received) has no bill; running triage then bill_processing", workflow.public_id)
                    asyncio.create_task(self._run_triage_background(updated, access_token))
                else:
                    asyncio.create_task(self.spawn_bill_processing(workflow, access_token))
            else:
                # Already classified: spawn bill_processing with existing context
                logger.info("Duplicate workflow %s has no bill; spawning bill_processing", workflow.public_id)
                asyncio.create_task(self.spawn_bill_processing(workflow, access_token))

        is_duplicate = not created
        return (workflow, is_duplicate, task_created_or_updated)
    
    async def _run_triage_background(
        self,
        workflow: Workflow,
        access_token: str,
    ) -> None:
        """
        Run triage in the background. Errors are logged, not raised.
        """
        try:
            await self.run_triage(workflow, access_token)
            logger.info(f"Background triage completed for workflow {workflow.public_id}")
        except Exception as e:
            logger.exception(f"Background triage failed for workflow {workflow.public_id}: {e}")
            # Try to transition to error state
            try:
                self.orchestrator.transition(
                    public_id=workflow.public_id,
                    trigger="classification_failed",
                    context_updates={"triage_error": str(e)},
                    created_by="email_intake_executor",
                )
            except Exception:
                logger.exception("Failed to transition workflow to error state")
    
    async def run_triage(
        self,
        workflow: Workflow,
        access_token: str,
    ) -> Workflow:
        """
        Run the email triage step.
        
        Transitions: received -> classifying -> classified
        """
        logger.info(f"Running triage for workflow {workflow.public_id}")
        
        # Transition to classifying
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger="start_classification",
            created_by="email_intake_executor",
        )
        
        # Run the email triage agent
        context = AgentContext(
            tenant_id=workflow.tenant_id,
            access_token=access_token,
            workflow_public_id=workflow.public_id,
            workflow_context=workflow.context,
            trigger_data={
                "message_id": workflow.trigger_message_id,
            },
        )
        
        result = await self.agents.email_triage.run(context)
        
        if not result.success:
            # Transition to needs_review on failure
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="classification_failed",
                context_updates={
                    "triage_error": result.error,
                },
                created_by="email_triage_agent",
            )
            # Sync task status
            try:
                from entities.tasks.business.service import TaskService
                TaskService().sync_status_from_workflow(workflow.id, workflow.state)
            except Exception as e:
                logger.warning("Failed to sync task status for workflow %s: %s", workflow.public_id, e)
            return workflow
        
        # Update workflow with triage results; preserve confirmed_entity_type if user pre-selected Bill
        context_updates = dict(result.context_updates or {})
        confirmed = (workflow.context or {}).get("confirmed_entity_type")
        if confirmed:
            context_updates["confirmed_entity_type"] = confirmed
            context_updates["classification"] = (workflow.context or {}).get("classification") or {"entity_type": confirmed}
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger="classification_complete",
            context_updates=context_updates,
            created_by="email_triage_agent",
        )

        # Sync task status
        try:
            from entities.tasks.business.service import TaskService
            TaskService().sync_status_from_workflow(workflow.id, workflow.state)
        except Exception as e:
            logger.warning("Failed to sync task status for workflow %s: %s", workflow.public_id, e)

        # Check if multiple bills detected - create child workflows
        detected_bills = result.context_updates.get("detected_bills", [])
        if len(detected_bills) > 1:
            await self._create_child_workflows(workflow, detected_bills, access_token)

        # When user chose Bill at Create Task, auto-confirm and spawn bill processing
        if (workflow.context or {}).get("confirmed_entity_type") == "bill":
            # Transition parent workflow: awaiting_confirmation → confirmed → completed
            try:
                workflow = self.orchestrator.transition(
                    public_id=workflow.public_id,
                    trigger="confirm_type",
                    context_updates={"confirmed_by": "auto", "auto_confirmed": True},
                    created_by="system",
                )
                workflow = self.orchestrator.transition(
                    public_id=workflow.public_id,
                    trigger="complete",
                    created_by="system",
                )
            except Exception as e:
                logger.warning("Auto-confirm transition failed for workflow %s: %s", workflow.public_id, e)

            # Sync task status after auto-confirm
            try:
                from entities.tasks.business.service import TaskService
                TaskService().sync_status_from_workflow(workflow.id, workflow.state)
            except Exception as e:
                logger.warning("Failed to sync task status after auto-confirm for workflow %s: %s", workflow.public_id, e)

            asyncio.create_task(self.spawn_bill_processing(workflow, access_token))

        return workflow
    
    async def _create_child_workflows(
        self,
        parent: Workflow,
        detected_bills: List[Dict],
        access_token: str,
    ) -> List[Workflow]:
        """Create child workflows for multi-bill emails."""
        children = []
        
        for i, bill_info in enumerate(detected_bills):
            child_context = {
                **parent.context,
                "parent_workflow_id": parent.public_id,
                "bill_index": i,
                "bill_info": bill_info,
            }
            
            child = self.orchestrator.create_child_workflow(
                parent=parent,
                context=child_context,
                created_by="email_intake_executor",
            )
            children.append(child)
            
            logger.info(f"Created child workflow {child.public_id} for bill {i+1}")
        
        return children
    
    async def send_approval_request(
        self,
        workflow: Workflow,
        access_token: str,
        approver_email: str,
    ) -> Workflow:
        """
        Send an approval request email and transition to awaiting_approval.
        
        Args:
            workflow: The workflow in 'classified' state
            access_token: MS Graph access token (delegated, send-as)
            approver_email: Email address to send approval request to
        """
        logger.info(f"Sending approval request for workflow {workflow.public_id}")
        
        ctx = workflow.context or {}
        classification = ctx.get("classification", {})
        vendor_match = ctx.get("vendor_match", {})
        project_match = ctx.get("project_match", {})
        
        # Render the approval request email
        email_body = self._render_template("approval_request.html", {
            "vendor_name": vendor_match.get("vendor", {}).get("name", "Unknown Vendor"),
            "invoice_number": classification.get("invoice_number"),
            "amount": classification.get("amount"),
            "invoice_date": classification.get("invoice_date"),
            "project_name": project_match.get("project", {}).get("name"),
            "project_confidence": project_match.get("confidence"),
            "detected_bills": ctx.get("detected_bills", []),
            "workflow_id": workflow.public_id,
        })
        
        # Get the original email subject for reply threading
        email_info = ctx.get("email", {})
        original_subject = email_info.get("subject", "Invoice")
        subject = f"RE: {original_subject} - ACTION NEEDED: Approval Request"
        
        # Send the email
        send_result = self.capabilities.email.send_as_user(
            access_token=access_token,
            to_recipients=[approver_email],
            subject=subject,
            body=email_body,
            body_type="html",
        )
        
        if not send_result.success:
            logger.error(f"Failed to send approval request: {send_result.error}")
            raise WorkflowStepError(
                f"Failed to send approval email: {send_result.error}",
                step_name="send_approval_request",
                retryable=True,
            )
        
        # Transition to awaiting_approval
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger="request_approval",
            context_updates={
                "approval_request": {
                    "sent_to": approver_email,
                    "sent_at": send_result.data.get("sent_at"),
                    "message_id": send_result.data.get("message_id"),
                },
                "reminder_count": 0,
            },
            created_by="email_intake_executor",
        )
        
        return workflow
    
    async def process_approval_reply(
        self,
        workflow: Workflow,
        access_token: str,
        reply_body: str,
        reply_subject: str = "",
        from_address: str = "",
    ) -> Workflow:
        """
        Process an approval reply and transition accordingly.
        
        Args:
            workflow: The workflow in 'awaiting_approval' state
            access_token: MS Graph access token
            reply_body: The reply email body
            reply_subject: The reply subject
            from_address: Who sent the reply
        """
        logger.info(f"Processing approval reply for workflow {workflow.public_id}")
        
        # Transition to parsing
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger="receive_reply",
            context_updates={
                "reply_received": {
                    "body": reply_body[:1000],  # Truncate for storage
                    "from": from_address,
                },
            },
            created_by="email_intake_executor",
        )
        
        # Run the approval parser agent
        context = AgentContext(
            tenant_id=workflow.tenant_id,
            access_token=access_token,
            workflow_public_id=workflow.public_id,
            workflow_context=workflow.context,
            trigger_data={
                "reply_body": reply_body,
                "reply_subject": reply_subject,
            },
        )
        
        result = await self.agents.approval_parser.run(context)
        
        if not result.success:
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="parse_failed",
                context_updates={"parse_error": result.error},
                created_by="approval_parser_agent",
            )
            return workflow
        
        # Transition based on parsing result
        next_trigger = result.next_trigger or "parse_failed"
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger=next_trigger,
            context_updates=result.context_updates,
            created_by="approval_parser_agent",
        )
        
        # If approved, continue to entity creation
        if next_trigger == "approval_granted":
            workflow = await self.create_entities(workflow, access_token)
        
        return workflow
    
    async def create_entities(
        self,
        workflow: Workflow,
        access_token: str,
    ) -> Workflow:
        """
        Create bill and related entities after approval.
        
        Transitions: approved -> creating_entities -> syncing -> completed
        """
        logger.info(f"Creating entities for workflow {workflow.public_id}")
        
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger="start_entity_creation",
            created_by="email_intake_executor",
        )
        
        ctx = workflow.context or {}
        classification = ctx.get("classification", {})
        vendor_match = ctx.get("vendor_match", {})
        project_match = ctx.get("project_match", {})
        approval = ctx.get("approval_response", {})
        
        try:
            # Step 1: Create the bill
            vendor_id = vendor_match.get("vendor", {}).get("id")
            project_id = approval.get("project_id") or project_match.get("project", {}).get("id")
            
            if not vendor_id:
                raise WorkflowStepError("No vendor ID available", step_name="create_bill")
            
            bill_result = self.capabilities.entity.create_bill(
                tenant_id=workflow.tenant_id,
                vendor_id=vendor_id,
                amount=classification.get("amount", 0),
                invoice_number=classification.get("invoice_number"),
                invoice_date=classification.get("invoice_date"),
                project_id=project_id,
                description=approval.get("notes"),
            )
            
            if not bill_result.success:
                raise WorkflowStepError(
                    f"Failed to create bill: {bill_result.error}",
                    step_name="create_bill",
                )
            
            bill = bill_result.data
            bill_id = bill.get("id")
            
            self.orchestrator.log_step(
                workflow_id=workflow.id,
                step_name="create_bill",
                data={"bill_id": bill_id, "already_existed": bill_result.metadata.get("already_existed")},
                created_by="email_intake_executor",
            )
            
            # Step 2: Upload to SharePoint (if project configured)
            sharepoint_result = None
            if project_id:
                attachment_urls = ctx.get("attachment_blob_urls", [])
                for blob_url in attachment_urls:
                    # Download from blob storage
                    download = self.capabilities.storage.download_blob(blob_url)
                    if download.success:
                        # Upload to SharePoint
                        filename = blob_url.split("/")[-1]
                        sp_result = self.capabilities.sharepoint.upload_to_project_folder(
                            access_token=access_token,
                            project_id=project_id,
                            filename=filename,
                            content=download.data["content"],
                            content_type=download.data.get("content_type", "application/pdf"),
                            subfolder="Invoices",
                        )
                        if sp_result.success:
                            sharepoint_result = sp_result.data
                            self.orchestrator.log_step(
                                workflow_id=workflow.id,
                                step_name="upload_to_sharepoint",
                                data=sp_result.data,
                                created_by="email_intake_executor",
                            )
            
            # Step 3: Append to worksheet (if configured)
            if project_id and bill:
                row = [
                    classification.get("invoice_date", ""),
                    vendor_match.get("vendor", {}).get("name", ""),
                    classification.get("invoice_number", ""),
                    classification.get("amount", 0),
                    approval.get("cost_code", ""),
                    approval.get("notes", ""),
                ]
                
                ws_result = self.capabilities.sharepoint.append_to_project_worksheet(
                    access_token=access_token,
                    project_id=project_id,
                    rows=[row],
                )
                
                if ws_result.success:
                    self.orchestrator.log_step(
                        workflow_id=workflow.id,
                        step_name="update_worksheet",
                        data=ws_result.data,
                        created_by="email_intake_executor",
                    )
            
            # Transition to syncing
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="entities_created",
                context_updates={
                    "created_bill_id": bill_id,
                    "sharepoint_file": sharepoint_result,
                },
                created_by="email_intake_executor",
            )

            # Sync task status
            try:
                from entities.tasks.business.service import TaskService
                TaskService().sync_status_from_workflow(workflow.id, workflow.state)
            except Exception as e:
                logger.warning("Failed to sync task status for workflow %s: %s", workflow.public_id, e)

            # Step 4: Sync to QBO (optional, non-blocking)
            await self.sync_to_qbo(workflow, access_token)

            return workflow
            
        except WorkflowStepError as e:
            logger.error(f"Entity creation failed: {e}")
            self.orchestrator.log_error(
                workflow_id=workflow.id,
                step_name=e.step_name or "create_entities",
                error=e,
                created_by="email_intake_executor",
            )
            
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="entity_creation_failed",
                context_updates={"entity_error": str(e)},
                created_by="email_intake_executor",
            )

            # Sync task status
            try:
                from entities.tasks.business.service import TaskService
                TaskService().sync_status_from_workflow(workflow.id, workflow.state)
            except Exception as e_sync:
                logger.warning("Failed to sync task status for workflow %s: %s", workflow.public_id, e_sync)

            return workflow
    
    async def sync_to_qbo(
        self,
        workflow: Workflow,
        access_token: str,
    ) -> Workflow:
        """
        Sync bill to QuickBooks Online.
        
        This step is optional - failure doesn't block the workflow.
        """
        logger.info(f"Syncing to QBO for workflow {workflow.public_id}")
        
        ctx = workflow.context or {}
        bill_id = ctx.get("created_bill_id")
        
        if not bill_id:
            logger.warning("No bill_id to sync")
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="sync_complete",
                context_updates={"qbo_sync": "skipped", "qbo_skip_reason": "no_bill_id"},
                created_by="email_intake_executor",
            )
            return workflow
        
        # TODO: Get QBO credentials from tenant config
        # For now, mark as skipped if not configured
        realm_id = None  # Would come from tenant settings
        qbo_token = None  # Would come from OAuth token storage
        
        if not realm_id or not qbo_token:
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="sync_complete",
                context_updates={"qbo_sync": "skipped", "qbo_skip_reason": "not_configured"},
                created_by="email_intake_executor",
            )
            return workflow
        
        # Attempt sync
        sync_result = self.capabilities.sync.push_bill_to_qbo(
            bill_id=bill_id,
            realm_id=realm_id,
            access_token=qbo_token,
        )
        
        if sync_result.success:
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="sync_complete",
                context_updates={
                    "qbo_sync": "completed",
                    "qbo_bill_id": sync_result.data.get("qbo_id"),
                },
                created_by="email_intake_executor",
            )
        else:
            # Sync failure is non-blocking - log and continue
            self.orchestrator.log_error(
                workflow_id=workflow.id,
                step_name="sync_to_qbo",
                error=Exception(sync_result.error),
                created_by="email_intake_executor",
            )
            
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="sync_complete",
                context_updates={
                    "qbo_sync": "failed",
                    "qbo_error": sync_result.error,
                },
                created_by="email_intake_executor",
            )
        
        return workflow
    
    async def send_reminder(
        self,
        workflow: Workflow,
        access_token: str,
    ) -> Workflow:
        """Send a reminder for a workflow awaiting approval."""
        logger.info(f"Sending reminder for workflow {workflow.public_id}")
        
        ctx = workflow.context or {}
        approval_request = ctx.get("approval_request", {})
        classification = ctx.get("classification", {})
        vendor_match = ctx.get("vendor_match", {})
        reminder_count = ctx.get("reminder_count", 0) + 1
        
        approver_email = approval_request.get("sent_to")
        if not approver_email:
            logger.warning("No approver email for reminder")
            return workflow
        
        # Render reminder email
        email_body = self._render_template("reminder.html", {
            "vendor_name": vendor_match.get("vendor", {}).get("name", "Unknown"),
            "invoice_number": classification.get("invoice_number"),
            "amount": classification.get("amount"),
            "days_waiting": reminder_count * 3,  # Approximate
            "original_request_date": approval_request.get("sent_at", "Unknown"),
            "reminder_count": reminder_count,
            "workflow_id": workflow.public_id,
        })
        
        # Send reminder
        send_result = self.capabilities.email.send_as_user(
            access_token=access_token,
            to_recipients=[approver_email],
            subject=f"REMINDER: Invoice Awaiting Approval - {classification.get('invoice_number', 'Invoice')}",
            body=email_body,
            body_type="html",
        )
        
        # Update reminder count (stays in awaiting_approval)
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger="send_reminder",
            context_updates={
                "reminder_count": reminder_count,
                "last_reminder_sent": send_result.data.get("sent_at") if send_result.success else None,
            },
            created_by="email_intake_executor",
        )
        
        return workflow
    
    def _render_template(self, template_name: str, context: Dict) -> str:
        """Render an email template."""
        if self.jinja_env is None:
            # Fallback to simple text
            return f"Invoice approval request. Details: {context}"
        
        try:
            template = self.jinja_env.get_template(template_name)
            return template.render(**context)
        except Exception as e:
            logger.error(f"Template rendering failed: {e}")
            return f"Invoice approval request. Details: {context}"
    
    # =========================================================================
    # Bill Processing Workflow
    # =========================================================================
    
    async def spawn_bill_processing(
        self,
        parent_workflow: Workflow,
        access_token: str,
    ) -> Workflow:
        """
        Spawn a bill_processing workflow from a confirmed email_intake workflow.
        
        This creates a child workflow that handles:
        - Extracting bill fields from documents
        - Matching vendor
        - Creating draft Bill entity
        
        Args:
            parent_workflow: The email_intake workflow that was confirmed as type=bill
            access_token: MS Graph access token
            
        Returns:
            Created bill_processing workflow
        """
        logger.info(f"Spawning bill_processing workflow from {parent_workflow.public_id}")
        
        parent_context = parent_workflow.context or {}
        
        # Build context for child workflow
        child_context = {
            "parent_workflow_id": parent_workflow.public_id,
            "parent_workflow_type": parent_workflow.workflow_type,
            "source": "email_intake",
            # Copy relevant data from parent
            "email": parent_context.get("email", {}),
            "attachments": parent_context.get("attachments", []),
            "attachment_blob_urls": parent_context.get("attachment_blob_urls", []),
            "conversation": parent_context.get("conversation", []),
            "conversation_id": parent_workflow.conversation_id,
            "trigger_message_id": parent_workflow.trigger_message_id,
            # Classification info
            "classification": parent_context.get("classification", {}),
            "confirmed_entity_type": parent_context.get("confirmed_entity_type", "bill"),
            "confirmed_by": parent_context.get("confirmed_by"),
            "confirmed_at": parent_context.get("confirmed_at"),
        }
        
        # Create the bill_processing workflow
        workflow, _ = self.orchestrator.create_workflow(
            tenant_id=parent_workflow.tenant_id,
            workflow_type="bill_processing",
            conversation_id=parent_workflow.conversation_id,
            trigger_message_id=parent_workflow.trigger_message_id,
            parent_workflow_id=parent_workflow.id,
            context=child_context,
            created_by="bill_intake_executor",
        )
        
        logger.info(f"Created bill_processing workflow {workflow.public_id}")
        
        # Run extraction in background
        asyncio.create_task(self._run_bill_processing_background(workflow, access_token))
        
        return workflow
    
    async def _run_bill_processing_background(
        self,
        workflow: Workflow,
        access_token: str,
    ) -> None:
        """
        Run bill processing steps in the background.
        
        Steps:
        1. Extract bill fields from documents
        2. Match vendor
        3. Create draft Bill
        """
        try:
            # Step 1: Extraction
            workflow = await self._run_bill_extraction(workflow, access_token)
            
            if workflow.state == "needs_review":
                logger.warning(f"Bill extraction failed for {workflow.public_id}")
                return
            
            # Step 2: Vendor matching
            workflow = await self._run_vendor_matching(workflow)
            
            if workflow.state == "needs_review":
                logger.warning(f"Vendor matching failed for {workflow.public_id}")
                return
            
            # Step 3: Create draft bill
            workflow = await self._create_draft_bill(workflow)
            
            logger.info(f"Bill processing completed for {workflow.public_id}")
            
        except Exception as e:
            logger.exception(f"Bill processing failed for {workflow.public_id}: {e}")
            try:
                self.orchestrator.transition(
                    public_id=workflow.public_id,
                    trigger="extraction_failed",
                    context_updates={"processing_error": str(e)},
                    created_by="bill_intake_executor",
                )
            except Exception:
                logger.exception("Failed to transition workflow to error state")
    
    async def _run_bill_extraction(
        self,
        workflow: Workflow,
        access_token: str,
    ) -> Workflow:
        """Run the bill extraction step."""
        logger.info(f"Running bill extraction for {workflow.public_id}")
        
        # Transition to extracting
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger="start_extraction",
            created_by="bill_intake_executor",
        )
        
        # Run the extraction agent
        context = AgentContext(
            tenant_id=workflow.tenant_id,
            access_token=access_token,
            workflow_public_id=workflow.public_id,
            workflow_context=workflow.context,
            trigger_data={},
        )
        
        result = await self.agents.bill_extraction.run(context)
        
        if not result.success:
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="extraction_failed",
                context_updates={"extraction_error": result.error},
                created_by="bill_extraction_agent",
            )
            return workflow
        
        # Update workflow with extraction results
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger="extraction_complete",
            context_updates=result.context_updates,
            created_by="bill_extraction_agent",
        )
        
        self.orchestrator.log_step(
            workflow_id=workflow.id,
            step_name="extract_bill_fields",
            data={"extracted": result.data},
            created_by="bill_extraction_agent",
        )
        
        return workflow
    
    async def _run_vendor_matching(self, workflow: Workflow) -> Workflow:
        """Run vendor matching step using VendorMatchAgent."""
        logger.info(f"Running vendor matching for {workflow.public_id}")
        
        ctx = workflow.context or {}
        extracted = ctx.get("extracted", {})
        vendor_name = extracted.get("vendor_name")
        
        if not vendor_name:
            # No vendor to match, proceed without match
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="vendor_not_found",
                context_updates={
                    "vendor_match": {"matched": False, "reason": "No vendor name extracted"},
                },
                created_by="bill_intake_executor",
            )
            return workflow
        
        # Use VendorMatchAgent for matching
        context = AgentContext(
            tenant_id=workflow.tenant_id,
            access_token="",  # Not needed for matching
            workflow_public_id=workflow.public_id,
            trigger_data={"vendor_name": vendor_name},
        )
        result = await self.agents.vendor_match.run(context)
        
        if result.success:
            vendor_match = result.data
            trigger = "vendor_matched" if vendor_match.get("matched") else "vendor_not_found"
        else:
            vendor_match = {"matched": False, "reason": result.error or "Match failed"}
            trigger = "vendor_not_found"
        
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger=trigger,
            context_updates={"vendor_match": vendor_match},
            created_by="bill_intake_executor",
        )
        
        self.orchestrator.log_step(
            workflow_id=workflow.id,
            step_name="match_vendor",
            data=vendor_match,
            created_by="vendor_match_agent",
        )
        
        return workflow
    
    async def _create_draft_bill(self, workflow: Workflow) -> Workflow:
        """Create a draft Bill entity from extracted data."""
        logger.info(f"Creating draft bill for {workflow.public_id}")
        
        ctx = workflow.context or {}
        extracted = ctx.get("extracted", {})
        vendor_match = ctx.get("vendor_match", {})
        
        # Get vendor ID if matched
        vendor_id = None
        if vendor_match.get("matched"):
            vendor_id = vendor_match.get("vendor", {}).get("id")
        
        # Get matched entities from extraction
        payment_term = extracted.get("payment_term", {})
        matched_project = extracted.get("matched_project", {})
        matched_sub_cost_code = extracted.get("matched_sub_cost_code", {})
        
        # Build memo from approval email: sender name + body text
        memo = self._build_approval_memo(ctx)
        
        bill_result = self.capabilities.entity.create_bill(
            tenant_id=workflow.tenant_id,
            vendor_id=vendor_id,
            amount=extracted.get("total_amount"),
            invoice_number=extracted.get("invoice_number"),
            invoice_date=extracted.get("bill_date"),  # Use bill_date from new extraction
            due_date=extracted.get("due_date"),
            description=memo,  # Approval text from final email
            line_items=extracted.get("line_items"),
            is_draft=True,
            # New fields from enhanced extraction
            payment_term_public_id=payment_term.get("public_id"),
            project_public_id=matched_project.get("public_id"),
            sub_cost_code_id=matched_sub_cost_code.get("id"),
            is_billable=extracted.get("is_billable", True),
        )
        
        if not bill_result.success:
            workflow = self.orchestrator.transition(
                public_id=workflow.public_id,
                trigger="draft_creation_failed",
                context_updates={
                    "draft_error": bill_result.error,
                },
                created_by="bill_intake_executor",
            )
            return workflow
        
        bill_data = bill_result.data
        
        # Link attachment to first line item if we have attachments
        attachment_linked = False
        attachment_blob_urls = ctx.get("attachment_blob_urls", [])
        first_line_item_public_id = bill_data.get("first_line_item_public_id")
        
        if first_line_item_public_id and attachment_blob_urls:
            # Get the first attachment (typically the invoice PDF)
            first_blob_url = attachment_blob_urls[0]
            
            # Get attachment info from context
            attachments = ctx.get("attachments", [])
            filename = "attachment.pdf"
            content_type = "application/pdf"
            file_size = None
            
            if attachments:
                first_attachment = attachments[0]
                filename = first_attachment.get("name", filename)
                content_type = first_attachment.get("content_type", content_type)
                file_size = first_attachment.get("size")
            
            # Link the attachment to the first bill line item
            link_result = self.capabilities.entity.link_attachment_to_bill_line_item(
                bill_line_item_public_id=first_line_item_public_id,
                blob_url=first_blob_url,
                filename=filename,
                content_type=content_type,
                file_size=file_size,
            )
            
            if link_result.success:
                attachment_linked = True
                logger.info(f"Linked attachment to first line item {first_line_item_public_id}")
            else:
                logger.warning(f"Failed to link attachment: {link_result.error}")
        
        # Link workflow to bill
        workflow = self.orchestrator.transition(
            public_id=workflow.public_id,
            trigger="draft_created",
            context_updates={
                "created_bill_id": bill_data.get("id"),
                "created_bill_public_id": bill_data.get("public_id"),
                "bill_already_existed": bill_result.metadata.get("already_existed", False),
                "attachment_linked": attachment_linked,
            },
            created_by="bill_intake_executor",
        )

        # Link parent (email_intake) task to this bill so list routes to bill
        parent_public_id = (workflow.context or {}).get("parent_workflow_id")
        if parent_public_id:
            try:
                from entities.tasks.business.service import TaskService
                TaskService().set_task_bill_for_parent_workflow(
                    parent_workflow_public_id=parent_public_id,
                    bill_id=bill_data.get("id"),
                    bill_public_id=bill_data.get("public_id"),
                )
            except Exception as e:
                logger.warning("Failed to link parent task to bill for %s: %s", parent_public_id, e)

        self.orchestrator.log_step(
            workflow_id=workflow.id,
            step_name="create_draft_bill",
            data={
                "bill_id": bill_data.get("id"),
                "bill_public_id": bill_data.get("public_id"),
                "vendor_id": vendor_id,
                "amount": extracted.get("total_amount"),
                "attachment_linked": attachment_linked,
            },
            created_by="bill_intake_executor",
        )
        
        logger.info(f"Created draft bill {bill_data.get('public_id')} for workflow {workflow.public_id}")
        
        return workflow
    
    def _build_approval_memo(self, ctx: dict) -> str:
        """
        Build memo from the approval email.
        
        Format:
            Sender Name
            Body text (cleaned)
        
        Example:
            Austin Rogers
            Approved.
            MR2 – Main
            25.0 – HVAC rough in
        """
        import re
        from html import unescape
        
        # Get the conversation - the last message is the approval
        conversation = ctx.get("conversation", [])
        if not conversation:
            # Fall back to extracted memo or line_description
            extracted = ctx.get("extracted", {})
            return extracted.get("memo") or extracted.get("line_description") or ""
        
        # Get the final/approval message (last in conversation)
        approval_msg = conversation[-1]
        
        # Get sender name
        sender_name = approval_msg.get("from_name") or approval_msg.get("from_address", "").split("@")[0]
        
        # Get body text and clean it
        body = approval_msg.get("body", "")
        body_type = approval_msg.get("body_type", "text")
        
        if body_type == "html":
            # Strip HTML tags, preserving line breaks
            body = re.sub(r'<br\s*/?>', '\n', body, flags=re.IGNORECASE)
            body = re.sub(r'</?p[^>]*>', '\n', body, flags=re.IGNORECASE)
            body = re.sub(r'</?div[^>]*>', '\n', body, flags=re.IGNORECASE)
            body = re.sub(r'<[^>]+>', '', body)
            body = unescape(body)
        
        # Split into lines, preserving empty lines for paragraph breaks
        lines = body.split('\n')
        
        # Remove common email artifacts (signatures, disclaimers, quoted replies)
        stop_phrases = [
            "sent from my", "get outlook", "confidential", "disclaimer",
            "this email", "unsubscribe", "________________________________"
        ]
        # Patterns that indicate quoted/forwarded email content
        quoted_email_patterns = [
            r'^from:\s*\S',  # "From: someone" - start of quoted email header
            r'^on\s+.+wrote:',  # "On [date], [name] wrote:"
            r'^-{3,}\s*original\s+message',  # "--- Original Message ---"
            r'^>{1,2}\s',  # "> " or ">> " quoted text
        ]
        
        cleaned_lines = []
        for line in lines:
            stripped_line = line.strip()
            lower_line = stripped_line.lower()
            
            # Stop at signature/disclaimer phrases
            if any(phrase in lower_line for phrase in stop_phrases):
                break
            
            # Stop at quoted email patterns
            if any(re.match(pattern, lower_line) for pattern in quoted_email_patterns):
                break
            
            cleaned_lines.append(stripped_line)
        
        # Join lines, then collapse multiple blank lines into single blank line
        body_text = '\n'.join(cleaned_lines)
        body_text = re.sub(r'\n{3,}', '\n\n', body_text).strip()
        
        # Build the memo
        if sender_name and body_text:
            return f"{sender_name}\n{body_text}"
        elif sender_name:
            return sender_name
        elif body_text:
            return body_text
        else:
            # Fall back to extracted data
            extracted = ctx.get("extracted", {})
            return extracted.get("memo") or extracted.get("line_description") or ""
