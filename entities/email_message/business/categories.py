"""Outlook category labels used by the email-agent pipeline.

The agent operates as an opt-in: only messages tagged `AGENT_PROCESS` by
a human are picked up. After processing the agent applies one outcome
category so the human can audit at a glance.

Keeping these as constants in one place avoids drift between the poll
filter, the agent's tagging logic, and any future UI tooling.
"""

# Input — human applies this to enroll a message. We use Outlook's
# default "Blue category" so users don't have to create a custom
# category before getting started; the *outcome* categories below stay
# semantic so an Outlook viewer can audit results at a glance.
AGENT_PROCESS = "Blue category"

# Outcomes — exactly one of these gets stamped after processing.
AGENT_PROCESSED = "Agent: Processed"
AGENT_AWAITING_APPROVAL = "Agent: Awaiting Approval"
AGENT_NEEDS_REVIEW = "Agent: Needs Review"
AGENT_IRRELEVANT = "Agent: Irrelevant"

OUTCOME_CATEGORIES = (
    AGENT_PROCESSED,
    AGENT_AWAITING_APPROVAL,
    AGENT_NEEDS_REVIEW,
    AGENT_IRRELEVANT,
)

ALL_AGENT_CATEGORIES = (AGENT_PROCESS, *OUTCOME_CATEGORIES)


def has_outcome(categories: list[str]) -> bool:
    """True if the message already carries an outcome category — meaning
    we've already processed it and shouldn't pick it up again."""
    if not categories:
        return False
    return any(c in OUTCOME_CATEGORIES for c in categories)
