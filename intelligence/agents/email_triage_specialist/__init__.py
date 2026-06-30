"""Email triage specialist package — read-only cascade demo agent.

Importing this package registers the email-message read tools the agent uses
(`read_email_message`, `search_email_sender_history`) and the agent itself.
"""
# Read-only email tools (read_email_message, search_email_sender_history, …).
import entities.email_message.intelligence.tools  # noqa: F401

from intelligence.agents.email_triage_specialist.definition import (  # noqa: F401
    email_triage_specialist,
)
