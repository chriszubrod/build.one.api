# Python Standard Library Imports
import base64
import logging
from typing import Optional
from decimal import Decimal

# Third-party Imports
import pyodbc

# Local Imports
from core.ai.agents.vendor_agent.business.models import (
    VendorAgentRun,
    VendorAgentProposal,
    VendorAgentProposalField,
    VendorAgentConversation,
)
from shared.database import (
    call_procedure,
    get_connection,
    map_database_error,
)

logger = logging.getLogger(__name__)


class VendorAgentRepository:
    """
    Repository for VendorAgent persistence operations.

    Handles all database interactions for:
    - VendorAgentRun
    - VendorAgentProposal
    - VendorAgentProposalField
    - VendorAgentConversation
    """

    def __init__(self):
        """Initialize the VendorAgentRepository."""
        pass

    # =========================================================================
    # VendorAgentRun Methods
    # =========================================================================

    def _run_from_db(self, row: pyodbc.Row) -> Optional[VendorAgentRun]:
        """Convert a database row into a VendorAgentRun dataclass."""
        if not row:
            return None

        try:
            return VendorAgentRun(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                tenant_id=row.TenantId,
                agent_type=row.AgentType,
                trigger_type=row.TriggerType,
                trigger_source=row.TriggerSource,
                status=row.Status,
                started_datetime=row.StartedDatetime,
                completed_datetime=row.CompletedDatetime,
                vendors_processed=row.VendorsProcessed or 0,
                proposals_created=row.ProposalsCreated or 0,
                error_count=row.ErrorCount or 0,
                context=row.Context,
                summary=row.Summary,
                created_by=row.CreatedBy,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during VendorAgentRun mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during VendorAgentRun mapping: {error}")
            raise map_database_error(error)

    def create_run(
        self,
        *,
        tenant_id: int,
        trigger_type: str,
        agent_type: str = "vendor_agent",
        trigger_source: Optional[str] = None,
        context: Optional[str] = None,
        created_by: Optional[str] = None,
    ) -> VendorAgentRun:
        """Create a new agent run record."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "TenantId": tenant_id,
                    "AgentType": agent_type,
                    "TriggerType": trigger_type,
                    "TriggerSource": trigger_source,
                    "Context": context,
                    "CreatedBy": created_by,
                }
                call_procedure(cursor=cursor, name="CreateVendorAgentRun", params=params)
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendorAgentRun did not return a row.")
                    raise map_database_error(Exception("CreateVendorAgentRun failed"))
                return self._run_from_db(row)
        except Exception as error:
            logger.error(f"Error during create agent run: {error}")
            raise map_database_error(error)

    def update_run_status(
        self,
        public_id: str,
        status: str,
        *,
        vendors_processed: Optional[int] = None,
        proposals_created: Optional[int] = None,
        error_count: Optional[int] = None,
        summary: Optional[str] = None,
    ) -> Optional[VendorAgentRun]:
        """Update an agent run's status and metrics."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "PublicId": public_id,
                    "Status": status,
                    "VendorsProcessed": vendors_processed,
                    "ProposalsCreated": proposals_created,
                    "ErrorCount": error_count,
                    "Summary": summary,
                }
                call_procedure(cursor=cursor, name="UpdateVendorAgentRunStatus", params=params)
                row = cursor.fetchone()
                return self._run_from_db(row)
        except Exception as error:
            logger.error(f"Error during update agent run status: {error}")
            raise map_database_error(error)

    def read_run_by_public_id(self, public_id: str) -> Optional[VendorAgentRun]:
        """Read an agent run by public ID."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorAgentRunByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._run_from_db(row)
        except Exception as error:
            logger.error(f"Error during read agent run by public ID: {error}")
            raise map_database_error(error)

    def read_runs_by_tenant(
        self,
        tenant_id: int,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> list[VendorAgentRun]:
        """Read agent runs for a tenant, optionally filtered by status."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "TenantId": tenant_id,
                    "Status": status,
                    "Limit": limit,
                }
                call_procedure(cursor=cursor, name="ReadVendorAgentRunsByTenant", params=params)
                rows = cursor.fetchall()
                return [self._run_from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read agent runs by tenant: {error}")
            raise map_database_error(error)

    # =========================================================================
    # VendorAgentProposal Methods
    # =========================================================================

    def _proposal_from_db(self, row: pyodbc.Row) -> Optional[VendorAgentProposal]:
        """Convert a database row into a VendorAgentProposal dataclass."""
        if not row:
            return None

        try:
            confidence = None
            if row.Confidence is not None:
                confidence = Decimal(str(row.Confidence))

            return VendorAgentProposal(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                tenant_id=row.TenantId,
                vendor_id=row.VendorId,
                agent_run_id=row.AgentRunId,
                status=row.Status,
                reasoning=row.Reasoning,
                confidence=confidence,
                responded_datetime=row.RespondedDatetime,
                responded_by=row.RespondedBy,
                rejection_reason=row.RejectionReason,
                applied_datetime=row.AppliedDatetime,
                applied_by=row.AppliedBy,
                context=row.Context,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during VendorAgentProposal mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during VendorAgentProposal mapping: {error}")
            raise map_database_error(error)

    def create_proposal(
        self,
        *,
        tenant_id: int,
        vendor_id: int,
        agent_run_id: int,
        reasoning: str,
        confidence: Optional[Decimal] = None,
        context: Optional[str] = None,
    ) -> VendorAgentProposal:
        """Create a new proposal for a vendor."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "TenantId": tenant_id,
                    "VendorId": vendor_id,
                    "AgentRunId": agent_run_id,
                    "Reasoning": reasoning,
                    "Confidence": float(confidence) if confidence is not None else None,
                    "Context": context,
                }
                call_procedure(cursor=cursor, name="CreateVendorAgentProposal", params=params)
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendorAgentProposal did not return a row.")
                    raise map_database_error(Exception("CreateVendorAgentProposal failed"))
                return self._proposal_from_db(row)
        except Exception as error:
            logger.error(f"Error during create proposal: {error}")
            raise map_database_error(error)

    def approve_proposal(self, public_id: str, responded_by: str) -> Optional[VendorAgentProposal]:
        """Approve a pending proposal."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "PublicId": public_id,
                    "RespondedBy": responded_by,
                }
                call_procedure(cursor=cursor, name="ApproveVendorAgentProposal", params=params)
                row = cursor.fetchone()
                return self._proposal_from_db(row)
        except Exception as error:
            logger.error(f"Error during approve proposal: {error}")
            raise map_database_error(error)

    def reject_proposal(
        self,
        public_id: str,
        responded_by: str,
        rejection_reason: str,
    ) -> Optional[VendorAgentProposal]:
        """Reject a pending proposal with a required explanation."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "PublicId": public_id,
                    "RespondedBy": responded_by,
                    "RejectionReason": rejection_reason,
                }
                call_procedure(cursor=cursor, name="RejectVendorAgentProposal", params=params)
                row = cursor.fetchone()
                return self._proposal_from_db(row)
        except Exception as error:
            logger.error(f"Error during reject proposal: {error}")
            raise map_database_error(error)

    def mark_proposal_applied(self, public_id: str, applied_by: str) -> Optional[VendorAgentProposal]:
        """Mark an approved proposal as applied."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "PublicId": public_id,
                    "AppliedBy": applied_by,
                }
                call_procedure(cursor=cursor, name="MarkVendorAgentProposalApplied", params=params)
                row = cursor.fetchone()
                return self._proposal_from_db(row)
        except Exception as error:
            logger.error(f"Error during mark proposal applied: {error}")
            raise map_database_error(error)

    def read_proposal_by_public_id(self, public_id: str) -> Optional[VendorAgentProposal]:
        """Read a proposal by public ID."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorAgentProposalByPublicId",
                    params={"PublicId": public_id},
                )
                row = cursor.fetchone()
                return self._proposal_from_db(row)
        except Exception as error:
            logger.error(f"Error during read proposal by public ID: {error}")
            raise map_database_error(error)

    def read_proposals_by_vendor(
        self,
        vendor_id: int,
        status: Optional[str] = None,
    ) -> list[VendorAgentProposal]:
        """Read proposals for a vendor, optionally filtered by status."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "VendorId": vendor_id,
                    "Status": status,
                }
                call_procedure(cursor=cursor, name="ReadVendorAgentProposalsByVendor", params=params)
                rows = cursor.fetchall()
                return [self._proposal_from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read proposals by vendor: {error}")
            raise map_database_error(error)

    def read_pending_proposals(self, tenant_id: int) -> list[VendorAgentProposal]:
        """Read all pending proposals for a tenant."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadPendingVendorAgentProposals",
                    params={"TenantId": tenant_id},
                )
                rows = cursor.fetchall()
                return [self._proposal_from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read pending proposals: {error}")
            raise map_database_error(error)

    def read_rejected_proposals(self, vendor_id: int) -> list[VendorAgentProposal]:
        """Read rejected proposals for a vendor (for agent learning)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadRejectedVendorAgentProposals",
                    params={"VendorId": vendor_id},
                )
                rows = cursor.fetchall()
                return [self._proposal_from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read rejected proposals: {error}")
            raise map_database_error(error)

    # =========================================================================
    # VendorAgentProposalField Methods
    # =========================================================================

    def _proposal_field_from_db(self, row: pyodbc.Row) -> Optional[VendorAgentProposalField]:
        """Convert a database row into a VendorAgentProposalField dataclass."""
        if not row:
            return None

        try:
            return VendorAgentProposalField(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                proposal_id=row.ProposalId,
                field_name=row.FieldName,
                old_value=row.OldValue,
                new_value=row.NewValue,
                old_display_value=row.OldDisplayValue,
                new_display_value=row.NewDisplayValue,
                field_reasoning=row.FieldReasoning,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during VendorAgentProposalField mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during VendorAgentProposalField mapping: {error}")
            raise map_database_error(error)

    def create_proposal_field(
        self,
        *,
        proposal_id: int,
        field_name: str,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        old_display_value: Optional[str] = None,
        new_display_value: Optional[str] = None,
        field_reasoning: Optional[str] = None,
    ) -> VendorAgentProposalField:
        """Create a field change record for a proposal."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "ProposalId": proposal_id,
                    "FieldName": field_name,
                    "OldValue": old_value,
                    "NewValue": new_value,
                    "OldDisplayValue": old_display_value,
                    "NewDisplayValue": new_display_value,
                    "FieldReasoning": field_reasoning,
                }
                call_procedure(cursor=cursor, name="CreateVendorAgentProposalField", params=params)
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendorAgentProposalField did not return a row.")
                    raise map_database_error(Exception("CreateVendorAgentProposalField failed"))
                return self._proposal_field_from_db(row)
        except Exception as error:
            logger.error(f"Error during create proposal field: {error}")
            raise map_database_error(error)

    def read_proposal_fields(self, proposal_id: int) -> list[VendorAgentProposalField]:
        """Read all field changes for a proposal."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ReadVendorAgentProposalFields",
                    params={"ProposalId": proposal_id},
                )
                rows = cursor.fetchall()
                return [self._proposal_field_from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read proposal fields: {error}")
            raise map_database_error(error)

    # =========================================================================
    # VendorAgentConversation Methods
    # =========================================================================

    def _conversation_from_db(self, row: pyodbc.Row) -> Optional[VendorAgentConversation]:
        """Convert a database row into a VendorAgentConversation dataclass."""
        if not row:
            return None

        try:
            return VendorAgentConversation(
                id=row.Id,
                public_id=str(row.PublicId) if row.PublicId else None,
                row_version=base64.b64encode(row.RowVersion).decode("ascii") if row.RowVersion else None,
                created_datetime=row.CreatedDatetime,
                modified_datetime=row.ModifiedDatetime,
                tenant_id=row.TenantId,
                vendor_id=row.VendorId,
                role=row.Role,
                content=row.Content,
                message_type=row.MessageType,
                agent_run_id=row.AgentRunId,
                proposal_id=row.ProposalId,
                metadata=row.Metadata,
            )
        except AttributeError as error:
            logger.error(f"Attribute error during VendorAgentConversation mapping: {error}")
            raise map_database_error(error)
        except Exception as error:
            logger.error(f"Unexpected error during VendorAgentConversation mapping: {error}")
            raise map_database_error(error)

    def create_conversation_message(
        self,
        *,
        tenant_id: int,
        vendor_id: int,
        role: str,
        content: str,
        message_type: Optional[str] = None,
        agent_run_id: Optional[int] = None,
        proposal_id: Optional[int] = None,
        metadata: Optional[str] = None,
    ) -> VendorAgentConversation:
        """Create a conversation message for a vendor."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "TenantId": tenant_id,
                    "VendorId": vendor_id,
                    "Role": role,
                    "Content": content,
                    "MessageType": message_type,
                    "AgentRunId": agent_run_id,
                    "ProposalId": proposal_id,
                    "Metadata": metadata,
                }
                call_procedure(cursor=cursor, name="CreateVendorAgentConversationMessage", params=params)
                row = cursor.fetchone()
                if not row:
                    logger.error("CreateVendorAgentConversationMessage did not return a row.")
                    raise map_database_error(Exception("CreateVendorAgentConversationMessage failed"))
                return self._conversation_from_db(row)
        except Exception as error:
            logger.error(f"Error during create conversation message: {error}")
            raise map_database_error(error)

    def read_conversation(self, vendor_id: int, limit: int = 100) -> list[VendorAgentConversation]:
        """Read the full conversation history for a vendor."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "VendorId": vendor_id,
                    "Limit": limit,
                }
                call_procedure(cursor=cursor, name="ReadVendorAgentConversation", params=params)
                rows = cursor.fetchall()
                return [self._conversation_from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read conversation: {error}")
            raise map_database_error(error)

    def read_recent_conversation(self, vendor_id: int, limit: int = 20) -> list[VendorAgentConversation]:
        """Read recent conversation messages for context window."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                params = {
                    "VendorId": vendor_id,
                    "Limit": limit,
                }
                call_procedure(cursor=cursor, name="ReadRecentVendorAgentConversation", params=params)
                rows = cursor.fetchall()
                return [self._conversation_from_db(row) for row in rows if row]
        except Exception as error:
            logger.error(f"Error during read recent conversation: {error}")
            raise map_database_error(error)

    def clear_conversation(self, vendor_id: int) -> None:
        """Clear conversation history for a vendor (fresh start)."""
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                call_procedure(
                    cursor=cursor,
                    name="ClearVendorAgentConversation",
                    params={"VendorId": vendor_id},
                )
        except Exception as error:
            logger.error(f"Error during clear conversation: {error}")
            raise map_database_error(error)
