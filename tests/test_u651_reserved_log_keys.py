"""
U-651: `filename` is a reserved logging.LogRecord attribute. Passing it in
`extra=` makes Logger.makeRecord raise KeyError whenever that level is enabled,
so the two sites below raised in prod instead of logging.
"""
import ast
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx

import entities.time_entry.business.external_timesheet_import_service as timesheet_module
from entities.time_entry.business.external_timesheet_import_service import (
    ExternalTimesheetImportService,
)
from integrations.ms.outbox.business.worker import MsOutboxWorker

# tests/ must be on sys.path for `from conftest import ...` (same pattern as
# test_qbo_reconciliation_recorder.py); run alone, nothing else inserts it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import REPO_ROOT, iter_prod_python_sources  # noqa: E402

TIMESHEET_LOGGER = "entities.time_entry.business.external_timesheet_import_service"
MS_WORKER_LOGGER = "integrations.ms.outbox.business.worker"

LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"}
RESERVED_LOG_RECORD_KEYS = set(
    vars(logging.LogRecord("", 0, "", 0, "", None, None))
) | {"message", "asctime"}


def test_timesheet_parse_failure_logs_csv_filename_and_returns_error(monkeypatch, caplog):
    monkeypatch.setattr(timesheet_module, "TimeEntryBulkImportService", MagicMock())
    caplog.set_level(logging.ERROR, logger=TIMESHEET_LOGGER)

    header = b"worker_firstname,worker_lastname,work_date,hours,note\n"
    oversized_field = b"A,B,2026-01-01,1," + b"x" * 200_000 + b"\n"

    result = ExternalTimesheetImportService().import_csv(
        header + oversized_field, filename="bad.csv"
    )

    assert result["errors"][0]["error"].startswith("CSV parse failed")
    records = [
        r for r in caplog.records
        if r.name == TIMESHEET_LOGGER and r.getMessage() == "external_timesheet.parse.failed"
    ]
    assert records, "external_timesheet.parse.failed was not logged"
    assert records[0].csv_filename == "bad.csv"
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None  # logger.exception keeps the traceback


def test_ms_upload_completed_without_response_body_logs_upload_filename_and_returns_none(
    monkeypatch, caplog
):
    content = b"%PDF-1.4 test body"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )

    worker = MsOutboxWorker(repo=MagicMock())
    monkeypatch.setattr(worker, "_persist_payload", lambda row, payload: True)
    caplog.set_level(logging.WARNING, logger=MS_WORKER_LOGGER)

    payload = {
        "drive_id": "drive-1",
        "parent_item_id": "parent-1",
        "filename": "Invoice 1.pdf",
        "upload_session_url": "https://upload.example.test/session",
    }

    result = worker._upload_large_file_with_resume(
        row=SimpleNamespace(public_id="ob-1"),
        payload=payload,
        content=content,
        total_size=len(content),
        content_type="application/pdf",
    )

    assert result is None
    records = [
        r for r in caplog.records
        if r.getMessage() == "ms.sharepoint.upload.completed_without_response_body"
    ]
    assert records, "completed_without_response_body was not logged"
    record = records[0]
    assert record.upload_filename == "Invoice 1.pdf"
    assert record.levelno == logging.WARNING
    assert record.event_name == "ms.sharepoint.upload.completed_without_response_body"
    assert record.outbox_public_id == "ob-1"


def _reserved_extra_keys_in_source(source: str, rel: str) -> list[str]:
    """Reserved LogRecord keys used in literal `extra=` dicts of logging calls in one file."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source, filename=rel)):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute) or node.func.attr not in LOG_METHODS:
            continue
        for keyword in node.keywords:
            if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                continue
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value in RESERVED_LOG_RECORD_KEYS:
                    hits.append(f"{rel}:{key.lineno} key={key.value!r}")
    return hits


def _scan_repo() -> tuple[int, list[str]]:
    """(production .py files scanned, reserved-key hits), via the shared conftest walker."""
    scanned, hits = 0, []
    for path in iter_prod_python_sources():
        if ".worktrees" in path.relative_to(REPO_ROOT).parts:  # a nested checkout is not this repo's code
            continue
        scanned += 1
        source = path.read_text(encoding="utf-8")
        if "extra" not in source:  # cheap pre-filter: no extra= means nothing to parse
            continue
        hits += _reserved_extra_keys_in_source(source, str(path.relative_to(REPO_ROOT)))
    return scanned, hits


def test_guard_detects_reserved_keys_and_ignores_safe_ones():
    source = (
        "logger.warning('a', extra={'filename': 1, 'upload_filename': 2})\n"
        "log.fatal('b', extra={'lineno': 3})\n"
        "adapter.info('c', extra={'csv_filename': 4})\n"
    )
    hits = _reserved_extra_keys_in_source(source, "fixture.py")
    assert hits == ["fixture.py:1 key='filename'", "fixture.py:2 key='lineno'"]


def test_no_reserved_logrecord_keys_in_log_extra_dicts():
    scanned, hits = _scan_repo()
    assert scanned > 500, f"guard scanned only {scanned} files under {REPO_ROOT}"  # an empty walk must not pass
    assert not hits, (
        "reserved LogRecord attribute(s) passed in logging extra= (KeyError at emit):\n"
        + "\n".join(hits)
    )
