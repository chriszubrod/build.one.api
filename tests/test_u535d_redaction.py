"""U-535d — centralized exception/log redaction (W-9 TIN leak channels)."""

from __future__ import annotations

import asyncio
import io
import logging
import traceback
from unittest.mock import patch

import httpx
import pyodbc
import pytest
from fastapi import HTTPException

from shared.api import admin as admin_mod
from shared.database import (
    extract_sql_error_number,
    format_exception_for_log,
    map_database_error,
    retry_on_transient,
)
from shared import observability as observability_mod
from shared.observability import (
    APP_INSIGHTS_ENV,
    configure_app_insights,
    configure_process_logging,
    install_log_redaction,
    silence_pypdf_loggers,
)


def reset_logging_redaction_for_tests() -> None:
    observability_mod.reset_logging_redaction_for_tests()


from shared.storage import AzureBlobStorageError
from integrations.ms.outbox.business.worker import MsOutboxWorker

FAKE_TIN = "123-45-6789"
FAKE_NAME = "TaxpayerJaneDoe"


@pytest.fixture
def logging_redaction_isolated():
    """Clean logging hooks; remove handlers added during the test."""
    saved_factory = logging.getLogRecordFactory()
    saved_make_record = logging.Logger.makeRecord
    root = logging.getLogger()
    saved_root_handlers = root.handlers[:]
    saved_root_level = root.level
    reset_logging_redaction_for_tests()
    added_handlers: list[logging.Handler] = []

    def _track(handler: logging.Handler) -> logging.Handler:
        root.addHandler(handler)
        added_handlers.append(handler)
        return handler

    yield _track
    for h in added_handlers:
        try:
            root.removeHandler(h)
        except ValueError:
            pass
    root.handlers = saved_root_handlers
    root.setLevel(saved_root_level)
    logging.setLogRecordFactory(saved_factory)
    logging.Logger.makeRecord = saved_make_record
    reset_logging_redaction_for_tests()


def _sensitive_pyodbc_error() -> pyodbc.Error:
    msg = (
        f"('[42000]', '[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
        f"Violation for {FAKE_NAME} TIN {FAKE_TIN}. (50001) (SQLExecDirectW)')"
    )
    return pyodbc.Error(msg)


def _real_pyodbc(num: int, body: str, state: str = "42000") -> pyodbc.Error:
    return pyodbc.Error(
        state,
        f"[{state}] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]{body} ({num}) (SQLExecDirectW)",
    )


class _OtelStyleHandler(logging.Handler):
    """Reads exc_info and getMessage() like Azure Monitor's LoggingHandler."""

    def __init__(self, sink: io.StringIO):
        super().__init__()
        self.sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            chain = []
            cur = exc
            while cur is not None:
                chain.append(str(cur))
                cur = cur.__cause__ or cur.__context__
            msg = f"{msg} | exc={type(exc).__name__} chain={' -> '.join(chain)}"
        self.sink.write(msg + "\n")


def test_map_database_error_str_redacts_tin_and_name():
    mapped = map_database_error(_sensitive_pyodbc_error())
    text = str(mapped)
    assert FAKE_TIN not in text
    assert FAKE_NAME not in text
    assert "50001" in text
    assert FAKE_TIN in mapped.original


def test_retry_on_transient_logs_redacted(caplog):
    @retry_on_transient(max_retries=0)
    def _boom():
        raise _sensitive_pyodbc_error()

    caplog.set_level(logging.ERROR)
    with pytest.raises(pyodbc.Error):
        _boom()
    blob = caplog.text
    assert FAKE_TIN not in blob
    assert FAKE_NAME not in blob


def test_repo_style_logger_error_fstring_redacted_by_filter(caplog, logging_redaction_isolated):
    configure_process_logging()
    log = logging.getLogger("tests.u535d.repo_style")
    caplog.set_level(logging.ERROR)
    err = _sensitive_pyodbc_error()
    log.error(f"repo failed: {err}")
    assert FAKE_TIN not in caplog.text
    assert FAKE_NAME not in caplog.text


def test_timed_redact_errors_hides_message_in_http_detail():
    def _run():
        raise ValueError(f"secret {FAKE_TIN} {FAKE_NAME}")

    async def _invoke():
        with pytest.raises(HTTPException) as exc_info:
            await admin_mod._timed("job.test", _run)
        return exc_info

    exc_info = asyncio.run(_invoke())
    detail = exc_info.value.detail
    assert FAKE_TIN not in detail
    assert FAKE_NAME not in detail
    assert "ValueError" in detail


def test_azure_blob_storage_error_str_and_cause():
    inner = RuntimeError(f"body had {FAKE_TIN}")
    err = AzureBlobStorageError(
        "download blob",
        status_code=503,
        error_code="ServerBusy",
        cause=inner,
    )
    text = str(err)
    assert FAKE_TIN not in text
    assert "status=503" in text
    assert "code=ServerBusy" in text
    assert err.__cause__ is inner


def test_pypdf_logger_configured_by_observability():
    silence_pypdf_loggers()
    assert logging.getLogger("pypdf").level > logging.CRITICAL


def test_constraint_classification_unchanged():
    from shared.database import DatabaseConstraintError, DatabaseConcurrencyError
    from shared.db_constraints import FK_REFERENCE, FK_REFERENCE_MESSAGE, UNIQUE
    from tests.test_database_error_mapping import FK_DELETE_547, UQ_2627, _odbc

    fk = map_database_error(_odbc(FK_DELETE_547))
    assert isinstance(fk, DatabaseConstraintError)
    assert not isinstance(fk, DatabaseConcurrencyError)
    assert fk.violation.kind == FK_REFERENCE
    assert str(fk) == FK_REFERENCE_MESSAGE

    uq = map_database_error(_odbc(UQ_2627))
    assert isinstance(uq, DatabaseConstraintError)
    assert uq.violation.kind == UNIQUE


def test_r1_p0_1_child_logger_redacted_after_handler_installed(logging_redaction_isolated):
    """Prod order: scrubbing factory before Azure-style handler; child logger propagates."""
    buf = io.StringIO()
    fmt = logging.Formatter("%(levelname)s %(message)s")
    install_log_redaction()
    h = logging.StreamHandler(buf)
    h.setFormatter(fmt)
    logging_redaction_isolated(h)
    otel = _OtelStyleHandler(buf)
    logging_redaction_isolated(otel)

    log = logging.getLogger("entities.taxpayer.persistence.repo")
    err = _real_pyodbc(2628, f"Truncated value: '{FAKE_TIN}'.")
    log.error(f"repo failed: {err}")

    out = buf.getvalue()
    assert FAKE_TIN not in out
    assert FAKE_NAME not in out
    assert "2628" in out or "pyodbc.Error" in out


def test_r1_p0_1_configure_app_insights_order(logging_redaction_isolated):
    buf = io.StringIO()
    fmt = logging.Formatter("%(message)s")

    def fake_configure(**kwargs):
        h = logging.StreamHandler(buf)
        h.setFormatter(fmt)
        logging_redaction_isolated(h)

    configure_app_insights(environ={APP_INSIGHTS_ENV: "x"}, configure=fake_configure)
    log = logging.getLogger("entities.taxpayer.persistence.repo")
    log.error(f"repo failed: {_real_pyodbc(245, f'nvarchar {FAKE_TIN}', '22018')}")
    assert FAKE_TIN not in buf.getvalue()


def test_r1_p0_2_traceback_and_exc_info_chain_redacted(logging_redaction_isolated):
    install_log_redaction()
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s\n%(exc_info)s"))
    logging_redaction_isolated(handler)
    otel = _OtelStyleHandler(buf)
    logging_redaction_isolated(otel)

    original = _real_pyodbc(2628, f"Truncated value: {FAKE_TIN}")

    def repo():
        raise original

    try:
        try:
            repo()
        except pyodbc.Error as e:
            raise map_database_error(e) from e
    except Exception:
        logging.getLogger("entities.taxpayer.persistence.repo").exception("taxpayer create failed")

    out = buf.getvalue()
    assert FAKE_TIN not in out
    assert type(original).__name__ == "Error"
    assert FAKE_TIN in str(original)
    assert "DatabaseOperationError" in out or "pyodbc.Error" in out


def test_r1_p0_2_mapped_exception_chain_redacted(logging_redaction_isolated):
    install_log_redaction()
    buf = io.StringIO()
    logging_redaction_isolated(_OtelStyleHandler(buf))
    original = _real_pyodbc(2628, f"Truncated value: {FAKE_TIN}")
    try:
        try:
            raise original
        except pyodbc.Error as e:
            raise map_database_error(e) from e
    except Exception:
        logging.getLogger("x").exception("mapped")

    assert FAKE_TIN not in buf.getvalue()


def test_r1_p1_1_trailing_native_number_and_short_codes():
    text = (
        "[42000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
        "Taxpayer (12345) 999 (50000) (SQLExecDirectW)"
    )
    assert extract_sql_error_number(text) == "50000"
    e245 = _real_pyodbc(245, f"Conversion failed {FAKE_TIN}", "22018")
    mapped = format_exception_for_log(e245)
    assert FAKE_TIN not in mapped
    assert "245" in mapped


def test_r1_p1_1_driver_shaped_without_four_digit_requirement():
    text = "[08S01] [Microsoft][ODBC Driver 18 for SQL Server]Communication link failure (10054) (SQLExecDirectW)"
    assert extract_sql_error_number(text) == "10054"
    safe = format_exception_for_log(pyodbc.Error("08S01", text))
    assert FAKE_TIN not in safe
    assert "10054" in safe


def test_r1_p1_2_admin_drain_ms_route_redacts_body_and_log(
    logging_redaction_isolated, caplog, monkeypatch
):
    err = _sensitive_pyodbc_error()

    class _BoomWorker:
        def drain_all(self, max_rows=None, time_budget_seconds=None):
            raise err

    monkeypatch.setattr(
        "integrations.ms.outbox.business.worker.MsOutboxWorker",
        _BoomWorker,
    )

    caplog.set_level(logging.ERROR)
    install_log_redaction()

    async def _invoke():
        return await admin_mod.drain_ms_outbox_router()

    result = asyncio.run(_invoke())
    assert FAKE_TIN not in str(result)
    assert FAKE_NAME not in str(result)
    assert "50001" in str(result)
    assert FAKE_TIN not in caplog.text


def test_r1_p2_1_dict_style_log_args_preserved(logging_redaction_isolated):
    install_log_redaction()
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    logging_redaction_isolated(h)
    log = logging.getLogger("x.y")
    log.error("dict style %(a)s", {"a": "hello"})
    assert buf.getvalue().strip() == "dict style hello"
    buf.seek(0)
    buf.truncate(0)
    log.error(
        "dict style %(a)s",
        {
            "a": f"odbc {FAKE_TIN} [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]x (547) (SQLExecDirectW)"
        },
    )
    assert FAKE_TIN not in buf.getvalue()


def test_r1_p2_2_logger_error_msg_is_exception(logging_redaction_isolated):
    install_log_redaction()
    buf = io.StringIO()
    logging_redaction_isolated(logging.StreamHandler(buf))
    logging.getLogger("x").error(_real_pyodbc(2628, FAKE_TIN))
    assert FAKE_TIN not in buf.getvalue()


def test_r1_p2_3_raiserror_50000_app_message_preserved(logging_redaction_isolated):
    install_log_redaction()
    buf = io.StringIO()
    logging_redaction_isolated(logging.StreamHandler(buf))
    app_msg = f"Taxpayer {FAKE_TIN} already exists"
    log = logging.getLogger("x")
    log.error(f"repo failed: {_real_pyodbc(50000, app_msg)}")
    out = buf.getvalue()
    assert "ODBC Driver" not in out
    assert app_msg in out


def test_r1_p2_5_is_permanent_blob_miss_uses_status_code():
    response = httpx.Response(404, request=httpx.Request("GET", "https://blob.example/x"))
    inner = httpx.HTTPStatusError("404", request=response.request, response=response)
    err = AzureBlobStorageError("download blob", status_code=404, error_code="BlobNotFound", cause=inner)
    assert MsOutboxWorker._is_permanent_blob_miss(err) is True
    assert MsOutboxWorker._is_permanent_blob_miss(
        AzureBlobStorageError("download blob", status_code=503, cause=inner)
    ) is False


def test_r1_non_db_log_record_unchanged(logging_redaction_isolated):
    install_log_redaction()
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    log = logging.getLogger("plain.non_db")
    log.setLevel(logging.DEBUG)
    log.addHandler(h)
    log.propagate = False
    plain = "hello world %(name)s"
    log.info(plain, {"name": "ok"})
    assert buf.getvalue().strip() == "hello world ok"


# --- Round 2 (N1–N4) -------------------------------------------------------- #


def test_r2_n1_false_positive_messages_preserved(logging_redaction_isolated):
    install_log_redaction()
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter("%(message)s"))
    logging_redaction_isolated(h)
    logging.getLogger().setLevel(logging.INFO)
    log = logging.getLogger("scan.upload")
    log.setLevel(logging.INFO)
    log.info("[ERROR] retry %s of %s", 1, 3)
    log.info("file Scan [00012].pdf uploaded for bill %s", 42)
    log.info("('ok', 'tuple-looking') vendor %s", "acme")
    log.info("subject: %s", "Re: PO [AB123] status")
    out = buf.getvalue()
    assert "[ERROR] retry 1 of 3" in out
    assert "file Scan [00012].pdf uploaded for bill 42" in out
    assert "('ok', 'tuple-looking') vendor acme" in out
    assert "Re: PO [AB123] status" in out


def test_r2_n2_traceback_keeps_real_types_and_app_message(logging_redaction_isolated):
    install_log_redaction()
    formatted: list[str] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            if record.exc_info:
                formatted.append("".join(traceback.format_exception(*record.exc_info)))

    logging_redaction_isolated(_CaptureHandler())

    class AppErr(Exception):
        pass

    def repo():
        raise _real_pyodbc(2628, f"Truncated value: '{FAKE_TIN}'.")

    try:
        try:
            repo()
        except pyodbc.Error:
            raise AppErr("Bill 991 not found for vendor 12")
    except Exception:
        logging.getLogger("entities.bill.business.service").exception("svc")

    assert formatted
    tb = formatted[0]
    assert "AppErr" in tb
    assert "Bill 991 not found for vendor 12" in tb
    assert "repo" in tb
    assert FAKE_TIN not in tb
    assert "pyodbc.Error" in tb or "Error" in tb


def test_r2_n3_nested_args_and_extra_scrubbed(logging_redaction_isolated):
    install_log_redaction()
    buf = io.StringIO()
    logging_redaction_isolated(logging.StreamHandler(buf))
    log = logging.getLogger("nested")
    raw = _real_pyodbc(2628, FAKE_TIN)
    log.error("tuple %s", (raw,))
    assert FAKE_TIN not in buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    log.error("list %s", [raw])
    assert FAKE_TIN not in buf.getvalue()
    buf.seek(0)
    buf.truncate(0)
    log.error("extra", extra={"err": str(raw)})
    assert FAKE_TIN not in buf.getvalue()


def test_r2_n4_concurrency_raw_text_redacted_in_format_helper():
    from shared.database import DatabaseConcurrencyError

    driver_tail = (
        f"[23000] [Microsoft][ODBC Driver 18 for SQL Server][SQL Server]"
        f"Violation duplicate key ({FAKE_TIN}). (2627) (SQLExecDirectW)"
    )
    msg = f"Concurrency violation: {driver_tail}"
    err = DatabaseConcurrencyError(msg, original=driver_tail)
    safe = format_exception_for_log(err)
    assert FAKE_TIN not in safe
    assert "2627" in safe or "Concurrency violation" in safe


def test_r2_admin_routes_leak_free(monkeypatch, logging_redaction_isolated):
    install_log_redaction()

    class _MsBoom:
        def drain_once(self):
            raise map_database_error(_real_pyodbc(2628, FAKE_TIN))

    class _QboBoom:
        def drain_once(self):
            raise map_database_error(_real_pyodbc(2628, FAKE_TIN))

    monkeypatch.setattr(
        "integrations.ms.outbox.business.worker.MsOutboxWorker",
        _MsBoom,
    )
    monkeypatch.setattr(
        "integrations.intuit.qbo.outbox.business.worker.QboOutboxWorker",
        _QboBoom,
    )

    async def _call(fn):
        try:
            return await fn()
        except HTTPException as e:
            return {"detail": e.detail}

    for route in (
        admin_mod.drain_qbo_outbox_router,
        admin_mod.drain_ms_outbox_router,
    ):
        body = asyncio.run(_call(route))
        assert FAKE_TIN not in str(body)


# --- Round 3 (EM): redaction touches only what is driver-shaped ----------------


class _CaptureRecords(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _install_and_capture(logging_redaction_isolated, name: str):
    install_log_redaction()
    cap = logging_redaction_isolated(_CaptureRecords())
    log = logging.getLogger(name)
    log.setLevel(logging.DEBUG)
    return log, cap


def test_r3_new1_non_db_exception_arg_keeps_its_message(logging_redaction_isolated):
    log, cap = _install_and_capture(logging_redaction_isolated, "u535d.r3.new1")
    log.error("workflow failed: %s", ValueError("Bill 991 not found for vendor 12"))
    log.error("wrapped %s", [KeyError("missing-key")])
    rendered = [r.getMessage() for r in cap.records]
    assert rendered[0] == "workflow failed: Bill 991 not found for vendor 12"
    assert "missing-key" in rendered[1]


def test_r3_new2_record_without_driver_text_is_untouched(logging_redaction_isolated):
    log, cap = _install_and_capture(logging_redaction_isolated, "u535d.r3.new2")
    log.info("user %s did %d things", "bob", 3)
    log.info("vendor %(v)s", {"v": "Acme"})
    first, second = cap.records
    assert (first.msg, first.args) == ("user %s did %d things", ("bob", 3))
    assert second.msg == "vendor %(v)s" and second.args == {"v": "Acme"}


def test_r3_new2_driver_arg_is_still_redacted(logging_redaction_isolated):
    log, cap = _install_and_capture(logging_redaction_isolated, "u535d.r3.new2b")
    log.error("repo failed: %s", _real_pyodbc(245, f"Conversion failed for '{FAKE_TIN}'"))
    out = cap.records[0].getMessage()
    assert FAKE_TIN not in out and "245" in out


def test_r3_new3_required_init_exceptions_keep_type_and_message(logging_redaction_isolated):
    install_log_redaction()
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging_redaction_isolated(handler)
    log = logging.getLogger("u535d.r3.new3")
    for outer in (HTTPException(409, "Bill locked"), FileNotFoundError(2, "No such file", "/x/y.pdf")):
        try:
            try:
                raise _sensitive_pyodbc_error()
            except pyodbc.Error as inner:
                raise outer from inner
        except Exception:
            log.exception("failed")
    text = buf.getvalue()
    assert FAKE_TIN not in text and FAKE_NAME not in text
    assert "HTTPException" in text and "Bill locked" in text
    assert "FileNotFoundError" in text and "/x/y.pdf" in text
    assert "_RedactedExcInfo" not in text


def test_r3_new4_exception_group_member_redacted(logging_redaction_isolated):
    install_log_redaction()
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    logging_redaction_isolated(handler)
    log = logging.getLogger("u535d.r3.new4")
    try:
        raise ExceptionGroup("batch", [_sensitive_pyodbc_error(), ValueError("fine")])
    except ExceptionGroup:
        log.exception("group failed")
    try:
        try:
            raise _sensitive_pyodbc_error()
        except pyodbc.Error as inner:
            err = RuntimeError("wrapper")
            err.add_note(f"driver said {FAKE_TIN} [Microsoft][ODBC Driver 18 for SQL Server]")
            raise ExceptionGroup("nested", [err]) from inner
    except ExceptionGroup:
        log.exception("nested group failed")
    text = buf.getvalue()
    assert FAKE_TIN not in text and FAKE_NAME not in text
    assert "ValueError: fine" in text  # non-driver members keep their message


def test_r3_new5_non_str_msg_object_redacted(logging_redaction_isolated):
    log, cap = _install_and_capture(logging_redaction_isolated, "u535d.r3.new5")
    raw = _sensitive_pyodbc_error()
    log.error({"err": str(raw)})
    log.error(raw.args)
    log.error({"ok": "plain dict"})
    rendered = [r.getMessage() for r in cap.records]
    assert all(FAKE_TIN not in m and FAKE_NAME not in m for m in rendered)
    assert rendered[2] == "{'ok': 'plain dict'}"
