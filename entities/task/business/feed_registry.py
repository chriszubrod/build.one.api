# Python Standard Library Imports
import logging
from typing import Optional

# Local Imports
from entities.task.business.feed import TaskFeed

logger = logging.getLogger(__name__)


_REGISTRY: dict[str, TaskFeed] = {}


def register(feed: TaskFeed) -> None:
    """
    Register a TaskFeed. Idempotent: re-registering the same task_type
    overwrites the prior entry (handy for tests + reloads). Call this at
    app startup, not at module import time, so import order can't bite.
    """
    if not getattr(feed, "task_type", None):
        raise ValueError("TaskFeed must declare a non-empty task_type")
    if feed.task_type in _REGISTRY:
        logger.info("task_feed.replace task_type=%s", feed.task_type)
    _REGISTRY[feed.task_type] = feed


def get(task_type: str) -> Optional[TaskFeed]:
    return _REGISTRY.get(task_type)


def all_feeds() -> list[TaskFeed]:
    return list(_REGISTRY.values())


def clear() -> None:
    """For tests."""
    _REGISTRY.clear()
