# Python Standard Library Imports
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

"""
Pending actions require user approval before execution.

Agents emit pending actions instead of performing writes; the orchestrator
pauses and the UI shows an approval list. On approve, an executor runs the
action payload; on reject, the action is discarded.
"""

# Action types (extend as needed)
ACTION_UPDATE_TAXPAYER = "update_taxpayer"
ACTION_BACKFILL_W9 = "backfill_w9"
ACTION_BACKFILL_COI = "backfill_coi"
ACTION_BACKFILL_BL = "backfill_bl"


@dataclass
class PendingAction:
    """
    A single action waiting for user approval.
    """
    id: str
    type: str  # ACTION_* constant
    summary: str
    payload: Dict[str, Any]
    created_at: str  # ISO 8601

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "summary": self.summary,
            "payload": self.payload,
            "created_at": self.created_at,
        }


def create_pending_action(
    action_type: str,
    summary: str,
    payload: Dict[str, Any],
    id: Optional[str] = None,
) -> PendingAction:
    """Create a pending action with a new id and timestamp."""
    return PendingAction(
        id=id or str(uuid.uuid4()),
        type=action_type,
        summary=summary,
        payload=payload,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


def merge_pending_actions(
    existing: Optional[List[Dict[str, Any]]],
    new: List[PendingAction],
) -> List[Dict[str, Any]]:
    """
    Merge new pending actions into existing list (e.g. in workflow_context).
    Returns a list of dicts suitable for context_updates["pending_actions"].
    """
    out = list(existing) if existing else []
    for pa in new:
        out.append(pa.to_dict())
    return out
