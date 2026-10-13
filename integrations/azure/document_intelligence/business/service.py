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

      "raw": {...},   # full DI analyzeResult (unmasked for agents; SQL cache is reduced — see
                      # project_analyze_result_for_cache)
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

    def extract_text(self, content: bytes, content_type: str) -> dict:
        """Plain-text OCR via `prebuilt-read` (no key-value pairs, no tables).

        Cheaper than `extract_invoice` for scanned PDFs whose text is all the
        caller needs. Raises DocumentIntelligenceConfigError if DI is unconfigured.
        """
        analyze_result = analyze_document_bytes(
            content,
            content_type=content_type,
            model_id="prebuilt-read",
            features=None,
        )
        return {
            "content": analyze_result.get("content") or "",
            "pages_count": len(analyze_result.get("pages") or []),
            "key_value_pairs": [],
        }

    def _hoist_and_validate(self, analyze_result: dict) -> dict:
        """Reshape `prebuilt-layout` analyzeResult into a flat agent-
        friendly dict. No semantic interpretation — the agent does that.
        """
        # keep in step: the cache stores only what project_analyze_result_for_cache reads
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


def _di_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _di_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def project_analyze_result_for_cache(raw: dict) -> dict:
    """Reduced analyzeResult shape stored in EmailAttachment.DiResultJson.

    Keeps only fields the cache reader uses (content, page stubs, keyValuePairs,
    tables). Never raises on malformed input.
    """
    if not isinstance(raw, dict):
        return {}
    reduced: dict = {}

    content = raw.get("content")
    if isinstance(content, str):
        reduced["content"] = content

    pages_raw = raw.get("pages")
    if isinstance(pages_raw, list):
        stubs: list[dict] = []
        for idx, page in enumerate(pages_raw):
            page_number = idx + 1
            if isinstance(page, dict):
                pn = _di_int(page.get("pageNumber"))
                if pn is not None:
                    page_number = pn
            stubs.append({"pageNumber": page_number})
        reduced["pages"] = stubs

    kvps_out: list[dict] = []
    kvps_raw = raw.get("keyValuePairs")
    if isinstance(kvps_raw, list):
        for kvp in kvps_raw:
            if not isinstance(kvp, dict):
                kvps_out.append({"key": {"content": None}, "value": None, "confidence": None})
                continue
            key_obj = kvp.get("key")
            key_content = (
                _di_str(key_obj.get("content")) if isinstance(key_obj, dict) else None
            )
            kvp_out: dict = {"key": {"content": key_content}}
            if "value" not in kvp:
                kvp_out["value"] = None
            else:
                val_obj = kvp.get("value")
                if val_obj is None:
                    kvp_out["value"] = None
                elif isinstance(val_obj, dict):
                    val_content = _di_str(val_obj.get("content"))
                    kvp_out["value"] = {"content": val_content}
                else:
                    kvp_out["value"] = {"content": None}
            conf = kvp.get("confidence")
            if isinstance(conf, (int, float)) and not isinstance(conf, bool):
                kvp_out["confidence"] = conf
            else:
                kvp_out["confidence"] = None
            kvps_out.append(kvp_out)
        reduced["keyValuePairs"] = kvps_out

    tables_out: list[dict] = []
    tables_raw = raw.get("tables")
    if isinstance(tables_raw, list):
        for table in tables_raw:
            if not isinstance(table, dict):
                continue
            table_out: dict = {}
            row_count = _di_int(table.get("rowCount"))
            col_count = _di_int(table.get("columnCount"))
            if row_count is not None:
                table_out["rowCount"] = row_count
            if col_count is not None:
                table_out["columnCount"] = col_count
            cells_out: list[dict] = []
            cells_raw = table.get("cells")
            if isinstance(cells_raw, list):
                for cell in cells_raw:
                    if not isinstance(cell, dict):
                        continue
                    cell_out: dict = {}
                    ri = _di_int(cell.get("rowIndex"))
                    ci = _di_int(cell.get("columnIndex"))
                    if ri is not None:
                        cell_out["rowIndex"] = ri
                    if ci is not None:
                        cell_out["columnIndex"] = ci
                    cell_content = _di_str(cell.get("content"))
                    cell_out["content"] = cell_content
                    cells_out.append(cell_out)
            table_out["cells"] = cells_out
            tables_out.append(table_out)
        reduced["tables"] = tables_out

    return reduced


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
