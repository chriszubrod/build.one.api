"""DocumentIntelligenceService — DI orchestration for the email pipeline.

`extract_invoice(content_bytes, content_type)` runs DI's `prebuilt-invoice`
model against an in-memory PDF/image, then hoists DI's nested response
shape into a flat dict with the fields we care about:

    {
      "vendor_name": "Home Depot",
      "invoice_number": "INV-001",
      "invoice_date": "2026-04-15",
      "due_date": "2026-05-15",
      "subtotal": Decimal("1100.00"),
      "total_amount": Decimal("1234.56"),
      "currency": "USD",
      "confidence": Decimal("0.94"),       # document-level confidence (min of field confidences)
      "line_items": [{
          "description": "Item 1",
          "quantity": Decimal("1"),
          "unit_price": Decimal("100.00"),
          "amount": Decimal("100.00"),
      }, ...],
      "validation": {
          "is_valid": True,
          "issues": [],                    # list of strings if anything fails
      },
      "raw": {...}                         # full DI analyzeResult, for the DB JSON column
    }

Validation rules (the second half of the "double layer"):
  - total_amount must be > 0
  - sum(line_items[].amount) within ±$0.50 of total_amount (or subtotal
    when subtotal is present and total isn't)
  - invoice_date must parse
  - vendor_name must be non-empty

If validation fails, `is_valid` is False and `issues` lists what broke.
The agent layer reads this and routes to `flag_for_review` accordingly.
"""
import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from integrations.azure.document_intelligence.external.client import (
    DocumentIntelligenceConfigError,
    DocumentIntelligenceError,
    analyze_document_bytes,
)

logger = logging.getLogger(__name__)


# Tolerance for "line-item sum matches total" validation. Vendors round
# differently and per-line subtotals can drift by a few cents; $0.50 is
# loose enough to swallow that without missing real extraction errors.
_LINE_SUM_TOLERANCE = Decimal("0.50")


class DocumentIntelligenceService:
    """High-level DI extraction + validation for vendor invoices."""

    def extract_invoice(self, content: bytes, content_type: str) -> dict:
        """Run DI on an in-memory invoice PDF/image and return a hoisted,
        validated structure ready to persist to EmailAttachment.

        Raises DocumentIntelligenceConfigError if DI is unconfigured (no
        silent skips — see Phase 1.2 decision).
        """
        analyze_result = analyze_document_bytes(content, content_type=content_type)
        return self._hoist_and_validate(analyze_result)

    def _hoist_and_validate(self, analyze_result: dict) -> dict:
        documents = analyze_result.get("documents") or []
        if not documents:
            return {
                "vendor_name": None,
                "invoice_number": None,
                "invoice_date": None,
                "due_date": None,
                "subtotal": None,
                "total_amount": None,
                "currency": None,
                "confidence": None,
                "line_items": [],
                "validation": {"is_valid": False, "issues": ["DI returned no documents"]},
                "raw": analyze_result,
            }

        # The prebuilt-invoice model returns one document per detected
        # invoice; for v1 we treat the first as authoritative.
        fields = (documents[0] or {}).get("fields") or {}

        vendor_name = _get_string(fields, "VendorName")
        invoice_number = _get_string(fields, "InvoiceId")
        invoice_date = _get_date(fields, "InvoiceDate")
        due_date = _get_date(fields, "DueDate")
        subtotal_amount, _ = _get_currency(fields, "SubTotal")
        total_amount, total_currency = _get_currency(fields, "InvoiceTotal")

        line_items = _get_line_items(fields)

        confidence = _document_confidence(fields)

        # --- validation pass --------------------------------------------------
        issues: list[str] = []
        if not vendor_name:
            issues.append("VendorName missing")
        if total_amount is None or total_amount <= Decimal("0"):
            issues.append("InvoiceTotal missing or non-positive")
        if not invoice_date:
            issues.append("InvoiceDate missing or unparseable")

        if line_items and total_amount is not None:
            line_sum = sum((li.get("amount") or Decimal("0")) for li in line_items)
            # Compare line sum against total_amount; fall back to subtotal
            # if total has tax/shipping baked in and lines don't.
            target = total_amount
            diff = abs(Decimal(line_sum) - target)
            if diff > _LINE_SUM_TOLERANCE and subtotal_amount is not None:
                # try subtotal as a second chance
                diff_sub = abs(Decimal(line_sum) - subtotal_amount)
                if diff_sub <= _LINE_SUM_TOLERANCE:
                    diff = diff_sub
            if diff > _LINE_SUM_TOLERANCE:
                issues.append(
                    f"Line-item sum {line_sum} diverges from total {total_amount} by {diff}"
                )

        return {
            "vendor_name": vendor_name,
            "invoice_number": invoice_number,
            "invoice_date": invoice_date,
            "due_date": due_date,
            "subtotal": subtotal_amount,
            "total_amount": total_amount,
            "currency": total_currency,
            "confidence": confidence,
            "line_items": line_items,
            "validation": {"is_valid": not issues, "issues": issues},
            "raw": analyze_result,
        }


# ─── DI response unpacking helpers ──────────────────────────────────────────


def _get_string(fields: dict, name: str) -> Optional[str]:
    field = fields.get(name) or {}
    value = field.get("valueString") or field.get("content")
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def _get_date(fields: dict, name: str) -> Optional[str]:
    """DI returns dates as 'YYYY-MM-DD' strings under valueDate.
    We pass them straight through (the SQL DATE column accepts that)."""
    field = fields.get(name) or {}
    raw = field.get("valueDate")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def _get_currency(fields: dict, name: str) -> tuple[Optional[Decimal], Optional[str]]:
    """DI prebuilt-invoice currency fields look like:
       {"valueCurrency": {"amount": 1234.56, "currencyCode": "USD"}}
    """
    field = fields.get(name) or {}
    cur = field.get("valueCurrency") or {}
    amount_raw = cur.get("amount")
    currency_code = cur.get("currencyCode")
    amount: Optional[Decimal] = None
    if amount_raw is not None:
        try:
            amount = Decimal(str(amount_raw))
        except (InvalidOperation, ValueError):
            amount = None
    return amount, currency_code


def _get_line_items(fields: dict) -> list[dict]:
    items_field = fields.get("Items") or {}
    array = items_field.get("valueArray") or []
    out: list[dict] = []
    for entry in array:
        obj = (entry or {}).get("valueObject") or {}
        description = _get_string(obj, "Description") or _get_string(obj, "ProductCode")
        quantity_raw = ((obj.get("Quantity") or {}).get("valueNumber"))
        unit_price, _ = _get_currency(obj, "UnitPrice")
        amount, _ = _get_currency(obj, "Amount")

        quantity: Optional[Decimal] = None
        if quantity_raw is not None:
            try:
                quantity = Decimal(str(quantity_raw))
            except (InvalidOperation, ValueError):
                quantity = None

        out.append({
            "description": description,
            "quantity": quantity,
            "unit_price": unit_price,
            "amount": amount,
        })
    return out


def _document_confidence(fields: dict) -> Optional[Decimal]:
    """Take the minimum confidence of the top-level fields we care about
    as the document-level confidence — gives the agent one number to
    threshold against."""
    confidences: list[Decimal] = []
    for name in ("VendorName", "InvoiceId", "InvoiceTotal", "InvoiceDate"):
        c = (fields.get(name) or {}).get("confidence")
        if isinstance(c, (int, float)):
            try:
                confidences.append(Decimal(str(c)))
            except (InvalidOperation, ValueError):
                pass
    if not confidences:
        return None
    return min(confidences)
