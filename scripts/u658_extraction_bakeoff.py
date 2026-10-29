"""
U-658 bite 1: offline field-extraction bake-off harness (READ text + LLM vs LAYOUT + parsers).

Compares per document:
  A — prebuilt-layout + existing rule parsers
  B — prebuilt-read (TIN-masked text) + DeepSeek-V4-Flash (LLM seam stub in bite 1)
  C — prebuilt-read (TIN-masked text) + claude-haiku-5-5 (LLM seam stub in bite 1)

DB / SQL dependencies (read-only; rollback only):
  dbo.Attachment — Id, PublicId, BlobUrl, ContentType, Category
  dbo.Taxpayer — Id, EntityName, BusinessName, Classification, TaxpayerIdNumber,
      TaxpayerIdNumberHash, IsDeleted
  dbo.TaxpayerAttachment — TaxpayerId, AttachmentId
  dbo.Vendor — Id, TaxpayerId
  dbo.CertificateOfInsurance — Id, AttachmentId, IssuingAuthority, IssueDate,
      VerificationStatus, IsDeleted
  dbo.VendorInsurancePolicy — CertificateOfInsuranceId, CoverageType, Carrier,
      PolicyNumber, EachOccurrence, Aggregate, EffectiveDate, ExpiryDate
  dbo.BusinessLicense — Id, VerificationStatus, LicenseNumber, IssuingAuthority,
      IssueDate, ExpiryDate, IsDeleted
  dbo.BusinessLicenseAttachment — BusinessLicenseId, AttachmentId
  dbo.ContractorsLicense — Id, VerificationStatus, LicenseNumber, IssuingAuthority,
      Classification, IssueDate, ExpiryDate, IsDeleted
  dbo.ContractorsLicenseAttachment — ContractorsLicenseId, AttachmentId
  dbo.Bill — Id, BillNumber, QboId, RealmId, IntakeSource, SourceEmailMessageId
  dbo.BillLineItem — BillId, Id
  dbo.BillLineItemAttachment — BillLineItemId, AttachmentId
  dbo.ContractLabor — BillLineItemId
  dbo.ContractLaborLineItem — BillLineItemId

Parser / service dependencies:
  integrations.azure.document_intelligence.business.service.DocumentIntelligenceService
      — extract_invoice (prebuilt-layout), extract_text (prebuilt-read)
  entities.vendor_compliance.business.ingest_fetch._load_from_attachment pattern
      — AttachmentService.read_by_public_id + AzureBlobStorage.download_file(BlobUrl)
  entities.taxpayer.business.w9_parser.parse_w9_fields
  entities.certificate_of_insurance.business.coi_parser.parse_certificate_of_insurance_fields
  entities.business_license.business.bl_parser.parse_business_license_fields
  entities.contractors_license.business.cl_parser.parse_contractors_license_fields
  entities.attachment.business.extraction_service.parse_vendor_invoice_number
  entities.attachment.business.tin_redaction — mask_tins
  entities.taxpayer.business.service._normalize_tin (TIN normalization for truth compare)
  shared.encryption — decrypt_if_encrypted, blind_index

Usage (repo root):
  ./.venv/bin/python -m scripts.u658_extraction_bakeoff
  ./.venv/bin/python -m scripts.u658_extraction_bakeoff --run --arms A
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import re
import signal
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from entities.attachment.business.extraction_service import parse_vendor_invoice_number
from entities.attachment.business.tin_redaction import mask_tins
from entities.business_license.business.bl_parser import parse_business_license_fields
from entities.certificate_of_insurance.business.coi_parser import (
    parse_certificate_of_insurance_fields,
)
from entities.contractors_license.business.cl_parser import parse_contractors_license_fields
from entities.taxpayer.business.model import TaxpayerClassification
from entities.taxpayer.business.w9_parser import parse_w9_fields
from intelligence.cascade.core import Attempt, CascadeResult, Rung, StructuredTask, run_cascade
from intelligence.observability.pricing import compute_cost_usd
from intelligence.transport.base import Usage
from scripts.sync_helper import assert_cli_system_admin
from scripts.u654a_text_backtest import assert_outputs_privacy_safe as _u654a_privacy_safe
from shared.database import get_connection
from shared.encryption import decrypt_if_encrypted

MODEL_VERSION = "u658-extraction-bakeoff-bite2-v1"
LLM_TEXT_CHAR_BUDGET = 24_000
LLM_EXTRACT_THRESHOLD = 0.0
ARM_B_LADDER: tuple[Rung, ...] = (Rung("foundry", "DeepSeek-V4-Flash"),)
ARM_C_LADDER: tuple[Rung, ...] = (Rung("anthropic", "claude-haiku-5-5"),)
DEFAULT_SEED = 658
DEFAULT_MAX_COST_USD = 10.0
MAX_COST_USD_CEILING = 10.0
DEFAULT_OUT_DIR = Path("docs/design/u658")

# Azure DI list prices (USD per 1k pages) — Layout rate VERIFY against current Azure pricing.
DI_PRICE_USD_PER_1K_PAGES: dict[str, float] = {
    "prebuilt-read": 1.50,
    "prebuilt-layout": 10.0,  # VERIFY
}
CONSERVATIVE_PAGES_ESTIMATE = 3

# LLM placeholder pricing for dry-run budgeting (bite 2 wires real calls).
_LLM_ESTIMATE_INPUT_CHARS = 8000
ARM_TO_LLM_MODEL: dict[str, str] = {
    "B": "DeepSeek-V4-Flash",
    "C": "claude-haiku-5-5",
}

DEFAULT_SAMPLE_PER_TYPE: dict[str, int | None] = {
    "w9": 60,
    "coi": 80,
    "bl": 60,
    "cl": 60,
    "bill": 200,
}

_DOC_TYPES = ("w9", "coi", "bl", "cl", "bill")
_ARMS = ("A", "B", "C")

_TIN_OUTPUT_RE = re.compile(
    r"(?<!\d)(?:\d{3}[-\s]?\d{2}[-\s]?\d{4}|\d{2}[-\s]?\d{7}|\d{9})(?!\d)"
)
_UUID_TOKEN_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_LLM_PAYLOAD_SEPARATOR_RE = re.compile(r"[\s\-\.\u2212\r\n]+")
VERDICT_MIN_SCORED_DOCS = 20
# Chris 2026-10-16 (U-658 decision b): the LLM arms (B/C) run on W-9s only. W-9
# Read text is aggressively masked (no digit leaves the process); the non-W-9
# shape/label masking below is kept but not used for LLM calls — six review rounds
# kept finding labelled-TIN variants it misses. Arm A still runs on every doc type.
LLM_ARMS = frozenset({"B", "C"})
LLM_DOC_TYPES = frozenset({"w9"})
_COI_COVERAGE_TYPES = frozenset({"GL", "WC", "OTHER"})
_FORBIDDEN_VALUE_KEYS = frozenset(
    {
        "entity_name",
        "business_name",
        "issuing_authority",
        "carrier",
        "policy_number",
        "license_number",
        "invoice_number",
        "content",
        "masked_text",
        "raw_text",
        "taxpayer_id_number",
    }
)

# --- Sample / population SQL (parameterized limits only) ---

_POPULATION_W9_SQL = """
SELECT COUNT(DISTINCT a.[Id])
FROM dbo.[TaxpayerAttachment] ta
INNER JOIN dbo.[Taxpayer] t ON t.[Id] = ta.[TaxpayerId] AND t.[IsDeleted] = 0
INNER JOIN dbo.[Attachment] a ON a.[Id] = ta.[AttachmentId]
WHERE a.[BlobUrl] IS NOT NULL AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
"""

_SAMPLE_W9_SQL = """
SELECT
    a.[Id] AS AttachmentId,
    a.[PublicId] AS AttachmentPublicId,
    t.[Id] AS TaxpayerId,
    t.[EntityName],
    t.[TaxpayerIdNumber],
    t.[TaxpayerIdNumberHash],
    v.[Id] AS VendorId
FROM dbo.[TaxpayerAttachment] ta
INNER JOIN dbo.[Taxpayer] t ON t.[Id] = ta.[TaxpayerId] AND t.[IsDeleted] = 0
INNER JOIN dbo.[Attachment] a ON a.[Id] = ta.[AttachmentId]
LEFT JOIN dbo.[Vendor] v ON v.[TaxpayerId] = t.[Id]
WHERE a.[BlobUrl] IS NOT NULL AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
"""

_POPULATION_COI_SQL = """
SELECT COUNT(*)
FROM dbo.[CertificateOfInsurance] coi
INNER JOIN dbo.[Attachment] a ON a.[Id] = coi.[AttachmentId]
WHERE coi.[IsDeleted] = 0
  AND coi.[VerificationStatus] = 'Verified'
  AND coi.[AttachmentId] IS NOT NULL
  AND a.[BlobUrl] IS NOT NULL AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
"""

_SAMPLE_COI_SQL = """
SELECT
    a.[Id] AS AttachmentId,
    a.[PublicId] AS AttachmentPublicId,
    coi.[Id] AS CoiId,
    coi.[IssuingAuthority],
    coi.[IssueDate]
FROM dbo.[CertificateOfInsurance] coi
INNER JOIN dbo.[Attachment] a ON a.[Id] = coi.[AttachmentId]
WHERE coi.[IsDeleted] = 0
  AND coi.[VerificationStatus] = 'Verified'
  AND a.[BlobUrl] IS NOT NULL AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
"""

_COI_POLICIES_SQL = """
SELECT
    [CoverageType],
    [Carrier],
    [PolicyNumber],
    [EachOccurrence],
    [Aggregate],
    [EffectiveDate],
    [ExpiryDate]
FROM dbo.[VendorInsurancePolicy]
WHERE [CertificateOfInsuranceId] = ?
"""

_POPULATION_BL_SQL = """
SELECT COUNT(*)
FROM dbo.[BusinessLicense] bl
INNER JOIN dbo.[BusinessLicenseAttachment] bla ON bla.[BusinessLicenseId] = bl.[Id]
INNER JOIN dbo.[Attachment] a ON a.[Id] = bla.[AttachmentId]
WHERE bl.[IsDeleted] = 0
  AND bl.[VerificationStatus] = 'Verified'
  AND a.[BlobUrl] IS NOT NULL AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
"""

_SAMPLE_BL_SQL = """
SELECT
    a.[Id] AS AttachmentId,
    a.[PublicId] AS AttachmentPublicId,
    bl.[Id] AS LicenseId,
    bl.[LicenseNumber],
    bl.[IssuingAuthority],
    bl.[IssueDate],
    bl.[ExpiryDate]
FROM dbo.[BusinessLicense] bl
INNER JOIN dbo.[BusinessLicenseAttachment] bla ON bla.[BusinessLicenseId] = bl.[Id]
INNER JOIN dbo.[Attachment] a ON a.[Id] = bla.[AttachmentId]
WHERE bl.[IsDeleted] = 0
  AND bl.[VerificationStatus] = 'Verified'
  AND a.[BlobUrl] IS NOT NULL AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
"""

_POPULATION_CL_SQL = """
SELECT COUNT(*)
FROM dbo.[ContractorsLicense] cl
INNER JOIN dbo.[ContractorsLicenseAttachment] cla ON cla.[ContractorsLicenseId] = cl.[Id]
INNER JOIN dbo.[Attachment] a ON a.[Id] = cla.[AttachmentId]
WHERE cl.[IsDeleted] = 0
  AND cl.[VerificationStatus] = 'Verified'
  AND a.[BlobUrl] IS NOT NULL AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
"""

_SAMPLE_CL_SQL = """
SELECT
    a.[Id] AS AttachmentId,
    a.[PublicId] AS AttachmentPublicId,
    cl.[Id] AS LicenseId,
    cl.[LicenseNumber],
    cl.[IssuingAuthority],
    cl.[Classification],
    cl.[IssueDate],
    cl.[ExpiryDate]
FROM dbo.[ContractorsLicense] cl
INNER JOIN dbo.[ContractorsLicenseAttachment] cla ON cla.[ContractorsLicenseId] = cl.[Id]
INNER JOIN dbo.[Attachment] a ON a.[Id] = cla.[AttachmentId]
WHERE cl.[IsDeleted] = 0
  AND cl.[VerificationStatus] = 'Verified'
  AND a.[BlobUrl] IS NOT NULL AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
"""

_BILL_TRUTH_FILTER = """
    b.[QboId] IS NOT NULL
    AND b.[RealmId] IS NOT NULL
    AND (b.[BillNumber] IS NULL OR b.[BillNumber] NOT LIKE 'QBO-%')
    AND b.[SourceEmailMessageId] IS NULL
    AND NOT EXISTS (
        SELECT 1 FROM dbo.[BillLineItem] bli2
        INNER JOIN dbo.[ContractLabor] cl ON cl.[BillLineItemId] = bli2.[Id]
        WHERE bli2.[BillId] = b.[Id]
    )
    AND NOT EXISTS (
        SELECT 1 FROM dbo.[BillLineItem] bli3
        INNER JOIN dbo.[ContractLaborLineItem] clli ON clli.[BillLineItemId] = bli3.[Id]
        WHERE bli3.[BillId] = b.[Id]
    )
"""

_POPULATION_BILL_SQL = f"""
SELECT COUNT(DISTINCT a.[Id])
FROM dbo.[Bill] b
INNER JOIN dbo.[BillLineItem] bli ON bli.[BillId] = b.[Id]
INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[BillLineItemId] = bli.[Id]
INNER JOIN dbo.[Attachment] a ON a.[Id] = blia.[AttachmentId]
WHERE {_BILL_TRUTH_FILTER.strip()}
  AND a.[BlobUrl] IS NOT NULL AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
"""

_SAMPLE_BILL_SQL = f"""
WITH Linked AS (
    SELECT
        a.[Id] AS AttachmentId,
        a.[PublicId] AS AttachmentPublicId,
        b.[Id] AS BillId,
        b.[BillNumber],
        ROW_NUMBER() OVER (
            PARTITION BY a.[Id]
            ORDER BY blia.[Id] ASC
        ) AS Rn
    FROM dbo.[Bill] b
    INNER JOIN dbo.[BillLineItem] bli ON bli.[BillId] = b.[Id]
    INNER JOIN dbo.[BillLineItemAttachment] blia ON blia.[BillLineItemId] = bli.[Id]
    INNER JOIN dbo.[Attachment] a ON a.[Id] = blia.[AttachmentId]
    WHERE {_BILL_TRUTH_FILTER.strip()}
      AND a.[BlobUrl] IS NOT NULL AND LTRIM(RTRIM(a.[BlobUrl])) <> ''
)
SELECT
    AttachmentId,
    AttachmentPublicId,
    BillId,
    BillNumber
FROM Linked
WHERE Rn = 1
"""


@dataclass(frozen=True)
class SampleDoc:
    doc_type: str
    attachment_id: int
    attachment_public_id: str
    vendor_id: Optional[int] = None
    entity_id: Optional[int] = None
    blob_url: Optional[str] = None
    content_type: Optional[str] = None


@dataclass
class TruthRecord:
    """In-memory ground truth; never serialized to outputs."""

    fields: dict[str, Any] = field(default_factory=dict)
    coi_policies: list[dict[str, Any]] = field(default_factory=list)
    _tin_digits: Optional[str] = field(default=None, repr=False)


@dataclass(frozen=True)
class FieldScore:
    field: str
    exact: bool
    normalized: bool
    predicted_null: bool
    truth_null: bool
    wrong: bool


@dataclass
class DocArmResult:
    attachment_public_id: str
    doc_type: str
    arm: str
    field_scores: list[FieldScore]
    policy_precision: Optional[float] = None
    policy_recall: Optional[float] = None
    di_pages: int = 0
    di_cost_usd: float = 0.0
    llm_cost_usd: float = 0.0
    latency_ms: float = 0.0
    failed: bool = False
    classification_scored: bool = False
    llm_failure_reason: str = ""
    # B/C only: the extra non-W-9 TIN mask changed the text, or the payload was
    # refused. The doc is then excluded from the A-vs-B/C comparison for ALL arms
    # (masking can hide a legitimate 9-digit ID from B/C but not from A).
    masking_excluded: bool = False


@dataclass
class LlmExtractResult:
    """Outcome of one LLM extraction call (arms B/C)."""

    fields: dict[str, Any]
    llm_cost_usd: float
    failure_reason: str = ""
    llm_latency_ms: float = 0.0


def truth_lookup_key(doc_type: str, attachment_public_id: str) -> tuple[str, str]:
    return (doc_type, attachment_public_id)


# Non-W-9 TIN masking (U-658 r4). Two rules, then an independent guard.
#
# 1. Shape rule: only the shapes a TIN is actually written in — SSN 3-2-4, EIN 2-7
#    (and 2-3-4), nine digits in a row, or nine single-digit comb boxes — with ANY
#    run of whitespace/punctuation between the groups (up to _MASK_MAX_GAP chars).
#    Digit groups use str.isdigit, so full-width, superscript and Arabic-Indic
#    digits count. Arbitrary groupings that merely total 9 digits (a 7-digit policy
#    number beside a date, a SKU + qty + price) are NOT TIN shapes and are left alone.
# 2. Label rule: every digit within _LABEL_SPAN characters after a TIN label
#    (SSN, EIN, FEIN, Fed ID, Tax ID, TIN, Taxpayer ID) is masked, whatever its grouping.
#
# The guard (_non_w9_payload_has_unmasked_tin) is a separate implementation over a
# digit-normalised copy of the payload, with a much wider gap: what the mask
# missed is refused (payload_refused), never sent.
_MASK_MAX_GAP = 12
_GUARD_MAX_GAP = 40
_LABEL_SPAN = 40
_TIN_GROUP_SHAPES: tuple[tuple[int, ...], ...] = (
    (3, 2, 4),
    (2, 7),
    (2, 3, 4),
    (9,),
    (1,) * 9,
)
# TIN labels. Unambiguous ones (SSN, EIN, Social Security, Employer/Tax
# Identification, Fed/Tax ID) always count; the bare words TIN and Taxpayer only
# when an ID marker or a digit follows ("TIN:", "TIN #", "Taxpayer ID", "TIN 12"),
# so "TIN FLASHING" / "tin snips" on a bill do not fire.
_TIN_LABEL_RE = re.compile(
    r"(?<![A-Za-z])(?:"
    # SSN: written solid, or dotted ("S.S.N.", "S.S. No."); "SS#" only before a digit run
    r"SSN|S\.\s?S\.\s?N\.?|S\.\s?S\.\s?(?:No\b\.?|#)|SS\s?#(?=[^\dA-Za-z]{0,3}\d{3})"
    r"|Soc(?:ial)?\.?\s*Sec(?:urity)?\.?(?:\s*(?:No\.?|Number|#))?"
    # EIN / FEIN: solid or dotted — never spaced letters ("Grade E in 2")
    r"|F?EIN|F?\.?E\.\s?I\.\s?N\.?"
    r"|Employer\s+Identification(?:\s+(?:No\.?|Number))?"
    r"|Tax\s*(?:Payer\s*)?Identification(?:\s+(?:No\.?|Number))?"
    r"|Fed(?:eral)?\.?\s*(?:Tax\s*)?I\.?\s?D\.?(?:\s*(?:No\.?|#))?"
    r"|Tax\s*(?:Payer\s*)?I\.?\s?D\.?(?:\s*(?:No\.?|#))?"
    # bare TIN / Taxpayer: only with an ID marker, or a digit after up to 6
    # non-alphanumeric characters ("TIN – 12", "TIN (12", "TIN\n\n12")
    r"|(?:TIN|Taxpayer)(?=\s*(?:I\.?\s?D\b|No\b|Number\b|#|:)|[^\dA-Za-z]{0,6}\d)"
    r")(?![A-Za-z])",
    re.IGNORECASE,
)


def _digit_groups(text: str) -> list[tuple[int, int]]:
    groups: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            groups.append((i, j))
            i = j
        else:
            i += 1
    return groups


def _gap_joins(gap: str, max_gap: int) -> bool:
    """A gap joins two digit groups when it is short and holds no letter and no
    mask character ('*' marks digits already masked, so it breaks a chain)."""
    return 0 < len(gap) <= max_gap and not any(ch.isalnum() or ch == "*" for ch in gap)


def _tin_shape_windows(text: str, max_gap: int) -> list[list[tuple[int, int]]]:
    """Every run of consecutive, joinable digit groups whose lengths match a TIN shape."""
    groups = _digit_groups(text)
    windows: list[list[tuple[int, int]]] = []
    for a in range(len(groups)):
        for shape in _TIN_GROUP_SHAPES:
            window = groups[a : a + len(shape)]
            if len(window) != len(shape):
                continue
            if tuple(e - s for s, e in window) != shape:
                continue
            if all(
                _gap_joins(text[window[k][1] : window[k + 1][0]], max_gap)
                for k in range(len(window) - 1)
            ):
                # Bounded: a longer digit group touching either end is not a TIN.
                windows.append(window)
    return windows


def mask_extra_non_w9_tins(text: str) -> tuple[str, bool]:
    """Mask TIN-shaped windows (keeping the last 4 digits, as mask_tins does) and
    every digit just after a TIN label. Returns (masked_text, fired)."""
    if not text:
        return "", False
    chars = list(text)
    fired = False
    for window in _tin_shape_windows(text, _MASK_MAX_GAP):
        positions = [i for start, end in window for i in range(start, end)]
        for i in positions[:-4]:
            if chars[i] != "*":
                chars[i] = "*"
                fired = True
    for m in _TIN_LABEL_RE.finditer(text):
        for i in range(m.end(), min(len(chars), m.end() + _LABEL_SPAN)):
            if chars[i].isdigit():
                chars[i] = "*"
                fired = True
    return "".join(chars), fired


_INVISIBLE_CATEGORIES = frozenset({"Cf", "Mn", "Me"})


def strip_invisible(text: str) -> str:
    """Drop format, combining and enclosing characters (zero-width space/joiner,
    soft hyphen, BOM, word joiner, combining accents). One inside a TIN splits
    its digit group for every rule here while the model still reads nine digits."""
    # NFC first: a decomposed accent ("e" + U+0301) recomposes into "é" and survives;
    # only marks with nothing to combine with (e.g. inside a digit run) are dropped.
    text = unicodedata.normalize("NFC", text)
    return "".join(ch for ch in text if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES)


def prepare_llm_read_text_with_flag(text: str, doc_type: str) -> tuple[str, bool]:
    """TIN-mask Read text for an LLM. Returns (masked, extra_mask_fired); the flag
    is always False for W-9 (aggressive masking removes every digit anyway)."""
    aggressive = doc_type == "w9"
    masked, _ = mask_tins(strip_invisible(text or ""), aggressive=aggressive)
    if aggressive:
        return masked, False
    return mask_extra_non_w9_tins(masked)


def prepare_llm_read_text(text: str, doc_type: str) -> str:
    return prepare_llm_read_text_with_flag(text, doc_type)[0]


def _ascii_digits(text: str) -> str:
    """Map every digit character (any script, superscripts included) to 0-9."""
    out = []
    for ch in text:
        if ch.isdigit() and not ("0" <= ch <= "9"):
            try:
                out.append(str(unicodedata.digit(ch)))
            except (TypeError, ValueError):
                out.append("0")
        else:
            out.append(ch)
    return "".join(out)


_G_SEP = rf"[^0-9A-Za-z*]{{1,{_GUARD_MAX_GAP}}}"
_GUARD_TIN_RE = re.compile(
    rf"(?<![0-9*])(?:"
    rf"[0-9]{{3}}{_G_SEP}[0-9]{{2}}{_G_SEP}[0-9]{{4}}"
    rf"|[0-9]{{2}}{_G_SEP}[0-9]{{7}}"
    rf"|[0-9]{{2}}{_G_SEP}[0-9]{{3}}{_G_SEP}[0-9]{{4}}"
    rf"|[0-9]{{9}}"
    rf"|(?:[0-9]{_G_SEP}){{8}}[0-9]"
    rf")(?![0-9*])"
)


_LONE_STAR_RE = re.compile(r"(?<!\*)\*(?!\*)")


def _non_w9_payload_has_unmasked_tin(text: str) -> bool:
    """Independent of the mask (own digit normalisation, own regex, far wider gaps).

    Invisible characters are removed here too, and a lone '*' is treated as a
    separator: a mask run is always 2+ stars or a star touching a masked run, so a
    single star between digit groups can only be a separator ("123*45*6789")."""
    cleaned = "".join(
        ch for ch in (text or "") if unicodedata.category(ch) not in ("Cf", "Mn", "Me")
    )
    cleaned = _LONE_STAR_RE.sub(" ", _ascii_digits(cleaned))
    return bool(_GUARD_TIN_RE.search(cleaned))


def assert_llm_text_safe(text: str, *, doc_type: str = "w9") -> None:
    """Fail-closed preflight on the exact truncated payload sent to the LLM."""
    payload = text or ""
    if doc_type == "w9":
        collapsed = _LLM_PAYLOAD_SEPARATOR_RE.sub("", payload)
        if re.search(r"\d{9,}", collapsed):
            raise ValueError("LLM payload still contains a 9+ digit run")
        return
    if _non_w9_payload_has_unmasked_tin(payload):
        raise ValueError("LLM payload still contains an unmasked TIN or 9-digit run")


def truth_entity_name_has_digit(truth: TruthRecord) -> bool:
    name = truth.fields.get("entity_name") or ""
    return any(ch.isdigit() for ch in str(name))


_W9_CLASSIFICATION_VALUES = frozenset(c.value for c in TaxpayerClassification)

_EXTRACT_SYSTEM_BASE = (
    "You extract structured fields from OCR text of a business document.\n"
    "Rules:\n"
    "- Extract ONLY what is explicitly printed in the text; use null when absent.\n"
    "- NEVER invent, infer, or guess values not shown.\n"
    "- Dates as ISO 8601 YYYY-MM-DD strings or null.\n"
    "- Dollar amounts as JSON numbers (no currency symbols) or null.\n"
    "- Respond with ONLY a JSON object matching the requested schema.\n"
    "- Include a numeric \"confidence\" field in [0, 1] for your overall extraction.\n"
)


def truncate_llm_text(text: str, budget: int = LLM_TEXT_CHAR_BUDGET) -> tuple[str, bool]:
    text = text or ""
    if len(text) <= budget:
        return text, False
    return text[:budget] + "\n…(truncated)", True


def _optional_str_field(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _optional_bool_field(value: Any) -> bool:
    return value is None or isinstance(value, bool)


def _optional_iso_date(value: Any) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.strptime(value.strip()[:10], "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _optional_number(value: Any) -> bool:
    if value is None:
        return True
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_w9_extraction(result: dict[str, Any]) -> tuple[bool, str]:
    for key in ("entity_name", "business_name"):
        if not _optional_str_field(result.get(key)):
            return False, f"{key} must be string or null"
    cls = result.get("classification")
    if cls is not None and cls not in _W9_CLASSIFICATION_VALUES:
        return False, "classification not in TaxpayerClassification"
    if not _optional_bool_field(result.get("is_signed")):
        return False, "is_signed must be bool or null"
    if not _optional_iso_date(result.get("signature_date")):
        return False, "signature_date must be ISO date or null"
    return True, "ok"


def validate_coi_extraction(result: dict[str, Any]) -> tuple[bool, str]:
    if not _optional_str_field(result.get("issuing_authority")):
        return False, "issuing_authority must be string or null"
    if not _optional_iso_date(result.get("issue_date")):
        return False, "issue_date must be ISO date or null"
    policies = result.get("policies")
    if policies is None:
        return True, "ok"
    if not isinstance(policies, list):
        return False, "policies must be array or null"
    for i, pol in enumerate(policies):
        if not isinstance(pol, dict):
            return False, f"policies[{i}] must be object"
        cov = pol.get("coverage_type")
        if cov is None or not isinstance(cov, str) or cov not in _COI_COVERAGE_TYPES:
            return False, f"policies[{i}].coverage_type must be GL, WC, or OTHER"
        for key in ("carrier", "policy_number"):
            if not _optional_str_field(pol.get(key)):
                return False, f"policies[{i}].{key} invalid"
        for key in ("each_occurrence", "aggregate"):
            if not _optional_number(pol.get(key)):
                return False, f"policies[{i}].{key} must be number or null"
        for key in ("effective_date", "expiry_date"):
            if not _optional_iso_date(pol.get(key)):
                return False, f"policies[{i}].{key} must be ISO date or null"
    return True, "ok"


def validate_bl_extraction(result: dict[str, Any]) -> tuple[bool, str]:
    for key in ("license_number", "issuing_authority"):
        if not _optional_str_field(result.get(key)):
            return False, f"{key} must be string or null"
    for key in ("issue_date", "expiry_date"):
        if not _optional_iso_date(result.get(key)):
            return False, f"{key} must be ISO date or null"
    return True, "ok"


def validate_cl_extraction(result: dict[str, Any]) -> tuple[bool, str]:
    ok, reason = validate_bl_extraction(result)
    if not ok:
        return ok, reason
    if not _optional_str_field(result.get("classification")):
        return False, "classification must be string or null"
    return True, "ok"


def validate_bill_extraction(result: dict[str, Any]) -> tuple[bool, str]:
    if not _optional_str_field(result.get("invoice_number")):
        return False, "invoice_number must be string or null"
    return True, "ok"


def _system_prompt_for_doc_type(doc_type: str) -> str:
    if doc_type == "w9":
        schema = (
            '{"entity_name": str|null, "business_name": str|null, '
            f'"classification": {sorted(_W9_CLASSIFICATION_VALUES)}|null, '
            '"is_signed": bool|null, "signature_date": "YYYY-MM-DD"|null, '
            '"confidence": number}'
        )
    elif doc_type == "coi":
        schema = (
            '{"issuing_authority": str|null, "issue_date": "YYYY-MM-DD"|null, '
            '"policies": [{"coverage_type": "GL"|"WC"|"OTHER", "carrier": str|null, '
            '"policy_number": str|null, "each_occurrence": number|null, '
            '"aggregate": number|null, "effective_date": "YYYY-MM-DD"|null, '
            '"expiry_date": "YYYY-MM-DD"|null}]|null, "confidence": number}'
        )
    elif doc_type == "bl":
        schema = (
            '{"license_number": str|null, "issuing_authority": str|null, '
            '"issue_date": "YYYY-MM-DD"|null, "expiry_date": "YYYY-MM-DD"|null, '
            '"confidence": number}'
        )
    elif doc_type == "cl":
        schema = (
            '{"license_number": str|null, "issuing_authority": str|null, '
            '"classification": str|null, "issue_date": "YYYY-MM-DD"|null, '
            '"expiry_date": "YYYY-MM-DD"|null, "confidence": number}'
        )
    elif doc_type == "bill":
        schema = '{"invoice_number": str|null, "confidence": number}'
    else:
        raise ValueError(f"unknown doc_type {doc_type!r}")
    return _EXTRACT_SYSTEM_BASE + f"Schema for this document ({doc_type}):\n{schema}\n"


def _validate_for_doc_type(doc_type: str) -> Callable[[dict[str, Any]], tuple[bool, str]]:
    validators = {
        "w9": validate_w9_extraction,
        "coi": validate_coi_extraction,
        "bl": validate_bl_extraction,
        "cl": validate_cl_extraction,
        "bill": validate_bill_extraction,
    }
    return validators[doc_type]


def _structured_task_for_doc_type(doc_type: str) -> StructuredTask:
    return StructuredTask(
        name=f"u658_extract_{doc_type}",
        system_prompt=_system_prompt_for_doc_type(doc_type),
        threshold=LLM_EXTRACT_THRESHOLD,
        build_user_message=lambda payload: str(payload.get("text") or ""),
        validate=_validate_for_doc_type(doc_type),
    )


def _ladder_for_model(model: str) -> tuple[Rung, ...]:
    if model == ARM_TO_LLM_MODEL["B"]:
        return ARM_B_LADDER
    if model == ARM_TO_LLM_MODEL["C"]:
        return ARM_C_LADDER
    raise ValueError(f"unsupported LLM model {model!r}")


def _provider_for_model(model: str) -> str:
    return _ladder_for_model(model)[0].provider


def _max_tokens_for_model(model: str) -> int:
    return _ladder_for_model(model)[0].max_tokens


def estimate_llm_call_cost_usd(model: str, input_chars: int) -> float:
    provider = _provider_for_model(model)
    max_out = _max_tokens_for_model(model)
    est_in = max(1, int(input_chars / 4) + 512)
    usage = Usage(input_tokens=est_in, output_tokens=max_out)
    cost = compute_cost_usd(provider=provider, model=model, usage=usage)
    return float(cost or 0.0)


def _attempt_llm_cost_usd(attempt: Attempt) -> float:
    cost = compute_cost_usd(
        provider=attempt.rung.provider,
        model=attempt.rung.model,
        usage=attempt.usage,
    )
    return float(cost or 0.0)


def classify_llm_attempt(attempt: Optional[Attempt]) -> tuple[dict[str, Any], str]:
    """Map a single-rung cascade attempt to (fields, failure_reason)."""
    if attempt is None:
        return {}, "transport_error"
    err = (attempt.error or "").strip()
    if err:
        low = err.lower()
        if "429" in err or "throttl" in low or "rate limit" in low:
            return {}, "throttled"
        if err == "no parseable result":
            return {}, "invalid_json"
        return {}, "transport_error"
    if attempt.result is None:
        return {}, "invalid_json"
    if not attempt.validated:
        return {}, "validation_failed"
    fields = {k: v for k, v in attempt.result.items() if k != "confidence"}
    return fields, ""


def record_spend(
    cost_state: dict[str, Any],
    amount: float,
    max_cost_usd: float,
) -> None:
    """Accrue billed spend; cap stops the next call, never under-report prior spend."""
    if amount:
        cost_state["usd"] += amount
    if cost_state["usd"] > max_cost_usd:
        cost_state["stopped"] = True


def extract_with_llm(
    doc_type: str,
    masked_text: str,
    model: str,
    *,
    cost_state: dict[str, Any],
    max_cost_usd: float,
    truncation_count: list[int],
    run_cascade_fn: Callable[..., Any],
    transport_for: Optional[Callable[[str], Any]] = None,
) -> LlmExtractResult:
    """LLM field extraction via a one-rung cascade (arms B/C)."""
    if doc_type not in LLM_DOC_TYPES:
        # Defence in depth for the W-9-only decision: never send a non-W-9 doc.
        return LlmExtractResult(fields={}, llm_cost_usd=0.0, failure_reason="payload_refused")
    text, truncated = truncate_llm_text(masked_text)
    try:
        assert_llm_text_safe(text, doc_type=doc_type)
    except ValueError:
        return LlmExtractResult(fields={}, llm_cost_usd=0.0, failure_reason="payload_refused")
    if truncated:
        truncation_count[0] += 1

    est = estimate_llm_call_cost_usd(model, len(text))
    if not cost_cap_allows(cost_state["usd"], est, max_cost_usd):
        cost_state["stopped"] = True
        return LlmExtractResult(fields={}, llm_cost_usd=0.0, failure_reason="cost_cap")

    task = _structured_task_for_doc_type(doc_type)
    ladder = _ladder_for_model(model)

    t0 = time.perf_counter()

    async def _invoke() -> CascadeResult:
        return await run_cascade_fn(
            task,
            {"doc_type": doc_type, "text": text},
            ladder=ladder,
            transport_for=transport_for,
        )

    cascade_result = asyncio.run(_invoke())

    latency_ms = (time.perf_counter() - t0) * 1000.0
    attempt = cascade_result.attempts[0] if cascade_result.attempts else None
    llm_cost = _attempt_llm_cost_usd(attempt) if attempt else 0.0
    record_spend(cost_state, llm_cost, max_cost_usd)
    fields, failure_reason = classify_llm_attempt(attempt)
    return LlmExtractResult(
        fields=fields,
        llm_cost_usd=llm_cost,
        failure_reason=failure_reason,
        llm_latency_ms=latency_ms,
    )


def normalize_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.casefold()
    s = re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)
    return s or None


def normalize_date(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s)
    if m:
        mo, da, yr = m.groups()
        y = int(yr)
        if y < 100:
            y += 2000 if y < 70 else 1900
        try:
            return date(y, int(mo), int(da)).isoformat()
        except ValueError:
            return None
    return normalize_string(s)


def normalize_money(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value.quantize(Decimal("0.01")))
    s = str(value).strip()
    if not s:
        return None
    s = re.sub(r"[^\d.\-]", "", s)
    if not s:
        return None
    try:
        return str(Decimal(s).quantize(Decimal("0.01")))
    except (InvalidOperation, ValueError):
        return normalize_string(value)


def normalize_alnum(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = re.sub(r"[^A-Za-z0-9]", "", str(value))
    return s.casefold() if s else None


def normalize_field_value(field: str, value: Any) -> Optional[str]:
    if field.endswith("_date") or field in ("issue_date", "expiry_date", "effective_date", "signature_date"):
        return normalize_date(value)
    if field in ("each_occurrence", "aggregate"):
        return normalize_money(value)
    if field in ("license_number", "policy_number", "invoice_number"):
        return normalize_alnum(value)
    return normalize_string(value)


def score_scalar_field(field: str, predicted: Any, truth: Any) -> FieldScore:
    pred_null = predicted is None or str(predicted).strip() == ""
    truth_null = truth is None or str(truth).strip() == ""
    if pred_null and truth_null:
        return FieldScore(field, True, True, True, True, False)
    if pred_null and not truth_null:
        return FieldScore(field, False, False, True, False, False)
    if not pred_null and truth_null:
        return FieldScore(field, False, False, False, True, True)
    exact = str(predicted).strip() == str(truth).strip()
    norm_p = normalize_field_value(field, predicted)
    norm_t = normalize_field_value(field, truth)
    normalized = norm_p == norm_t and norm_p is not None
    wrong = not normalized
    return FieldScore(field, exact, normalized, False, False, wrong)


def _policy_key(p: dict[str, Any]) -> str:
    return str(p.get("coverage_type") or "OTHER").upper()


def _normalize_policy(p: dict[str, Any]) -> dict[str, Optional[str]]:
    return {
        "coverage_type": _policy_key(p),
        "carrier": normalize_string(p.get("carrier")),
        "policy_number": normalize_alnum(p.get("policy_number")),
        "each_occurrence": normalize_money(p.get("each_occurrence")),
        "aggregate": normalize_money(p.get("aggregate")),
        "effective_date": normalize_date(p.get("effective_date")),
        "expiry_date": normalize_date(p.get("expiry_date")),
    }


def _group_policies_by_coverage(
    policies: Sequence[dict[str, Any]],
) -> dict[str, list[dict[str, Optional[str]]]]:
    grouped: dict[str, list[dict[str, Optional[str]]]] = defaultdict(list)
    for p in policies:
        grouped[_policy_key(p)].append(_normalize_policy(p))
    for cov in grouped:
        grouped[cov].sort(key=lambda x: (x.get("policy_number") or "", x.get("carrier") or ""))
    return grouped


def _match_policies_within_coverage(
    predicted: list[dict[str, Optional[str]]],
    truth: list[dict[str, Optional[str]]],
) -> tuple[list[tuple[dict[str, Optional[str]], dict[str, Optional[str]]]], list, list]:
    """Pair policies within one coverage type; extras stay unmatched."""
    remaining_truth = list(truth)
    remaining_pred = list(predicted)
    pairs: list[tuple[dict[str, Optional[str]], dict[str, Optional[str]]]] = []

    truth_by_pn: dict[str, dict[str, Optional[str]]] = {}
    for t in remaining_truth:
        pn = t.get("policy_number")
        if pn and pn not in truth_by_pn:
            truth_by_pn[pn] = t

    for p in list(remaining_pred):
        pn = p.get("policy_number")
        if pn and pn in truth_by_pn:
            t = truth_by_pn.pop(pn)
            remaining_truth.remove(t)
            remaining_pred.remove(p)
            pairs.append((p, t))

    remaining_pred.sort(key=lambda x: (x.get("policy_number") or "", x.get("carrier") or ""))
    remaining_truth.sort(key=lambda x: (x.get("policy_number") or "", x.get("carrier") or ""))
    while remaining_pred and remaining_truth:
        pairs.append((remaining_pred.pop(0), remaining_truth.pop(0)))

    return pairs, remaining_pred, remaining_truth


def match_coi_policies(
    predicted: Sequence[dict[str, Any]],
    truth: Sequence[dict[str, Any]],
) -> tuple[list[FieldScore], float, float]:
    pred_groups = _group_policies_by_coverage(predicted)
    truth_groups = _group_policies_by_coverage(truth)
    all_cov = set(pred_groups) | set(truth_groups)

    policy_fields = (
        "carrier",
        "policy_number",
        "each_occurrence",
        "aggregate",
        "effective_date",
        "expiry_date",
    )
    scores: list[FieldScore] = []
    matched_pred = 0
    matched_truth = 0
    total_pred = len(predicted)
    total_truth = len(truth)

    for cov in sorted(all_cov):
        pairs, extra_pred, extra_truth = _match_policies_within_coverage(
            pred_groups.get(cov, []),
            truth_groups.get(cov, []),
        )
        matched_pred += len(pairs)
        matched_truth += len(pairs)
        for idx, (pp, tp) in enumerate(pairs):
            suffix = f"{cov}_{idx}" if len(pairs) > 1 else cov
            for pf in policy_fields:
                scores.append(
                    score_scalar_field(f"policy_{suffix}_{pf}", pp.get(pf), tp.get(pf))
                )
        for ep_idx, _ep in enumerate(extra_pred):
            for pf in policy_fields:
                scores.append(
                    FieldScore(
                        f"policy_{cov}_extra_{ep_idx}_{pf}",
                        False,
                        False,
                        False,
                        True,
                        True,
                    )
                )
        for _ in extra_truth:
            for pf in policy_fields:
                scores.append(
                    FieldScore(f"policy_{cov}_missing_{pf}", False, False, True, False, False)
                )

    precision = matched_pred / total_pred if total_pred else 0.0
    recall = matched_truth / total_truth if total_truth else 0.0
    return scores, precision, recall


def tin_last4_match(predicted_tin: Any, truth_digits: Optional[str]) -> bool:
    if not truth_digits:
        return False
    pred = re.sub(r"\D", "", str(predicted_tin or ""))
    if len(pred) < 4:
        return False
    return pred[-4:] == truth_digits[-4:]


def _truth_tin_digits(truth: TruthRecord) -> Optional[str]:
    return truth._tin_digits


def score_w9_fields(
    predicted: dict[str, Any],
    truth: TruthRecord,
    *,
    label_classification: Optional[str] = None,
    include_tin_last4: bool = True,
) -> tuple[list[FieldScore], bool]:
    scores = [
        score_scalar_field("entity_name", predicted.get("entity_name"), truth.fields.get("entity_name")),
    ]
    if include_tin_last4:
        match = tin_last4_match(
            predicted.get("taxpayer_id_number"), _truth_tin_digits(truth)
        )
        scores.append(
            FieldScore(
                "tin_last4",
                match,
                match,
                predicted.get("taxpayer_id_number") in (None, ""),
                _truth_tin_digits(truth) is None,
                not match and _truth_tin_digits(truth) is not None,
            )
        )
    cls_scored = label_classification is not None
    if cls_scored:
        scores.append(
            score_scalar_field(
                "classification",
                predicted.get("classification"),
                label_classification,
            )
        )
    return scores, cls_scored


def core_fields_for_doc_type(
    doc_type: str, *, w9_labels: bool, arm: Optional[str] = None
) -> list[str]:
    if doc_type == "w9":
        fields = ["entity_name"]
        if arm in (None, "A"):
            fields.append("tin_last4")
        if w9_labels:
            fields.append("classification")
        return fields
    if doc_type == "coi":
        return ["issuing_authority", "issue_date"]
    if doc_type in ("bl", "cl"):
        base = ["license_number", "issuing_authority", "issue_date", "expiry_date"]
        if doc_type == "cl":
            base.append("classification")
        return base
    if doc_type == "bill":
        return ["invoice_number"]
    return []


def di_cost_usd(model: str, pages: int) -> float:
    rate = DI_PRICE_USD_PER_1K_PAGES.get(model, DI_PRICE_USD_PER_1K_PAGES["prebuilt-layout"])
    return (pages / 1000.0) * rate


def cost_cap_allows(current_usd: float, increment_usd: float, max_cost_usd: float) -> bool:
    return current_usd + increment_usd <= max_cost_usd


def parse_arm_a(doc_type: str, di_layout: dict) -> dict[str, Any]:
    if doc_type == "w9":
        return parse_w9_fields(di_layout)
    if doc_type == "coi":
        return parse_certificate_of_insurance_fields(di_layout)
    if doc_type == "bl":
        return parse_business_license_fields(di_layout)
    if doc_type == "cl":
        return parse_contractors_license_fields(di_layout)
    if doc_type == "bill":
        inv = parse_vendor_invoice_number(
            di_layout.get("key_value_pairs"), di_layout.get("content")
        )
        return {"invoice_number": inv}
    raise ValueError(f"unknown doc_type {doc_type!r}")


def flatten_llm_prediction(doc_type: str, raw: dict) -> dict[str, Any]:
    """Normalize bite-2 LLM dict shape to scorer inputs."""
    return dict(raw)


def score_prediction(
    doc_type: str,
    predicted: dict[str, Any],
    truth: TruthRecord,
    *,
    label_classification: Optional[str] = None,
    arm: str = "A",
) -> DocArmResult:
    scores: list[FieldScore] = []
    prec = rec = None
    cls_scored = False
    if doc_type == "w9":
        scores, cls_scored = score_w9_fields(
            predicted,
            truth,
            label_classification=label_classification,
            include_tin_last4=(arm == "A"),
        )
    elif doc_type == "coi":
        scores.append(
            score_scalar_field(
                "issuing_authority",
                predicted.get("issuing_authority"),
                truth.fields.get("issuing_authority"),
            )
        )
        scores.append(
            score_scalar_field(
                "issue_date", predicted.get("issue_date"), truth.fields.get("issue_date")
            )
        )
        p_scores, prec, rec = match_coi_policies(
            predicted.get("policies") or [], truth.coi_policies
        )
        scores.extend(p_scores)
    elif doc_type == "bl":
        for f in ("license_number", "issuing_authority", "issue_date", "expiry_date"):
            scores.append(score_scalar_field(f, predicted.get(f), truth.fields.get(f)))
    elif doc_type == "cl":
        for f in (
            "license_number",
            "issuing_authority",
            "classification",
            "issue_date",
            "expiry_date",
        ):
            scores.append(score_scalar_field(f, predicted.get(f), truth.fields.get(f)))
    elif doc_type == "bill":
        scores.append(
            score_scalar_field(
                "invoice_number",
                predicted.get("invoice_number"),
                truth.fields.get("invoice_number"),
            )
        )
    return DocArmResult(
        attachment_public_id="",
        doc_type=doc_type,
        arm="",
        field_scores=scores,
        policy_precision=prec,
        policy_recall=rec,
        classification_scored=cls_scored,
    )


def aggregate_field_rates(
    results: Sequence[DocArmResult],
    fields: Sequence[str],
    *,
    skip_field: Optional[Callable[[DocArmResult, str], bool]] = None,
) -> dict[str, dict[str, float]]:
    counts: dict[str, dict[str, int]] = {
        f: {"n": 0, "exact": 0, "normalized": 0, "null_pred": 0, "wrong": 0}
        for f in fields
    }
    for res in results:
        by_field = {fs.field: fs for fs in res.field_scores}
        for f in fields:
            if skip_field and skip_field(res, f):
                continue
            fs = by_field.get(f)
            if fs is None:
                continue
            counts[f]["n"] += 1
            counts[f]["exact"] += int(fs.exact)
            counts[f]["normalized"] += int(fs.normalized)
            counts[f]["null_pred"] += int(fs.predicted_null)
            counts[f]["wrong"] += int(fs.wrong)
    out: dict[str, dict[str, float]] = {}
    for f, c in counts.items():
        n = c["n"] or 1
        out[f] = {
            "exact_rate": c["exact"] / n,
            "normalized_rate": c["normalized"] / n,
            "null_rate": c["null_pred"] / n,
            "wrong_rate": c["wrong"] / n,
            "n": float(c["n"]),
        }
    return out


def count_w9_entity_name_digit_exclusions(
    results: Sequence[DocArmResult],
    truth_by_pid: dict[tuple[str, str], TruthRecord],
) -> int:
    seen: set[tuple[str, str]] = set()
    for res in results:
        if res.doc_type != "w9" or res.failed:
            continue
        key = (res.doc_type, res.attachment_public_id)
        if key in seen:
            continue
        truth = truth_by_pid.get(truth_lookup_key(res.doc_type, res.attachment_public_id))
        if truth and truth_entity_name_has_digit(truth):
            seen.add(key)
    return len(seen)


def w9_entity_name_skip(
    res: DocArmResult,
    field: str,
    truth_by_pid: dict[tuple[str, str], TruthRecord],
) -> bool:
    if res.doc_type != "w9" or field != "entity_name":
        return False
    truth = truth_by_pid.get(truth_lookup_key(res.doc_type, res.attachment_public_id))
    return truth is not None and truth_entity_name_has_digit(truth)


def masking_excluded_keys(results: Sequence[DocArmResult]) -> set[tuple[str, str]]:
    """(doc_type, attachment) of every doc whose B/C payload was extra-masked or refused."""
    return {(r.doc_type, r.attachment_public_id) for r in results if r.masking_excluded}


def build_arm_metrics(
    results: Sequence[DocArmResult],
    doc_types: Sequence[str],
    arms: Sequence[str],
    *,
    w9_labels: bool,
    truth_by_pid: dict[tuple[str, str], TruthRecord],
) -> tuple[dict[str, dict[str, dict[str, dict[str, float]]]], dict[str, int]]:
    """Per doc type and arm field rates, with the fairness exclusions applied to
    EVERY arm: W-9 truth names containing digits (aggressive masking hides them
    from B/C) and docs whose B/C payload was extra-masked or refused."""
    excluded = masking_excluded_keys(results)

    def skip(res: DocArmResult, field: str) -> bool:
        if (res.doc_type, res.attachment_public_id) in excluded:
            return True
        return w9_entity_name_skip(res, field, truth_by_pid)

    arm_metrics: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for dt in doc_types:
        for arm in arms:
            arm_results = [r for r in results if r.doc_type == dt and r.arm == arm and not r.failed]
            fields = core_fields_for_doc_type(dt, w9_labels=w9_labels, arm=arm)
            arm_metrics.setdefault(dt, {})[arm] = aggregate_field_rates(
                arm_results, fields, skip_field=skip
            )
    counts = {dt: sum(1 for d, _ in excluded if d == dt) for dt in doc_types}
    return arm_metrics, counts


def comparison_core_fields(doc_type: str, *, w9_labels: bool) -> list[str]:
    """Core fields used to compare layout (A) vs LLM challengers (B/C)."""
    if doc_type == "w9":
        fields = ["entity_name"]
        if w9_labels:
            fields.append("classification")
        return fields
    return core_fields_for_doc_type(doc_type, w9_labels=w9_labels, arm="A")


def _arm_scored_doc_count(rates: dict[str, dict[str, float]], core: Sequence[str]) -> int:
    if not rates or not core:
        return 0
    return int(min(rates.get(f, {}).get("n", 0.0) for f in core))


def _challenger_vs_a_verdict(
    arm_a: dict[str, dict[str, float]],
    challenger: dict[str, dict[str, float]],
    core: Sequence[str],
) -> str:
    if not challenger:
        return "NOT-EVALUATED"
    if not arm_a:
        return "NOT-EVALUATED"
    n_a = _arm_scored_doc_count(arm_a, core)
    n_ch = _arm_scored_doc_count(challenger, core)
    if n_a < VERDICT_MIN_SCORED_DOCS or n_ch < VERDICT_MIN_SCORED_DOCS:
        return "INSUFFICIENT-SAMPLE"
    all_gte = True
    any_strict = False
    for f in core:
        a_norm = arm_a.get(f, {}).get("normalized_rate", 0.0)
        c_norm = challenger.get(f, {}).get("normalized_rate", 0.0)
        if c_norm < a_norm:
            all_gte = False
        if c_norm > a_norm:
            any_strict = True
    if all_gte and any_strict:
        return "SWITCH-CANDIDATE"
    return "KEEP-LAYOUT"


def verdict_for_doc_type(
    doc_type: str,
    arm_a: dict[str, dict[str, float]],
    arm_b: dict[str, dict[str, float]],
    arm_c: dict[str, dict[str, float]],
    *,
    w9_labels: bool,
    incomplete: bool,
    arms_run: Sequence[str],
) -> str:
    if incomplete:
        return "INCOMPLETE"
    # An LLM arm that cannot run on this doc type (W-9 only, Chris 2026-10-16) was
    # not evaluated — it must not read as INSUFFICIENT-SAMPLE.
    challengers = [
        a for a in ("B", "C")
        if a in arms_run and (a not in LLM_ARMS or doc_type in LLM_DOC_TYPES)
    ]
    if "A" not in arms_run or not challengers:
        return "NOT-EVALUATED"
    core = comparison_core_fields(doc_type, w9_labels=w9_labels)
    by_arm = {"B": arm_b, "C": arm_c}
    statuses = [_challenger_vs_a_verdict(arm_a, by_arm[ch], core) for ch in challengers]
    if any(s == "NOT-EVALUATED" for s in statuses):
        return "NOT-EVALUATED"
    if any(s == "INSUFFICIENT-SAMPLE" for s in statuses):
        return "INSUFFICIENT-SAMPLE"
    if all(s == "SWITCH-CANDIDATE" for s in statuses):
        return "SWITCH-CANDIDATE"
    return "KEEP-LAYOUT"


def assert_outputs_privacy_safe(path: Path) -> None:
    _u654a_privacy_safe(path)
    text = path.read_text(encoding="utf-8")
    tin_scan = _UUID_TOKEN_RE.sub("", text)
    if _TIN_OUTPUT_RE.search(tin_scan):
        raise ValueError(f"TIN-shaped digit sequence in output file {path}")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        _walk_forbidden_json_keys(data)


def _walk_forbidden_json_keys(node: Any, prefix: str = "") -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if str(k).lower() in _FORBIDDEN_VALUE_KEYS:
                raise ValueError(f"forbidden JSON key {k!r} under {prefix}")
            _walk_forbidden_json_keys(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _walk_forbidden_json_keys(item, f"{prefix}[{i}]")


def load_w9_labels(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            pid = (row.get("attachment_public_id") or "").strip()
            cls = (row.get("classification") or "").strip()
            if pid and cls:
                out[pid] = cls
    return out


def _parse_sql_date(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    s = str(val).strip()
    return s[:10] if s else None


def _tin_digits_from_encrypted(stored: str) -> Optional[str]:
    plain = decrypt_if_encrypted(stored)
    digits = re.sub(r"\D", "", plain or "")
    return digits or None


def fetch_population_counts(conn) -> dict[str, int]:
    cur = conn.cursor()
    counts: dict[str, int] = {}
    for key, sql in (
        ("w9", _POPULATION_W9_SQL),
        ("coi", _POPULATION_COI_SQL),
        ("bl", _POPULATION_BL_SQL),
        ("cl", _POPULATION_CL_SQL),
        ("bill", _POPULATION_BILL_SQL),
    ):
        cur.execute(sql)
        row = cur.fetchone()
        counts[key] = int(row[0]) if row else 0
    return counts


def _sample_limit(doc_type: str, population: int, override: Optional[int]) -> int:
    if override is not None:
        return min(override, population)
    default = DEFAULT_SAMPLE_PER_TYPE[doc_type]
    if default is None:
        return population
    return min(default, population)


def _rng_sample(rows: list[Any], n: int, seed: int) -> list[Any]:
    if n >= len(rows):
        return list(rows)
    rng = random.Random(seed)
    return rng.sample(rows, n)


def dedupe_w9_attachment_rows(rows: Sequence[Any]) -> list[Any]:
    """One row per attachment; deterministic MIN(VendorId) when vendors duplicate."""
    by_attachment: dict[int, Any] = {}
    for row in rows:
        aid = int(row.AttachmentId)
        existing = by_attachment.get(aid)
        if existing is None:
            by_attachment[aid] = row
            continue
        v_new = row.VendorId
        v_old = existing.VendorId
        if v_new is None:
            continue
        if v_old is None or int(v_new) < int(v_old):
            by_attachment[aid] = row
    return list(by_attachment.values())


def load_samples_and_truth(
    doc_types: Sequence[str],
    *,
    sample_per_type: dict[str, Optional[int]],
    seed: int,
) -> tuple[list[SampleDoc], dict[tuple[str, str], TruthRecord], list[SampleDoc], dict[str, int]]:
    docs: list[SampleDoc] = []
    truth_by_pid: dict[tuple[str, str], TruthRecord] = {}
    w9_label_rows: list[SampleDoc] = []
    populations: dict[str, int] = {}

    with get_connection() as conn:
        conn.autocommit = False
        try:
            cur = conn.cursor()
            populations = fetch_population_counts(conn)

            if "w9" in doc_types:
                n = _sample_limit("w9", populations["w9"], sample_per_type.get("w9"))
                cur.execute(_SAMPLE_W9_SQL)
                w9_rows = dedupe_w9_attachment_rows(cur.fetchall())
                for row in _rng_sample(w9_rows, n, seed + 1):
                    pid = str(row.AttachmentPublicId)
                    doc = SampleDoc(
                        doc_type="w9",
                        attachment_id=int(row.AttachmentId),
                        attachment_public_id=pid,
                        vendor_id=int(row.VendorId) if row.VendorId is not None else None,
                        entity_id=int(row.TaxpayerId),
                    )
                    docs.append(doc)
                    w9_label_rows.append(doc)
                    tr = TruthRecord(
                        fields={"entity_name": str(row.EntityName or "")},
                    )
                    tr._tin_digits = _tin_digits_from_encrypted(str(row.TaxpayerIdNumber))
                    truth_by_pid[truth_lookup_key("w9", pid)] = tr

            if "coi" in doc_types:
                n = _sample_limit("coi", populations["coi"], sample_per_type.get("coi"))
                cur.execute(_SAMPLE_COI_SQL)
                for row in _rng_sample(list(cur.fetchall()), n, seed + 2):
                    pid = str(row.AttachmentPublicId)
                    coi_id = int(row.CoiId)
                    docs.append(
                        SampleDoc(
                            doc_type="coi",
                            attachment_id=int(row.AttachmentId),
                            attachment_public_id=pid,
                            entity_id=coi_id,
                        )
                    )
                    tr = TruthRecord(
                        fields={
                            "issuing_authority": row.IssuingAuthority,
                            "issue_date": _parse_sql_date(row.IssueDate),
                        }
                    )
                    cur.execute(_COI_POLICIES_SQL, (coi_id,))
                    policies = []
                    for pr in cur.fetchall():
                        policies.append(
                            {
                                "coverage_type": str(pr.CoverageType or "OTHER"),
                                "carrier": pr.Carrier,
                                "policy_number": pr.PolicyNumber,
                                "each_occurrence": pr.EachOccurrence,
                                "aggregate": pr.Aggregate,
                                "effective_date": _parse_sql_date(pr.EffectiveDate),
                                "expiry_date": _parse_sql_date(pr.ExpiryDate),
                            }
                        )
                    tr.coi_policies = policies
                    truth_by_pid[truth_lookup_key("coi", pid)] = tr

            if "bl" in doc_types:
                n = _sample_limit("bl", populations["bl"], sample_per_type.get("bl"))
                cur.execute(_SAMPLE_BL_SQL)
                for row in _rng_sample(list(cur.fetchall()), n, seed + 3):
                    pid = str(row.AttachmentPublicId)
                    docs.append(
                        SampleDoc(
                            doc_type="bl",
                            attachment_id=int(row.AttachmentId),
                            attachment_public_id=pid,
                            entity_id=int(row.LicenseId),
                        )
                    )
                    truth_by_pid[truth_lookup_key("bl", pid)] = TruthRecord(
                        fields={
                            "license_number": row.LicenseNumber,
                            "issuing_authority": row.IssuingAuthority,
                            "issue_date": _parse_sql_date(row.IssueDate),
                            "expiry_date": _parse_sql_date(row.ExpiryDate),
                        }
                    )

            if "cl" in doc_types:
                n = _sample_limit("cl", populations["cl"], sample_per_type.get("cl"))
                cur.execute(_SAMPLE_CL_SQL)
                for row in _rng_sample(list(cur.fetchall()), n, seed + 4):
                    pid = str(row.AttachmentPublicId)
                    docs.append(
                        SampleDoc(
                            doc_type="cl",
                            attachment_id=int(row.AttachmentId),
                            attachment_public_id=pid,
                            entity_id=int(row.LicenseId),
                        )
                    )
                    truth_by_pid[truth_lookup_key("cl", pid)] = TruthRecord(
                        fields={
                            "license_number": row.LicenseNumber,
                            "issuing_authority": row.IssuingAuthority,
                            "classification": row.Classification,
                            "issue_date": _parse_sql_date(row.IssueDate),
                            "expiry_date": _parse_sql_date(row.ExpiryDate),
                        }
                    )

            if "bill" in doc_types:
                n = _sample_limit("bill", populations["bill"], sample_per_type.get("bill"))
                cur.execute(_SAMPLE_BILL_SQL)
                for row in _rng_sample(list(cur.fetchall()), n, seed + 5):
                    pid = str(row.AttachmentPublicId)
                    docs.append(
                        SampleDoc(
                            doc_type="bill",
                            attachment_id=int(row.AttachmentId),
                            attachment_public_id=pid,
                            entity_id=int(row.BillId),
                        )
                    )
                    truth_by_pid[truth_lookup_key("bill", pid)] = TruthRecord(
                        fields={"invoice_number": row.BillNumber},
                    )
        finally:
            conn.rollback()

    return docs, truth_by_pid, w9_label_rows, populations


def dry_run_summary(
    populations: dict[str, int],
    doc_types: Sequence[str],
    arms: Sequence[str],
    sample_counts: dict[str, int],
) -> dict[str, Any]:
    # sample_counts carries a "_total" key beside the per-type counts: count docs once.
    all_docs = sum(v for k, v in sample_counts.items() if k != "_total")
    llm_docs = sum(sample_counts.get(dt, 0) for dt in LLM_DOC_TYPES)  # W-9 only
    layout_cost = (
        di_cost_usd("prebuilt-layout", all_docs * CONSERVATIVE_PAGES_ESTIMATE) if "A" in arms else 0.0
    )
    read_cost = (
        di_cost_usd("prebuilt-read", llm_docs * CONSERVATIVE_PAGES_ESTIMATE)
        if ("B" in arms or "C" in arms)
        else 0.0
    )
    llm_cost = 0.0
    for arm in arms:
        if arm in ("B", "C"):
            llm_cost += llm_docs * estimate_llm_call_cost_usd(
                ARM_TO_LLM_MODEL[arm],
                _LLM_ESTIMATE_INPUT_CHARS,
            )
    return {
        "model_version": MODEL_VERSION,
        "populations": {k: populations[k] for k in doc_types if k in populations},
        "sample_counts": sample_counts,
        "arms": list(arms),
        "estimated_di_layout_usd": round(layout_cost, 4),
        "estimated_di_read_usd": round(read_cost, 4),
        "estimated_llm_usd": round(llm_cost, 4),
        "estimated_total_usd_conservative": round(layout_cost + read_cost + llm_cost, 4),
        "llm_implementation": "bite-2 (extract_with_llm + one-rung cascade per arm)",
    }


def write_w9_labelling_sheet(path: Path, docs: Sequence[SampleDoc]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["attachment_public_id", "vendor_id", "classification"])
        for d in docs:
            if d.doc_type != "w9":
                continue
            writer.writerow([d.attachment_public_id, d.vendor_id or "", ""])
    assert_outputs_privacy_safe(path)


def run_bakeoff(
    docs: Sequence[SampleDoc],
    truth_by_pid: dict[tuple[str, str], TruthRecord],
    *,
    arms: Sequence[str],
    max_cost_usd: float,
    w9_labels: dict[str, str],
    di_service_factory: Callable[[], Any],
    download_attachment: Callable[[SampleDoc], tuple[bytes, str]],
    run_cascade_fn: Optional[Callable[..., Any]] = None,
    transport_for: Optional[Callable[[str], Any]] = None,
) -> tuple[list[DocArmResult], dict[str, Any], bool]:
    cost_state: dict[str, Any] = {
        "usd": 0.0,
        "stopped": False,
        "llm_truncations": 0,
        "llm_failure_counts": defaultdict(int),
    }
    results: list[DocArmResult] = []
    failed_docs = 0
    interrupted = False
    di_service = di_service_factory()
    cascade_fn = run_cascade_fn or run_cascade
    trunc_counter: list[int] = [0]

    try:
        for doc in docs:
            tkey = truth_lookup_key(doc.doc_type, doc.attachment_public_id)
            truth = truth_by_pid[tkey]
            label_cls = w9_labels.get(doc.attachment_public_id)
            layout_cache: Optional[dict] = None
            read_cache: Optional[dict] = None
            attachment_payload: Optional[tuple[bytes, str]] = None
            read_di_billed = False

            def _attachment_bytes() -> tuple[bytes, str]:
                nonlocal attachment_payload
                if attachment_payload is None:
                    attachment_payload = download_attachment(doc)
                return attachment_payload

            for arm in arms:
                if arm in LLM_ARMS and doc.doc_type not in LLM_DOC_TYPES:
                    continue  # Chris 2026-10-16: LLM arms run on W-9 only (verdict NOT-EVALUATED)
                if cost_state["stopped"]:
                    results.append(
                        DocArmResult(
                            attachment_public_id=doc.attachment_public_id,
                            doc_type=doc.doc_type,
                            arm=arm,
                            field_scores=[],
                            failed=True,
                            llm_failure_reason="cost_cap",
                        )
                    )
                    failed_docs += 1
                    continue

                t0 = time.perf_counter()
                di_pages = 0
                di_cost = 0.0
                llm_cost = 0.0
                failed = False
                llm_reason = ""
                masking_excluded = False
                llm_outcome: Optional[LlmExtractResult] = None
                predicted: dict[str, Any] = {}

                try:
                    if arm == "A":
                        est = di_cost_usd(
                            "prebuilt-layout",
                            CONSERVATIVE_PAGES_ESTIMATE,
                        )
                        if not cost_cap_allows(cost_state["usd"], est, max_cost_usd):
                            cost_state["stopped"] = True
                            failed = True
                        else:
                            if layout_cache is None:
                                data, ctype = _attachment_bytes()
                                layout_cache = di_service.extract_invoice(data, ctype)
                            di_pages = int(
                                layout_cache.get("pages_count") or CONSERVATIVE_PAGES_ESTIMATE
                            )
                            actual_cost = di_cost_usd("prebuilt-layout", di_pages)
                            record_spend(cost_state, actual_cost, max_cost_usd)
                            di_cost = actual_cost
                            predicted = parse_arm_a(doc.doc_type, layout_cache)

                    elif arm in ("B", "C"):
                        model_name = ARM_TO_LLM_MODEL[arm]
                        est = di_cost_usd("prebuilt-read", CONSERVATIVE_PAGES_ESTIMATE)
                        est += estimate_llm_call_cost_usd(
                            model_name, min(_LLM_ESTIMATE_INPUT_CHARS, LLM_TEXT_CHAR_BUDGET)
                        )
                        if not cost_cap_allows(cost_state["usd"], est, max_cost_usd):
                            cost_state["stopped"] = True
                            failed = True
                        else:
                            if read_cache is None:
                                data, ctype = _attachment_bytes()
                                read_cache = di_service.extract_text(data, ctype)
                            di_pages = int(
                                read_cache.get("pages_count") or CONSERVATIVE_PAGES_ESTIMATE
                            )
                            actual_di = di_cost_usd("prebuilt-read", di_pages)
                            masked, extra_masked = prepare_llm_read_text_with_flag(
                                read_cache.get("content") or "",
                                doc.doc_type,
                            )
                            llm_pre_est = estimate_llm_call_cost_usd(
                                model_name, len(truncate_llm_text(masked)[0])
                            )
                            di_increment = actual_di if not read_di_billed else 0.0
                            if not cost_cap_allows(
                                cost_state["usd"], di_increment + llm_pre_est, max_cost_usd
                            ):
                                cost_state["stopped"] = True
                                failed = True
                            else:
                                if not read_di_billed:
                                    record_spend(cost_state, actual_di, max_cost_usd)
                                    read_di_billed = True
                                    di_cost = actual_di
                                else:
                                    di_cost = 0.0
                                llm_outcome = extract_with_llm(
                                    doc.doc_type,
                                    masked,
                                    model_name,
                                    cost_state=cost_state,
                                    max_cost_usd=max_cost_usd,
                                    truncation_count=trunc_counter,
                                    run_cascade_fn=cascade_fn,
                                    transport_for=transport_for,
                                )
                                llm_cost = llm_outcome.llm_cost_usd
                                llm_reason = llm_outcome.failure_reason
                                masking_excluded = extra_masked or llm_reason == "payload_refused"
                                if llm_reason:
                                    cost_state["llm_failure_counts"][llm_reason] += 1
                                if llm_reason in ("cost_cap",):
                                    failed = True
                                else:
                                    predicted = flatten_llm_prediction(
                                        doc.doc_type, llm_outcome.fields
                                    )
                except Exception:
                    failed = True
                    failed_docs += 1
                    llm_reason = llm_reason or "error"

                latency_ms = (time.perf_counter() - t0) * 1000.0
                if llm_outcome is not None:
                    latency_ms = max(latency_ms, llm_outcome.llm_latency_ms)

                if failed:
                    results.append(
                        DocArmResult(
                            attachment_public_id=doc.attachment_public_id,
                            doc_type=doc.doc_type,
                            arm=arm,
                            field_scores=[],
                            di_pages=di_pages,
                            di_cost_usd=di_cost,
                            llm_cost_usd=llm_cost,
                            latency_ms=latency_ms,
                            failed=True,
                            llm_failure_reason=llm_reason
                            or ("cost_cap" if cost_state["stopped"] else ""),
                        )
                    )
                    continue

                scored = score_prediction(
                    doc.doc_type,
                    predicted,
                    truth,
                    label_classification=label_cls,
                    arm=arm,
                )
                scored.attachment_public_id = doc.attachment_public_id
                scored.arm = arm
                scored.di_pages = di_pages
                scored.di_cost_usd = di_cost
                scored.llm_cost_usd = llm_cost
                scored.latency_ms = latency_ms
                scored.llm_failure_reason = llm_reason
                scored.masking_excluded = masking_excluded
                results.append(scored)
    except KeyboardInterrupt:
        interrupted = True
    finally:
        cost_state["llm_truncations"] = int(trunc_counter[0])

    overshoot = max(0.0, cost_state["usd"] - max_cost_usd)
    meta = {
        "total_cost_usd": cost_state["usd"],
        "cost_cap_usd": max_cost_usd,
        "cost_overshoot_usd": round(overshoot, 6),
        "cap_tripped": cost_state["stopped"],
        "failed_doc_runs": failed_docs,
        "interrupted": interrupted,
        "llm_text_truncations": cost_state.get("llm_truncations", 0),
        "llm_failure_counts": dict(cost_state.get("llm_failure_counts", {})),
    }
    incomplete = interrupted or cost_state["stopped"] or failed_docs > 0
    return results, meta, incomplete


def write_outputs(
    out_dir: Path,
    stamp: str,
    results: Sequence[DocArmResult],
    summary: dict[str, Any],
) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"u658_report_{stamp}.md"
    csv_path = out_dir / f"u658_per_doc_{stamp}.csv"
    json_path = out_dir / f"u658_summary_{stamp}.json"

    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "attachment_public_id",
                "doc_type",
                "arm",
                "field",
                "exact_match",
                "normalized_match",
                "predicted_null",
                "truth_null",
                "wrong",
                "di_pages",
                "di_cost_usd",
                "llm_cost_usd",
                "latency_ms",
                "failed",
                "llm_failure_reason",
            ]
        )
        for r in results:
            if r.failed or not r.field_scores:
                writer.writerow(
                    [
                        r.attachment_public_id,
                        r.doc_type,
                        r.arm,
                        "",
                        "",
                        "",
                        "",
                        "",
                        "",
                        r.di_pages,
                        f"{r.di_cost_usd:.6f}",
                        f"{r.llm_cost_usd:.6f}",
                        f"{r.latency_ms:.2f}",
                        "1",
                        r.llm_failure_reason,
                    ]
                )
                continue
            for fs in r.field_scores:
                writer.writerow(
                    [
                        r.attachment_public_id,
                        r.doc_type,
                        r.arm,
                        fs.field,
                        int(fs.exact),
                        int(fs.normalized),
                        int(fs.predicted_null),
                        int(fs.truth_null),
                        int(fs.wrong),
                        r.di_pages,
                        f"{r.di_cost_usd:.6f}",
                        f"{r.llm_cost_usd:.6f}",
                        f"{r.latency_ms:.2f}",
                        "0",
                        r.llm_failure_reason,
                    ]
                )

    json_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        f"# U-658 extraction bake-off ({stamp})",
        "",
        f"- Model version: `{MODEL_VERSION}`",
        f"- Run status: **{summary.get('run_status', 'unknown')}**",
        "",
        "## Populations",
        "",
        "```json",
        json.dumps(summary.get("populations", {}), indent=2),
        "```",
        "",
        "## Verdicts",
        "",
    ]
    for dt, v in summary.get("verdicts", {}).items():
        lines.append(f"- **{dt}**: {v}")
    lines.extend(
        [
            "",
            "## Arm metrics (normalized accuracy)",
            "",
            "```json",
            json.dumps(summary.get("arm_metrics", {}), indent=2),
            "```",
            "",
        ]
    )
    report_path.write_text("\n".join(lines), encoding="utf-8")

    for p in (csv_path, json_path, report_path):
        assert_outputs_privacy_safe(p)
    return report_path, csv_path, json_path


def _sigterm_raises_keyboard_interrupt(signum: int, frame: Any) -> None:
    """Treat SIGTERM like Ctrl-C so partial bake-off results flush to disk."""
    raise KeyboardInterrupt()


def arm_c_route_is_foundry() -> bool:
    """Arm C must not hit the direct Anthropic API (Foundry-routed Haiku only)."""
    from intelligence.transport.anthropic import (
        ANTHROPIC_DIRECT_URL,
        _resolve_messages_route,
    )

    url, _, _ = _resolve_messages_route(
        ARM_TO_LLM_MODEL["C"],
        direct_api_key=None,
    )
    return url.rstrip("/") != ANTHROPIC_DIRECT_URL.rstrip("/")


def _default_download(doc: SampleDoc) -> tuple[bytes, str]:
    from entities.attachment.business.service import AttachmentService
    from shared.storage import AzureBlobStorage

    att = AttachmentService().read_by_public_id(public_id=doc.attachment_public_id)
    if not att or not att.blob_url:
        raise ValueError("attachment missing blob")
    data, _ = AzureBlobStorage().download_file(att.blob_url)
    ctype = att.content_type or "application/pdf"
    return data, ctype


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="U-658 field extraction bake-off.")
    ap.add_argument(
        "--doc-types",
        default=",".join(_DOC_TYPES),
        help="Comma-separated: w9,coi,bl,cl,bill",
    )
    ap.add_argument("--sample-per-type", type=int, default=None)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--arms", default="A,B,C", help="Comma-separated arms A,B,C")
    ap.add_argument("--max-cost-usd", type=float, default=DEFAULT_MAX_COST_USD)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--w9-labels", type=Path, default=None)
    ap.add_argument(
        "--run",
        action="store_true",
        help="Execute DI / scoring (default: dry-run only)",
    )
    args = ap.parse_args(argv)

    if args.max_cost_usd > MAX_COST_USD_CEILING:
        ap.error(f"--max-cost-usd must be <= {MAX_COST_USD_CEILING:g} (hard ceiling)")

    signal.signal(signal.SIGTERM, _sigterm_raises_keyboard_interrupt)

    doc_types = [x.strip().lower() for x in args.doc_types.split(",") if x.strip()]
    arms = [x.strip().upper() for x in args.arms.split(",") if x.strip()]
    for a in arms:
        if a not in _ARMS:
            raise SystemExit(f"invalid arm {a!r}")

    sample_override = args.sample_per_type
    sample_per_type = {dt: sample_override for dt in doc_types}

    w9_labels: dict[str, str] = {}
    if args.w9_labels:
        w9_labels = load_w9_labels(args.w9_labels)

    docs, truth_by_pid, w9_sheet_docs, populations = load_samples_and_truth(
        doc_types,
        sample_per_type=sample_per_type,
        seed=args.seed,
    )
    sample_counts: dict[str, int] = defaultdict(int)
    for d in docs:
        sample_counts[d.doc_type] += 1
    sample_counts["_total"] = len(docs)

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    label_path = args.out_dir / f"u658_w9_labelling_sheet_{stamp}.csv"
    write_w9_labelling_sheet(label_path, w9_sheet_docs)

    dry = dry_run_summary(populations, doc_types, arms, sample_counts)
    print(json.dumps(dry, indent=2))

    if not args.run:
        return 0

    if "C" in arms and not arm_c_route_is_foundry():
        print(
            "ERROR: Arm C (claude-haiku-5-5) must route via Azure Foundry — refusing "
            "the direct Anthropic API. Configure Foundry routing or omit arm C.",
            flush=True,
        )
        return 2

    from integrations.azure.document_intelligence.business.service import (
        DocumentIntelligenceService,
    )

    results: list[DocArmResult] = []
    run_meta: dict[str, Any] = {}
    incomplete = True
    interrupted = False
    # U-701d: offline harness — meter LLM spend (Claude AND Foundry) into the
    # in-process isolated ledger, never the prod LlmDailySpend / live Claude cap.
    from intelligence.cascade import claude_budget

    claude_budget.use_isolated_spend_ledger(Decimal(str(args.max_cost_usd)))
    try:
        results, run_meta, incomplete = run_bakeoff(
            docs,
            truth_by_pid,
            arms=arms,
            max_cost_usd=args.max_cost_usd,
            w9_labels=w9_labels,
            di_service_factory=DocumentIntelligenceService,
            download_attachment=_default_download,
        )
    except KeyboardInterrupt:
        interrupted = True
        incomplete = True
    finally:
        claude_budget.clear_isolated_spend_ledger()

    verdicts: dict[str, str] = {}
    w9_entity_name_digit_excluded = count_w9_entity_name_digit_exclusions(
        results, truth_by_pid
    )
    arm_metrics, masking_excluded_counts = build_arm_metrics(
        results, doc_types, arms, w9_labels=bool(w9_labels), truth_by_pid=truth_by_pid
    )
    for dt in doc_types:
        a_rates = arm_metrics.get(dt, {}).get("A", {})
        b_rates = arm_metrics.get(dt, {}).get("B", {})
        c_rates = arm_metrics.get(dt, {}).get("C", {})
        verdicts[dt] = verdict_for_doc_type(
            dt,
            a_rates,
            b_rates,
            c_rates,
            w9_labels=bool(w9_labels),
            incomplete=incomplete,
            arms_run=arms,
        )

    if interrupted or run_meta.get("interrupted"):
        run_status = "partial"
    elif incomplete:
        run_status = "INCOMPLETE"
    else:
        run_status = "complete"

    summary = {
        "model_version": MODEL_VERSION,
        "run_status": run_status,
        "populations": {k: populations[k] for k in doc_types},
        "sample_counts": sample_counts,
        "arms": arms,
        "w9_classification": (
            "scored_via_hand_labels" if w9_labels else "unscored (no hand labels)"
        ),
        "arm_metrics": arm_metrics,
        "verdicts": verdicts,
        "cost": run_meta,
        "cap_tripped": run_meta.get("cap_tripped"),
        "llm_text_truncations": run_meta.get("llm_text_truncations", 0),
        "llm_failure_counts": run_meta.get("llm_failure_counts", {}),
        "w9_entity_name_digit_excluded": w9_entity_name_digit_excluded,
        "masking_excluded_docs": masking_excluded_counts,
    }

    write_outputs(args.out_dir, stamp, results, summary)
    return 0


if __name__ == "__main__":
    assert_cli_system_admin()
    raise SystemExit(main())
