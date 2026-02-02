# Python Standard Library Imports
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set

# Local Imports
from workflows.workflow.business.capabilities.registry import get_capability_registry
from workflows.workflow.business.executor import BillIntakeExecutor
from workflows.workflow.business.models import Workflow
from workflows.workflow.business.orchestrator import WorkflowOrchestrator
from workflows.workflow.persistence.repo import WorkflowRepository
from workflows.workflow.business.agents.correlation import CorrelationAgent

logger = logging.getLogger(__name__)


class WorkflowScheduler:
    """
    Scheduler for polling emails and managing workflow timeouts.
    
    Responsibilities:
    - Poll inbox for new qualifying emails
    - Check for replies to approval requests
    - Send reminders for stale workflows
    - Archive abandoned workflows
    """
    
    def __init__(
        self,
        orchestrator: Optional[WorkflowOrchestrator] = None,
    ):
        self.orchestrator = orchestrator or WorkflowOrchestrator()
        self.workflow_repo = WorkflowRepository()
        self.capabilities = get_capability_registry()
        self.executor = BillIntakeExecutor(self.orchestrator, self.capabilities)
        self.correlation_agent = CorrelationAgent(self.capabilities)
        
        # Track processed message IDs to avoid duplicates
        self._processed_messages: Set[str] = set()
    
    async def poll_inbox(
        self,
        tenant_id: int,
        access_token: str,
        since: Optional[str] = None,
        known_vendor_emails: Optional[List[str]] = None,
    ) -> List[Workflow]:
        """
        Poll inbox for new qualifying emails and start workflows.
        
        Args:
            tenant_id: Tenant ID
            access_token: MS Graph access token
            since: ISO datetime to filter messages after
            known_vendor_emails: List of known vendor email addresses
            
        Returns:
            List of created workflows
        """
        logger.info(f"Polling inbox for tenant {tenant_id}")
        
        # Get known vendor emails if not provided
        if known_vendor_emails is None:
            vendor_result = self.capabilities.entity.get_known_vendor_emails(tenant_id)
            known_vendor_emails = vendor_result.data if vendor_result.success else []
        
        # Fetch new messages with attachments
        messages_result = self.capabilities.email.get_new_messages(
            access_token=access_token,
            folder="inbox",
            since=since,
            has_attachments=True,
            from_addresses=known_vendor_emails if known_vendor_emails else None,
            top=50,
        )
        
        if not messages_result.success:
            logger.error(f"Failed to fetch messages: {messages_result.error}")
            return []
        
        messages = messages_result.data
        logger.info(f"Found {len(messages)} qualifying messages")
        
        created_workflows = []
        
        for message in messages:
            # Skip already processed messages
            if message.id in self._processed_messages:
                continue
            
            # Check if workflow already exists for this message
            existing = self.workflow_repo.read_by_trigger_message_id(message.id)
            if existing:
                self._processed_messages.add(message.id)
                continue
            
            try:
                workflow = await self.executor.start_from_email(
                    tenant_id=tenant_id,
                    access_token=access_token,
                    message_id=message.id,
                    conversation_id=message.conversation_id,
                    workflow_type="email_intake",
                )
                created_workflows.append(workflow)
                self._processed_messages.add(message.id)
                logger.info(f"Created workflow {workflow.public_id} for message {message.id}")
                
            except Exception as e:
                logger.exception(f"Failed to process message {message.id}: {e}")
        
        return created_workflows
    
    async def check_for_replies(
        self,
        tenant_id: int,
        access_token: str,
    ) -> List[Workflow]:
        """
        Check for replies to approval requests and process them.
        
        Returns:
            List of updated workflows
        """
        logger.info(f"Checking for approval replies for tenant {tenant_id}")
        
        # Get workflows awaiting approval
        awaiting = self.workflow_repo.read_by_tenant_and_state(
            tenant_id=tenant_id,
            state="awaiting_approval",
        )
        
        if not awaiting:
            logger.info("No workflows awaiting approval")
            return []
        
        updated_workflows = []
        
        for workflow in awaiting:
            ctx = workflow.context or {}
            conversation_id = workflow.conversation_id
            
            if not conversation_id:
                continue
            
            # Get messages in this thread
            thread_result = self.capabilities.email.get_thread_messages(
                access_token=access_token,
                conversation_id=conversation_id,
            )
            
            if not thread_result.success:
                continue
            
            messages = thread_result.data
            
            # Find new replies (after approval request was sent)
            approval_request = ctx.get("approval_request", {})
            sent_at = approval_request.get("sent_at")
            
            for message in messages:
                # Skip messages we sent
                if message.id == approval_request.get("message_id"):
                    continue
                
                # Skip already processed replies
                processed_replies = ctx.get("processed_reply_ids", [])
                if message.id in processed_replies:
                    continue
                
                # Process this reply
                try:
                    updated = await self.executor.process_approval_reply(
                        workflow=workflow,
                        access_token=access_token,
                        reply_body=message.body,
                        reply_subject=message.subject,
                        from_address=message.from_address,
                    )
                    
                    updated_workflows.append(updated)
                    
                    # Mark reply as processed
                    processed_replies.append(message.id)
                    self.workflow_repo.update_context(
                        public_id=workflow.public_id,
                        context={**ctx, "processed_reply_ids": processed_replies},
                    )
                    
                    logger.info(f"Processed reply for workflow {workflow.public_id}")
                    break  # Process one reply at a time
                    
                except Exception as e:
                    logger.exception(f"Failed to process reply for workflow {workflow.public_id}: {e}")
        
        return updated_workflows
    
    async def check_orphan_emails(
        self,
        tenant_id: int,
        access_token: str,
        since: Optional[str] = None,
    ) -> List[Workflow]:
        """
        Check for orphan emails that might be replies to existing workflows.
        
        These are emails that don't match by conversation ID but might
        be responses to our approval requests.
        """
        logger.info(f"Checking for orphan emails for tenant {tenant_id}")
        
        # Get recent messages
        messages_result = self.capabilities.email.get_new_messages(
            access_token=access_token,
            folder="inbox",
            since=since,
            top=50,
        )
        
        if not messages_result.success:
            return []
        
        messages = messages_result.data
        updated_workflows = []
        
        for message in messages:
            # Skip if already processed
            if message.id in self._processed_messages:
                continue
            
            # Skip if we have an exact conversation match
            if message.conversation_id:
                matching = self.workflow_repo.read_by_conversation_id(message.conversation_id)
                if matching:
                    continue
            
            # Try to correlate using the agent
            from workflows.workflow.business.agents.base import AgentContext
            
            context = AgentContext(
                tenant_id=tenant_id,
                access_token=access_token,
                trigger_data={
                    "email_body": message.body,
                    "subject": message.subject,
                    "from_address": message.from_address,
                },
            )
            
            result = await self.correlation_agent.run(context)
            
            if result.success and result.data.get("matched_workflow_id"):
                matched_id = result.data["matched_workflow_id"]
                confidence = result.data.get("confidence", 0)
                
                if confidence >= 0.7:
                    # Process as a reply to the matched workflow
                    workflow = self.workflow_repo.read_by_public_id(matched_id)
                    if workflow and workflow.state == "awaiting_approval":
                        updated = await self.executor.process_approval_reply(
                            workflow=workflow,
                            access_token=access_token,
                            reply_body=message.body,
                            reply_subject=message.subject,
                            from_address=message.from_address,
                        )
                        updated_workflows.append(updated)
                        
                        logger.info(
                            f"Correlated orphan email to workflow {matched_id} "
                            f"(confidence: {confidence:.0%})"
                        )
            
            self._processed_messages.add(message.id)
        
        return updated_workflows
    
    async def process_timeouts(
        self,
        tenant_id: int,
        access_token: str,
        reminder_days: int = 3,
        abandon_days: int = 30,
    ) -> Dict[str, int]:
        """
        Process workflow timeouts - send reminders and abandon stale workflows.
        
        Args:
            tenant_id: Tenant ID
            access_token: MS Graph access token
            reminder_days: Days before sending a reminder
            abandon_days: Days before abandoning a workflow
            
        Returns:
            Dict with counts of reminders sent and workflows abandoned
        """
        logger.info(f"Processing timeouts for tenant {tenant_id}")
        
        results = {"reminders_sent": 0, "abandoned": 0}
        
        # Get workflows past reminder threshold
        reminder_workflows = self.workflow_repo.read_past_timeout(
            tenant_id=tenant_id,
            state="awaiting_approval",
            timeout_days=reminder_days,
        )
        
        for workflow in reminder_workflows:
            ctx = workflow.context or {}
            reminder_count = ctx.get("reminder_count", 0)
            
            # Check if past abandon threshold
            days_waiting = reminder_count * reminder_days + reminder_days
            if days_waiting >= abandon_days:
                # Abandon the workflow
                self.orchestrator.transition(
                    public_id=workflow.public_id,
                    trigger="timeout_abandon",
                    context_updates={"abandon_reason": "timeout"},
                    created_by="workflow_scheduler",
                )
                results["abandoned"] += 1
                logger.info(f"Abandoned workflow {workflow.public_id} after {days_waiting} days")
            else:
                # Send reminder
                try:
                    await self.executor.send_reminder(workflow, access_token)
                    results["reminders_sent"] += 1
                except Exception as e:
                    logger.error(f"Failed to send reminder for {workflow.public_id}: {e}")
        
        return results
    
    async def run_full_cycle(
        self,
        tenant_id: int,
        access_token: str,
        since: Optional[str] = None,
    ) -> Dict:
        """
        Run a full polling and processing cycle.
        
        This is the main entry point for scheduled runs.
        """
        logger.info(f"Running full cycle for tenant {tenant_id}")
        
        results = {
            "new_workflows": 0,
            "replies_processed": 0,
            "orphans_matched": 0,
            "reminders_sent": 0,
            "abandoned": 0,
        }
        
        # 1. Poll for new emails
        new_workflows = await self.poll_inbox(tenant_id, access_token, since)
        results["new_workflows"] = len(new_workflows)
        
        # 2. Check for replies
        reply_workflows = await self.check_for_replies(tenant_id, access_token)
        results["replies_processed"] = len(reply_workflows)
        
        # 3. Check orphan emails
        orphan_workflows = await self.check_orphan_emails(tenant_id, access_token, since)
        results["orphans_matched"] = len(orphan_workflows)
        
        # 4. Process timeouts
        timeout_results = await self.process_timeouts(tenant_id, access_token)
        results["reminders_sent"] = timeout_results["reminders_sent"]
        results["abandoned"] = timeout_results["abandoned"]
        
        logger.info(f"Cycle complete: {results}")
        
        return results
