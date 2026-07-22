# Python Standard Library Imports
import re
from datetime import datetime
from typing import Any, Optional

# Third-party Imports

# Local Imports

_LICENSE_NUMBER_KEY_FRAGMENTS = (
    "license number",
    "business number",
    "account number",
    "license #",
    "business tax number",
)

_ISSUE_DATE_KEY_FRAGMENTS = (
    "date issued",
    "issue date",
    "effective date",
    "effective",
    "issued",
)

_EXPIRY_DATE_KEY_FRAGMENTS = (
    "expiration date",
    "this license expires",
    "expires",
    "valid until",
    "expiration",
)

_CORE_CONFIDENCE_FIELDS = ("license_number", "expiry_date")

_CITY_OF_RE = re.compile(r"^City of .+", re.IGNORECASE)
_COUNTY_RE = re.compile(r".+ County\s*$", re.IGNORECASE)
_TOKEN_AFTER_LABEL_RE = re.compile(
    r"(?:license\s*#?|license\s+number|business\s+number|account\s+number|business\s+tax\s+number)"
    r"\s*[:\s#]*\s*([A-Za-z0-9][A-Za-z0-9\-/]*)",
    re.IGNORECASE,
)
_DATE_NEAR_LABEL_RE = re.compile(
    r"(\d{1,2}[-/][A-Za-z]{3}[-/]\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})"
)


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _build_kvp_map(key_value_pairs: Any) -> dict[str, str]:
    """Lowercased key -> value; later pairs overwrite earlier (dedupe repeated blocks)."""
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


def _key_matches_any(key_lower: str, fragments: tuple[str, ...]) -> bool:
    return any(frag in key_lower for frag in fragments)


def _is_expiry_key(key_lower: str) -> bool:
    if "expiration" in key_lower or "expires" in key_lower or "valid until" in key_lower:
        return True
    return _key_matches_any(key_lower, _EXPIRY_DATE_KEY_FRAGMENTS)


def _is_issue_key(key_lower: str) -> bool:
    if _is_expiry_key(key_lower):
        return False
    if "issue" in key_lower or "issued" in key_lower:
        return True
    if "effective" in key_lower:
        return True
    return _key_matches_any(key_lower, _ISSUE_DATE_KEY_FRAGMENTS)


def _parse_date(raw: str) -> Optional[str]:
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None

    try:
        dt = datetime.strptime(s, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{4})$", s, re.IGNORECASE)
    if m:
        day, mon, year = m.group(1), m.group(2).title(), m.group(3)
        try:
            dt = datetime.strptime(f"{day}-{mon}-{year}", "%d-%b-%Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    try:
        dt = datetime.strptime(s, "%m/%d/%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    for m in _DATE_NEAR_LABEL_RE.finditer(s):
        candidate = m.group(1).strip()
        if candidate == s:
            continue  # already tried above — avoid infinite self-recursion on malformed dates
        parsed = _parse_date(candidate)
        if parsed:
            return parsed
    return None


def _pick_license_number_from_kvp(kvp: dict[str, str]) -> Optional[str]:
    for key_lower, value in kvp.items():
        if _key_matches_any(key_lower, _LICENSE_NUMBER_KEY_FRAGMENTS):
            token = value.strip()
            if token:
                return token
    return None


def _pick_license_number_from_content(content: str) -> Optional[str]:
    if not content:
        return None
    m = _TOKEN_AFTER_LABEL_RE.search(content)
    if m:
        token = (m.group(1) or "").strip()
        # A license number contains digits — this rejects "THIS LICENSE EXPIRES" -> "EXPIRES".
        if any(ch.isdigit() for ch in token):
            return token
    lowered = content.lower()
    for label in _LICENSE_NUMBER_KEY_FRAGMENTS:
        idx = lowered.find(label)
        if idx < 0:
            continue
        window = content[idx : idx + len(label) + 80]
        tail = window[len(label) :].lstrip(" :#-\t")
        token_m = re.match(r"([A-Za-z0-9][A-Za-z0-9\-/]*)", tail)
        if token_m and any(ch.isdigit() for ch in token_m.group(1)):
            return token_m.group(1)
    return None


def _pick_date_from_kvp(
    kvp: dict[str, str], *, for_expiry: bool
) -> Optional[str]:
    for key_lower, value in kvp.items():
        if for_expiry:
            if not _is_expiry_key(key_lower):
                continue
        else:
            if not _is_issue_key(key_lower):
                continue
        parsed = _parse_date(value)
        if parsed:
            return parsed
    return None


def _pick_date_from_content(content: str, *, for_expiry: bool) -> Optional[str]:
    if not content:
        return None
    fragments = _EXPIRY_DATE_KEY_FRAGMENTS if for_expiry else _ISSUE_DATE_KEY_FRAGMENTS
    lowered = content.lower()
    best_idx: Optional[int] = None
    best_raw: Optional[str] = None

    for label in fragments:
        if not for_expiry and label in ("expires", "expiration"):
            continue
        if for_expiry and label in ("effective", "issued", "issue date", "date issued"):
            continue
        start = 0
        while True:
            idx = lowered.find(label, start)
            if idx < 0:
                break
            window = content[idx : idx + len(label) + 60]
            for m in _DATE_NEAR_LABEL_RE.finditer(window):
                raw = m.group(1)
                parsed = _parse_date(raw)
                if parsed and (best_idx is None or idx < best_idx):
                    best_idx = idx
                    best_raw = parsed
            start = idx + len(label)

    return best_raw


def _pick_issuing_authority(content: str) -> Optional[str]:
    if not content:
        return None
    for line in content.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        if _CITY_OF_RE.match(trimmed):
            return trimmed
        if _COUNTY_RE.match(trimmed):
            return trimmed
        lower = trimmed.lower()
        if "county clerk" in lower:
            return trimmed
        if "clerk" in lower and (
            "county" in lower or "city" in lower or _CITY_OF_RE.search(trimmed)
        ):
            return trimmed
    return None


def parse_business_license_fields(di_result: dict) -> dict:
    """Best-effort map of Document Intelligence prebuilt-layout output to license fields."""
    if not isinstance(di_result, dict):
        di_result = {}

    content = _safe_str(di_result.get("content"))
    kvp = _build_kvp_map(di_result.get("key_value_pairs"))

    license_number = _pick_license_number_from_kvp(kvp)
    if not license_number:
        license_number = _pick_license_number_from_content(content)

    issuing_authority = _pick_issuing_authority(content)

    expiry_date = _pick_date_from_kvp(kvp, for_expiry=True)
    if not expiry_date:
        expiry_date = _pick_date_from_content(content, for_expiry=True)

    issue_date = _pick_date_from_kvp(kvp, for_expiry=False)
    if not issue_date:
        issue_date = _pick_date_from_content(content, for_expiry=False)

    found = sum(1 for v in (license_number, expiry_date) if v)
    confidence = found / len(_CORE_CONFIDENCE_FIELDS)

    unresolved = [
        name
        for name, val in (
            ("license_number", license_number),
            ("issuing_authority", issuing_authority),
            ("issue_date", issue_date),
            ("expiry_date", expiry_date),
        )
        if not val
    ]

    return {
        "license_number": license_number,
        "issuing_authority": issuing_authority,
        "issue_date": issue_date,
        "expiry_date": expiry_date,
        "confidence": confidence,
        "unresolved": unresolved,
    }
