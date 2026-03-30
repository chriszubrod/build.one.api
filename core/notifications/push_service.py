"""
core/notifications/push_service.py
====================================
High-level push notification service.

Combines APNsService (the HTTP/2 transport layer) with DeviceTokenRepository
(the database layer) to send notifications to Build.One users by user_id.

Automatically deactivates invalid tokens returned by APNs.

Usage:
    from core.notifications.push_service import send_push_to_user

    await send_push_to_user(
        user_id=42,
        title="Action Required",
        body="A bill document needs your review.",
        data={"thread_id": "abc-123", "stage": "REVIEW_NEEDED"},
    )
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


async def send_push_to_user(
    user_id:    int,
    title:      str,
    body:       str,
    data:       Optional[dict[str, Any]] = None,
    badge:      Optional[int]            = None,
) -> bool:
    """
    Send a push notification to all active devices for a user.

    Returns True if at least one notification was delivered successfully.
    Returns False if APNs is not configured, user has no active tokens,
    or all deliveries failed. Never raises — all errors are logged.

    Invalid tokens returned by APNs (BadDeviceToken, Unregistered) are
    automatically deactivated in the database.
    """
    from core.notifications.apns_service import get_apns_service
    from entities.device_token.persistence.repo import DeviceTokenRepository

    apns = get_apns_service()
    if apns is None:
        logger.debug(f"APNs not configured — skipping push for user {user_id}")
        return False

    repo = DeviceTokenRepository()

    try:
        tokens = repo.read_active_by_user_id(user_id)
    except Exception as error:
        logger.error(f"push_service: failed to read tokens for user {user_id}: {error}")
        return False

    if not tokens:
        logger.debug(f"push_service: no active tokens for user {user_id}")
        return False

    token_strings = [t.device_token for t in tokens if t.device_token]

    try:
        results = await apns.send_to_user(
            device_tokens=token_strings,
            title=title,
            body=body,
            data=data,
            badge=badge,
        )
    except Exception as error:
        logger.error(f"push_service: APNs send failed for user {user_id}: {error}")
        return False

    # Deactivate any invalid tokens APNs reported
    invalid_reasons = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}
    for result in results:
        if not result.success and result.reason in invalid_reasons:
            try:
                repo.deactivate(result.device_token)
                logger.info(
                    f"push_service: deactivated invalid token "
                    f"...{result.device_token[-8:]} ({result.reason})"
                )
            except Exception as error:
                logger.warning(
                    f"push_service: failed to deactivate token: {error}"
                )

    any_success = any(r.success for r in results)
    return any_success


# ---------------------------------------------------------------------------
# Notification helpers — named shortcuts for specific business events
# ---------------------------------------------------------------------------

async def notify_review_needed(
    user_id:    int,
    thread_id:  str,
    subject:    str,
    category:   str,
) -> bool:
    """
    Notify an owner that an email thread requires their review.
    Fired when EmailThread.current_stage = REVIEW_NEEDED.
    """
    return await send_push_to_user(
        user_id=user_id,
        title="Review Required",
        body=f"A {_category_label(category)} needs your attention: {subject or 'No subject'}",
        data={
            "type":      "REVIEW_NEEDED",
            "thread_id": thread_id,
            "category":  category,
        },
    )


async def notify_sla_breach(
    user_id:    int,
    thread_id:  str,
    stage:      str,
    hours:      int,
) -> bool:
    """
    Notify an owner that an email thread has been stalled past its SLA.
    Fired by the APScheduler SLA breach job.
    """
    return await send_push_to_user(
        user_id=user_id,
        title="Overdue Item",
        body=f"An item has been waiting for {hours} hours and needs action.",
        data={
            "type":      "SLA_BREACH",
            "thread_id": thread_id,
            "stage":     stage,
            "hours":     hours,
        },
    )


def _category_label(category: str) -> str:
    """Return a human-friendly label for an email process category."""
    labels = {
        "BILL_DOCUMENT":             "bill",
        "BILL_CREDIT_DOCUMENT":      "bill credit",
        "EXPENSE_DOCUMENT":          "expense",
        "EXPENSE_REFUND_DOCUMENT":   "expense refund",
        "UNKNOWN":                   "document",
    }
    return labels.get(category, "document")
