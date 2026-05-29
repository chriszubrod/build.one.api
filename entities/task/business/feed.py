# Python Standard Library Imports
from dataclasses import dataclass, asdict
from decimal import Decimal
from typing import Optional, Protocol


@dataclass
class TaskRow:
    """
    Uniform row shape emitted by every TaskFeed. The Task inbox renders these
    rows directly without branching on entity type. New task types translate
    their native data into this shape at the feed boundary.
    """
    task_type: str                       # e.g. "Review" — registry discriminator
    entity_type: str                     # e.g. "Bill" | "Expense" | "BillCredit" | "Invoice"
    is_credit: bool                      # Expense.IsCredit flag (false for non-Expense)
    parent_public_id: str
    parent_id: int
    parent_number: Optional[str]
    counterparty_name: Optional[str]
    amount: Optional[Decimal]
    review_public_id: Optional[str]
    status_name: str
    status_color: Optional[str]
    status_sort_order: int
    status_is_final: bool
    status_is_declined: bool
    submitter_firstname: Optional[str]
    submitter_lastname: Optional[str]
    last_activity_at: str                # ISO-8601 string
    assigned_to_me: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.amount is not None:
            d["amount"] = str(self.amount)
        return d


@dataclass
class TaskCount:
    task_type: str
    entity_type: str
    is_credit: bool
    mine: int                # assigned to me (PM/Owner on the project)
    total: int               # visible in 'all' scope (any UserProject membership)
    mine_submitted: int      # submitted by me (sent-box)


class TaskFeed(Protocol):
    """
    Contract for a Task feed. Each concrete feed lives next to the entity it
    surfaces tasks for (e.g. `entities/review/business/task_feed.py`) and
    self-registers via `feed_registry.register()` at startup.

    `task_type` is the registry key — must be unique across all feeds.
    """
    task_type: str

    def read_inbox(
        self,
        *,
        current_user_id: int,
        is_system_admin: bool,
        scope: str,
        entity_type: Optional[str],
        status_public_id: Optional[str],
        page: int,
        page_size: int,
    ) -> list[TaskRow]: ...

    def read_counts(
        self,
        *,
        current_user_id: int,
        is_system_admin: bool,
    ) -> list[TaskCount]: ...
