"""
Bill Extraction Mapper
======================
Maps Azure Document Intelligence ExtractionResult output to structured
BillExtractionResult fields.

Designed to handle a wide variety of vendor invoice formats using a
multi-strategy approach: key-value pairs first (highest confidence),
then content pattern matching, then email/filename signals as fallback.
"""
import re
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class LineItemExtraction:
    description: str
    amount: Optional[Decimal] = None
    quantity: Optional[float] = None
    unit_price: Optional[Decimal] = None
    confidence: float = 0.0


@dataclass
class VendorMatch:
    public_id: str
    name: str
    confidence: float


@dataclass
class ProjectMatch:
    public_id: str
    name: str
    confidence: float


@dataclass
class PaymentTermMatch:
    public_id: str
    name: str
    confidence: float


@dataclass
class SubCostCodeMatch:
    id: str             # SubCostCode.id (string in this entity)
    name: str
    confidence: float


@dataclass
class BillExtractionResult:
    # --- Extracted raw fields ---
    vendor_name: Optional[str] = None
    bill_number: Optional[str] = None
    bill_date: Optional[str] = None        # ISO format: YYYY-MM-DD
    due_date: Optional[str] = None         # ISO format: YYYY-MM-DD (if present)
    total_amount: Optional[Decimal] = None
    payment_terms_raw: Optional[str] = None
    memo: Optional[str] = None
    ship_to_address: Optional[str] = None  # Used for project matching suggestions

    # --- Line items extracted from tables ---
    line_items: list[LineItemExtraction] = field(default_factory=list)

    # --- AI hints (pre-resolution) ---
    project_hint: Optional[str] = None
    sub_cost_code_hint: Optional[str] = None
    is_billable: Optional[bool] = None

    # --- Database entity matches (resolved, not just names) ---
    vendor_match: Optional[VendorMatch] = None
    project_match: Optional[ProjectMatch] = None
    payment_term_match: Optional[PaymentTermMatch] = None
    sub_cost_code_match: Optional[SubCostCodeMatch] = None

    # --- Per-field confidence scores (0.0 – 1.0) ---
    vendor_confidence: float = 0.0
    bill_number_confidence: float = 0.0
    date_confidence: float = 0.0
    amount_confidence: float = 0.0
    overall_confidence: float = 0.0

    # --- Multi-candidate vendor list (for DB matching) ---
    vendor_candidates: list[tuple[str, float]] = field(default_factory=list)

    # --- Diagnostics ---
    extraction_notes: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.extraction_notes.append(msg)


# ---------------------------------------------------------------------------
# Mapper
# ---------------------------------------------------------------------------

class BillExtractionMapper:
    """
    Maps a Document Intelligence ExtractionResult to a BillExtractionResult.

    Usage:
        mapper = BillExtractionMapper()
        result = mapper.map(
            extraction=extraction_result,
            from_email="ar@vendor.com",
            email_subject="Invoice 540119",
            attachment_filename="20260224_540119.pdf",
        )
        # Optionally resolve DB entities:
        result = mapper.resolve_entities(result)
    """

    # Key-value keys that identify the vendor / company name
    VENDOR_KEYS = [
        'vendor', 'vendor name', 'company', 'company name',
        'bill to', 'billed to', 'sold to', 'remit to',
        'from', 'supplier', 'payee', 'contractor',
    ]

    # Document header words to exclude from vendor name candidates
    HEADER_WORDS = {
        'INVOICE', 'STATEMENT', 'CREDIT MEMO', 'CREDIT', 'RECEIPT',
        'BILL', 'PURCHASE ORDER', 'ESTIMATE', 'QUOTE', 'PROPOSAL',
        'PAGE', 'REPRINT', 'REMITTANCE', 'VOUCHER', 'ORDER',
        'TAX INVOICE', 'PROFORMA', 'DELIVERY NOTE',
    }

    # Patterns that indicate a line item description rather than a vendor name
    LINE_ITEM_INDICATORS = re.compile(
        r"""
        \d+/\d+["\']           |  # fractions with units: 3/8", 1/2'
        \d+\s*(?:x|X)\s*\d+   |  # dimensions: 4x8, 12 x 24
        \d+\s*(?:"|\'|in|ft|mm|cm|lb|lbs|oz|gal|ea|pc|pcs|sf|lf)\b  |  # measurements
        \bqty\b                |  # quantity
        \beach\b               |  # each (pricing)
        \bper\s+(?:unit|piece|ft|sf|lf|hour|hr)\b  |  # per-unit pricing
        \bhdwe\b               |  # hardware abbreviation
        \btempered\b           |  # glass/material term
        \bgalvanized\b         |  # material term
        \bstainless\b          |  # material term
        \binstall\w*\b         |  # installation
        ^\$?\d[\d,.]*$            # bare number / price
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    # Key-value keys that identify a bill/invoice number
    BILL_NUMBER_KEYS = [
        'doc#', 'doc #', 'document#', 'document #', 'document number',
        'invoice#', 'invoice #', 'invoice number', 'invoice no', 'invoice no.',
        'inv#', 'inv #', 'inv no', 'inv. no', 'inv. no.',
        'bill number', 'bill#', 'bill no',
        # Vendor credit / credit memo numbers
        'credit#', 'credit #', 'credit number', 'credit no', 'credit no.',
        'cm#', 'cm #', 'credit memo#', 'credit memo #',
        'reference', 'ref#', 'ref #', 'ref no', 'ref. no',
        'order#', 'order #', 'order number', 'order no',
        'po#', 'po #', 'po number', 'purchase order', 'purchase order #',
        'ticket#', 'ticket #', 'ticket number',
        'receipt#', 'receipt #', 'receipt number',
    ]

    # Key-value keys that identify the invoice/bill date
    DATE_KEYS = [
        'date', 'invoice date', 'bill date', 'billing date',
        'document date', 'order date', 'issue date', 'issued',
        'dated', 'transaction date', 'service date',
    ]

    # Key-value keys that identify the total amount due
    TOTAL_KEYS = [
        'total amount', 'total amount due', 'amount due',
        'balance due', 'balance', 'grand total', 'invoice total',
        'net total', 'total', 'amount charged', 'amount payable',
        'payment due', 'please pay',
    ]

    # Key-value keys for payment terms
    TERMS_KEYS = [
        'terms', 'payment terms', 'net terms', 'credit terms',
        'pay terms', 'conditions',
    ]

    # Key-value keys for due date
    DUE_DATE_KEYS = [
        'due date', 'payment due date', 'due', 'pay by', 'pay before',
    ]

    def map(
        self,
        extraction,                          # ExtractionResult from document_intelligence.py
        from_email: Optional[str] = None,
        email_subject: Optional[str] = None,
        attachment_filename: Optional[str] = None,
    ) -> BillExtractionResult:
        """
        Primary entry point. Maps a Document Intelligence result to bill fields.
        Email metadata (from_email, subject, filename) are used as fallback signals.
        """
        result = BillExtractionResult()

        # Build a normalized key-value lookup (lowercase, stripped)
        kv = self._normalize_kv_pairs(extraction.key_value_pairs or [])
        content = extraction.content or ""

        # --- Parse structured subject line if present ---
        # Construction billing pattern: "[Project] - [Vendor] - [Invoice#]"
        # e.g. "WVA - JTA Land Surveying - 26-8697"
        # Parsed BEFORE other strategies so these take priority over document content.
        subject_project_code: Optional[str] = None
        subject_vendor_name: Optional[str] = None
        subject_invoice_number: Optional[str] = None
        if email_subject:
            parts = email_subject.split(' - ')
            if len(parts) >= 3 and re.search(r'\d', parts[-1]):
                subject_project_code = parts[0].strip()     # e.g. "WVA"
                subject_vendor_name = parts[1].strip()      # e.g. "JTA Land Surveying"
                subject_invoice_number = parts[-1].strip()  # e.g. "26-8697"
                result.note(
                    f"Subject parsed as construction billing pattern: "
                    f"project={subject_project_code!r}, vendor={subject_vendor_name!r}, "
                    f"invoice={subject_invoice_number!r}"
                )

        # --- Extract each field ---
        result.vendor_name, result.vendor_confidence, result.vendor_candidates = self._extract_vendor(
            extraction, kv, from_email, email_subject,
            subject_vendor_override=subject_vendor_name,
        )

        result.bill_number, result.bill_number_confidence = self._extract_bill_number(
            kv, email_subject, attachment_filename, content,
            subject_number_override=subject_invoice_number,
        )

        result.bill_date, result.date_confidence = self._extract_bill_date(
            kv, content
        )

        result.due_date, _ = self._extract_due_date(kv, content)

        result.total_amount, result.amount_confidence = self._extract_total_amount(
            kv, content
        )

        result.payment_terms_raw, _ = self._extract_payment_terms(kv)

        result.ship_to_address = self._extract_ship_to_address(kv, content)

        result.line_items = self._extract_line_items(extraction.tables or [])

        result.memo = self._build_memo(result.line_items)

        # --- Overall confidence ---
        scored = [c for c in [
            result.vendor_confidence,
            result.bill_number_confidence,
            result.date_confidence,
            result.amount_confidence,
        ] if c > 0]
        result.overall_confidence = round(sum(scored) / len(scored), 3) if scored else 0.0

        # Log at INFO when extraction is poor (missing key fields)
        missing = [f for f, v in [
            ('vendor', result.vendor_name),
            ('bill_number', result.bill_number),
            ('date', result.bill_date),
            ('amount', result.total_amount),
        ] if not v]

        if missing:
            logger.info(
                "BillExtractionMapper: missing fields=%s | kv_keys=%s | paragraphs[:3]=%s",
                missing,
                list(kv.keys()),
                [p[:60] for p in (extraction.paragraphs or [])[:3]],
            )

        logger.debug(
            "BillExtractionMapper: vendor=%r bill_number=%r date=%r amount=%r overall=%.2f",
            result.vendor_name, result.bill_number, result.bill_date,
            result.total_amount, result.overall_confidence,
        )

        return result

    def resolve_entities(
        self,
        result: BillExtractionResult,
        tenant_id: int = 1,
    ) -> BillExtractionResult:
        """
        Attempt to resolve extracted strings to database entity matches.
        Populates result.vendor_match, .project_match, .payment_term_match.
        Call after map() when you want database IDs for pre-filling forms.
        """
        from entities.vendor.business.service import VendorService
        from entities.project.business.service import ProjectService
        from entities.payment_term.business.service import PaymentTermService

        # --- Vendor matching (multi-candidate) ---
        # Try each extracted vendor candidate against the DB; pick the best
        # combined score (extraction confidence * DB match score).
        candidates = result.vendor_candidates or []
        if not candidates and result.vendor_name:
            candidates = [(result.vendor_name, result.vendor_confidence)]

        if candidates:
            try:
                vendor_svc = VendorService()
                all_vendors = vendor_svc.read_all()
                vendor_pairs = [(v.public_id, v.name) for v in all_vendors]

                best_match = None      # (public_id, name)
                best_combined = 0.0    # extraction_conf * db_match_score
                best_candidate_name = None

                for cand_name, cand_conf in candidates:
                    # Try exact match first
                    exact = vendor_svc.read_by_name(cand_name)
                    if exact:
                        combined = cand_conf * 0.99
                        if combined > best_combined:
                            best_combined = combined
                            best_match = (exact.public_id, exact.name)
                            best_candidate_name = cand_name
                        continue

                    # Fuzzy match
                    match, score = self._fuzzy_match(cand_name, vendor_pairs)
                    if match and score >= 0.5:
                        combined = cand_conf * score
                        if combined > best_combined:
                            best_combined = combined
                            best_match = match
                            best_candidate_name = cand_name

                if best_match and best_combined >= 0.35:
                    result.vendor_match = VendorMatch(
                        public_id=best_match[0],
                        name=best_match[1],
                        confidence=round(best_combined, 3),
                    )
                    result.vendor_name = best_candidate_name
                    result.vendor_confidence = best_combined
                    result.note(
                        f"Vendor multi-candidate match: '{best_candidate_name}' → "
                        f"{best_match[1]} (combined={best_combined:.0%})"
                    )
                else:
                    # No DB match — keep the highest-confidence raw extraction
                    result.note(
                        f"No vendor DB match for any candidate: "
                        f"{[c[0] for c in candidates]}"
                    )
            except Exception as e:
                logger.warning("Vendor resolution failed: %s", e)
                result.note(f"Vendor resolution error: {e}")

        # --- Project matching (from AI hint, then ship-to address fallback) ---
        project_query = result.project_hint or result.ship_to_address
        if project_query:
            try:
                project_svc = ProjectService()
                all_projects = project_svc.read_all()
                project_pairs = [(p.public_id, p.name) for p in all_projects]

                match, score = self._fuzzy_match(project_query, project_pairs)
                if match and score >= 0.4:
                    result.project_match = ProjectMatch(
                        public_id=match[0],
                        name=match[1],
                        confidence=round(score, 3),
                    )
                    result.note(f"Project match: {match[1]} ({score:.0%}) from hint={result.project_hint!r}")
                elif result.ship_to_address and result.project_hint:
                    # AI hint didn't match — try ship-to address as fallback
                    match, score = self._fuzzy_match(result.ship_to_address, project_pairs)
                    if match and score >= 0.5:
                        result.project_match = ProjectMatch(
                            public_id=match[0],
                            name=match[1],
                            confidence=round(score, 3),
                        )
                        result.note(f"Project match (ship-to fallback): {match[1]} ({score:.0%})")
                else:
                    result.note(f"No project match for: {project_query!r}")
            except Exception as e:
                logger.warning("Project resolution failed: %s", e)
                result.note(f"Project resolution error: {e}")

        # --- Payment terms matching ---
        if result.payment_terms_raw:
            try:
                term_svc = PaymentTermService()
                term = term_svc.read_by_name(result.payment_terms_raw)
                if term:
                    result.payment_term_match = PaymentTermMatch(
                        public_id=term.public_id,
                        name=term.name,
                        confidence=0.99,
                    )
                    result.note(f"Payment terms exact match: {term.name}")
                else:
                    all_terms = term_svc.read_all()
                    match, score = self._fuzzy_match(
                        result.payment_terms_raw,
                        [(t.public_id, t.name) for t in all_terms],
                    )
                    if match and score >= 0.5:
                        result.payment_term_match = PaymentTermMatch(
                            public_id=match[0],
                            name=match[1],
                            confidence=round(score, 3),
                        )
                        result.note(f"Payment terms fuzzy match: {match[1]} ({score:.0%})")
            except Exception as e:
                logger.warning("Payment terms resolution failed: %s", e)
                result.note(f"Payment terms resolution error: {e}")

        # --- Sub cost code matching (from AI hint) ---
        if result.sub_cost_code_hint:
            try:
                from entities.sub_cost_code.business.service import SubCostCodeService
                scc_svc = SubCostCodeService()
                all_sccs = scc_svc.read_all()
                scc_pairs = [
                    (scc.id, f"{scc.number} {scc.name}" if scc.number else scc.name)
                    for scc in all_sccs if scc.name
                ]
                match, score = self._fuzzy_match(result.sub_cost_code_hint, scc_pairs)
                if match and score >= 0.4:
                    matched_scc = next((s for s in all_sccs if s.id == match[0]), None)
                    if matched_scc:
                        result.sub_cost_code_match = SubCostCodeMatch(
                            id=matched_scc.id,
                            name=matched_scc.name,
                            confidence=round(score, 3),
                        )
                        result.note(f"SubCostCode match: {matched_scc.name} ({score:.0%})")
                else:
                    result.note(f"No SubCostCode match for: {result.sub_cost_code_hint!r}")
            except Exception as e:
                logger.warning("SubCostCode resolution failed: %s", e)
                result.note(f"SubCostCode resolution error: {e}")

        return result

    # -----------------------------------------------------------------------
    # Field extractors (private)
    # -----------------------------------------------------------------------

    def _extract_vendor(
        self,
        extraction,
        kv: dict,
        from_email: Optional[str],
        email_subject: Optional[str],
        *,
        subject_vendor_override: Optional[str] = None,
    ) -> tuple[Optional[str], float, list[tuple[str, float]]]:
        """
        Extract vendor name candidates from multiple strategies.

        Returns (best_name, best_confidence, all_candidates) where
        all_candidates is a list of (name, confidence) tuples for
        multi-candidate DB matching in resolve_entities().
        """
        candidates: list[tuple[str, float]] = []

        # Strategy 0: Structured subject line override (highest priority)
        if subject_vendor_override:
            candidates.append((subject_vendor_override, 0.85))

        # Strategy 1: Key-value pairs (vendor-specific labels)
        for key in self.VENDOR_KEYS:
            if key in kv:
                val = kv[key].strip()
                if val and 2 < len(val) < 120 and not self._is_header_word(val):
                    candidates.append((val, 0.90))
                    break  # Take the first matching KV key

        # Strategy 2: First qualifying paragraphs (top of invoice)
        if extraction.paragraphs:
            for para in extraction.paragraphs[:5]:
                candidate = para.strip()
                if (
                    candidate
                    and 3 < len(candidate) < 60
                    and not self._looks_like_address(candidate)
                    and not self._looks_like_phone(candidate)
                    and not self._is_header_word(candidate)
                    and not self._looks_like_line_item(candidate)
                ):
                    candidates.append((candidate, 0.70))
                    break  # Take the first qualifying paragraph

        # Strategy 3: First non-address line in raw content
        if extraction.content:
            for line in extraction.content.split('\n')[:6]:
                line = line.strip()
                if (
                    line
                    and 3 < len(line) < 60
                    and not self._looks_like_address(line)
                    and not self._looks_like_phone(line)
                    and not self._is_header_word(line)
                    and not self._looks_like_line_item(line)
                ):
                    # Avoid duplicating a paragraph candidate
                    if not any(c[0] == line for c in candidates):
                        candidates.append((line, 0.60))
                    break

        # Strategy 4: Email sender domain name
        if from_email and '@' in from_email:
            domain_parts = from_email.split('@')[1].split('.')
            domain_part = domain_parts[-2] if len(domain_parts) >= 3 else domain_parts[0]
            display = domain_part.replace('-', ' ').replace('_', ' ').title()
            if display and len(display) > 1:
                candidates.append((display, 0.40))

        if not candidates:
            return None, 0.0, []

        # Return the highest-confidence candidate as the primary,
        # but pass all candidates for DB matching in resolve_entities()
        best = max(candidates, key=lambda c: c[1])
        return best[0], best[1], candidates

    def _is_header_word(self, text: str) -> bool:
        """Check if text is a common document header that should not be a vendor name."""
        upper = text.strip().upper()
        # Exact match
        if upper in self.HEADER_WORDS:
            return True
        # Starts with a header word (e.g., "INVOICE #1234", "CREDIT MEMO")
        for hw in self.HEADER_WORDS:
            if upper.startswith(hw) and (len(upper) == len(hw) or not upper[len(hw)].isalpha()):
                return True
        return False

    def _extract_bill_number(
        self,
        kv: dict,
        email_subject: Optional[str],
        filename: Optional[str],
        content: str,
        *,
        subject_number_override: Optional[str] = None,
    ) -> tuple[Optional[str], float]:
        """Extract invoice/bill number."""
        if subject_number_override:
            return subject_number_override, 0.90

        # Strategy 1: Key-value pairs (highest confidence)
        for key in self.BILL_NUMBER_KEYS:
            if key in kv:
                val = kv[key].lstrip('#').strip()
                # Require at least one digit — bare words like "Invoice" are not valid numbers
                if val and not val.isspace() and re.search(r'\d', val):
                    return val, 0.95

        # Strategy 2: Email subject (e.g., "Invoice 540119", "Credit 798568/1")
        # Note: longer alternations first (invoice before inv) — regex uses first match
        if email_subject:
            match = re.search(
                r'(?:invoice|receipt|ticket|credit|bill|doc|inv|cm)\s*[#:]?\s*([A-Z0-9][\w\-/]{2,})',
                email_subject,
                re.IGNORECASE,
            )
            if match and re.search(r'\d', match.group(1)):
                return match.group(1), 0.85

        # Strategy 3: Attachment filename (e.g., "20260224_540119.pdf")
        if filename:
            stem = re.sub(r'\.(pdf|PDF|jpg|jpeg|png)$', '', filename)
            parts = re.split(r'[_\-\s]+', stem)
            for part in reversed(parts):  # Usually last segment is the invoice number
                if re.match(r'^[A-Z0-9]{4,}$', part, re.I) and not self._looks_like_date_token(part):
                    return part, 0.75

        # Strategy 4: Scan content for common patterns (includes credit/CM numbers)
        # Note: longer alternations first (INVOICE before INV) — regex uses first match
        if content:
            match = re.search(
                r'(?:INVOICE|RECEIPT|TICKET|CREDIT|BILL|DOC|INV|CM)\s*[#:]?\s*([A-Z0-9][\w\-/]{2,})',
                content,
                re.IGNORECASE,
            )
            if match and re.search(r'\d', match.group(1)):
                return match.group(1), 0.65

        return None, 0.0

    def _extract_bill_date(
        self,
        kv: dict,
        content: str,
    ) -> tuple[Optional[str], float]:
        """Extract bill date in YYYY-MM-DD format."""

        # Strategy 1: Key-value pairs
        for key in self.DATE_KEYS:
            if key in kv:
                parsed = self._parse_date(kv[key])
                if parsed:
                    return parsed, 0.95

        # Strategy 2: Scan content for dates near date-label words
        if content:
            # Look for "Date 2/24/26" or "Date: 2/24/26" patterns
            match = re.search(
                r'(?:date|dated|invoice\s+date|bill\s+date)\s*[:\s]+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})',
                content,
                re.IGNORECASE,
            )
            if match:
                parsed = self._parse_date(match.group(1))
                if parsed:
                    return parsed, 0.75

            # Strategy 3: Written month format near date labels
            # e.g. "Date: February 24, 2026" or "Feb 24, 2026"
            match = re.search(
                r'(?:date|dated|invoice\s+date|bill\s+date)\s*[:\s]+'
                r'((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4})',
                content,
                re.IGNORECASE,
            )
            if match:
                parsed = self._parse_written_date(match.group(1))
                if parsed:
                    return parsed, 0.75

            # Fallback: first date found anywhere (numeric)
            match = re.search(r'\b(\d{1,2}/\d{1,2}/\d{2,4})\b', content)
            if match:
                parsed = self._parse_date(match.group(1))
                if parsed:
                    return parsed, 0.50

            # Fallback: first written date found anywhere
            match = re.search(
                r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2},?\s+\d{4})\b',
                content,
                re.IGNORECASE,
            )
            if match:
                parsed = self._parse_written_date(match.group(1))
                if parsed:
                    return parsed, 0.40

        return None, 0.0

    def _extract_due_date(
        self,
        kv: dict,
        content: str,
    ) -> tuple[Optional[str], float]:
        """Extract due date if present."""
        for key in self.DUE_DATE_KEYS:
            if key in kv:
                parsed = self._parse_date(kv[key])
                if parsed:
                    return parsed, 0.90
        return None, 0.0

    def _extract_total_amount(
        self,
        kv: dict,
        content: str,
    ) -> tuple[Optional[Decimal], float]:
        """Extract total amount due."""

        # Strategy 1: Key-value pairs (specific order matters — total > subtotal)
        for key in self.TOTAL_KEYS:
            if key in kv:
                amount = self._parse_amount(kv[key])
                if amount is not None and amount != 0:
                    return amount, 0.95

        # Strategy 2: Content regex patterns
        # \b word boundaries prevent matching SUBTOTAL.
        # Capture optional leading minus sign for vendor credits (negative totals).
        if content:
            patterns = [
                r'\bTOTAL\s+AMOUNT\b\s*[\$:]?\s*(-?[\d,]+\.?\d{0,2})',
                r'\bAMOUNT\s+DUE\b\s*[\$:]?\s*(-?[\d,]+\.?\d{0,2})',
                r'\bBALANCE\s+DUE\b\s*[\$:]?\s*(-?[\d,]+\.?\d{0,2})',
                r'\bGRAND\s+TOTAL\b\s*[\$:]?\s*(-?[\d,]+\.?\d{0,2})',
                r'\bTOTAL\b\s*[\$:]?\s*(-?[\d,]+\.?\d{0,2})',
            ]
            for pattern in patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    amount = self._parse_amount(match.group(1))
                    if amount is not None and amount != 0:
                        return amount, 0.75

            # Strategy 3: Look for dollar amounts preceded by common labels on same line
            line_patterns = [
                r'(?:please\s+pay|pay\s+this\s+amount)\s*[:\s]*\$?\s*(-?[\d,]+\.\d{2})',
                r'\$\s*(-?[\d,]+\.\d{2})\s*(?:due|total|owed)',
            ]
            for pattern in line_patterns:
                match = re.search(pattern, content, re.IGNORECASE)
                if match:
                    amount = self._parse_amount(match.group(1))
                    if amount is not None and amount != 0:
                        return amount, 0.60

        return None, 0.0

    def _extract_payment_terms(
        self,
        kv: dict,
    ) -> tuple[Optional[str], float]:
        """Extract raw payment terms string."""
        for key in self.TERMS_KEYS:
            if key in kv:
                return kv[key], 0.90
        return None, 0.0

    def _extract_ship_to_address(
        self,
        kv: dict,
        content: str,
    ) -> Optional[str]:
        """Extract ship-to / delivery address for project matching."""
        ship_keys = [
            'ship to', 'shipto', 'deliver to', 'delivery address',
            'job site', 'project address', 'service address',
            # POS receipt style (Home Depot, etc.) — project name encoded here
            'p.o.#/job name', 'p.o./job name', 'po#/job name', 'job name',
            'p.o. #/job name', 'job #', 'job no', 'job number',
        ]
        for key in ship_keys:
            if key in kv:
                return kv[key]

        if content:
            # Standard ship-to
            match = re.search(
                r'(?:Ship\s+To|Deliver\s+To|Job\s+Site)\s*\n(.+)',
                content,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()

            # POS receipt style: "P.O.#/JOB NAME: 424 WESTVIEW AVE"
            match = re.search(
                r'P\.?\s*O\.?\s*[#\./]*\s*(?:JOB\s+)?NAME\s*[:\s]+([^\n]+)',
                content,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()

            # Generic job name label
            match = re.search(
                r'(?:JOB\s+NAME|JOB\s+SITE|JOB\s+#)\s*[:\s]+([^\n]+)',
                content,
                re.IGNORECASE,
            )
            if match:
                return match.group(1).strip()

        return None

    def _extract_line_items(self, tables: list) -> list[LineItemExtraction]:
        """Extract line items from document tables."""
        items = []

        for table in tables:
            cells = table.cells if hasattr(table, 'cells') else table.get('cells', [])
            if not cells:
                continue

            # Build header map: columnIndex → header text
            headers = {}
            for cell in cells:
                if cell.get('rowIndex') == 0:
                    headers[cell.get('columnIndex', 0)] = (
                        cell.get('content', '').lower().strip()
                    )

            # Group cells by row
            rows: dict[int, dict[int, str]] = {}
            for cell in cells:
                row_idx = cell.get('rowIndex', 0)
                if row_idx == 0:
                    continue  # Skip header
                col_idx = cell.get('columnIndex', 0)
                rows.setdefault(row_idx, {})[col_idx] = cell.get('content', '').strip()

            # Identify key columns
            desc_col = self._find_column(headers, ['description', 'desc', 'item', 'product', 'sku', 'notes'])
            amount_col = self._find_column(headers, ['extension', 'amount', 'total', 'price', 'ext', 'charge'])
            qty_col = self._find_column(headers, ['quantity', 'qty', 'ordered', 'shipped', 'units'])
            rate_col = self._find_column(headers, ['price/per', 'rate', 'unit price', 'unit cost', 'price'])

            for row_idx in sorted(rows):
                row = rows[row_idx]
                desc = row.get(desc_col, '').strip() if desc_col is not None else ''
                amount_str = row.get(amount_col, '').strip() if amount_col is not None else ''
                qty_str = row.get(qty_col, '').strip() if qty_col is not None else ''
                rate_str = row.get(rate_col, '').strip() if rate_col is not None else ''

                # Skip summary rows
                if not desc or desc.upper() in ('SUBTOTAL', 'TAX', 'TOTAL', 'TAXABLE',
                                                  'NON-TAXABLE', 'TAX AMOUNT', 'TOTAL AMOUNT'):
                    continue

                item = LineItemExtraction(
                    description=desc,
                    amount=self._parse_amount(amount_str),
                    confidence=0.85 if amount_str else 0.55,
                )
                if qty_str:
                    try:
                        item.quantity = float(re.sub(r'[^\d.]', '', qty_str))
                    except (ValueError, TypeError):
                        pass
                if rate_str:
                    item.unit_price = self._parse_amount(rate_str)

                items.append(item)

        return items

    def _build_memo(self, line_items: list[LineItemExtraction]) -> Optional[str]:
        """Build a memo string from line item descriptions."""
        if not line_items:
            return None
        if len(line_items) == 1:
            return line_items[0].description
        return f"Multiple items ({len(line_items)})"

    # -----------------------------------------------------------------------
    # Fuzzy matching
    # -----------------------------------------------------------------------

    def _fuzzy_match(
        self,
        query: str,
        candidates: list[tuple[str, str]],  # List of (id, name)
        threshold: float = 0.0,
    ) -> tuple[Optional[tuple[str, str]], float]:
        """
        Fuzzy match using multiple scoring strategies. Returns (best_candidate, score).
        Combines Jaccard token similarity, containment scoring, substring matching,
        and prefix bonus — no external library required.
        """
        if not query or not candidates:
            return None, 0.0

        query_tokens = set(re.split(r'\W+', query.lower()))
        query_tokens.discard('')
        q_lower = query.lower().strip()

        best_candidate = None
        best_score = 0.0

        for cid, cname in candidates:
            cname_tokens = set(re.split(r'\W+', cname.lower()))
            cname_tokens.discard('')
            n_lower = cname.lower().strip()

            if not cname_tokens:
                continue

            # 1. Jaccard similarity (symmetric token overlap)
            intersection = query_tokens & cname_tokens
            union = query_tokens | cname_tokens
            jaccard = len(intersection) / len(union) if union else 0.0

            # 2. Containment: fraction of query tokens found in candidate
            #    Handles "Rosales" matching "Rosales Construction" (1/2 = 0.5 * 0.85 = 0.425)
            containment = len(intersection) / len(query_tokens) if query_tokens else 0.0

            # 3. Substring match: one string contains the other
            substring_score = 0.0
            if q_lower in n_lower or n_lower in q_lower:
                substring_score = 0.75

            # 4. Starts-with bonus: candidate starts with query or vice versa
            prefix_score = 0.0
            if n_lower.startswith(q_lower) or q_lower.startswith(n_lower):
                prefix_score = 0.80

            # Combine: take the best of all strategies
            score = max(
                jaccard,
                containment * 0.85,
                substring_score,
                prefix_score,
            )

            if score > best_score:
                best_score = score
                best_candidate = (cid, cname)

        if best_score >= threshold:
            return best_candidate, best_score
        return None, 0.0

    # -----------------------------------------------------------------------
    # Parse helpers
    # -----------------------------------------------------------------------

    def _normalize_kv_pairs(self, kv_pairs: list) -> dict:
        """Build a lowercase, stripped dict from Document Intelligence key-value pairs."""
        normalized: dict[str, str] = {}
        for pair in kv_pairs:
            key = (pair.get('key') or '').lower().strip().rstrip(':').strip()
            value = (pair.get('value') or '').strip()
            if key and value:
                normalized[key] = value
        return normalized

    def _parse_date(self, date_str: str) -> Optional[str]:
        """Parse various date formats to YYYY-MM-DD. Returns None on failure."""
        if not date_str:
            return None
        date_str = date_str.strip()

        # M/D/YY or MM/DD/YY
        m = re.match(r'^(\d{1,2})[/\-](\d{1,2})[/\-](\d{2})$', date_str)
        if m:
            mo, dy, yr = m.groups()
            year = 2000 + int(yr)
            return f"{year}-{int(mo):02d}-{int(dy):02d}"

        # M/D/YYYY or MM/DD/YYYY
        m = re.match(r'^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})$', date_str)
        if m:
            mo, dy, yr = m.groups()
            return f"{int(yr)}-{int(mo):02d}-{int(dy):02d}"

        # Already ISO: YYYY-MM-DD
        if re.match(r'^\d{4}-\d{2}-\d{2}$', date_str):
            return date_str

        return None

    _MONTH_MAP = {
        'jan': 1, 'january': 1, 'feb': 2, 'february': 2,
        'mar': 3, 'march': 3, 'apr': 4, 'april': 4,
        'may': 5, 'jun': 6, 'june': 6, 'jul': 7, 'july': 7,
        'aug': 8, 'august': 8, 'sep': 9, 'sept': 9, 'september': 9,
        'oct': 10, 'october': 10, 'nov': 11, 'november': 11,
        'dec': 12, 'december': 12,
    }

    def _parse_written_date(self, date_str: str) -> Optional[str]:
        """Parse written date like 'February 24, 2026' or 'Feb 24 2026' to YYYY-MM-DD."""
        if not date_str:
            return None
        m = re.match(r'(\w+)\s+(\d{1,2}),?\s+(\d{4})', date_str.strip())
        if not m:
            return None
        month_word, day_str, year_str = m.groups()
        month = self._MONTH_MAP.get(month_word.lower())
        if not month:
            return None
        day = int(day_str)
        year = int(year_str)
        if 1 <= day <= 31 and 2000 <= year <= 2100:
            return f"{year}-{month:02d}-{day:02d}"
        return None

    def _parse_amount(self, amount_str: str) -> Optional[Decimal]:
        """
        Parse a currency string to Decimal. Preserves negative values.

        Handles:
          - Standard:            $438.98, 438.98, 1,234.56
          - Negative with dash:  -438.98, -$438.98
          - Parenthetical neg:   (438.98), ($438.98)  — accounting convention
          - Trailing R suffix:   438.98R  — some vendor credit notations
        Returns None on failure or empty input.
        """
        if not amount_str:
            return None
        s = amount_str.strip()

        # Detect negative: leading minus OR parenthetical (438.98) notation
        negative = s.startswith('-') or (s.startswith('(') and s.endswith(')'))

        # Strip all non-numeric characters except decimal point
        clean = re.sub(r'[^\d.]', '', s.replace(',', ''))
        if not clean or clean == '.':
            return None
        try:
            val = Decimal(clean)
            return -val if (negative and val != 0) else val
        except InvalidOperation:
            return None

    def _find_column(self, headers: dict, candidates: list) -> Optional[int]:
        """Return the first column index whose header contains any candidate token."""
        for col_idx, header_text in headers.items():
            for candidate in candidates:
                if candidate in header_text:
                    return col_idx
        return None

    def _looks_like_address(self, text: str) -> bool:
        """Heuristic: does this text look like a street address?"""
        return bool(re.match(r'^\d+\s+\w+', text)) or \
               any(word in text.upper() for word in ('RD', 'ST', 'AVE', 'BLVD', 'DR', 'LN', 'WAY', 'PO BOX'))

    def _looks_like_phone(self, text: str) -> bool:
        """Heuristic: does this text look like a phone number?"""
        return bool(re.search(r'\(?\d{3}\)?[\s\-]\d{3}[\s\-]\d{4}', text)) or \
               'PHONE' in text.upper()

    def _looks_like_line_item(self, text: str) -> bool:
        """Heuristic: does this text look like a line item description rather than a vendor name?"""
        # Contains measurement/material indicators
        if self.LINE_ITEM_INDICATORS.search(text):
            return True
        # Multiple commas suggest a multi-part description, not a company name
        if text.count(',') >= 2:
            return True
        # Contains a colon at the end (label: or continuation)
        if text.rstrip().endswith(':'):
            return True
        return False

    def _looks_like_date_token(self, token: str) -> bool:
        """Heuristic: does this token look like YYYYMMDD?"""
        if len(token) == 8 and token.isdigit():
            try:
                yr, mo, dy = int(token[:4]), int(token[4:6]), int(token[6:8])
                return 2000 <= yr <= 2100 and 1 <= mo <= 12 and 1 <= dy <= 31
            except ValueError:
                pass
        return False
