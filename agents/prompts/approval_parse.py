# Approval response parsing prompts

SYSTEM_PROMPT = """You are parsing an email response to an invoice/bill approval request.

Your job is to determine:
1. Was the invoice approved, rejected, or is there a question?
2. If approved, extract the project name and cost code
3. Extract any additional notes or instructions

DECISION CATEGORIES:
- "approved": The invoice is approved for payment. Look for: "approved", "yes", "ok", "go ahead", "looks good", "process it", "pay this"
- "rejected": The invoice is rejected. Look for: "rejected", "no", "deny", "not approved", "hold", "don't pay", "wrong"
- "question": The approver is asking for more information before deciding. Look for: question marks, "what is", "can you clarify", "I need more info"
- "unclear": The response doesn't clearly indicate a decision

COST CODE FORMAT:
Cost codes typically look like: "XX-XXX" (e.g., "03-200", "01-100", "15-450")
They may also appear as: "cost code XXX", "charge to XXX", "code: XXX"

OUTPUT FORMAT:
Respond with valid JSON only."""


USER_PROMPT_TEMPLATE = """Parse this approval response email.

ORIGINAL REQUEST CONTEXT:
- Vendor: {vendor}
- Amount: {amount}
- Invoice #: {invoice_number}
- Suggested Project: {project_guess}

RESPONSE EMAIL:
{reply_body}

Extract the decision and any project/cost code mentioned.
Respond with JSON:
{{
    "decision": "approved|rejected|question|unclear",
    "confidence": 0.0-1.0,
    "project_name": "project name mentioned, or null",
    "cost_code": "cost code like XX-XXX, or null",
    "notes": "any additional instructions or notes",
    "question_text": "if decision is question, what are they asking"
}}"""


def build_approval_parse_prompt(
    reply_body: str,
    vendor: str = None,
    amount: float = None,
    invoice_number: str = None,
    project_guess: str = None,
) -> str:
    """
    Build the user prompt for approval parsing.
    """
    return USER_PROMPT_TEMPLATE.format(
        vendor=vendor or "Unknown",
        amount=f"${amount:,.2f}" if amount else "Unknown",
        invoice_number=invoice_number or "Unknown",
        project_guess=project_guess or "Unknown",
        reply_body=reply_body[:2000],
    )
