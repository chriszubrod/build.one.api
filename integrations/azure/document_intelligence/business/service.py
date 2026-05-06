"""DocumentIntelligenceService — DI orchestration for the email pipeline.

`extract_invoice(content_bytes, content_type)` runs DI's `prebuilt-layout`
model (with the `keyValuePairs` add-on) against an in-memory PDF/image,
then reshapes DI's nested response into a flat dict the agent layer can
read directly:

    {
      "content": "Full document text as a single string ...",
      "key_value_pairs": [
          {"key": "Invoice #", "value": "202980/1", "confidence": 0.95},
          {"key": "Total",     "value": "$3,553.71", "confidence": 0.91},
          ...
      ],
      "tables": [
          {"row_count": 5, "column_count": 3,
           "rows": [["Description", "Qty", "Amount"],
                    ["1.5x5.5 LVL",  "8",   "$526.00"], ...]},
          ...
      ],
      "pages_count": 1,

      # Backward-compat keys retained as None — populated separately when
      # the agent extracts typed fields and persists them via a follow-up
      # tool call (tracked as deferred work, not done here).
      "vendor_name":    None,
      "invoice_number": None,
      "invoice_date":   None,
      "due_date":       None,
      "subtotal":       None,
      "total_amount":   None,
      "currency":       None,
      "confidence":     None,
      "line_items":     [],

      # Validation moves from rule-based hoist to LLM narration; this
      # block is preserved (always is_valid=True / issues=[]) so existing
      # callers don't crash on the new shape.
      "validation": {"is_valid": True, "issues": []},

      "raw": {...},   # full DI analyzeResult, persisted to DiResultJson
    }

The previous prebuilt-invoice + typed-field hoist logic has been replaced
with this thin pass-through. Document-type classification, field
extraction, and validation now happen in the email_specialist agent's
prompt, which reads `content` + `key_value_pairs` + `tables` directly.
"""
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from integrations.azure.document_intelligence.external.client import (
    DocumentIntelligenceConfigError,
    DocumentIntelligenceError,
    analyze_document_bytes,
)

logger = logging.getLogger(__name__)


class DocumentIntelligenceService:
    """High-level DI extraction for arbitrary documents (invoices, credit
    memos, receipts, statements, etc.). Returns a generic structure; the
    caller decides what the document is."""

    def extract_invoice(self, content: bytes, content_type: str) -> dict:
        """Run DI on an in-memory PDF/image and return a hoisted, agent-
        friendly structure. Method name kept for backward compat with
        existing callers; under the new model this is doc-type-agnostic.

        Raises DocumentIntelligenceConfigError if DI is unconfigured.
        """
        analyze_result = analyze_document_bytes(content, content_type=content_type)
        return self._hoist_and_validate(analyze_result)

    def _hoist_and_validate(self, analyze_result: dict) -> dict:
        """Reshape `prebuilt-layout` analyzeResult into a flat agent-
        friendly dict. No semantic interpretation — the agent does that.
        """
        return {
            "content":         analyze_result.get("content") or "",
            "key_value_pairs": _flatten_key_value_pairs(analyze_result),
            "tables":          _flatten_tables(analyze_result),
            "pages_count":     len(analyze_result.get("pages") or []),

            # Backward-compat — populated by future agent-driven flow
            "vendor_name":    None,
            "invoice_number": None,
            "invoice_date":   None,
            "due_date":       None,
            "subtotal":       None,
            "total_amount":   None,
            "currency":       None,
            "confidence":     None,
            "line_items":     [],

            "validation": {"is_valid": True, "issues": []},
            "raw":        analyze_result,
        }


# ─── DI response unpacking helpers ──────────────────────────────────────────


def _flatten_key_value_pairs(analyze_result: dict) -> list[dict]:
    """`prebuilt-layout` with features=keyValuePairs returns a top-level
    `keyValuePairs` array shaped:
        [{"key": {"content": "Invoice #"},
          "value": {"content": "202980/1"},
          "confidence": 0.945}, ...]

    Flatten to {"key", "value", "confidence"} and drop entries with no
    key text (DI sometimes emits orphan values for stray text regions).
    """
    out: list[dict] = []
    for kvp in analyze_result.get("keyValuePairs") or []:
        key_text = ((kvp.get("key") or {}).get("content") or "").strip()
        if not key_text:
            continue
        value_obj = kvp.get("value") or {}
        value_text = (value_obj.get("content") or "").strip()
        confidence_raw = kvp.get("confidence")
        confidence: Optional[Decimal] = None
        if isinstance(confidence_raw, (int, float)):
            try:
                confidence = Decimal(str(confidence_raw))
            except (InvalidOperation, ValueError):
                confidence = None
        out.append({
            "key": key_text,
            "value": value_text,
            "confidence": confidence,
        })
    return out


def _flatten_tables(analyze_result: dict) -> list[dict]:
    """Reshape DI's cell-list table representation into a row-major matrix
    of cell-content strings — much easier for an LLM to read than
    the original {rowIndex, columnIndex, content} records.
    """
    out: list[dict] = []
    for table in analyze_result.get("tables") or []:
        row_count = int(table.get("rowCount") or 0)
        col_count = int(table.get("columnCount") or 0)
        if row_count <= 0 or col_count <= 0:
            continue
        rows: list[list[str]] = [["" for _ in range(col_count)] for _ in range(row_count)]
        for cell in table.get("cells") or []:
            r = cell.get("rowIndex")
            c = cell.get("columnIndex")
            if not isinstance(r, int) or not isinstance(c, int):
                continue
            if 0 <= r < row_count and 0 <= c < col_count:
                rows[r][c] = (cell.get("content") or "").strip()
        out.append({
            "row_count": row_count,
            "column_count": col_count,
            "rows": rows,
        })
    return out
