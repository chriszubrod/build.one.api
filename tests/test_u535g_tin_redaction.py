"""U-535g — TIN redaction in attachment extraction (pure + service)."""

from __future__ import annotations

import json
import re
import types

import pytest

from entities.attachment.business.extraction_service import (
    EXTRACTION_BLOB_KEYS,
    REDACTION_VERSION,
    AttachmentExtractionService,
)
from entities.attachment.business.tin_redaction import mask_tins, mask_tins_in_fields
from tests._attachment_extraction_fakes import _FakeDI, _FakeRepo, _FakeStorage, _text_pdf_bytes

# Test-only taxpayer-id shapes (not real identifiers).
_SSN = "123-45-6789"
_SSN_MASKED = "***-**-6789"
_EIN = "12-3456789"
_EIN_MASKED = "**-***6789"
_BARE9 = "123456789"

_UNMASKED_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")


def _visible_digits(s: str) -> str:
    return "".join(c for c in s if c.isdigit())


def _assert_no_unmasked_tin_in_payload(raw: bytes) -> None:
    payload = json.loads(raw)
    blob = json.dumps(payload)
    assert not _UNMASKED_SSN.search(blob)
    assert _BARE9 not in blob and _EIN not in blob


def _attachment(**overrides):
    base = dict(
        id=1,
        public_id="aaaaaaaa-0000-0000-0000-000000000001",
        blob_url="https://blob.example/source.pdf",
        content_type="application/pdf",
        file_hash="hash-abc",
        vendor_invoice_number=None,
        extraction_status="pending",
        extracted_text_blob_url=None,
        category="bill_line_item",
        parse_invoice_number=True,
        claim_modified_datetime=None,
        is_taxpayer_document=True,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


# --- mask_tins unit ------------------------------------------------------- #


@pytest.mark.parametrize(
    "raw, aggressive, expected, count",
    [
        (None, True, "", 0),
        (None, False, "", 0),
        ("", False, "", 0),
        ("", True, "", 0),
        (_SSN, False, _SSN_MASKED, 1),
        (_EIN, False, _EIN_MASKED, 1),
        ("12 \u2013 3456789", False, "** \u2013 ***6789", 1),
        ("112-3456789", False, "112-3456789", 0),
        ("12-34567890", False, "12-34567890", 0),
        (_BARE9, False, _BARE9, 0),
        ("1 2 3 4 5 6 7 8 9", False, "1 2 3 4 5 6 7 8 9", 0),
        ("TIN " + _SSN + " and " + _EIN, False, "TIN " + _SSN_MASKED + " and " + _EIN_MASKED, 2),
        ("123 – 45 – 6789", False, "*** – ** – 6789", 1),
        ("123 - 45 - 6789", False, "*** - ** - 6789", 1),
        ("Invoice 2025-10-13", False, "Invoice 2025-10-13", 0),
        ("1123-45-6789", False, "1123-45-6789", 0),
        ("123-45-67890", False, "123-45-67890", 0),
        (_SSN, True, "***-**-****", 3),
        (_EIN, True, "**-*******", 2),
        (_BARE9, True, "*********", 1),
        ("Zip 37013-1234, phone 6155551234", True, "Zip *****-****, phone **********", 3),
        ("Name only", True, "Name only", 0),
    ],
)
def test_mask_tins_cases(raw, aggressive, expected, count):
    out, n = mask_tins(raw, aggressive=aggressive)
    assert n == count
    assert out == expected
    if raw is not None:
        assert len(out) == len(raw)


# Taxpayer text keeps NO digit, whatever separates the TIN's digit groups.
@pytest.mark.parametrize(
    "raw",
    [
        "1 2 - 3 4 5 6 7 8 9",
        "123 - 45 - 6789",
        "12 - 3456789",
        "123\r\n45\r\n6789",
        "123\n 45\n 6789",
        "123\t\t45\t\t6789",
        "123–45–6789",
        "12—3456789",
        "2025 1 2 3 4 5 6 7 8 9",
        "0 123456789",
        "123\u200945\u20096789",
        "123\u202f45\u202f6789",
        "123     45     6789",
        "SSN 123-45 continued 6789",
        "１２３-４５-６７８９",
    ],
)
def test_aggressive_masks_every_digit(raw):
    out, n = mask_tins(raw, aggressive=True)
    assert n >= 1
    assert len(out) == len(raw)
    assert _visible_digits(out) == ""


@pytest.mark.parametrize(
    "fields",
    [
        ["SSN 123", "45-6789"],
        ["SSN", "123-45", "continued", "6789"],
        ["", "123", "", "45", "6789", ""],
    ],
)
def test_mask_tins_in_fields_aggressive_keeps_shape_and_hides_digits(fields):
    out, n = mask_tins_in_fields(fields, aggressive=True)
    assert n >= 1
    assert [len(x) for x in out] == [len(x) for x in fields]
    assert _visible_digits("".join(out)) == ""


def test_mask_tins_in_fields_ssn_split_across_fields_non_aggressive():
    fields = ["SSN 123-", "45-6789", "EIN 12-", "3456789", "Invoice 2025-10-13"]
    out, n = mask_tins_in_fields(fields, aggressive=False)
    assert n == 2
    assert [len(x) for x in out] == [len(x) for x in fields]
    assert out[0] == "SSN ***-" and out[1] == "**-6789"
    assert out[2] == "EIN **-" and out[3] == "***6789" and out[4] == fields[4]


def test_mask_tins_in_fields_empty():
    assert mask_tins_in_fields([], aggressive=True) == ([], 0)


# --- extraction service --------------------------------------------------- #


def test_taxpayer_doc_text_layer_masks_every_digit():
    pdf = _text_pdf_bytes(["W-9", f"SSN {_SSN}", f"EIN {_EIN}", "Zip 37013"])
    att = _attachment(is_taxpayer_document=True, parse_invoice_number=False)
    storage = _FakeStorage(downloads={att.blob_url: pdf})
    svc = AttachmentExtractionService(repo=_FakeRepo(), storage=storage, di=_FakeDI(forbid=True))

    result = svc._extract(att)

    assert result["status"] == "completed"
    body = json.loads(storage.uploads[0]["file_content"])
    assert body["redaction_version"] == REDACTION_VERSION
    assert "SSN" in body["content"] and "W-9" not in body["content"]  # "9" in "W-9" is masked too
    assert _visible_digits(body["content"]) == ""
    assert body["key_value_pairs"] == []


@pytest.mark.parametrize(
    "kvps",
    [
        [{"key": f"SSN {_SSN}", "value": _BARE9, "confidence": 0.9}],
        [{"key": "SSN 123", "value": "45-6789", "confidence": 0.9}],
        [
            {"key": "SSN", "value": "123-45", "confidence": 0.9},
            {"key": "continued", "value": "6789", "confidence": 0.9},
        ],
    ],
)
def test_taxpayer_doc_di_path_persists_no_kvps(kvps):
    att = _attachment(is_taxpayer_document=True, parse_invoice_number=False, content_type="image/png")
    storage = _FakeStorage(downloads={att.blob_url: b"\x89PNG\r\n\x1a\n scan"})
    di = _FakeDI(text_result={"content": f"Name Jane Doe SSN {_SSN}", "pages_count": 1, "key_value_pairs": kvps})
    svc = AttachmentExtractionService(repo=_FakeRepo(), storage=storage, di=di)

    svc._extract(att)

    raw = storage.uploads[0]["file_content"]
    payload = json.loads(raw)
    assert payload["key_value_pairs"] == []
    assert _visible_digits(payload["content"]) == ""
    _assert_no_unmasked_tin_in_payload(raw)


def test_is_taxpayer_document_none_masks_every_digit_and_drops_kvps():
    att = _attachment(is_taxpayer_document=None, parse_invoice_number=False, content_type="image/png")
    storage = _FakeStorage(downloads={att.blob_url: b"\x89PNG\r\n\x1a\n scan"})
    di = _FakeDI(
        text_result={
            "content": f"Employer ID {_EIN}",
            "pages_count": 1,
            "key_value_pairs": [{"key": "EIN", "value": _EIN, "confidence": 0.9}],
        }
    )
    svc = AttachmentExtractionService(repo=_FakeRepo(), storage=storage, di=di)

    svc._extract(att)

    payload = json.loads(storage.uploads[0]["file_content"])
    assert _visible_digits(payload["content"]) == ""
    assert payload["key_value_pairs"] == []


def test_non_taxpayer_doc_masks_ssn_and_ein_keeps_kvps():
    att = _attachment(is_taxpayer_document=False, parse_invoice_number=False, content_type="image/png")
    storage = _FakeStorage(downloads={att.blob_url: b"\x89PNG\r\n\x1a\n scan"})
    di = _FakeDI(
        text_result={
            "content": f"Invoice {_EIN} vendor SSN {_SSN}",
            "pages_count": 1,
            "key_value_pairs": [
                {"key": "Invoice #", "value": _EIN, "confidence": 0.9},
                {"key": "SSN 123-", "value": "45-6789", "confidence": 0.9},
            ],
        }
    )
    svc = AttachmentExtractionService(repo=_FakeRepo(), storage=storage, di=di)

    svc._extract(att)

    payload = json.loads(storage.uploads[0]["file_content"])
    assert _EIN_MASKED in payload["content"] and _SSN_MASKED in payload["content"]
    kvps = payload["key_value_pairs"]
    assert kvps[0]["key"] == "Invoice #" and kvps[0]["value"] == _EIN_MASKED
    assert kvps[1]["key"] == "SSN ***-" and kvps[1]["value"] == "**-6789"


def test_non_taxpayer_doc_masks_ein_but_parses_invoice_number_first():
    line = f"Invoice {_EIN} vendor SSN {_SSN}"
    pdf = _text_pdf_bytes([line])
    att = _attachment(is_taxpayer_document=False, parse_invoice_number=True)
    repo = _FakeRepo()
    storage = _FakeStorage(downloads={att.blob_url: pdf})
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=_FakeDI(forbid=True))

    result = svc._extract(att)

    body = json.loads(storage.uploads[0]["file_content"])
    assert _EIN not in body["content"] and _EIN_MASKED in body["content"]
    assert _SSN_MASKED in body["content"]
    assert result["status"] == "completed"
    assert repo.updates[0]["vendor_invoice_number"] == _EIN


def test_vendor_invoice_number_from_unmasked_text_before_persist():
    pdf = _text_pdf_bytes(["ACME", f"Invoice # {_EIN}", f"SSN {_SSN}"])
    att = _attachment(is_taxpayer_document=True, parse_invoice_number=True)
    repo = _FakeRepo()
    storage = _FakeStorage(downloads={att.blob_url: pdf})
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=_FakeDI(forbid=True))

    svc._extract(att)

    assert repo.updates[0]["vendor_invoice_number"] == _EIN
    body = json.loads(storage.uploads[0]["file_content"])
    assert _visible_digits(body["content"]) == ""


def test_skip_when_completed_same_hash_and_redaction_version():
    prior_blob = "https://blob.example/attachment-extractions/prior.json"
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url=prior_blob,
        file_hash="hash-abc",
    )
    storage = _FakeStorage(
        downloads={
            prior_blob: json.dumps(
                {"source_file_hash": "hash-abc", "redaction_version": REDACTION_VERSION}
            ).encode("utf-8"),
        },
    )
    svc = AttachmentExtractionService(repo=_FakeRepo(), storage=storage, di=_FakeDI(forbid=True))
    result = svc._extract(att)
    assert result["status"] == "skipped"
    assert result["reason"] == "unchanged"
    assert storage.uploads == []


def _w9_pdf_with_ssn() -> bytes:
    return _text_pdf_bytes(
        [
            "Form W-9 Request for Taxpayer Identification Number and Certification",
            f"Social security number SSN {_SSN}",
            "Employer identification number",
        ]
    )


@pytest.mark.parametrize(
    "prior_payload",
    [
        {},
        {"redaction_version": "legacy"},
        {"redaction_version": None},
        # U-672: stamped, but carrying a pre-U-535a text key the current writer never writes.
        {"redaction_version": REDACTION_VERSION, "paragraphs": [{"content": "x"}]},
    ],
)
def test_reextract_when_hash_matches_but_redaction_version_stale(prior_payload):
    prior_blob = "https://blob.example/attachment-extractions/stale.json"
    pdf = _w9_pdf_with_ssn()
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url=prior_blob,
        file_hash="hash-abc",
    )
    payload = {"source_file_hash": "hash-abc", **prior_payload}
    storage = _FakeStorage(
        downloads={
            prior_blob: json.dumps(payload).encode("utf-8"),
            att.blob_url: pdf,
        },
    )
    repo = _FakeRepo()
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=_FakeDI(forbid=True))
    result = svc._extract(att)
    assert result["status"] == "completed"
    assert len(storage.uploads) == 1
    assert prior_blob in storage.deleted


def test_prior_blob_not_deleted_on_claim_lost():
    prior_blob = "https://blob.example/attachment-extractions/prior.json"
    pdf = _w9_pdf_with_ssn()
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url=prior_blob,
        file_hash="hash-abc",
        claim_modified_datetime="2026-01-01T00:00:00",
    )
    storage = _FakeStorage(
        downloads={
            prior_blob: json.dumps({"source_file_hash": "hash-old"}).encode("utf-8"),
            att.blob_url: pdf,
        },
    )
    repo = _FakeRepo(lose_claim=True)
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=_FakeDI(forbid=True))
    result = svc._extract(att)
    assert result["reason"] == "claim_lost"
    assert prior_blob not in storage.deleted


def test_prior_blob_not_deleted_on_terminal_write_failure():
    prior_blob = "https://blob.example/attachment-extractions/prior.json"
    pdf = _w9_pdf_with_ssn()
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url=prior_blob,
        file_hash="hash-abc",
    )
    storage = _FakeStorage(
        downloads={
            prior_blob: json.dumps({"source_file_hash": "hash-old"}).encode("utf-8"),
            att.blob_url: pdf,
        },
    )
    repo = _FakeRepo(fail_terminal=True)
    svc = AttachmentExtractionService(repo=repo, storage=storage, di=_FakeDI(forbid=True))
    result = svc._extract(att)
    assert result["reason"] == "terminal_write_failed"
    assert prior_blob not in storage.deleted


def test_prior_blob_delete_failure_does_not_fail_extraction():
    prior_blob = "https://blob.example/attachment-extractions/prior.json"
    pdf = _w9_pdf_with_ssn()
    att = _attachment(
        extraction_status="completed",
        extracted_text_blob_url=prior_blob,
        file_hash="hash-abc",
    )
    storage = _FakeStorage(
        downloads={
            prior_blob: json.dumps({"source_file_hash": "hash-old"}).encode("utf-8"),
            att.blob_url: pdf,
        },
        delete_error=RuntimeError("delete failed"),
    )
    svc = AttachmentExtractionService(repo=_FakeRepo(), storage=storage, di=_FakeDI(forbid=True))
    result = svc._extract(att)
    assert result["status"] == "completed"


# --- Unicode digits and dashes -------------------------------------------- #


@pytest.mark.parametrize(
    "raw",
    [
        "¹²³-⁴⁵-⁶⁷⁸⁹",  # superscript digits
        "①②③-④⑤-⑥⑦⑧⑨",  # circled digits
        "١٢٣-٤٥-٦٧٨٩",  # Arabic-Indic digits
        "Ⅰ½三",  # Roman numeral, vulgar fraction, CJK numeral
    ],
)
def test_aggressive_masks_every_numeric_character(raw):
    out, n = mask_tins(raw, aggressive=True)
    assert n >= 1
    assert len(out) == len(raw)
    assert not any(c.isnumeric() for c in out)


@pytest.mark.parametrize(
    "dash",
    ["－", "﹣", "﹘", "―", "‐", "−", "⸺"],
)
def test_non_aggressive_masks_ssn_with_any_unicode_dash(dash):
    raw = f"SSN 123{dash}45{dash}6789"
    out, n = mask_tins(raw, aggressive=False)
    assert n == 1
    assert out == f"SSN ***{dash}**{dash}6789"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("¹²³-⁴⁵-⁶⁷⁸⁹", "***-**-⁶⁷⁸⁹"),
        ("１２３－４５－６７８９", "***－**－６７８９"),
    ],
)
def test_non_aggressive_masks_ssn_in_unicode_digits(raw, expected):
    out, n = mask_tins(raw, aggressive=False)
    assert n == 1
    assert out == expected


def test_non_aggressive_unicode_digit_touching_shape_is_not_ssn():
    raw = "¹123-45-6789"
    out, n = mask_tins(raw, aggressive=False)
    assert n == 0
    assert out == raw


def test_service_taxpayer_doc_with_superscript_tin_persists_no_numeric():
    att = _attachment(is_taxpayer_document=True, parse_invoice_number=False, content_type="image/png")
    storage = _FakeStorage(downloads={att.blob_url: b"\x89PNG\r\n\x1a\n scan"})
    di = _FakeDI(
        text_result={
            "content": "SSN ¹²³-⁴⁵-⁶⁷⁸⁹",
            "pages_count": 1,
            "key_value_pairs": [],
        }
    )
    svc = AttachmentExtractionService(repo=_FakeRepo(), storage=storage, di=di)
    svc._extract(att)
    content = json.loads(storage.uploads[0]["file_content"])["content"]
    assert not any(c.isnumeric() for c in content)


def test_service_non_taxpayer_fullwidth_dash_ssn_masked():
    att = _attachment(is_taxpayer_document=False, parse_invoice_number=False, content_type="image/png")
    storage = _FakeStorage(downloads={att.blob_url: b"\x89PNG\r\n\x1a\n scan"})
    di = _FakeDI(text_result={"content": "SSN 123－45－6789", "pages_count": 1, "key_value_pairs": []})
    svc = AttachmentExtractionService(repo=_FakeRepo(), storage=storage, di=di)
    svc._extract(att)
    content = json.loads(storage.uploads[0]["file_content"])["content"]
    assert content == "SSN ***－**－6789"


# --- unassigned characters fail closed; EIN shapes ------------------------ #


def _remap(text: str, zero: int) -> str:
    return "".join(chr(zero + int(c)) if c.isdigit() else c for c in text)


@pytest.mark.parametrize("zero", [0x1CCF0, 0x11F50, 0x16D70])  # Unicode 16 outlined, Kawi, Kirat Rai
def test_aggressive_masks_digits_unknown_to_this_interpreter(zero):
    raw = "SSN " + _remap(_SSN, zero)
    out, n = mask_tins(raw, aggressive=True)
    assert n >= 1
    assert len(out) == len(raw)
    assert out == "SSN ***-**-****"


def test_aggressive_leaves_known_non_numeric_text():
    raw = "Name: Jane Doe \u00e9\u4e2d"
    assert mask_tins(raw, aggressive=True) == (raw, 0)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("EIN 12-3456789", "EIN **-***6789"),
        ("EIN 12\uff0d3456789", "EIN **\uff0d***6789"),
        ("Invoice # 25-0001234", "Invoice # **-***1234"),
    ],
)
def test_non_aggressive_masks_ein_shapes(raw, expected):
    out, n = mask_tins(raw, aggressive=False)
    assert n == 1
    assert out == expected


# --- lone surrogates fail closed ------------------------------------------ #


def test_aggressive_masks_lone_surrogates():
    # U+1D7CF..U+1D7D7 (mathematical bold digits 1..9) split into surrogate halves.
    def halves(cp: int) -> str:
        cp -= 0x10000
        return chr(0xD800 + (cp >> 10)) + chr(0xDC00 + (cp & 0x3FF))

    raw = "SSN " + "".join(halves(0x1D7CE + int(c)) if c.isdigit() else c for c in _SSN)
    out, n = mask_tins(raw, aggressive=True)
    assert len(out) == len(raw)
    assert n == 3
    assert not any(0xD800 <= ord(c) <= 0xDFFF for c in out)
    assert out.startswith("SSN ") and set(out[4:]) <= {"*", "-"}


def test_aggressive_count_is_runs_of_masked_characters():
    assert mask_tins("\u0378", aggressive=True) == ("*", 1)
    assert mask_tins("1\u03782 a 3", aggressive=True) == ("*** a *", 2)


# --- the persistence chokepoint masks on its own ---------------------------- #


@pytest.mark.parametrize("is_taxpayer_document", [True, None, False])
def test_persist_extraction_blob_masks_without_caller_help(is_taxpayer_document):
    att = _attachment(is_taxpayer_document=is_taxpayer_document)
    storage = _FakeStorage(downloads={})
    svc = AttachmentExtractionService(repo=_FakeRepo(), storage=storage, di=_FakeDI(forbid=True))

    svc._persist_extraction_blob(
        att, f"SSN {_SSN} EIN {_EIN}", [{"key": "EIN", "value": _EIN, "confidence": 0.9}], 1, "text_layer"
    )

    raw = storage.uploads[0]["file_content"]
    _assert_no_unmasked_tin_in_payload(raw)
    payload = json.loads(raw)
    assert payload["redaction_version"] == REDACTION_VERSION
    # Readers (U-654b) trust the stamp only for exactly this key set.
    assert set(payload) == EXTRACTION_BLOB_KEYS
    if is_taxpayer_document is False:
        assert payload["key_value_pairs"][0]["value"] == _EIN_MASKED
    else:
        assert payload["key_value_pairs"] == []
        assert _visible_digits(payload["content"]) == ""
