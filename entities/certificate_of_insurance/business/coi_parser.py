# Python Standard Library Imports
import re
from datetime import datetime
from typing import Any, Optional

# Third-party Imports

# Local Imports

_DATE_NEAR_LABEL_RE = re.compile(
    r"(\d{1,2}[-/][A-Za-z]{3}[-/]\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})"
)
_DOLLAR_AMOUNT_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d{2})?)")
_POLICY_NUMBER_RE = re.compile(
    r"(?:policy\s*(?:number|no\.?|#)\s*[:\s]*)?([A-Za-z0-9][A-Za-z0-9\-/]{2,})",
    re.IGNORECASE,
)
_INSURER_LINE_RE = re.compile(
    r"^\s*(?:INSURER\s*)?([A-F])\s*[:\-]\s*(.+?)\s*$",
    re.IGNORECASE,
)
_INSURER_LETTER_ONLY_RE = re.compile(r"^\s*([A-F])\s*$", re.IGNORECASE)

_ISSUING_AUTHORITY_KEY_FRAGMENTS = (
    "producer",
    "agency",
    "agent",
    "broker",
    "authorized representative",
)

_ISSUE_DATE_KEY_FRAGMENTS = (
    "date (mm/dd/yyyy)",
    "date mm/dd/yyyy",
    "certificate date",
)

_COVERAGE_ROW_LABELS = (
    "commercial general liability",
    "general liability",
    "workers compensation",
    "workers comp",
    "employers' liability",
    "employers liability",
    "automobile liability",
    "auto liability",
    "umbrella",
    "excess liability",
    "excess",
    "professional liability",
    "e&o",
    "errors and omissions",
    "pollution liability",
    "pollution",
)


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

    # Accept both hyphen and slash month-abbrev separators (01-Jan-2027 / 01/Jan/2027);
    # _DATE_NEAR_LABEL_RE matches both, so parse both (Codex U-116 finding #4).
    m = re.match(r"^(\d{1,2})[-/]([A-Za-z]{3})[-/](\d{4})$", s, re.IGNORECASE)
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

    try:
        dt = datetime.strptime(s, "%B %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    for m in _DATE_NEAR_LABEL_RE.finditer(s):
        candidate = m.group(1).strip()
        if candidate == s:
            continue
        parsed = _parse_date(candidate)
        if parsed:
            return parsed
    return None


def _normalize_amount(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    m = _DOLLAR_AMOUNT_RE.search(raw.replace("\n", " "))
    if not m:
        cleaned = re.sub(r"[^\d.]", "", raw)
        return cleaned or None
    return m.group(1).replace(",", "")


def _map_coverage_type(text: str) -> Optional[str]:
    if not text:
        return None
    lowered = text.lower()
    if "general liability" in lowered or "commercial general liability" in lowered:
        return "GL"
    if ("workers" in lowered and "comp" in lowered) or "workers compensation" in lowered:
        return "WC"
    if "employers' liability" in lowered or "employers liability" in lowered:
        return "WC"
    other_markers = (
        "automobile",
        "auto liability",
        "umbrella",
        "excess",
        "professional",
        "e&o",
        "errors and omissions",
        "pollution",
    )
    if any(marker in lowered for marker in other_markers):
        return "OTHER"
    for label in _COVERAGE_ROW_LABELS:
        if label in lowered:
            mapped = _map_coverage_type(label)
            if mapped:
                return mapped
    return None


def _parse_insurers(content: str, kvp: dict[str, str], tables: Any) -> dict[str, str]:
    insurers: dict[str, str] = {}

    for key_lower, value in kvp.items():
        m = re.match(r"^insurer\s*([a-f])$", key_lower.replace(" ", ""))
        if not m and key_lower.startswith("insurer "):
            m = re.match(r"^insurer\s+([a-f])\b", key_lower)
        if m and value.strip():
            insurers[m.group(1).upper()] = value.strip()

    if content:
        for line in content.splitlines():
            m = _INSURER_LINE_RE.match(line.strip())
            if m:
                letter = m.group(1).upper()
                name = (m.group(2) or "").strip()
                if name and letter not in insurers:
                    insurers[letter] = name

    if isinstance(tables, list):
        for table in tables:
            if not isinstance(table, dict):
                continue
            rows = table.get("rows")
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, list) or not row:
                    continue
                joined = " ".join(_safe_str(c) for c in row if c).strip()
                m = _INSURER_LINE_RE.match(joined)
                if m:
                    letter = m.group(1).upper()
                    name = (m.group(2) or "").strip()
                    if name:
                        insurers.setdefault(letter, name)
                elif len(row) >= 2:
                    first = _safe_str(row[0]).strip()
                    letter_m = _INSURER_LETTER_ONLY_RE.match(first)
                    if letter_m:
                        letter = letter_m.group(1).upper()
                        name = _safe_str(row[1]).strip()
                        if name:
                            insurers.setdefault(letter, name)

    return insurers


def _extract_dates_from_text(text: str) -> tuple[Optional[str], Optional[str]]:
    effective: Optional[str] = None
    expiry: Optional[str] = None
    lowered = text.lower()

    eff_patterns = ("policy eff", "pol eff", "eff date", " effective ")
    exp_patterns = ("policy exp", "pol exp", "exp date", " expiration ", " expires ")

    for m in _DATE_NEAR_LABEL_RE.finditer(text):
        raw = m.group(1)
        parsed = _parse_date(raw)
        if not parsed:
            continue
        start = max(0, m.start() - 30)
        context = lowered[start : m.start() + 5]
        if any(p in context for p in exp_patterns):
            if not expiry:
                expiry = parsed
        elif any(p in context for p in eff_patterns) or "eff" in context:
            if not effective:
                effective = parsed

    if not effective or not expiry:
        dates_found = [_parse_date(m.group(1)) for m in _DATE_NEAR_LABEL_RE.finditer(text)]
        dates_found = [d for d in dates_found if d]
        if dates_found and not effective:
            effective = dates_found[0]
        if len(dates_found) >= 2 and not expiry:
            expiry = dates_found[1]
        elif len(dates_found) == 1 and not expiry:
            expiry = dates_found[0]

    return effective, expiry


def _extract_limits_from_text(text: str) -> tuple[Optional[str], Optional[str]]:
    each: Optional[str] = None
    aggregate: Optional[str] = None
    lowered = text.lower()
    for m in _DOLLAR_AMOUNT_RE.finditer(text):
        start = max(0, m.start() - 40)
        context = lowered[start : m.start() + 10]
        amount = _normalize_amount(m.group(0))
        if not amount:
            continue
        if "each occurrence" in context or "each occ" in context:
            each = each or amount
        elif "general aggregate" in context or "aggregate" in context:
            aggregate = aggregate or amount
    return each, aggregate


def _extract_policy_number(text: str) -> Optional[str]:
    lowered = text.lower()
    idx = lowered.find("policy")
    search_text = text[idx:] if idx >= 0 else text
    m = _POLICY_NUMBER_RE.search(search_text)
    if m:
        token = (m.group(1) or "").strip()
        # A real policy number contains a digit — this rejects the literal label
        # words ("POLICY", "EFF", "EXP", coverage names) the regex can otherwise grab.
        if token and re.search(r"\d", token) and token.lower() not in ("number", "no", "eff", "exp"):
            return token
    for part in re.split(r"[\s/|]+", text):
        part = part.strip()
        if len(part) >= 4 and re.search(r"\d", part) and re.search(r"[A-Za-z0-9]", part):
            if part.lower() not in ("general", "liability", "workers", "compensation"):
                return part
    return None


def _extract_insurer_letter(text: str) -> Optional[str]:
    for cell in re.split(r"[\s|/]+", text):
        cell = cell.strip()
        if _INSURER_LETTER_ONLY_RE.match(cell):
            return cell.upper()
    m = re.search(r"\binsurer\s*([A-F])\b", text, re.IGNORECASE)
    if m:
        return m.group(1).upper()
    return None


def _policy_from_text_blob(
    blob: str,
    coverage_type: str,
    insurers: dict[str, str],
) -> dict:
    letter = _extract_insurer_letter(blob)
    carrier = insurers.get(letter) if letter else None
    effective, expiry = _extract_dates_from_text(blob)
    each, aggregate = _extract_limits_from_text(blob)
    return {
        "coverage_type": coverage_type,
        "carrier": carrier,
        "policy_number": _extract_policy_number(blob),
        "each_occurrence": each,
        "aggregate": aggregate,
        "effective_date": effective,
        "expiry_date": expiry,
    }


def _policy_score(policy: dict) -> int:
    score = 0
    for key in (
        "policy_number",
        "effective_date",
        "expiry_date",
        "carrier",
        "each_occurrence",
        "aggregate",
    ):
        if policy.get(key):
            score += 1
    return score


def _merge_policies(candidates: list[dict]) -> list[dict]:
    best_by_core: dict[str, dict] = {}
    other: list[dict] = []
    for policy in candidates:
        ct = policy.get("coverage_type")
        if ct in ("GL", "WC"):
            existing = best_by_core.get(ct)
            if existing is None or _policy_score(policy) > _policy_score(existing):
                best_by_core[ct] = policy
        else:
            other.append(policy)
    merged: list[dict] = []
    if "GL" in best_by_core:
        merged.append(best_by_core["GL"])
    if "WC" in best_by_core:
        merged.append(best_by_core["WC"])
    merged.extend(other)
    return merged


def _parse_policies_from_tables(tables: Any, insurers: dict[str, str]) -> list[dict]:
    if not isinstance(tables, list):
        return []
    candidates: list[dict] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        rows = table.get("rows")
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, list):
                continue
            row_text = " ".join(_safe_str(c) for c in row if c).strip()
            if not row_text:
                continue
            coverage_type = _map_coverage_type(row_text)
            if not coverage_type:
                continue
            candidates.append(_policy_from_text_blob(row_text, coverage_type, insurers))
    return _merge_policies(candidates)


def _parse_policies_from_content(content: str, insurers: dict[str, str]) -> list[dict]:
    if not content:
        return []
    lines = content.splitlines()
    candidates: list[dict] = []
    for i, line in enumerate(lines):
        coverage_type = _map_coverage_type(line)
        if not coverage_type:
            continue
        window = "\n".join(lines[i : min(i + 6, len(lines))])
        candidates.append(_policy_from_text_blob(window, coverage_type, insurers))
    return _merge_policies(candidates)


def _pick_issuing_authority(kvp: dict[str, str], content: str) -> Optional[str]:
    for key_lower, value in kvp.items():
        if any(frag in key_lower for frag in _ISSUING_AUTHORITY_KEY_FRAGMENTS):
            token = value.strip()
            if token:
                return token
    if content:
        for line in content.splitlines()[:40]:
            lowered = line.lower()
            if "producer" in lowered and ":" in line:
                _, _, tail = line.partition(":")
                token = tail.strip()
                if token:
                    return token
    return None


def _pick_issue_date(kvp: dict[str, str], content: str) -> Optional[str]:
    for key_lower, value in kvp.items():
        if any(frag in key_lower for frag in _ISSUE_DATE_KEY_FRAGMENTS):
            parsed = _parse_date(value)
            if parsed:
                return parsed
        if key_lower.strip() == "date":
            parsed = _parse_date(value)
            if parsed:
                return parsed
    if content:
        lowered = content.lower()
        idx = lowered.find("date (mm/dd/yyyy)")
        if idx < 0:
            idx = lowered.find("date")
        if idx >= 0:
            window = content[idx : idx + 60]
            for m in _DATE_NEAR_LABEL_RE.finditer(window):
                parsed = _parse_date(m.group(1))
                if parsed:
                    return parsed
    return None


def _compute_confidence(policies: list[dict]) -> float:
    found = 0
    for ct in ("GL", "WC"):
        for policy in policies:
            if policy.get("coverage_type") == ct and policy.get("expiry_date"):
                found += 1
                break
    return min(1.0, found / 2.0)


def _build_unresolved(
    *,
    issuing_authority: Optional[str],
    issue_date: Optional[str],
    policies: list[dict],
) -> list[str]:
    unresolved: list[str] = []
    if not issuing_authority:
        unresolved.append("issuing_authority")
    if not issue_date:
        unresolved.append("issue_date")
    has_gl = any(p.get("coverage_type") == "GL" for p in policies)
    has_wc = any(p.get("coverage_type") == "WC" for p in policies)
    if not has_gl:
        unresolved.append("gl_policy")
    if not has_wc:
        unresolved.append("wc_policy")
    return unresolved


def parse_certificate_of_insurance_fields(di_result: dict) -> dict:
    """Best-effort map of Document Intelligence prebuilt-layout output to COI fields."""
    if not isinstance(di_result, dict):
        di_result = {}

    try:
        content = _safe_str(di_result.get("content"))
        kvp = _build_kvp_map(di_result.get("key_value_pairs"))
        tables = di_result.get("tables")

        issuing_authority = _pick_issuing_authority(kvp, content)
        issue_date = _pick_issue_date(kvp, content)

        insurers = _parse_insurers(content, kvp, tables)
        policies = _parse_policies_from_tables(tables, insurers)
        if not policies:
            policies = _parse_policies_from_content(content, insurers)

        confidence = _compute_confidence(policies)
        unresolved = _build_unresolved(
            issuing_authority=issuing_authority,
            issue_date=issue_date,
            policies=policies,
        )

        return {
            "issuing_authority": issuing_authority,
            "issue_date": issue_date,
            "policies": policies,
            "confidence": confidence,
            "unresolved": unresolved,
        }
    except Exception:
        return {
            "issuing_authority": None,
            "issue_date": None,
            "policies": [],
            "confidence": 0.0,
            "unresolved": [
                "issuing_authority",
                "issue_date",
                "gl_policy",
                "wc_policy",
            ],
        }
