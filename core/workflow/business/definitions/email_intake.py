"""
Email Intake Workflow Definition
==================================
Defines the state machine for processing an incoming invoice inbox email
through classification, extraction, user review, and record creation.

States
------
  received         Message arrived in inbox, not yet processed
  classifying      AI classifier determining message type
  extracting       Document Intelligence running on attachment(s)
  needs_review     Awaiting accountant to confirm extracted fields
  creating_record  System creating Bill / Expense / Credit record
  completed        Record created, message archived
  skipped          User chose to skip / not process this message
  failed           Unrecoverable error

Transitions
-----------
  receive          → received
  start_classify   received        → classifying
  extraction_ready classifying     → extracting
  needs_review     extracting      → needs_review
  approve          needs_review    → creating_record
  record_created   creating_record → completed
  skip             needs_review    → skipped
  fail             (any)           → failed
  retry            failed          → received

Context keys stored on Workflow.context
---------------------------------------
  email: {
      subject, from_email, from_name, received_at,
      graph_message_id, conversation_id
  }
  attachments: [ {graph_attachment_id, filename, content_type, size, blob_url} ]
  classification: {
      message_type, confidence, signals
  }
  extraction: {
      vendor_name, bill_number, bill_date, due_date,
      total_amount, payment_terms_raw, memo, ship_to_address,
      line_items: [ {description, amount, quantity} ],
      vendor_match: {public_id, name, confidence},
      project_match: {public_id, name, confidence},
      payment_term_match: {public_id, name, confidence},
      overall_confidence, extraction_notes
  }
  confirmed_entity_type: "bill" | "expense" | "vendor_credit" | "inquiry" | "statement"
  created_entity_public_id: str   (set after record is created)
  error: str
"""
from core.workflow.business.definitions.base import (
    WorkflowDefinition,
    StateDefinition,
    Transition,
    StepDefinition,
)


# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

EMAIL_INTAKE_STATES = [
    StateDefinition(name="received"),
    StateDefinition(name="classifying"),
    StateDefinition(name="extracting"),
    StateDefinition(name="needs_review"),
    StateDefinition(name="creating_record"),
    StateDefinition(name="completed", is_final=True),
    StateDefinition(name="skipped", is_final=True),
    StateDefinition(name="failed", is_final=False),   # Not final — allows retry
]


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

EMAIL_INTAKE_TRANSITIONS = [
    # Happy path
    Transition(trigger="start_classify",   source="received",        dest="classifying"),
    Transition(trigger="extraction_ready", source="classifying",     dest="extracting"),
    Transition(trigger="needs_review",     source="extracting",      dest="needs_review"),
    Transition(trigger="approve",          source="needs_review",    dest="creating_record"),
    Transition(trigger="record_created",   source="creating_record", dest="completed"),

    # Shortcuts — skip directly to review if extraction not needed (e.g. inquiry)
    Transition(trigger="skip_extraction",  source="classifying",     dest="needs_review"),

    # User skips the message
    Transition(trigger="skip",             source="needs_review",    dest="skipped"),
    Transition(trigger="skip",             source="received",        dest="skipped"),

    # Error handling
    Transition(trigger="fail",             source="received",        dest="failed"),
    Transition(trigger="fail",             source="classifying",     dest="failed"),
    Transition(trigger="fail",             source="extracting",      dest="failed"),
    Transition(trigger="fail",             source="creating_record", dest="failed"),

    # Retry from failed
    Transition(trigger="retry",            source="failed",          dest="received"),
]


# ---------------------------------------------------------------------------
# Definition factory
# ---------------------------------------------------------------------------

def get_email_intake_workflow_definition() -> WorkflowDefinition:
    """Return the registered WorkflowDefinition for email_intake."""
    return WorkflowDefinition(
        name="email_intake",
        initial_state="received",
        states=EMAIL_INTAKE_STATES,
        transitions=EMAIL_INTAKE_TRANSITIONS,
        steps={
            # Steps that run automatically when entering classifying state
            "classifying": [
                StepDefinition(
                    name="classify_email",
                    agent="email_classifier",
                    required=True,
                    retry_count=0,
                ),
            ],
            # Steps that run when entering extracting state
            "extracting": [
                StepDefinition(
                    name="extract_document",
                    agent="document_extractor",
                    required=True,
                    retry_count=1,
                ),
            ],
        },
    )
