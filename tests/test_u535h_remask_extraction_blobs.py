"""U-535h — remask attachment extraction JSON blobs (pure + fake I/O)."""

from __future__ import annotations

import json
import re
from typing import Optional
import pytest

from entities.attachment.business.extraction_service import REDACTION_VERSION, extraction_payload_is_current
from scripts.remask_extraction_blobs import (
    _DOWNLOAD_ATTEMPTS,
    PayloadValidationError,
    _AttachmentRow,
    main,
    parse_attachment_ids,
    remask_extraction_blobs,
    remask_payload,
    run_remask,
)

# Test-only taxpayer-id shapes (not real identifiers).
_SSN = "123-45-6789"
_SSN_MASKED = "***-**-6789"
_EIN = "12-3456789"
_EIN_MASKED = "**-***6789"
_BARE9 = "123456789"

_UNMASKED_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")


def _assert_output_has_no_raw_tin(text: str) -> None:
    assert not _UNMASKED_SSN.search(text)
    assert _SSN not in text
    assert _EIN not in text
    assert _BARE9 not in text


class _FakeStorage:
    def __init__(self) -> None:
        self.blobs: dict[tuple[str, str], bytes] = {}
        self.upload_calls: list[tuple[str, str]] = []
        self.fail_download_urls: set[str] = set()
        self.download_attempts: int = 0
        self.corrupt_readback_after_upload: set[tuple[str, str]] = set()
        self._post_upload_corrupt: set[tuple[str, str]] = set()

    def seed(self, url: str, payload: dict) -> None:
        container, blob_name = self._parse_blob_url(url)
        self.blobs[(container, blob_name)] = json.dumps(payload).encode("utf-8")

    def _parse_blob_url(self, blob_url: str) -> tuple[str, str]:
        path = blob_url.split("://", 1)[-1].split("/", 1)[-1]
        container, blob_name = path.split("/", 1)
        return container, blob_name

    def download_file(self, blob_url: str) -> tuple[bytes, dict]:
        self.download_attempts += 1
        if blob_url in self.fail_download_urls:
            raise OSError("download failed")
        container, blob_name = self._parse_blob_url(blob_url)
        key = (container, blob_name)
        if key in self._post_upload_corrupt:
            return json.dumps({"content": "tampered"}).encode("utf-8"), {}
        data = self.blobs.get(key)
        if data is None:
            raise OSError("missing")
        return data, {}

    def upload_file(
        self,
        blob_name: str,
        file_content: bytes,
        content_type: str = "application/octet-stream",
        container_name: Optional[str] = None,
    ) -> str:
        container = container_name or "default"
        self.upload_calls.append((container, blob_name))
        key = (container, blob_name)
        self.blobs[key] = file_content
        if key in self.corrupt_readback_after_upload:
            self._post_upload_corrupt.add(key)
        return f"https://acct.blob.core.windows.net/{container}/{blob_name}"


def _row(
    attachment_id: int,
    *,
    url: str = "https://acct.blob.core.windows.net/extractions/a/b.json",
    taxpayer: bool = False,
) -> _AttachmentRow:
    return _AttachmentRow(
        id=attachment_id,
        extracted_text_blob_url=url,
        is_taxpayer_document=taxpayer,
    )


# --- CLI id parsing (P1) --------------------------------------------------- #


@pytest.mark.parametrize("ids_arg", ["", ","])
def test_parse_attachment_ids_empty_explicit_rejected(ids_arg: str):
    with pytest.raises(ValueError, match="at least one positive"):
        parse_attachment_ids(ids_arg)


def test_parse_attachment_ids_rejects_non_positive():
    with pytest.raises(ValueError, match="non-positive"):
        parse_attachment_ids("0,5")


def test_main_rejects_empty_ids_before_fetch(monkeypatch):
    called = {"fetch": False}

    def _boom(**_kwargs):
        called["fetch"] = True
        raise AssertionError("fetch should not run")

    monkeypatch.setattr("scripts.remask_extraction_blobs.fetch_attachment_rows", _boom)
    code = main(["--ids", ""])
    assert code != 0
    assert called["fetch"] is False


def test_main_rejects_comma_only_ids_before_fetch(monkeypatch):
    called = {"fetch": False}

    def _boom(**_kwargs):
        called["fetch"] = True
        raise AssertionError("fetch should not run")

    monkeypatch.setattr("scripts.remask_extraction_blobs.fetch_attachment_rows", _boom)
    code = main(["--ids", ","])
    assert code != 0
    assert called["fetch"] is False


# --- CLI numeric guards (P2c) --------------------------------------------- #


@pytest.mark.parametrize("argv", [["--workers", "0"], ["--limit", "-1"]])
def test_main_rejects_invalid_workers_or_limit_before_fetch(argv, monkeypatch):
    called = {"fetch": False}

    def _boom(**_kwargs):
        called["fetch"] = True
        raise AssertionError("fetch should not run")

    monkeypatch.setattr("scripts.remask_extraction_blobs.fetch_attachment_rows", _boom)
    code = main(argv)
    assert code != 0
    assert called["fetch"] is False


# --- remask_payload (pure) ------------------------------------------------ #


def test_remask_payload_taxpayer_masks_all_digits_and_drops_kvps():
    payload = {
        "content": f"TIN {_SSN} year 2024",
        "key_value_pairs": [{"key": "EIN", "value": _EIN, "confidence": "0.9"}],
        "method": "di",
        "pages_count": 1,
    }
    new_payload, changed = remask_payload(payload, aggressive=True)
    assert changed is True
    assert new_payload["redaction_version"] == REDACTION_VERSION
    assert "2" not in new_payload["content"] and "4" not in new_payload["content"]
    assert new_payload["key_value_pairs"] == []
    assert new_payload["method"] == "di"
    assert new_payload["pages_count"] == 1


def test_remask_payload_non_taxpayer_masks_shapes_and_kvps_drops_unknown_fields():
    payload = {
        "content": f"Vendor {_EIN}",
        "key_value_pairs": [{"key": "SSN", "value": _SSN, "confidence": None, "spans": [_SSN]}],
        "extra_field": "drop-me",
        "method": "pypdf",
        "redaction_version": "legacy",
    }
    new_payload, changed = remask_payload(payload, aggressive=False)
    assert changed is True
    assert _EIN_MASKED in new_payload["content"]
    assert new_payload["key_value_pairs"] == [{"key": "SSN", "value": _SSN_MASKED, "confidence": None}]
    assert set(new_payload) == {"content", "key_value_pairs", "method", "redaction_version"}
    assert new_payload["redaction_version"] == REDACTION_VERSION


# --- U-672: pre-U-535a blobs repeat the text in pages / paragraphs / tables -- #


def _legacy_payload(text: str) -> dict:
    """The shape of the 170 pre-2026-08-01 prod blobs (key set confirmed read-only)."""
    return {
        "content": text,
        "key_value_pairs": [],
        "pages": [{"page_number": 1, "lines": [text]}],
        "paragraphs": [{"content": text}],
        "tables": [{"cells": [{"content": text}]}],
    }


@pytest.mark.parametrize("aggressive", [True, False])
def test_u672_legacy_text_keys_are_dropped_and_count_as_changed(aggressive):
    new_payload, changed = remask_payload(_legacy_payload(f"EIN {_EIN} SSN {_SSN}"), aggressive=aggressive)
    assert changed is True
    assert set(new_payload) == {"content", "key_value_pairs", "redaction_version"}
    raw = json.dumps(new_payload)
    assert _SSN not in raw and _EIN not in raw
    assert extraction_payload_is_current(new_payload)


def test_u672_legacy_keys_dropped_even_when_content_is_already_clean():
    _new_payload, changed = remask_payload(_legacy_payload("no ids here"), aggressive=False)
    assert changed is True  # rewritten on a plain --apply, not only with --stamp-all


def test_u672_stamped_blob_with_legacy_keys_is_not_current_and_is_rewritten():
    storage = _FakeStorage()
    url = "https://acct.blob.core.windows.net/c/stamped-legacy.json"
    stamped = {**_legacy_payload(f"EIN {_EIN}"), "redaction_version": REDACTION_VERSION}
    storage.seed(url, stamped)
    summary = run_remask([_row(1, url=url)], storage=storage, apply=True)
    assert summary["counts"]["changed"] == 1
    assert len(storage.upload_calls) == 1
    raw, _ = storage.download_file(url)
    assert _EIN.encode() not in raw
    assert extraction_payload_is_current(json.loads(raw))


def test_remask_payload_already_masked_non_taxpayer_no_content_change():
    payload = {
        "content": f"Vendor {_EIN_MASKED}",
        "key_value_pairs": [{"key": "SSN", "value": _SSN_MASKED}],
        "redaction_version": "legacy-v2",
    }
    new_payload, changed = remask_payload(payload, aggressive=False)
    assert changed is False
    assert new_payload["content"] == payload["content"]
    assert new_payload["key_value_pairs"] == payload["key_value_pairs"]
    assert new_payload["redaction_version"] == REDACTION_VERSION


def test_remask_payload_missing_content_and_kvps_keys():
    payload = {"method": "pypdf", "pages_count": 2}
    new_payload, changed = remask_payload(payload, aggressive=False)
    assert changed is False
    assert "content" not in new_payload
    assert "key_value_pairs" not in new_payload
    assert new_payload["redaction_version"] == REDACTION_VERSION
    assert new_payload["method"] == "pypdf"


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"content": 123},
        {"key_value_pairs": None},
        {"key_value_pairs": ["not-a-dict"]},
        {"key_value_pairs": [{"key": 1, "value": "x"}]},
        {"key_value_pairs": [{"key": "k", "value": 99}]},
    ],
)
def test_remask_payload_invalid_shape_raises(bad_payload):
    with pytest.raises(PayloadValidationError):
        remask_payload(bad_payload, aggressive=False)


def test_invalid_payload_row_fails_without_upload():
    storage = _FakeStorage()
    url = "https://acct.blob.core.windows.net/c/bad-shape.json"
    storage.seed(url, {"content": 123, "key_value_pairs": []})
    summary = run_remask([_row(50, url=url)], storage=storage, apply=True)
    assert summary["counts"]["failed"] == 1
    assert storage.upload_calls == []


# --- I/O layer (fake storage) --------------------------------------------- #


def test_dry_run_uploads_nothing(capsys):
    storage = _FakeStorage()
    url = "https://acct.blob.core.windows.net/c/extraction.json"
    storage.seed(url, {"content": _SSN, "key_value_pairs": []})
    rows = [_row(10, url=url)]

    summary = run_remask(rows, storage=storage, apply=False)
    assert storage.upload_calls == []
    assert summary["counts"]["changed"] == 1
    out = capsys.readouterr().out
    _assert_output_has_no_raw_tin(out)


def test_current_version_skips_remask():
    storage = _FakeStorage()
    url = "https://acct.blob.core.windows.net/c/current.json"
    storage.seed(
        url,
        {"content": _SSN, "redaction_version": REDACTION_VERSION, "key_value_pairs": []},
    )
    summary = run_remask([_row(1, url=url)], storage=storage, apply=True)
    assert summary["counts"]["current"] == 1
    assert storage.upload_calls == []


def test_apply_uploads_only_changed_to_same_blob_name():
    storage = _FakeStorage()
    url = "https://acct.blob.core.windows.net/extractions/pid/abc.json"
    storage.seed(url, {"content": _SSN, "key_value_pairs": []})
    stamp_url = "https://acct.blob.core.windows.net/extractions/pid/stamp.json"
    storage.seed(
        stamp_url,
        {"content": "no tins here", "key_value_pairs": [], "redaction_version": "old"},
    )

    summary = run_remask(
        [_row(5, url=url), _row(6, url=stamp_url)],
        storage=storage,
        apply=True,
        stamp_all=False,
    )
    assert summary["counts"]["changed"] == 1
    assert summary["counts"]["stamp_only"] == 1
    assert len(storage.upload_calls) == 1
    assert storage.upload_calls[0] == ("extractions", "pid/abc.json")
    assert summary["counts"]["verified"] == 1


def test_apply_stamp_all_also_rewrites_stamp_only():
    storage = _FakeStorage()
    url = "https://acct.blob.core.windows.net/c/x/y.json"
    storage.seed(
        url,
        {"content": "plain text", "key_value_pairs": [], "redaction_version": "pre-u535g"},
    )
    summary = run_remask([_row(7, url=url)], storage=storage, apply=True, stamp_all=True)
    assert summary["counts"]["stamp_only"] == 1
    assert len(storage.upload_calls) == 1
    assert summary["counts"]["verified"] == 1
    stored = json.loads(storage.blobs[("c", "x/y.json")])
    assert stored["redaction_version"] == REDACTION_VERSION


def test_readback_mismatch_counts_failed():
    storage = _FakeStorage()
    url = "https://acct.blob.core.windows.net/c/bad-readback.json"
    storage.seed(url, {"content": _SSN, "key_value_pairs": []})
    container, blob_name = storage._parse_blob_url(url)
    storage.corrupt_readback_after_upload.add((container, blob_name))

    summary = run_remask([_row(8, url=url)], storage=storage, apply=True)
    assert summary["counts"]["failed"] == 1
    assert summary["counts"].get("verified", 0) == 0


def test_five_consecutive_failures_abort_stops_submission():
    storage = _FakeStorage()
    rows = []
    for i in range(20):
        url = f"https://acct.blob.core.windows.net/c/fail{i}.json"
        storage.fail_download_urls.add(url)
        rows.append(_row(100 + i, url=url))

    summary = run_remask(rows, storage=storage, apply=False, workers=1)
    assert summary["aborted"] is True
    assert summary["counts"]["failed"] == 5
    assert storage.download_attempts == 5 * _DOWNLOAD_ATTEMPTS


def test_remask_extraction_blobs_uses_fetch_and_storage(capsys):
    storage = _FakeStorage()
    url = "https://acct.blob.core.windows.net/c/fetch.json"
    storage.seed(url, {"content": _EIN, "key_value_pairs": []})

    summary = remask_extraction_blobs(
        fetch_rows=lambda: [_row(20, url=url)],
        storage=storage,
        apply=False,
    )
    assert summary["counts"]["changed"] == 1
    assert "20" in str(summary["changed_ids"])


def _patch_main_io(monkeypatch, storage: _FakeStorage, rows: list[_AttachmentRow]):
    monkeypatch.setattr(
        "scripts.remask_extraction_blobs.fetch_attachment_rows",
        lambda **kwargs: rows,
    )
    monkeypatch.setattr(
        "scripts.remask_extraction_blobs.AzureBlobStorage",
        lambda: storage,
    )


@pytest.mark.parametrize("apply_flag", [[], ["--apply"]])
def test_main_end_to_end_never_leaks_tin(apply_flag, monkeypatch, capsys, caplog):
    storage = _FakeStorage()
    url = "https://acct.blob.core.windows.net/c/tin-blob.json"
    storage.seed(url, {"content": f"secret {_SSN}", "key_value_pairs": [{"key": "EIN", "value": _EIN}]})
    leak_url = "https://acct.blob.core.windows.net/c/leak-msg.json"
    real_download = storage.download_file

    def _leak_download(blob_url: str):
        if blob_url == leak_url:
            raise OSError(f"broken blob contains {_SSN}")
        return real_download(blob_url)

    storage.download_file = _leak_download  # type: ignore[method-assign]

    rows = [
        _row(30, url=url),
        _row(31, url=leak_url),
    ]
    _patch_main_io(monkeypatch, storage, rows)

    with caplog.at_level("DEBUG"):
        code = main([*apply_flag, "--workers", "1"])

    captured = capsys.readouterr()
    combined = captured.out + captured.err + caplog.text
    _assert_output_has_no_raw_tin(combined)
    assert "rows:" in captured.out
    # The good row was really masked (and, on --apply, uploaded + verified); the bad row failed.
    assert "changed=1" in captured.out and "failed=1" in captured.out and " ids=30" in captured.out
    if apply_flag:
        assert storage.upload_calls == [("c", "tin-blob.json")]
        assert "verified=1" in captured.out
        stored = json.loads(storage.blobs[("c", "tin-blob.json")])
        assert _SSN not in json.dumps(stored) and _EIN not in json.dumps(stored)
    else:
        assert storage.upload_calls == []
    assert code == 1  # the leak row always fails


def test_main_summary_never_leaks_tin(capsys):
    from scripts.remask_extraction_blobs import _print_summary

    _print_summary(
        {"counts": {"current": 0, "changed": 1, "stamp_only": 0, "failed": 0}, "changed_ids": [42]},
        apply=False,
    )
    _assert_output_has_no_raw_tin(capsys.readouterr().out)


@pytest.mark.parametrize(
    "payload_tail",
    [
        {"key_value_pairs": [{"key": "k", "value": "v", "spans": ["12-3456789"]}]},
        {"key_value_pairs": [{"key": {"content": "12-3456789"}, "value": "v"}]},
        {"key_value_pairs": "not-a-list"},
        {"content": ["12-3456789"]},
    ],
)
def test_u672_stamp_is_not_current_with_unwritten_nested_shapes(payload_tail):
    payload = {"content": "x", "key_value_pairs": [], "redaction_version": REDACTION_VERSION, **payload_tail}
    assert not extraction_payload_is_current(payload)


def test_u672_writer_shaped_payload_is_current():
    payload = {
        "attachment_public_id": "a",
        "source_file_hash": "h",
        "redaction_version": REDACTION_VERSION,
        "method": "text_layer",
        "pages_count": 1,
        "content": "x",
        "key_value_pairs": [{"key": "k", "value": None, "confidence": "0.9"}],
    }
    assert extraction_payload_is_current(payload)
