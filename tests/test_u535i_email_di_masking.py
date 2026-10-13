"""U-535i — mask TIN shapes in EmailAttachment DiResultJson at persistence."""

from __future__ import annotations

import importlib
import json
import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from entities.email_message.business.model import EmailAttachment
from entities.email_message.business.service import EmailAttachmentExtractionService
from entities.email_message.persistence.repo import (
    EmailAttachmentRepository,
    mask_di_result_for_persistence,
    mask_di_result_json_for_persistence,
)
from integrations.azure.document_intelligence.business.service import (
    DocumentIntelligenceService,
)

_SSN = "123-45-6789"
_SSN_MASKED = "***-**-6789"
_EIN = "12-3456789"
_EIN_MASKED = "**-***6789"
_BARE9 = "123456789"

_UNMASKED_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")

# Byte-identical outputs captured from mask_di_result_json_for_persistence before PASS 2 refactor.
_BYTE_IDENTITY_GOLDEN: list[tuple[str, str]] = [
    (
        '{"content": "Vendor note 123-45-6789", "keyValuePairs": [{"key": {"content": "Tax ID"}, "value": {"content": "12-3456789"}, "confidence": 0.91}], "pages": [{"pageNumber": 1}], "tables": [{"rowCount": 1, "columnCount": 1, "cells": [{"rowIndex": 0, "columnIndex": 0, "content": "123-45-6789"}]}]}',
        '{"content": "Vendor note ***-**-6789", "pages": [{"pageNumber": 1}], "keyValuePairs": [{"key": {"content": "Tax ID"}, "value": {"content": "**-***6789"}, "confidence": 0.91}], "tables": [{"rowCount": 1, "columnCount": 1, "cells": [{"rowIndex": 0, "columnIndex": 0, "content": "***-**-6789"}]}]}',
    ),
    (
        '{"apiVersion": "2024-11-30", "modelId": "prebuilt-layout", "stringIndexType": "textElements", "content": "Invoice total note 123-45-6789 end", "pages": [{"pageNumber": 1, "words": [{"content": "Invoice", "span": {"offset": 0, "length": 7}}, {"content": "123-45-6789", "span": {"offset": 20, "length": 11}}], "lines": [{"content": "Invoice total note 123-45-6789 end", "spans": [{"offset": 0, "length": 34}]}], "spans": [{"offset": 0, "length": 34}]}], "paragraphs": [{"content": "Invoice total note 123-45-6789 end", "spans": [{"offset": 0, "length": 3}]}], "keyValuePairs": [{"key": {"content": "Tax ID"}, "value": {"content": "12-3456789"}, "confidence": 0.91}], "tables": [{"rowCount": 1, "columnCount": 1, "cells": [{"rowIndex": 0, "columnIndex": 0, "content": "123-45-6789"}]}], "documents": [{"docType": "invoice", "confidence": 0.5}], "selectionMarks": [{"state": "selected", "confidence": 0.9}]}',
        '{"content": "Invoice total note ***-**-6789 end", "pages": [{"pageNumber": 1}], "keyValuePairs": [{"key": {"content": "Tax ID"}, "value": {"content": "**-***6789"}, "confidence": 0.91}], "tables": [{"rowCount": 1, "columnCount": 1, "cells": [{"rowIndex": 0, "columnIndex": 0, "content": "***-**-6789"}]}]}',
    ),
    ("prefix 123-45-6789 suffix", "prefix ***-**-**** suffix"),
    (
        '{"broken": true, "leak": "123\\-45\\-6789"}',
        '{"broken": true, "leak": "***\\-**\\-****"}',
    ),
    ('["123-", "45-6789"]', '["***-", "**-****"]'),
    ('["12-", "3456789"]', '["**-", "*******"]'),
    ('"123-45-6789"', '"***-**-****"'),
    ('[["123"], ["45-6789"]]', '[["***"], ["**-****"]]'),
    ('["\\uff11\\uff12\\uff13-", "45-6789"]', '["\\uff**\\uff**\\uff**-", "**-****"]'),
    (
        '{"content": "Invoice # 12-3456789", "pages": []}',
        '{"content": "Invoice # **-***6789", "pages": []}',
    ),
    (
        '{"content": "no tin", "pages": [{"words": [{"content": "no tin"}]}]}',
        '{"content": "no tin", "pages": [{"pageNumber": 1}]}',
    ),
    ('{"content": "clean"}', '{"content": "clean"}'),
]


def _assert_no_test_tins_in_text(text: str) -> None:
    assert _SSN not in text
    assert _EIN not in text
    assert _BARE9 not in text
    assert not _UNMASKED_SSN.search(text)


def _assert_no_nine_digit_tin_subsequence(stored: dict, *, tin_digits: str = "123456789") -> None:
    dumped = json.dumps(stored)
    digits_only = re.sub(r"\D", "", dumped)
    assert tin_digits not in digits_only
    _assert_no_test_tins_in_text(dumped)


def _assert_reduced_di_shape(stored: dict) -> None:
    allowed_top = frozenset({"content", "pages", "keyValuePairs", "tables"})
    assert set(stored.keys()).issubset(allowed_top)
    forbidden = frozenset(
        {
            "words",
            "lines",
            "spans",
            "paragraphs",
            "styles",
            "documents",
            "languages",
            "figures",
            "sections",
            "selectionMarks",
            "apiVersion",
            "modelId",
            "stringIndexType",
        }
    )
    dumped = json.dumps(stored)
    for key in forbidden:
        assert f'"{key}"' not in dumped
    for page in stored.get("pages") or []:
        assert isinstance(page, dict)
        assert set(page.keys()) <= {"pageNumber"}


def _hoist_like_cache(analyze_result: dict) -> dict:
    svc = DocumentIntelligenceService()
    return svc._hoist_and_validate(analyze_result)


def _sample_analyze_result(*, content_suffix: str = "") -> dict:
    content = f"Vendor note {_SSN}{content_suffix}"
    return {
        "content": content,
        "keyValuePairs": [
            {
                "key": {"content": "Tax ID"},
                "value": {"content": _EIN},
                "confidence": 0.91,
            }
        ],
        "pages": [{"pageNumber": 1}],
        "tables": [
            {
                "rowCount": 1,
                "columnCount": 1,
                "cells": [{"rowIndex": 0, "columnIndex": 0, "content": _SSN}],
            }
        ],
    }


@pytest.mark.parametrize("inp,expected", _BYTE_IDENTITY_GOLDEN)
def test_mask_di_result_json_for_persistence_byte_identity(inp, expected):
    assert mask_di_result_json_for_persistence(inp) == expected


def test_mask_di_result_json_for_persistence_invalid_json_fallback():
    out = mask_di_result_json_for_persistence(f"prefix {_SSN} suffix")
    assert "prefix" in out and "suffix" in out
    digits = re.sub(r"\D", "", out)
    assert not re.search(r"\d{9}", digits)
    assert not re.search(r"\d{4,}", digits)


def test_mask_di_result_json_for_persistence_invalid_json_escapes_fail_closed():
    raw = r'{"broken": true, "leak": "123\-45\-6789"}'
    out = mask_di_result_json_for_persistence(raw)
    digits = re.sub(r"\D", "", out)
    assert not re.search(r"\d{9}", digits)


def _realistic_analyze_result() -> dict:
    """DI prebuilt-layout shaped payload with cache-irrelevant noise retained pre-mask."""
    content = f"Invoice total note {_SSN} end"
    return {
        "apiVersion": "2024-11-30",
        "modelId": "prebuilt-layout",
        "stringIndexType": "textElements",
        "content": content,
        "pages": [
            {
                "pageNumber": 1,
                "words": [
                    {"content": "Invoice", "span": {"offset": 0, "length": 7}},
                    {"content": _SSN, "span": {"offset": 20, "length": len(_SSN)}},
                ],
                "lines": [{"content": content, "spans": [{"offset": 0, "length": len(content)}]}],
                "spans": [{"offset": 0, "length": len(content)}],
            }
        ],
        "paragraphs": [{"content": content, "spans": [{"offset": 0, "length": 3}]}],
        "keyValuePairs": [
            {
                "key": {"content": "Tax ID"},
                "value": {"content": _EIN},
                "confidence": 0.91,
            }
        ],
        "tables": [
            {
                "rowCount": 1,
                "columnCount": 1,
                "cells": [{"rowIndex": 0, "columnIndex": 0, "content": _SSN}],
            }
        ],
        "documents": [{"docType": "invoice", "confidence": 0.5}],
        "selectionMarks": [{"state": "selected", "confidence": 0.9}],
    }


def test_mask_di_result_for_persistence_cache_hoist_matches_full_raw_modulo_masking():
    raw = _realistic_analyze_result()
    full_hoist = _hoist_like_cache(raw)
    masked, _ = mask_di_result_for_persistence(raw)
    reduced_hoist = _hoist_like_cache(masked)
    assert reduced_hoist["pages_count"] == full_hoist["pages_count"]
    assert _SSN_MASKED in reduced_hoist["content"]
    assert reduced_hoist["key_value_pairs"] == [
        {**kvp, "value": _EIN_MASKED} for kvp in full_hoist["key_value_pairs"]
    ]
    assert reduced_hoist["tables"][0]["rows"][0][0] == _SSN_MASKED
    _assert_reduced_di_shape(masked)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            {
                "content": "SSN 123 - 45 - 6789",
                "pages": [
                    {
                        "pageNumber": 1,
                        "words": [
                            {"content": "123", "span": {"offset": 4, "length": 3}},
                            {"content": "6789", "span": {"offset": 15, "length": 4}},
                        ],
                    }
                ],
            },
            id="drops_words_keeps_page_stub_and_masks_spaced_ssn_in_content",
        ),
        pytest.param(
            {
                "content": "123-45-",
                "tables": [
                    {
                        "rowCount": 1,
                        "columnCount": 1,
                        "cells": [
                            {
                                "rowIndex": 0,
                                "columnIndex": 0,
                                "content": _SSN,
                                "spans": [{"offset": 0, "length": 3}, {"offset": 7, "length": 7}],
                            }
                        ],
                    }
                ],
            },
            id="masks_split_ssn_across_content_prefix_and_table_cell",
        ),
        pytest.param(
            {
                "content": "☒ 123 - 45 -\n6789",
                "pages": [
                    {
                        "lines": [
                            {"content": "☒ 123 - 45 -"},
                            {"content": "6789"},
                        ],
                        "selectionMarks": [{"state": "selected"}],
                    }
                ],
            },
            id="drops_lines_and_selection_marks_keeps_page_stub",
        ),
        pytest.param(
            {
                "pages": [
                    {
                        "lines": [
                            {"content": "123 -"},
                            {"content": "45 -"},
                            {"content": "6789"},
                        ],
                    }
                ],
            },
            id="no_top_level_content_yields_empty_reduced_shape",
        ),
        pytest.param(
            {
                "stringIndexType": "textElements",
                "content": f"café {_SSN}",
                "pages": [{"textElements": [{"content": "é", "span": {"offset": 3, "length": 1}}]}],
            },
            id="drops_string_index_type_and_text_elements_keeps_content",
        ),
        pytest.param(
            {
                "content": _SSN,
                "pages": [
                    {
                        "words": [
                            {"content": _SSN, "span": {"offset": 0, "length": 11}},
                            {"content": "x", "span": None},
                        ],
                    }
                ],
            },
            id="drops_word_spans_keeps_page_number_stub",
        ),
    ],
)
def test_mask_di_result_for_persistence_reduced_shape_probes(raw):
    snapshot = json.loads(json.dumps(raw))
    masked, _ = mask_di_result_for_persistence(raw)
    assert raw == snapshot
    _assert_reduced_di_shape(masked)
    _assert_no_nine_digit_tin_subsequence(masked)


def test_mask_di_result_for_persistence_ein_invoice_in_content_accepted_tradeoff():
    """Chris 2026-10-13: masking cached DI may hide an EIN-shaped invoice # before DiInvoiceNumber is set."""
    ein_as_invoice = "12-3456789"
    raw = {"content": f"Invoice # {ein_as_invoice}", "pages": []}
    masked, _ = mask_di_result_for_persistence(raw)
    assert ein_as_invoice not in masked["content"]
    assert _EIN_MASKED in masked["content"]
    attachment = EmailAttachment(
        id=1,
        extraction_status="extracted",
        di_result_json=json.dumps(masked),
        di_invoice_number=None,
    )
    resp = EmailAttachmentExtractionService()._cached_extraction_response(attachment)
    assert resp is not None
    assert resp["invoice_number"] is None
    assert ein_as_invoice not in resp["content"]


# --- update_extraction chokepoint ----------------------------------------- #


def _patch_update_extraction_db(monkeypatch):
    captured: dict = {}

    class _FakeCursor:
        def fetchone(self):
            return SimpleNamespace(Id=1)

    @contextmanager
    def _fake_conn():
        yield SimpleNamespace(cursor=lambda: _FakeCursor())

    def _capture_call_procedure(*, cursor, name, params):
        captured["name"] = name
        captured["params"] = params

    monkeypatch.setattr(
        "entities.email_message.persistence.repo.get_connection",
        _fake_conn,
    )
    monkeypatch.setattr(
        "entities.email_message.persistence.repo.call_procedure",
        _capture_call_procedure,
    )
    return captured


def test_update_extraction_masks_di_json_and_vendor_name_not_invoice_number(monkeypatch):
    captured = _patch_update_extraction_db(monkeypatch)
    raw = json.dumps(_sample_analyze_result())

    EmailAttachmentRepository().update_extraction(
        id=7,
        extraction_status="extracted",
        di_result_json=raw,
        di_vendor_name=f"Acme {_SSN}",
        di_invoice_number=_EIN,
    )

    assert captured["name"] == "UpdateEmailAttachmentExtraction"
    params = captured["params"]
    assert params["DiInvoiceNumber"] == _EIN
    assert params["DiVendorName"] == f"Acme {_SSN_MASKED}"
    stored = json.loads(params["DiResultJson"])
    assert _SSN_MASKED in stored["content"]
    assert stored["keyValuePairs"][0]["value"]["content"] == _EIN_MASKED
    _assert_no_test_tins_in_text(params["DiResultJson"])
    _assert_no_test_tins_in_text(params["DiVendorName"])


def test_update_extracted_fields_masks_di_vendor_name(monkeypatch):
    captured = _patch_update_extraction_db(monkeypatch)

    EmailAttachmentRepository().update_extracted_fields(
        id=3,
        di_vendor_name=f"Vendor {_EIN}",
    )

    assert captured["name"] == "UpdateEmailAttachmentExtractedFields"
    assert captured["params"]["DiVendorName"] == f"Vendor {_EIN_MASKED}"


def test_update_extraction_di_result_json_none_passthrough(monkeypatch):
    captured = _patch_update_extraction_db(monkeypatch)

    EmailAttachmentRepository().update_extraction(
        id=1,
        extraction_status="failed",
        di_result_json=None,
    )

    assert captured["params"]["DiResultJson"] is None


# --- cached extraction read path ------------------------------------------ #


def test_cached_extraction_response_round_trip_on_masked_json():
    raw = _sample_analyze_result()
    masked, _ = mask_di_result_for_persistence(raw)
    attachment = EmailAttachment(
        id=99,
        extraction_status="extracted",
        di_result_json=json.dumps(masked),
        di_vendor_name="Vendor Co",
        di_invoice_number="INV-100",
        di_total_amount=None,
        di_currency="USD",
        di_confidence=None,
    )

    resp = EmailAttachmentExtractionService()._cached_extraction_response(attachment)
    assert resp is not None
    assert resp["cached"] is True
    assert _SSN_MASKED in resp["content"]
    assert resp["key_value_pairs"][0]["value"] == _EIN_MASKED
    assert resp["tables"][0]["rows"][0][0] == _SSN_MASKED
    assert resp["key_value_pairs"][0]["confidence"] == "0.91"
    assert resp["invoice_number"] == "INV-100"
    _assert_no_test_tins_in_text(json.dumps(resp))


# --- remask script -------------------------------------------------------- #


class _FakeRemaskRepo:
    updates: list[dict] = []

    def update_di_result_json(self, **kwargs):
        _FakeRemaskRepo.updates.append(kwargs)
        return True


def _patch_remask_rows(monkeypatch, mod, rows: list[tuple[int, str, bytes]]):
    def _batch(after_id: int):
        if after_id == 0:
            return rows
        return []

    monkeypatch.setattr(mod, "_fetch_di_result_batch", _batch)


@pytest.fixture
def remask_mod(monkeypatch):
    mod = importlib.import_module("scripts.remask_email_di_results")
    _FakeRemaskRepo.updates = []
    monkeypatch.setattr(mod, "EmailAttachmentRepository", _FakeRemaskRepo)
    return mod


@pytest.mark.parametrize(
    "rows,expect_would_change,expect_id",
    [
        (
            [(12, json.dumps({"content": "no tin", "pages": [{"words": [{"content": "no tin"}]}]}), b"rv12")],
            1,
            "12",
        ),
        (
            [
                (10, json.dumps(_sample_analyze_result()), b"rv10"),
                (11, json.dumps({"content": "no tin here"}), b"rv11"),
            ],
            1,
            "10",
        ),
    ],
)
def test_remask_script_dry_run(remask_mod, monkeypatch, capsys, rows, expect_would_change, expect_id):
    _patch_remask_rows(monkeypatch, remask_mod, rows)

    rc = remask_mod.main([])

    assert rc == 0
    assert _FakeRemaskRepo.updates == []
    out = capsys.readouterr().out
    assert f"would_change={expect_would_change}" in out
    assert expect_id in out
    assert "DRY RUN" in out
    _assert_no_test_tins_in_text(out)


def test_remask_script_apply_only_changed_rows_optimistic(remask_mod, monkeypatch, capsys):
    raw = json.dumps(_sample_analyze_result())
    already = json.dumps({"content": "clean"})
    _patch_remask_rows(
        monkeypatch,
        remask_mod,
        [(20, raw, b"rv20"), (21, already, b"rv21")],
    )

    masked = mask_di_result_json_for_persistence(raw)
    seen_ids: list[int] = []

    def _update(**kwargs):
        seen_ids.append(kwargs["id"])
        assert kwargs["id"] == 20
        assert kwargs["expected_row_version"] == b"rv20"
        assert kwargs["di_result_json"] == masked
        return True

    monkeypatch.setattr(_FakeRemaskRepo, "update_di_result_json", staticmethod(_update))

    rc = remask_mod.main(["--apply"])

    assert rc == 0
    assert seen_ids == [20]
    out = capsys.readouterr().out
    assert "updated=1" in out
    assert "skipped_changed=0" in out
    _assert_no_test_tins_in_text(out)


def test_update_di_result_json_optimistic_guard(monkeypatch):
    executed: list = []

    class _Cursor:
        rowcount = 0

        def execute(self, sql, *params):
            executed.append((sql, params))

    @contextmanager
    def _fake_conn():
        yield SimpleNamespace(cursor=lambda: _Cursor())

    monkeypatch.setattr(
        "entities.email_message.persistence.repo.get_connection",
        _fake_conn,
    )

    ok = EmailAttachmentRepository().update_di_result_json(
        id=5,
        di_result_json='{"content":"***-**-6789"}',
        expected_row_version=b"\x00\x01",
    )
    assert ok is False
    sql, params = executed[0]
    assert "SET [DiResultJson] = CAST(? AS NVARCHAR(MAX))" in sql
    where_clause = sql.rsplit(" WHERE ", 1)[-1]
    assert "[RowVersion] = ?" in where_clause
    assert "[DiResultJson]" not in where_clause
    assert params == (
        '{"content":"***-**-6789"}',
        5,
        b"\x00\x01",
    )


def test_remask_invalid_json_counts_as_changed(remask_mod, monkeypatch, capsys):
    raw = f"not-json {_SSN}"
    _patch_remask_rows(monkeypatch, remask_mod, [(30, raw, b"rv30")])

    rc = remask_mod.main([])

    assert rc == 0
    out = capsys.readouterr().out
    assert "would_change=1" in out
    _assert_no_test_tins_in_text(out)


@pytest.mark.parametrize(
    "payload",
    [
        '["123-", "45-6789"]',
        '["12-", "3456789"]',
        '"123-45-6789"',
        '[["123"], ["45-6789"]]',
        '["\\uff11\\uff12\\uff13-", "45-6789"]',
    ],
)
def test_non_object_json_fails_closed(payload):
    masked = mask_di_result_json_for_persistence(payload)
    assert not any(ch.isnumeric() for ch in masked)
    assert len(masked) == len(payload)
