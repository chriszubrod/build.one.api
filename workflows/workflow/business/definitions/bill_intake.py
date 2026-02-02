# Python Standard Library Imports

# Local Imports
from workflows.workflow.business.definitions.base import (
    WorkflowDefinition,
    StateDefinition,
    StepDefinition,
    Transition,
    COMMON_STATES,
)


# Bill Intake Workflow States
BILL_INTAKE_STATES = [
    StateDefinition(name="received"),
    StateDefinition(name="classifying"),
    StateDefinition(name="classified"),
    StateDefinition(
        name="awaiting_approval",
        timeout_days=3,
        timeout_trigger="send_reminder",
    ),
    StateDefinition(name="parsing_approval"),
    StateDefinition(name="approved"),
    StateDefinition(name="creating_entities"),
    StateDefinition(name="syncing"),
    COMMON_STATES["completed"],
    COMMON_STATES["abandoned"],
    COMMON_STATES["cancelled"],
    COMMON_STATES["needs_review"],
]


# Bill Intake Workflow Transitions
BILL_INTAKE_TRANSITIONS = [
    # Initial processing
    Transition(trigger="start_classification", source="received", dest="classifying"),
    Transition(trigger="classification_complete", source="classifying", dest="classified"),
    Transition(trigger="classification_failed", source="classifying", dest="needs_review"),
    
    # Request approval
    Transition(trigger="request_approval", source="classified", dest="awaiting_approval"),
    Transition(trigger="skip_approval", source="classified", dest="approved"),
    
    # Handle approval response
    Transition(trigger="receive_reply", source="awaiting_approval", dest="parsing_approval"),
    Transition(trigger="send_reminder", source="awaiting_approval", dest="awaiting_approval"),
    Transition(trigger="timeout_abandon", source="awaiting_approval", dest="abandoned"),
    
    # Parse approval outcome
    Transition(trigger="approval_granted", source="parsing_approval", dest="approved"),
    Transition(trigger="approval_denied", source="parsing_approval", dest="cancelled"),
    Transition(trigger="approval_question", source="parsing_approval", dest="awaiting_approval"),
    Transition(trigger="parse_failed", source="parsing_approval", dest="needs_review"),
    
    # Entity creation
    Transition(trigger="start_entity_creation", source="approved", dest="creating_entities"),
    Transition(trigger="entities_created", source="creating_entities", dest="syncing"),
    Transition(trigger="entity_creation_failed", source="creating_entities", dest="needs_review"),
    
    # Sync to external systems
    Transition(trigger="sync_complete", source="syncing", dest="completed"),
    Transition(trigger="sync_failed", source="syncing", dest="needs_review"),
    
    # Manual intervention paths
    Transition(trigger="resolve_manually", source="needs_review", dest="approved"),
    Transition(trigger="cancel_workflow", source="needs_review", dest="cancelled"),
    Transition(
        trigger="retry_step",
        source="needs_review",
        dest="needs_review",  # Stays in needs_review, step will be retried
    ),
]


# Steps to execute in each state
BILL_INTAKE_STEPS = {
    "classifying": [
        StepDefinition(name="extract_attachment", capability="document.extract"),
        StepDefinition(name="classify_email", agent="email_triage"),
    ],
    "classified": [
        StepDefinition(name="send_approval_request", capability="email.send_as"),
    ],
    "parsing_approval": [
        StepDefinition(name="parse_approval_reply", agent="approval_parser"),
    ],
    "creating_entities": [
        StepDefinition(name="create_bill", capability="entity.create_bill"),
        StepDefinition(name="upload_to_sharepoint", capability="sharepoint.upload_file"),
        StepDefinition(name="update_worksheet", capability="sharepoint.append_worksheet_rows"),
    ],
    "syncing": [
        StepDefinition(name="sync_to_qbo", capability="sync.push_bill_to_qbo", required=False),
    ],
}


# The complete workflow definition
BILL_INTAKE_WORKFLOW = WorkflowDefinition(
    name="bill_intake",
    initial_state="received",
    states=BILL_INTAKE_STATES,
    transitions=BILL_INTAKE_TRANSITIONS,
    steps=BILL_INTAKE_STEPS,
)

# =============================================================================
# Email Intake Workflow - Classification Only
# =============================================================================
# This workflow ONLY classifies emails into entity types. It does NOT extract
# fields or create entities. After user confirms the type, entity-specific
# workflows handle the rest.

EMAIL_INTAKE_STATES = [
    StateDefinition(name="received"),
    StateDefinition(name="classifying"),
    StateDefinition(name="awaiting_confirmation"),  # User confirms entity type
    StateDefinition(name="confirmed"),
    COMMON_STATES["completed"],
    COMMON_STATES["needs_review"],
    COMMON_STATES["cancelled"],
]

EMAIL_INTAKE_TRANSITIONS = [
    # Classification
    Transition(trigger="start_classification", source="received", dest="classifying"),
    Transition(trigger="classification_complete", source="classifying", dest="awaiting_confirmation"),
    Transition(trigger="classification_failed", source="classifying", dest="needs_review"),
    
    # User confirmation
    Transition(trigger="confirm_type", source="awaiting_confirmation", dest="confirmed"),
    Transition(trigger="change_type", source="awaiting_confirmation", dest="awaiting_confirmation"),
    
    # Completion
    Transition(trigger="complete", source="confirmed", dest="completed"),
    
    # Manual intervention
    Transition(trigger="retry_classification", source="needs_review", dest="classifying"),
    Transition(trigger="cancel_workflow", source="needs_review", dest="cancelled"),
    Transition(trigger="manual_confirm", source="needs_review", dest="awaiting_confirmation"),
]

EMAIL_INTAKE_STEPS = {
    "classifying": [
        StepDefinition(name="classify_email", agent="email_triage"),
    ],
    # No steps for awaiting_confirmation - waiting for user action
    # No steps for confirmed - workflow is done, entity workflow can be spawned
}

EMAIL_INTAKE_WORKFLOW = WorkflowDefinition(
    name="email_intake",
    initial_state="received",
    states=EMAIL_INTAKE_STATES,
    transitions=EMAIL_INTAKE_TRANSITIONS,
    steps=EMAIL_INTAKE_STEPS,
)

# Same structure as email_intake; expense-specific logic can be added later.
EXPENSE_INTAKE_WORKFLOW = WorkflowDefinition(
    name="expense_intake",
    initial_state="received",
    states=EMAIL_INTAKE_STATES,
    transitions=EMAIL_INTAKE_TRANSITIONS,
    steps=EMAIL_INTAKE_STEPS,
)


# =============================================================================
# Bill Processing Workflow - Entity-Specific Processing
# =============================================================================
# This workflow is spawned from email_intake after user confirms entity_type=bill.
# It handles: extraction → vendor matching → draft bill creation.

BILL_PROCESSING_STATES = [
    StateDefinition(name="pending"),  # Initial state, waiting to start
    StateDefinition(name="extracting"),  # Extracting fields from documents
    StateDefinition(name="matching_vendor"),  # Matching vendor name to existing vendors
    StateDefinition(name="creating_draft"),  # Creating draft Bill record
    COMMON_STATES["completed"],
    COMMON_STATES["needs_review"],
    COMMON_STATES["cancelled"],
]

BILL_PROCESSING_TRANSITIONS = [
    # Start extraction
    Transition(trigger="start_extraction", source="pending", dest="extracting"),
    Transition(trigger="extraction_complete", source="extracting", dest="matching_vendor"),
    Transition(trigger="extraction_failed", source="extracting", dest="needs_review"),
    
    # Vendor matching
    Transition(trigger="vendor_matched", source="matching_vendor", dest="creating_draft"),
    Transition(trigger="vendor_not_found", source="matching_vendor", dest="creating_draft"),  # Continue with unmatched
    Transition(trigger="matching_failed", source="matching_vendor", dest="needs_review"),
    
    # Draft creation
    Transition(trigger="draft_created", source="creating_draft", dest="completed"),
    Transition(trigger="draft_creation_failed", source="creating_draft", dest="needs_review"),
    
    # Manual intervention
    Transition(trigger="retry_extraction", source="needs_review", dest="extracting"),
    Transition(trigger="retry_matching", source="needs_review", dest="matching_vendor"),
    Transition(trigger="skip_to_draft", source="needs_review", dest="creating_draft"),
    Transition(trigger="cancel_workflow", source="needs_review", dest="cancelled"),
]

BILL_PROCESSING_STEPS = {
    "extracting": [
        StepDefinition(name="extract_bill_fields", agent="bill_extraction"),
    ],
    "matching_vendor": [
        StepDefinition(name="match_vendor", capability="entity.match_vendor"),
    ],
    "creating_draft": [
        StepDefinition(name="create_draft_bill", capability="entity.create_draft_bill"),
    ],
}

BILL_PROCESSING_WORKFLOW = WorkflowDefinition(
    name="bill_processing",
    initial_state="pending",
    states=BILL_PROCESSING_STATES,
    transitions=BILL_PROCESSING_TRANSITIONS,
    steps=BILL_PROCESSING_STEPS,
)
