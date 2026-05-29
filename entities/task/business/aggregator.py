# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from entities.task.business import feed_registry
from entities.task.business.feed import TaskCount, TaskRow

logger = logging.getLogger(__name__)


class TaskAggregator:
    """
    Read-only aggregator. Calls every registered TaskFeed and merges the
    results into a single inbox response.

    v1 has a single feed (Review) which already does its own SQL-side paging
    + sorting in one sproc. When a second feed lands we'll need to rethink
    cross-feed paging — see the in-line note in `read_inbox`.
    """

    def read_inbox(
        self,
        *,
        current_user_id: int,
        is_system_admin: bool,
        scope: str,
        task_type: Optional[str],
        entity_type: Optional[str],
        status_public_id: Optional[str],
        page: int,
        page_size: int,
    ) -> list[TaskRow]:
        feeds = self._feeds_for(task_type)
        if not feeds:
            return []

        # v1: one feed, so its sproc handles paging. With multiple feeds we
        # need to either fetch everything + page in Python (cheap if small)
        # or coordinate per-feed offsets. Revisit when the second feed lands.
        rows: list[TaskRow] = []
        for feed in feeds:
            rows.extend(
                feed.read_inbox(
                    current_user_id=current_user_id,
                    is_system_admin=is_system_admin,
                    scope=scope,
                    entity_type=entity_type,
                    status_public_id=status_public_id,
                    page=page,
                    page_size=page_size,
                )
            )

        # Defensive cross-feed sort: feeds may return in their own order.
        rows.sort(key=lambda r: (r.last_activity_at, r.parent_id), reverse=True)
        return rows

    def read_counts(
        self,
        *,
        current_user_id: int,
        is_system_admin: bool,
        task_type: Optional[str] = None,
    ) -> list[TaskCount]:
        feeds = self._feeds_for(task_type)
        counts: list[TaskCount] = []
        for feed in feeds:
            counts.extend(
                feed.read_counts(
                    current_user_id=current_user_id,
                    is_system_admin=is_system_admin,
                )
            )
        return counts

    def _feeds_for(self, task_type: Optional[str]):
        if task_type:
            f = feed_registry.get(task_type)
            return [f] if f is not None else []
        return feed_registry.all_feeds()
