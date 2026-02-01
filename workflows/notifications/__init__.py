# Agent notifications

from workflows.notifications.summary import DailySummaryGenerator

__all__ = ["DailySummaryGenerator"]

# Email templates are in templates/ directory:
# - approval_request.html: Initial approval request
# - reminder.html: Reminder for stale workflows
# - daily_summary.html: Daily workflow summary
