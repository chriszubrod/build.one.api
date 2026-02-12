# Python Standard Library Imports
from dataclasses import asdict, dataclass, field
from typing import Optional
from decimal import Decimal
import base64
import json

# Third-party Imports

# Local Imports


@dataclass
class VendorAgentRun:
    """
    Tracks each execution/invocation of the VendorAgent.

    An agent run represents a single execution cycle where the agent
    processes one or more vendors and potentially creates proposals.
    """
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    tenant_id: Optional[int] = None
    agent_type: Optional[str] = "vendor_agent"
    trigger_type: Optional[str] = None  # 'scheduled', 'event', 'manual'
    trigger_source: Optional[str] = None  # e.g., 'vendor_created', 'daily_review'
    status: Optional[str] = "running"  # 'running', 'completed', 'failed', 'cancelled'
    started_datetime: Optional[str] = None
    completed_datetime: Optional[str] = None
    vendors_processed: int = 0
    proposals_created: int = 0
    error_count: int = 0
    context: Optional[str] = None  # JSON string
    summary: Optional[str] = None  # JSON string
    created_by: Optional[str] = None

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    @property
    def is_running(self) -> bool:
        return self.status == "running"

    @property
    def is_completed(self) -> bool:
        return self.status in ("completed", "failed", "cancelled")

    def get_context_value(self, key: str, default=None):
        """Get a value from the JSON context."""
        if not self.context:
            return default
        try:
            ctx = json.loads(self.context)
            return ctx.get(key, default)
        except (json.JSONDecodeError, TypeError):
            return default

    def set_context_value(self, key: str, value) -> None:
        """Set a value in the JSON context."""
        try:
            ctx = json.loads(self.context) if self.context else {}
        except (json.JSONDecodeError, TypeError):
            ctx = {}
        ctx[key] = value
        self.context = json.dumps(ctx)

    def get_summary_value(self, key: str, default=None):
        """Get a value from the JSON summary."""
        if not self.summary:
            return default
        try:
            s = json.loads(self.summary)
            return s.get(key, default)
        except (json.JSONDecodeError, TypeError):
            return default

    def to_dict(self) -> dict:
        """Convert the dataclass to a dictionary."""
        return asdict(self)


@dataclass
class VendorAgentProposal:
    """
    A vendor-level proposal containing one or more field changes.

    Proposals are created by agent runs and require human approval
    before changes are applied to the vendor record.
    """
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    tenant_id: Optional[int] = None
    vendor_id: Optional[int] = None
    agent_run_id: Optional[int] = None
    status: Optional[str] = "pending"  # 'pending', 'approved', 'rejected', 'expired', 'applied'
    reasoning: Optional[str] = None
    confidence: Optional[Decimal] = None  # 0.00 to 1.00
    responded_datetime: Optional[str] = None
    responded_by: Optional[str] = None
    rejection_reason: Optional[str] = None
    applied_datetime: Optional[str] = None
    applied_by: Optional[str] = None
    context: Optional[str] = None  # JSON string

    # Related field changes (populated by service layer)
    fields: list = field(default_factory=list)

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    @property
    def is_pending(self) -> bool:
        return self.status == "pending"

    @property
    def is_approved(self) -> bool:
        return self.status == "approved"

    @property
    def is_rejected(self) -> bool:
        return self.status == "rejected"

    @property
    def is_applied(self) -> bool:
        return self.status == "applied"

    @property
    def awaiting_application(self) -> bool:
        """Returns True if approved but not yet applied."""
        return self.status == "approved" and self.applied_datetime is None

    def get_context_value(self, key: str, default=None):
        """Get a value from the JSON context."""
        if not self.context:
            return default
        try:
            ctx = json.loads(self.context)
            return ctx.get(key, default)
        except (json.JSONDecodeError, TypeError):
            return default

    def to_dict(self) -> dict:
        """Convert the dataclass to a dictionary."""
        result = asdict(self)
        # Convert Decimal to float for JSON serialization
        if result.get("confidence") is not None:
            result["confidence"] = float(result["confidence"])
        return result


@dataclass
class VendorAgentProposalField:
    """
    An individual field change within a proposal.

    Each record represents a single field that the agent proposes to change.
    Multiple fields can be changed in a single proposal.
    """
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    proposal_id: Optional[int] = None
    field_name: Optional[str] = None  # e.g., 'vendor_type_id', 'name'
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    old_display_value: Optional[str] = None  # Human-readable
    new_display_value: Optional[str] = None  # Human-readable
    field_reasoning: Optional[str] = None  # Optional per-field explanation

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    @property
    def has_change(self) -> bool:
        """Returns True if old and new values are different."""
        return self.old_value != self.new_value

    def to_dict(self) -> dict:
        """Convert the dataclass to a dictionary."""
        return asdict(self)


@dataclass
class VendorAgentConversation:
    """
    A message in the vendor-scoped conversation history.

    Conversation history provides context continuity across agent runs
    and enables the agent to learn from past interactions with a vendor.
    """
    id: Optional[int] = None
    public_id: Optional[str] = None
    row_version: Optional[str] = None
    created_datetime: Optional[str] = None
    modified_datetime: Optional[str] = None
    tenant_id: Optional[int] = None
    vendor_id: Optional[int] = None
    role: Optional[str] = None  # 'system', 'agent', 'user', 'tool'
    content: Optional[str] = None
    message_type: Optional[str] = None  # 'reasoning', 'proposal', 'approval', 'rejection', etc.
    agent_run_id: Optional[int] = None
    proposal_id: Optional[int] = None
    metadata: Optional[str] = None  # JSON string

    @property
    def row_version_bytes(self) -> Optional[bytes]:
        if self.row_version:
            return base64.b64decode(self.row_version)
        return None

    @property
    def row_version_hex(self) -> Optional[str]:
        if self.row_version_bytes:
            return self.row_version_bytes.hex()
        return None

    @property
    def is_from_agent(self) -> bool:
        return self.role == "agent"

    @property
    def is_from_user(self) -> bool:
        return self.role == "user"

    @property
    def is_tool_message(self) -> bool:
        return self.role == "tool"

    def get_metadata_value(self, key: str, default=None):
        """Get a value from the JSON metadata."""
        if not self.metadata:
            return default
        try:
            m = json.loads(self.metadata)
            return m.get(key, default)
        except (json.JSONDecodeError, TypeError):
            return default

    def set_metadata_value(self, key: str, value) -> None:
        """Set a value in the JSON metadata."""
        try:
            m = json.loads(self.metadata) if self.metadata else {}
        except (json.JSONDecodeError, TypeError):
            m = {}
        m[key] = value
        self.metadata = json.dumps(m)

    def to_dict(self) -> dict:
        """Convert the dataclass to a dictionary."""
        return asdict(self)
