"""
Claude AI Extraction Service
=============================
Replaces the heuristic BillExtractionMapper with a single Claude Haiku API call
for interpreting Document Intelligence OCR output into structured bill fields.

Cost: ~$0.001 per extraction (Haiku pricing).
Fallback: Returns None on any failure so the caller can fall back to heuristics.
"""
import json
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

import anthropic

import config
from entities.bill.business.extraction_mapper import (
    BillExtractionResult,
    LineItemExtraction,
)
from integrations.azure.ai.document_intelligence import ExtractionResult

logger = logging.getLogger(__name__)
settings = config.Settings()

# Maximum chars of document content to send (page 1 is usually enough)
MAX_CONTENT_CHARS = 6000

SYSTEM_PROMPT = """You are an invoice data extraction specialist for a construction company's accounts payable system.

Extract structured fields from the document text provided below. The document has already been OCR'd — you are interpreting the extracted text, key-value pairs, and table data.

Return ONLY valid JSON (no markdown, no explanation) with exactly these fields:

{
  "vendor_name": "The company that ISSUED this invoice (the seller/supplier, NOT the buyer/recipient)",
  "bill_number": "The invoice or document number (must contain at least one digit)",
  "bill_date": "Invoice date in YYYY-MM-DD format",
  "due_date": "Due date in YYYY-MM-DD format, or null",
  "total_amount": 0.00,
  "payment_terms": "Payment terms string like 'Net 30', or null",
  "ship_to_address": "Full ship-to or delivery address including street, city, state, zip (e.g. '1577 Moran Rd, Franklin TN 37024'). Look for 'Ship To', 'Deliver To', or 'Job Site' sections. Always include the street address, not just city/state/zip. Null if not found.",
  "memo": "Brief summary of what was purchased/provided (one sentence)",
  "memo_confidence": 0.0,
  "project_reference_raw": "The raw project/job reference exactly as it appears in the document (e.g. 'Moran Rd', 'Job #1234', 'Hillsboro Pike'), or null",
  "project_name": "Best matching project name from the AVAILABLE PROJECTS list, or null",
  "project_confidence": 0.0,
  "sub_cost_code_name": "Best matching sub cost code name from the AVAILABLE SUB COST CODES list, or null",
  "sub_cost_code_confidence": 0.0,
  "is_billable": true,
  "line_items": [
    {"description": "Line item description", "amount": 0.00, "quantity": null, "unit_price": null}
  ]
}

Rules:
- The vendor is the SENDER/ISSUER of the invoice, NOT the company being billed
- Look for the company name/logo at the top of the invoice — that is typically the vendor
- bill_number must contain at least one digit; do not return bare words like "Invoice"
- Dates must be YYYY-MM-DD; convert any date format you find (e.g., 2/24/26 → 2026-02-24)
- total_amount must be a plain number (no $ or commas); use negative for credits
- line_items should only include actual product/service lines, not subtotals, tax, or total rows
- If a field truly cannot be determined from the document, set it to null
- For memo, use the document content, the email context (subject line, sender name/company, attachment filename), AND the email body (which often contains approval notes, project references, and descriptions) to create a concise one-sentence description of what was purchased or provided. This becomes the line item description on the bill.
- project_name: Match to a project from the AVAILABLE PROJECTS list using ship-to address, job site references, project numbers, delivery locations, clues from the email subject/sender, AND project references in the email body (e.g. "charge to Riverside Heights"). Return the exact project name from the list, or null if no confident match.
- sub_cost_code_name: Match to a cost code from the AVAILABLE SUB COST CODES list based on the type of goods/services described in the document, the email context, AND the email body (e.g., if the sender is a lumber company, the cost code is likely materials; if a surveyor, likely professional services). Return the exact name from the list, or null if no confident match.
- is_billable: true if this is a job cost that should be billed to a customer (materials, subcontractor work, equipment for a job site); false if it's an overhead/office expense
- memo_confidence, project_confidence, sub_cost_code_confidence: self-assessed confidence (0.0–1.0) for each field. Use 0.9+ when the document/email clearly supports the value; 0.5–0.8 when inferred from indirect clues; below 0.5 when guessing"""


class ClaudeExtractionService:
    """
    Sends Document Intelligence OCR output to Claude Haiku for structured
    field extraction. Returns a BillExtractionResult or None on failure.
    """

    def __init__(self):
        self._client: Optional[anthropic.Anthropic] = None

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not settings.anthropic_api_key:
                raise ValueError("Anthropic API key not configured")
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        return self._client

    def extract(
        self,
        extraction: ExtractionResult,
        from_email: Optional[str] = None,
        email_subject: Optional[str] = None,
        attachment_filename: Optional[str] = None,
        projects: Optional[list] = None,
        sub_cost_codes: Optional[list] = None,
        email_body: Optional[str] = None,
    ) -> Optional[BillExtractionResult]:
        """
        Extract bill fields from Document Intelligence output using Claude.

        Returns a BillExtractionResult on success, or None if extraction fails
        (so the caller can fall back to the heuristic mapper).
        """
        try:
            user_message = self._build_user_message(
                extraction, from_email, email_subject, attachment_filename,
                projects, sub_cost_codes, email_body,
            )

            response = self.client.messages.create(
                model=settings.anthropic_model,
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )

            # Extract text from response
            text = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text

            if not text.strip():
                logger.warning("Claude extraction returned empty response")
                return None

            return self._parse_response(text)

        except anthropic.APIError as e:
            logger.warning("Claude extraction API error: %s", e)
            return None
        except ValueError as e:
            # API key not configured
            logger.debug("Claude extraction not available: %s", e)
            return None
        except Exception as e:
            logger.warning("Claude extraction unexpected error: %s", e)
            return None

    def _build_user_message(
        self,
        extraction: ExtractionResult,
        from_email: Optional[str],
        email_subject: Optional[str],
        attachment_filename: Optional[str],
        projects: Optional[list] = None,
        sub_cost_codes: Optional[list] = None,
        email_body: Optional[str] = None,
    ) -> str:
        """Build the user message with all available context."""
        parts = []

        # Email metadata (high-signal context)
        if from_email or email_subject or attachment_filename:
            parts.append("=== EMAIL CONTEXT ===")
            if from_email:
                parts.append(f"From: {from_email}")
            if email_subject:
                parts.append(f"Subject: {email_subject}")
            if attachment_filename:
                parts.append(f"Attachment: {attachment_filename}")
            parts.append("")

        # Email body (approval context, project refs, descriptions)
        if email_body and email_body.strip():
            body_text = email_body.strip()[:3000]
            if len(email_body.strip()) > 3000:
                body_text += "\n... [truncated]"
            parts.append("=== EMAIL BODY ===")
            parts.append(body_text)
            parts.append("")

        # Key-value pairs (compact, high-signal)
        if extraction.key_value_pairs:
            parts.append("=== KEY-VALUE PAIRS ===")
            for kv in extraction.key_value_pairs:
                key = kv.get("key", "").strip()
                value = kv.get("value", "").strip()
                if key and value:
                    parts.append(f"{key}: {value}")
            parts.append("")

        # Table data (structured line items)
        if extraction.tables:
            parts.append("=== TABLE DATA ===")
            for i, table in enumerate(extraction.tables):
                if hasattr(table, "to_list"):
                    grid = table.to_list()
                elif hasattr(table, "cells"):
                    # Build grid manually
                    grid = self._cells_to_grid(table)
                else:
                    continue

                if grid:
                    for row in grid:
                        parts.append(" | ".join(str(cell) for cell in row))
                    parts.append("")

        # Full document content (truncated for token efficiency)
        if extraction.content:
            content = extraction.content[:MAX_CONTENT_CHARS]
            if len(extraction.content) > MAX_CONTENT_CHARS:
                content += "\n... [truncated]"
            parts.append("=== DOCUMENT TEXT ===")
            parts.append(content)

        # Available projects for matching
        if projects:
            parts.append("")
            parts.append("=== AVAILABLE PROJECTS ===")
            for p in projects:
                abbr = f" ({p.abbreviation})" if getattr(p, "abbreviation", None) else ""
                parts.append(f"- {p.name}{abbr}")

        # Available sub cost codes for matching
        if sub_cost_codes:
            parts.append("")
            parts.append("=== AVAILABLE SUB COST CODES ===")
            for scc in sub_cost_codes:
                num = f"{scc.number} " if getattr(scc, "number", None) else ""
                parts.append(f"- {num}{scc.name}")

        return "\n".join(parts)

    def _cells_to_grid(self, table) -> list[list[str]]:
        """Convert table cells to a 2D grid."""
        cells = table.cells if hasattr(table, "cells") else []
        if not cells:
            return []

        row_count = getattr(table, "row_count", 0)
        col_count = getattr(table, "column_count", 0)
        if not row_count or not col_count:
            return []

        grid = [["" for _ in range(col_count)] for _ in range(row_count)]
        for cell in cells:
            r = cell.get("rowIndex", 0)
            c = cell.get("columnIndex", 0)
            if 0 <= r < row_count and 0 <= c < col_count:
                grid[r][c] = cell.get("content", "")
        return grid

    def _parse_response(self, text: str) -> Optional[BillExtractionResult]:
        """Parse Claude's JSON response into a BillExtractionResult."""
        # Strip markdown code fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (```json or ```)
            first_newline = cleaned.index("\n")
            cleaned = cleaned[first_newline + 1:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.warning("Claude extraction returned invalid JSON: %s", e)
            logger.debug("Raw response: %s", text[:500])
            return None

        result = BillExtractionResult()
        result.note("Claude AI extraction (model: %s)" % settings.anthropic_model)

        # --- Vendor ---
        vendor = data.get("vendor_name")
        if vendor and isinstance(vendor, str) and vendor.strip():
            result.vendor_name = vendor.strip()
            result.vendor_confidence = 0.95
            result.vendor_candidates = [(result.vendor_name, 0.95)]

        # --- Bill number ---
        bill_num = data.get("bill_number")
        if bill_num and isinstance(bill_num, str) and bill_num.strip():
            result.bill_number = str(bill_num).strip()
            result.bill_number_confidence = 0.95

        # --- Bill date ---
        bill_date = data.get("bill_date")
        if bill_date and isinstance(bill_date, str) and bill_date.strip():
            result.bill_date = bill_date.strip()
            result.date_confidence = 0.95

        # --- Due date ---
        due_date = data.get("due_date")
        if due_date and isinstance(due_date, str) and due_date.strip():
            result.due_date = due_date.strip()

        # --- Total amount ---
        total = data.get("total_amount")
        if total is not None:
            try:
                result.total_amount = Decimal(str(total))
                result.amount_confidence = 0.95
            except (InvalidOperation, ValueError):
                pass

        # --- Payment terms ---
        terms = data.get("payment_terms")
        if terms and isinstance(terms, str) and terms.strip():
            result.payment_terms_raw = terms.strip()

        # --- Ship-to address ---
        ship = data.get("ship_to_address")
        if ship and isinstance(ship, str) and ship.strip():
            result.ship_to_address = ship.strip()

        # --- Memo ---
        memo = data.get("memo")
        if memo and isinstance(memo, str) and memo.strip():
            result.memo = memo.strip()
            try:
                result.memo_confidence = float(data.get("memo_confidence", 0))
            except (ValueError, TypeError):
                result.memo_confidence = 0.0

        # --- Project hint ---
        project_ref_raw = data.get("project_reference_raw")
        if project_ref_raw and isinstance(project_ref_raw, str) and project_ref_raw.strip():
            result.project_reference_raw = project_ref_raw.strip()

        project_name = data.get("project_name")
        if project_name and isinstance(project_name, str) and project_name.strip():
            result.project_hint = project_name.strip()
            try:
                result.project_confidence = float(data.get("project_confidence", 0))
            except (ValueError, TypeError):
                result.project_confidence = 0.0

        # --- Sub cost code hint ---
        scc_name = data.get("sub_cost_code_name")
        if scc_name and isinstance(scc_name, str) and scc_name.strip():
            result.sub_cost_code_hint = scc_name.strip()
            try:
                result.sub_cost_code_confidence = float(data.get("sub_cost_code_confidence", 0))
            except (ValueError, TypeError):
                result.sub_cost_code_confidence = 0.0

        # --- Is billable ---
        is_billable = data.get("is_billable")
        if isinstance(is_billable, bool):
            result.is_billable = is_billable

        # --- Line items ---
        line_items = data.get("line_items")
        if isinstance(line_items, list):
            for li in line_items:
                if not isinstance(li, dict):
                    continue
                desc = li.get("description", "").strip()
                if not desc:
                    continue
                item = LineItemExtraction(description=desc)
                if li.get("amount") is not None:
                    try:
                        item.amount = Decimal(str(li["amount"]))
                    except (InvalidOperation, ValueError):
                        pass
                if li.get("quantity") is not None:
                    try:
                        item.quantity = float(li["quantity"])
                    except (ValueError, TypeError):
                        pass
                if li.get("unit_price") is not None:
                    try:
                        item.unit_price = Decimal(str(li["unit_price"]))
                    except (InvalidOperation, ValueError):
                        pass
                item.confidence = 0.90
                result.line_items.append(item)

        # --- Overall confidence ---
        scored = [c for c in [
            result.vendor_confidence,
            result.bill_number_confidence,
            result.date_confidence,
            result.amount_confidence,
        ] if c > 0]
        result.overall_confidence = round(sum(scored) / len(scored), 3) if scored else 0.0

        logger.info(
            "Claude extraction: vendor=%r bill_number=%r date=%r amount=%r overall=%.2f",
            result.vendor_name, result.bill_number, result.bill_date,
            result.total_amount, result.overall_confidence,
        )

        return result
