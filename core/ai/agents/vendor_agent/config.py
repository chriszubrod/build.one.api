# VendorAgent Configuration
#
# This file contains configurable settings for the VendorAgent.
# Adjust these values to tune agent behavior.


# =============================================================================
# Confidence Thresholds
# =============================================================================
#
# These thresholds determine how the agent handles uncertainty:
#
# - auto_propose (0.70): Confidence >= this creates a normal proposal
# - low_confidence (0.50): Confidence between skip and auto_propose
#   creates a proposal flagged as "low_confidence" for extra review
# - skip (0.50): Confidence below this causes the agent to skip the vendor
#
# Adjusting these thresholds:
# - Lower thresholds = more proposals, but may need more human review
# - Higher thresholds = fewer proposals, but higher quality

CONFIDENCE_THRESHOLDS = {
    "auto_propose": 0.70,      # Create proposal normally
    "low_confidence": 0.50,    # Create proposal but flag for attention
    "skip": 0.50,              # Below this, skip the vendor
}


# =============================================================================
# Batch Processing Limits
# =============================================================================
#
# These limits control how many items are processed in a single run.

DEFAULT_BATCH_LIMIT = 10       # Default vendors per batch
MAX_BATCH_LIMIT = 25          # Maximum vendors per batch


# =============================================================================
# Context Retrieval Limits
# =============================================================================
#
# These limits control how much historical data is retrieved for context.

DEFAULT_BILL_LIMIT = 20        # Bills to retrieve per vendor
DEFAULT_EXPENSE_LIMIT = 20     # Expenses to retrieve per vendor
DEFAULT_CONVERSATION_LIMIT = 20  # Recent messages for context


# =============================================================================
# Agent Run Configuration
# =============================================================================

AGENT_TYPE = "vendor_agent"

# Trigger types
TRIGGER_TYPE_SCHEDULED = "scheduled"
TRIGGER_TYPE_EVENT = "event"
TRIGGER_TYPE_MANUAL = "manual"

# Trigger sources
TRIGGER_SOURCE_DAILY_REVIEW = "daily_review"
TRIGGER_SOURCE_VENDOR_CREATED = "vendor_created"
TRIGGER_SOURCE_USER_REQUEST = "user_request"


# =============================================================================
# LLM Configuration
# =============================================================================

# System prompt for the VendorAgent
VENDOR_AGENT_SYSTEM_PROMPT = """You are the VendorAgent. You answer questions about the current vendor and propose actions for the user to take. You work with one vendor at a time (the vendor identified in the conversation).

CAPABILITIES:
- Answer questions about this vendor (details, bills, documents, taxpayer, addresses, type, etc.).
- Propose actions when appropriate (e.g. suggest a VendorType); the user approves or rejects.

GUARDRAILS:
- Always respond in English.
- Always check rejection history and learn from past rejections before making a proposal.
- Always consider chat/conversation history when responding.
- Never expose PII. Never invent data. Use tools to retrieve data for context; do not make up facts or figures.

WHEN PROPOSING AN ACTION:
- Check rejection history first. Use evidence from bills, expenses, and documents when relevant. Only propose if confidence is at least 0.50; otherwise skip with clear reasoning. Provide clear reasoning for any proposal.
- Confidence: 0.90+ strong match; 0.70–0.89 good match; 0.50–0.69 reasonable but limited evidence; below 0.50 do not propose, skip with reasoning.
"""

# Maximum LLM calls per agent run (safety limit)
MAX_LLM_CALLS_PER_RUN = 100
