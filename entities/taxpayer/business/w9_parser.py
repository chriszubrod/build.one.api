# Python Standard Library Imports
import re
from datetime import datetime
from typing import Any, Optional

# Third-party Imports

# Local Imports
from entities.taxpayer.business.model import TaxpayerClassification

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_EIN_RE = re.compile(r"\b\d{2}-\d{7}\b")
_BARE_9_RE = re.compile(r"\b(\d{9})\b")
_US_DATE_RE = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b")

_TIN_LABEL_PATTERNS = (
    "social security",
    "employer identification",
    "taxpayer identification",
    "tin",
)

_ENTITY_NAME_EXACT_KEYS = frozenset(
    {
        "name",
        "name (as shown on your income tax return)",
    }
)

_CORE_FIELDS = ("entity_name", "taxpayer_id_number", "classification")


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _build_kvp_map(key_value_pairs: Any) -> dict[str, str]:
    result: dict[str, str] = {}
    if not isinstance(key_value_pairs, list):
        return result
    for pair in key_value_pairs:
        if not isinstance(pair, dict):
            continue
        raw_key = pair.get("key")
        if raw_key is None:
            continue
        key_lower = _safe_str(raw_key).lower().strip()
        if not key_lower:
            continue
        raw_val = pair.get("value")
        if raw_val is None:
            continue
        val = _safe_str(raw_val).strip()
        if val:
            result[key_lower] = val
    return result


def _pick_entity_name(kvp: dict[str, str], key_value_pairs: Any) -> Optional[str]:
    for key_lower, value in kvp.items():
        if "name" not in key_lower:
            continue
        if "income tax return" in key_lower or key_lower in _ENTITY_NAME_EXACT_KEYS:
            return value or None

    if isinstance(key_value_pairs, list):
        for pair in key_value_pairs:
            if not isinstance(pair, dict):
                continue
            raw_key = pair.get("key")
            if raw_key is None:
                continue
            if _safe_str(raw_key).strip().lower() == "name":
                val = pair.get("value")
                if val is not None:
                    stripped = _safe_str(val).strip()
                    if stripped:
                        return stripped
    return kvp.get("name") or None


def _pick_business_name(kvp: dict[str, str]) -> Optional[str]:
    for key_lower, value in kvp.items():
        if "business name" in key_lower or "disregarded entity" in key_lower:
            return value or None
    return None


def _classification_from_text(text: str) -> Optional[str]:
    if not text:
        return None
    lowered = text.lower()

    if "limited liability" in lowered or re.search(r"\bllc\b", lowered):
        return TaxpayerClassification.LLC.value
    if "s corp" in lowered or "s corporation" in lowered:
        return TaxpayerClassification.S_CORPORATION.value
    if "c corp" in lowered or "c corporation" in lowered:
        return TaxpayerClassification.C_CORPORATION.value
    if "partnership" in lowered:
        return TaxpayerClassification.PARTNERSHIP.value
    if "trust" in lowered or "estate" in lowered:
        return TaxpayerClassification.TRUST_ESTATE.value
    if "individual" in lowered or "sole propriet" in lowered:
        return TaxpayerClassification.INDIVIDUAL_SOLE_PROPRIETOR.value
    return None


def _detect_classification(content: str, kvp: dict[str, str]) -> Optional[str]:
    parts = [content or ""]
    for key_lower, value in kvp.items():
        parts.append(key_lower)
        parts.append(value)
    combined = " ".join(parts)
    return _classification_from_text(combined)


def _tin_digits_from_match(match: re.Match) -> str:
    return re.sub(r"\D", "", match.group(0))


def _find_tin_near_labels(content: str) -> Optional[str]:
    if not content:
        return None
    lowered = content.lower()
    best: Optional[tuple[int, str]] = None
    for label in _TIN_LABEL_PATTERNS:
        start = 0
        while True:
            idx = lowered.find(label, start)
            if idx < 0:
                break
            window = content[idx : idx + 120]
            for pattern in (_SSN_RE, _EIN_RE, _BARE_9_RE):
                m = pattern.search(window)
                if m:
                    digits = _tin_digits_from_match(m)
                    if len(digits) == 9:
                        candidate = (idx, digits)
                        if best is None or candidate[0] < best[0]:
                            best = candidate
            start = idx + len(label)
    return best[1] if best else None


def _find_tin_in_content(content: str) -> Optional[str]:
    if not content:
        return None
    near = _find_tin_near_labels(content)
    if near:
        return near
    for pattern in (_SSN_RE, _EIN_RE):
        m = pattern.search(content)
        if m:
            digits = _tin_digits_from_match(m)
            if len(digits) == 9:
                return digits
    m = _BARE_9_RE.search(content)
    if m:
        return m.group(1)
    return None


def _find_tin_in_kvp(kvp: dict[str, str]) -> Optional[str]:
    for key_lower, value in kvp.items():
        label_hit = any(lbl in key_lower for lbl in _TIN_LABEL_PATTERNS) or "identification number" in key_lower
        if not label_hit:
            continue
        for pattern in (_SSN_RE, _EIN_RE, _BARE_9_RE):
            m = pattern.search(value)
            if m:
                digits = _tin_digits_from_match(m)
                if len(digits) == 9:
                    return digits
        digits = re.sub(r"\D", "", value)
        if len(digits) == 9:
            return digits
    return None


def _extract_taxpayer_id_number(content: str, kvp: dict[str, str]) -> Optional[str]:
    from_kvp = _find_tin_in_kvp(kvp)
    if from_kvp:
        return from_kvp
    return _find_tin_in_content(content)


def _parse_us_date_to_iso(raw: str) -> Optional[str]:
    raw = raw.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _extract_signature_date(content: str, kvp: dict[str, str]) -> Optional[str]:
    for key_lower, value in kvp.items():
        if "date" in key_lower and ("sign" in key_lower or "signature" in key_lower):
            for m in _US_DATE_RE.finditer(value):
                iso = _parse_us_date_to_iso(m.group(1))
                if iso:
                    return iso

    if not content:
        return None
    lowered = content.lower()
    sig_idx = lowered.rfind("signature")
    if sig_idx < 0:
        sig_idx = lowered.rfind("sign here")
    search_from = sig_idx if sig_idx >= 0 else max(0, len(content) - 800)
    block = content[search_from : search_from + 800]
    block_lower = block.lower()
    date_idx = block_lower.find("date")
    if date_idx >= 0:
        window = block[date_idx : date_idx + 80]
    else:
        window = block
    for m in _US_DATE_RE.finditer(window):
        iso = _parse_us_date_to_iso(m.group(1))
        if iso:
            return iso
    return None


def _detect_is_signed(content: str, kvp: dict[str, str], signature_date: Optional[str]) -> Optional[bool]:
    if not content and not kvp:
        return None
    lowered = (content or "").lower()
    if "signature" not in lowered and not any("sign" in k for k in kvp):
        return False

    if signature_date:
        return True

    for key_lower, value in kvp.items():
        if "sign" not in key_lower:
            continue
        val = _safe_str(value).strip()
        if not val:
            continue
        val_lower = val.lower()
        if val_lower in ("selected", "checked", "yes", "x", "✓", "☑", "☒"):
            return True
        if _US_DATE_RE.search(val):
            return True
        if len(val) > 1 and not val_lower.startswith("signature"):
            return True

    if _US_DATE_RE.search(lowered[lowered.rfind("signature") :] if "signature" in lowered else ""):
        return True
    return False


def parse_w9_fields(di_result: dict) -> dict:
    """Best-effort map of a Document Intelligence prebuilt-layout result to W-9 fields."""
    if not isinstance(di_result, dict):
        di_result = {}

    content = _safe_str(di_result.get("content"))
    key_value_pairs = di_result.get("key_value_pairs")
    kvp = _build_kvp_map(key_value_pairs)

    entity_name = _pick_entity_name(kvp, key_value_pairs)
    business_name = _pick_business_name(kvp)
    classification = _detect_classification(content, kvp)
    taxpayer_id_number = _extract_taxpayer_id_number(content, kvp)
    signature_date = _extract_signature_date(content, kvp)
    is_signed = _detect_is_signed(content, kvp, signature_date)

    values = {
        "entity_name": entity_name,
        "taxpayer_id_number": taxpayer_id_number,
        "classification": classification,
    }
    found = sum(1 for name in _CORE_FIELDS if values[name])
    confidence = round(found / len(_CORE_FIELDS), 4)
    unresolved = [
        field
        for field, val in (
            ("entity_name", entity_name),
            ("taxpayer_id_number", taxpayer_id_number),
            ("classification", classification),
        )
        if not val
    ]

    return {
        "entity_name": entity_name,
        "business_name": business_name,
        "classification": classification,
        "taxpayer_id_number": taxpayer_id_number,
        "is_signed": is_signed,
        "signature_date": signature_date,
        "confidence": confidence,
        "unresolved": unresolved,
    }
