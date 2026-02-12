# Python Standard Library Imports
import json
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports

# Local Imports
from core.ai.agents.vendor_agent.business.models import (
    VendorAgentRun,
    VendorAgentProposal,
    VendorAgentProposalField,
    VendorAgentConversation,
)
from core.ai.agents.vendor_agent.persistence.repo import VendorAgentRepository
from entities.vendor.business.service import VendorService
from entities.vendor.business.model import Vendor

logger = logging.getLogger(__name__)


class VendorAgentService:
    """
    Service for VendorAgent business operations.

    Handles:
    - Agent run lifecycle management
    - Proposal creation and approval workflow
    - Conversation history management
    - Application of approved changes
    """

    def __init__(self, repo: Optional[VendorAgentRepository] = None):
        """Initialize the VendorAgentService."""
        self.repo = repo or VendorAgentRepository()

    # =========================================================================
    # Agent Run Management
    # =========================================================================

    def start_run(
        self,
        *,
        tenant_id: int,
        trigger_type: str,
        trigger_source: Optional[str] = None,
        context: Optional[dict] = None,
        created_by: Optional[str] = None,
    ) -> VendorAgentRun:
        """
        Start a new agent run.

        Args:
            tenant_id: Tenant ID for multi-tenant isolation
            trigger_type: How the run was triggered ('scheduled', 'event', 'manual')
            trigger_source: Source details (e.g., 'vendor_created', 'daily_review')
            context: Optional context dictionary (will be JSON serialized)
            created_by: User or system that initiated the run

        Returns:
            The created VendorAgentRun instance
        """
        context_json = json.dumps(context) if context else None
        return self.repo.create_run(
            tenant_id=tenant_id,
            trigger_type=trigger_type,
            trigger_source=trigger_source,
            context=context_json,
            created_by=created_by,
        )

    def complete_run(
        self,
        run_public_id: str,
        *,
        vendors_processed: int = 0,
        proposals_created: int = 0,
        error_count: int = 0,
        summary: Optional[dict] = None,
    ) -> Optional[VendorAgentRun]:
        """
        Mark an agent run as completed.

        Args:
            run_public_id: Public ID of the run to complete
            vendors_processed: Number of vendors processed
            proposals_created: Number of proposals created
            error_count: Number of errors encountered
            summary: Optional summary dictionary

        Returns:
            The updated VendorAgentRun instance
        """
        summary_json = json.dumps(summary) if summary else None
        return self.repo.update_run_status(
            run_public_id,
            status="completed",
            vendors_processed=vendors_processed,
            proposals_created=proposals_created,
            error_count=error_count,
            summary=summary_json,
        )

    def fail_run(
        self,
        run_public_id: str,
        *,
        error_count: int = 1,
        summary: Optional[dict] = None,
    ) -> Optional[VendorAgentRun]:
        """
        Mark an agent run as failed.

        Args:
            run_public_id: Public ID of the run to fail
            error_count: Number of errors encountered
            summary: Optional summary dictionary with error details

        Returns:
            The updated VendorAgentRun instance
        """
        summary_json = json.dumps(summary) if summary else None
        return self.repo.update_run_status(
            run_public_id,
            status="failed",
            error_count=error_count,
            summary=summary_json,
        )

    def get_run(self, public_id: str) -> Optional[VendorAgentRun]:
        """Get an agent run by public ID."""
        return self.repo.read_run_by_public_id(public_id)

    def get_runs(
        self,
        tenant_id: int,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[VendorAgentRun]:
        """Get agent runs for a tenant."""
        return self.repo.read_runs_by_tenant(tenant_id, status=status, limit=limit)

    # =========================================================================
    # Proposal Management
    # =========================================================================

    def create_proposal(
        self,
        *,
        tenant_id: int,
        vendor_id: int,
        agent_run_id: int,
        reasoning: str,
        field_changes: list[dict],
        confidence: Optional[float] = None,
        context: Optional[dict] = None,
    ) -> VendorAgentProposal:
        """
        Create a proposal with field changes.

        Args:
            tenant_id: Tenant ID
            vendor_id: Database ID of the vendor
            agent_run_id: Database ID of the agent run creating this proposal
            reasoning: Overall reasoning for the proposal
            field_changes: List of field change dicts with keys:
                - field_name: Name of the field
                - old_value: Current value (optional)
                - new_value: Proposed value (optional)
                - old_display_value: Human-readable current (optional)
                - new_display_value: Human-readable proposed (optional)
                - field_reasoning: Per-field explanation (optional)
            confidence: Confidence score 0.0-1.0 (optional)
            context: Additional context dictionary (optional)

        Returns:
            The created VendorAgentProposal with fields populated
        """
        context_json = json.dumps(context) if context else None
        confidence_decimal = Decimal(str(confidence)) if confidence is not None else None

        # Create the proposal
        proposal = self.repo.create_proposal(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            agent_run_id=agent_run_id,
            reasoning=reasoning,
            confidence=confidence_decimal,
            context=context_json,
        )

        # Create field changes
        fields = []
        for fc in field_changes:
            field = self.repo.create_proposal_field(
                proposal_id=proposal.id,
                field_name=fc.get("field_name"),
                old_value=fc.get("old_value"),
                new_value=fc.get("new_value"),
                old_display_value=fc.get("old_display_value"),
                new_display_value=fc.get("new_display_value"),
                field_reasoning=fc.get("field_reasoning"),
            )
            fields.append(field)

        proposal.fields = fields
        return proposal

    def get_proposal(self, public_id: str, include_fields: bool = True) -> Optional[VendorAgentProposal]:
        """
        Get a proposal by public ID.

        Args:
            public_id: Public ID of the proposal
            include_fields: Whether to include field changes

        Returns:
            The proposal with optional fields populated
        """
        proposal = self.repo.read_proposal_by_public_id(public_id)
        if proposal and include_fields:
            proposal.fields = self.repo.read_proposal_fields(proposal.id)
        return proposal

    def get_proposals_for_vendor(
        self,
        vendor_id: int,
        status: Optional[str] = None,
        include_fields: bool = True,
    ) -> list[VendorAgentProposal]:
        """
        Get proposals for a vendor.

        Args:
            vendor_id: Database ID of the vendor
            status: Optional status filter
            include_fields: Whether to include field changes

        Returns:
            List of proposals
        """
        proposals = self.repo.read_proposals_by_vendor(vendor_id, status=status)
        if include_fields:
            for proposal in proposals:
                proposal.fields = self.repo.read_proposal_fields(proposal.id)
        return proposals

    def get_pending_proposals(
        self,
        tenant_id: int,
        include_fields: bool = True,
    ) -> list[VendorAgentProposal]:
        """
        Get all pending proposals for a tenant.

        Args:
            tenant_id: Tenant ID
            include_fields: Whether to include field changes

        Returns:
            List of pending proposals
        """
        proposals = self.repo.read_pending_proposals(tenant_id)
        if include_fields:
            for proposal in proposals:
                proposal.fields = self.repo.read_proposal_fields(proposal.id)
        return proposals

    def get_rejected_proposals_for_learning(
        self,
        vendor_id: int,
        include_fields: bool = True,
    ) -> list[VendorAgentProposal]:
        """
        Get rejected proposals for a vendor (for agent learning).

        This enables the agent to learn from past rejections and avoid
        making similar proposals.

        Args:
            vendor_id: Database ID of the vendor
            include_fields: Whether to include field changes

        Returns:
            List of rejected proposals with rejection reasons
        """
        proposals = self.repo.read_rejected_proposals(vendor_id)
        if include_fields:
            for proposal in proposals:
                proposal.fields = self.repo.read_proposal_fields(proposal.id)
        return proposals

    def approve_proposal(self, public_id: str, approved_by: str) -> Optional[VendorAgentProposal]:
        """
        Approve a pending proposal.

        Args:
            public_id: Public ID of the proposal
            approved_by: User who approved

        Returns:
            The updated proposal
        """
        proposal = self.repo.approve_proposal(public_id, responded_by=approved_by)
        if proposal:
            proposal.fields = self.repo.read_proposal_fields(proposal.id)
        return proposal

    def reject_proposal(
        self,
        public_id: str,
        rejected_by: str,
        rejection_reason: str,
    ) -> Optional[VendorAgentProposal]:
        """
        Reject a pending proposal with a required explanation.

        The rejection reason is stored for the agent to learn from.

        Args:
            public_id: Public ID of the proposal
            rejected_by: User who rejected
            rejection_reason: Required explanation for rejection

        Returns:
            The updated proposal
        """
        if not rejection_reason or not rejection_reason.strip():
            raise ValueError("Rejection reason is required")

        proposal = self.repo.reject_proposal(
            public_id,
            responded_by=rejected_by,
            rejection_reason=rejection_reason,
        )
        if proposal:
            proposal.fields = self.repo.read_proposal_fields(proposal.id)
            # Log rejection to conversation for learning
            self._log_rejection_to_conversation(proposal, rejected_by)
        return proposal

    def apply_approved_proposal(
        self,
        proposal_public_id: str,
        applied_by: str,
    ) -> tuple[Optional[VendorAgentProposal], Optional[Vendor]]:
        """
        Apply an approved proposal to the vendor.

        This method:
        1. Validates the proposal is approved
        2. Applies the field changes to the vendor
        3. Marks the proposal as applied
        4. Logs the application to conversation

        Args:
            proposal_public_id: Public ID of the proposal
            applied_by: User or system applying the changes

        Returns:
            Tuple of (updated proposal, updated vendor)
        """
        proposal = self.get_proposal(proposal_public_id, include_fields=True)
        if not proposal:
            logger.warning(f"Proposal {proposal_public_id} not found")
            return None, None

        if not proposal.is_approved:
            logger.warning(f"Proposal {proposal_public_id} is not approved (status: {proposal.status})")
            raise ValueError(f"Proposal must be approved before applying (current status: {proposal.status})")

        # Get the vendor
        vendor_service = VendorService()
        vendor = vendor_service.read_by_id(proposal.vendor_id)
        if not vendor:
            logger.error(f"Vendor {proposal.vendor_id} not found for proposal {proposal_public_id}")
            raise ValueError(f"Vendor not found")

        # Build update kwargs from field changes
        update_kwargs = {"row_version": vendor.row_version}
        for field in proposal.fields:
            if field.field_name == "vendor_type_id":
                # For vendor_type_id, we store the public_id in new_value
                update_kwargs["vendor_type_public_id"] = field.new_value
            elif field.field_name == "name":
                update_kwargs["name"] = field.new_value
            elif field.field_name == "abbreviation":
                update_kwargs["abbreviation"] = field.new_value
            elif field.field_name == "is_draft":
                update_kwargs["is_draft"] = field.new_value.lower() == "true" if field.new_value else None
            # Add more field mappings as needed

        # Apply the update
        updated_vendor = vendor_service.update_by_public_id(
            vendor.public_id,
            **update_kwargs,
        )

        # Mark proposal as applied
        updated_proposal = self.repo.mark_proposal_applied(proposal_public_id, applied_by=applied_by)
        if updated_proposal:
            updated_proposal.fields = proposal.fields

        # Log to conversation
        self._log_application_to_conversation(updated_proposal, applied_by)

        return updated_proposal, updated_vendor

    # =========================================================================
    # Conversation Management
    # =========================================================================

    def add_agent_message(
        self,
        *,
        tenant_id: int,
        vendor_id: int,
        content: str,
        message_type: Optional[str] = None,
        agent_run_id: Optional[int] = None,
        proposal_id: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> VendorAgentConversation:
        """Add a message from the agent to the conversation."""
        metadata_json = json.dumps(metadata) if metadata else None
        return self.repo.create_conversation_message(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            role="agent",
            content=content,
            message_type=message_type,
            agent_run_id=agent_run_id,
            proposal_id=proposal_id,
            metadata=metadata_json,
        )

    def add_user_message(
        self,
        *,
        tenant_id: int,
        vendor_id: int,
        content: str,
        message_type: Optional[str] = None,
        proposal_id: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> VendorAgentConversation:
        """Add a message from the user to the conversation."""
        metadata_json = json.dumps(metadata) if metadata else None
        return self.repo.create_conversation_message(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            role="user",
            content=content,
            message_type=message_type,
            proposal_id=proposal_id,
            metadata=metadata_json,
        )

    def add_system_message(
        self,
        *,
        tenant_id: int,
        vendor_id: int,
        content: str,
        message_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> VendorAgentConversation:
        """Add a system message to the conversation."""
        metadata_json = json.dumps(metadata) if metadata else None
        return self.repo.create_conversation_message(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            role="system",
            content=content,
            message_type=message_type,
            metadata=metadata_json,
        )

    def add_tool_message(
        self,
        *,
        tenant_id: int,
        vendor_id: int,
        content: str,
        agent_run_id: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> VendorAgentConversation:
        """Add a tool result message to the conversation."""
        metadata_json = json.dumps(metadata) if metadata else None
        return self.repo.create_conversation_message(
            tenant_id=tenant_id,
            vendor_id=vendor_id,
            role="tool",
            content=content,
            message_type="tool_result",
            agent_run_id=agent_run_id,
            metadata=metadata_json,
        )

    def get_conversation(self, vendor_id: int, limit: int = 100) -> list[VendorAgentConversation]:
        """Get full conversation history for a vendor."""
        return self.repo.read_conversation(vendor_id, limit=limit)

    def get_recent_conversation(self, vendor_id: int, limit: int = 20) -> list[VendorAgentConversation]:
        """Get recent conversation for context window."""
        return self.repo.read_recent_conversation(vendor_id, limit=limit)

    def clear_conversation(self, vendor_id: int) -> None:
        """Clear conversation history for a vendor (fresh start)."""
        self.repo.clear_conversation(vendor_id)

    def get_conversation_context_for_agent(
        self,
        vendor_id: int,
        limit: int = 20,
    ) -> list[dict]:
        """
        Get conversation history formatted for LLM context.

        Returns messages in a format suitable for passing to the LLM.

        Args:
            vendor_id: Database ID of the vendor
            limit: Maximum number of recent messages

        Returns:
            List of message dicts with 'role' and 'content' keys
        """
        messages = self.get_recent_conversation(vendor_id, limit=limit)
        return [
            {
                "role": msg.role if msg.role != "agent" else "assistant",
                "content": msg.content,
            }
            for msg in messages
        ]

    # =========================================================================
    # Private Helper Methods
    # =========================================================================

    def _log_rejection_to_conversation(
        self,
        proposal: VendorAgentProposal,
        rejected_by: str,
    ) -> None:
        """Log a rejection to the conversation for agent learning."""
        try:
            content = f"Proposal rejected by {rejected_by}. Reason: {proposal.rejection_reason}"
            self.repo.create_conversation_message(
                tenant_id=proposal.tenant_id,
                vendor_id=proposal.vendor_id,
                role="user",
                content=content,
                message_type="rejection",
                proposal_id=proposal.id,
            )
        except Exception as e:
            logger.warning(f"Failed to log rejection to conversation: {e}")

    def _log_application_to_conversation(
        self,
        proposal: VendorAgentProposal,
        applied_by: str,
    ) -> None:
        """Log a proposal application to the conversation."""
        try:
            field_summary = ", ".join(
                f"{f.field_name}: {f.old_display_value or f.old_value} → {f.new_display_value or f.new_value}"
                for f in proposal.fields
            )
            content = f"Proposal applied by {applied_by}. Changes: {field_summary}"
            self.repo.create_conversation_message(
                tenant_id=proposal.tenant_id,
                vendor_id=proposal.vendor_id,
                role="system",
                content=content,
                message_type="applied",
                proposal_id=proposal.id,
            )
        except Exception as e:
            logger.warning(f"Failed to log application to conversation: {e}")
