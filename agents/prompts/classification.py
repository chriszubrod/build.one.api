# Email classification prompts for the Email Triage Agent

SYSTEM_PROMPT = """You are an email classification assistant for a construction company's accounts payable department.

Your job is to analyze incoming emails and determine:
1. What type of email this is (bill/invoice, payment inquiry, project update, or other)
2. Who sent it (vendor identification)
3. What project it relates to
4. Key financial details if it's a bill

CLASSIFICATION CATEGORIES:
- "bill": An invoice or bill requesting payment. Usually has an amount, invoice number, and due date.
- "payment_inquiry": A vendor asking about the status of a payment, when they will be paid, or following up on an invoice.
- "project_update": Updates about project progress, schedules, change orders, or deliveries.
- "other": Anything that doesn't fit the above categories.

TIPS FOR ACCURATE CLASSIFICATION:
- Look for invoice numbers (formats: INV-XXX, #XXX, Invoice XXX)
- Look for dollar amounts ($X,XXX.XX)
- Look for "Please pay", "Payment due", "Invoice attached" → likely a bill
- Look for "When will we be paid", "Payment status", "Outstanding balance" → likely payment inquiry
- If attachment text mentions amounts and line items → likely a bill
- Emails from known vendors with attachments are often bills

OUTPUT FORMAT:
Respond with valid JSON only, no markdown or explanation."""


USER_PROMPT_TEMPLATE = """Analyze this email and classify it.

EMAIL SUBJECT: {subject}

EMAIL BODY:
{email_body}

{attachment_section}
{vendors_section}
{projects_section}

Respond with JSON:
{{
    "category": "bill|payment_inquiry|project_update|other",
    "confidence": 0.0-1.0,
    "vendor_guess": "vendor name or null if unknown",
    "project_guess": "project name or null if unknown",
    "amount": number or null if not a bill,
    "invoice_number": "string or null",
    "invoice_date": "YYYY-MM-DD or null",
    "due_date": "YYYY-MM-DD or null",
    "reasoning": "brief explanation of classification",
    "detected_bills": [
        {{
            "description": "what this bill is for",
            "amount": number,
            "invoice_number": "string or null"
        }}
    ]
}}"""


def build_classification_prompt(
    subject: str,
    email_body: str,
    attachment_text: str = None,
    known_vendors: list = None,
    known_projects: list = None,
) -> str:
    """
    Build the user prompt for email classification.
    
    Args:
        subject: Email subject
        email_body: Email body text
        attachment_text: Extracted text from attachments
        known_vendors: List of known vendor names
        known_projects: List of known project names
        
    Returns:
        Formatted prompt string
    """
    # Attachment section
    if attachment_text:
        attachment_section = f"ATTACHMENT TEXT:\n{attachment_text[:4000]}"
    else:
        attachment_section = "ATTACHMENTS: None"
    
    # Vendors section
    if known_vendors:
        vendors_list = ", ".join(known_vendors[:30])
        vendors_section = f"KNOWN VENDORS: {vendors_list}"
    else:
        vendors_section = "KNOWN VENDORS: None provided"
    
    # Projects section
    if known_projects:
        projects_list = ", ".join(known_projects[:30])
        projects_section = f"KNOWN PROJECTS: {projects_list}"
    else:
        projects_section = "KNOWN PROJECTS: None provided"
    
    return USER_PROMPT_TEMPLATE.format(
        subject=subject,
        email_body=email_body[:3000],  # Limit body length
        attachment_section=attachment_section,
        vendors_section=vendors_section,
        projects_section=projects_section,
    )


# For multi-bill detection in attachments
MULTI_BILL_PROMPT = """Analyze this document text and identify if there are multiple invoices/bills.

DOCUMENT TEXT:
{document_text}

For each invoice found, extract:
- Page or section where it appears
- Invoice number
- Amount
- Vendor name (if different vendors)
- Brief description

Respond with JSON:
{{
    "bill_count": number,
    "bills": [
        {{
            "page_or_section": "description of where this bill appears",
            "invoice_number": "string or null",
            "amount": number or null,
            "vendor_name": "string or null",
            "description": "what this bill is for"
        }}
    ]
}}"""


def build_multi_bill_prompt(document_text: str) -> str:
    """Build prompt for detecting multiple bills in a document."""
    return MULTI_BILL_PROMPT.format(document_text=document_text[:6000])
