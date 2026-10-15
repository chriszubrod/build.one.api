"""Which loggers reach App Insights at INFO, decided in one place.

The Azure exporter is attached to the root logger, and the root stays at WARNING
so library and request INFO noise is not exported. A logger's INFO records reach
App Insights only when it is listed in EXPORTED_INFO_LOGGERS, which
export_info_loggers() raises to INFO and nothing else.

U-535d: process-wide logging redaction (DB driver messages) and pypdf silencing
live here so every entry point can opt in with one call.

LogRecord ``extra=`` fields are merged in ``Logger.makeRecord`` after the record
factory runs; install wraps ``makeRecord`` (idempotent) to scrub those values too.

Only driver-shaped content is touched (strong markers: ``[Microsoft]``, ``ODBC
Driver``, ``[SQL Server]``, or a pyodbc / DB-mapped error object); a record with
none keeps its template, args and exc_info exactly as created. Known residuals:
app-owned RAISERROR 50000 text passes through by design; driver text without the
markers; an arbitrary argument object whose ``__str__`` yields driver text (only
str, exceptions and containers of them are inspected); the raw text kept on a
mapped error's ``.original`` attribute (no formatter or exporter reads it).
"""

from __future__ import annotations

import copy as copy_module
import logging
import os
import re
import warnings
from collections.abc import Mapping
from typing import Any, Callable, Optional

APP_INSIGHTS_ENV = "APPLICATIONINSIGHTS_CONNECTION_STRING"

# Clients and per-row loggers left out at INFO: docs/runbooks/observability-info-export.md
EXPORTED_INFO_LOGGERS: tuple[str, ...] = (
    # Cascade rung outcomes (U-649).
    "intelligence.cascade",
    "intelligence.transport.cascade",
    # Outbox row lifecycle: enqueue, claim, send, dead-letter.
    "intelligence.outbox",
    "integrations.intuit.qbo.outbox",
    "integrations.ms.outbox",
    "integrations.box.outbox",
    # Reconciliation runs started and completed.
    "integrations.intuit.qbo.reconciliation",
    # QBO sync pull started and completed (qbo.sync.pull.*), and scheduler lifecycle.
    "shared.scheduler",
)

_PYPDF_WARNING_MODULE = r"pypdf(\..*)?"
_PROCESS_LOGGING_CONFIGURED = False
_REDACTION_INSTALLED = False
_BASE_LOG_RECORD_FACTORY = logging.getLogRecordFactory()
_BASE_LOGGER_MAKE_RECORD = logging.Logger.makeRecord
_SCRUB_NESTED_MAX_DEPTH = 8
_LOG_RECORD_STD_KEYS = frozenset(logging.makeLogRecord({}).__dict__)

_MAPPED_DB_ERROR_PREFIXES = (
    "Database operation failed:",
    "Database connection failed:",
    "Database operation timed out:",
    "Concurrency violation:",
)

_TUPLE_ODBC_BLOB_RE = re.compile(
    r"\('[\w\d]+',\s*'(?:\[[0-9A-Z]{5}\]\s*)?\[Microsoft\]\[ODBC Driver[^\]]+\]\[SQL Server\][^']*'\s*\)",
)


class _RedactedExcInfo(BaseException):
    """Stand-in exception whose str() is safe for log formatters."""


def _mapped_db_error_redaction(text: str) -> Optional[str]:
    """If ``text`` is a mapped DB error line with driver tail, return redacted line."""
    from shared.database import _looks_like_odbc_driver_text, extract_sql_error_number

    for prefix in _MAPPED_DB_ERROR_PREFIXES:
        if text.startswith(prefix):
            tail = text[len(prefix) :].strip()
            if _looks_like_odbc_driver_text(tail):
                category = prefix.rstrip(":").strip()
                num = extract_sql_error_number(tail)
                return f"{category} ({num})" if num else category
            return None
    return None


def _looks_like_mapped_db_error_line(text: str) -> bool:
    return any(text.startswith(p) for p in _MAPPED_DB_ERROR_PREFIXES)


def _redact_mapped_db_line(text: str) -> str:
    redacted = _mapped_db_error_redaction(text)
    return redacted if redacted is not None else text


def _odbc_blob_start_index(text: str) -> int:
    """Index where ODBC/SQL driver text begins, or -1 if none."""
    candidates: list[int] = []
    for needle in ("[Microsoft][ODBC", "[Microsoft]", "ODBC Driver", "[SQL Server]"):
        idx = text.find(needle)
        if idx >= 0:
            candidates.append(idx)
    return min(candidates) if candidates else -1


def redact_log_text(text: Any) -> Any:
    """Scrub driver-shaped SQL/ODBC text from a log line; never raises."""
    try:
        if not isinstance(text, str) or not text:
            return text
        from shared.database import _looks_like_odbc_driver_text

        if _looks_like_mapped_db_error_line(text):
            return _redact_mapped_db_line(text)
        if not _looks_like_odbc_driver_text(text):
            return text
        start = _odbc_blob_start_index(text)
        if start > 0:
            prefix, tail = text[:start].rstrip(), text[start:]
            if prefix.endswith(":") or prefix.lower().endswith(" failed"):
                redacted_tail = _redact_odbc_substrings(tail)
                return f"{prefix} {redacted_tail}"
            return _redact_odbc_substrings(tail)
        return _redact_odbc_substrings(text)
    except Exception:
        try:
            if isinstance(text, str) and text:
                from shared.database import _looks_like_odbc_driver_text

                if _looks_like_odbc_driver_text(text):
                    return "pyodbc.Error"
        except Exception:
            pass
        return text


def _redact_odbc_substrings(text: str) -> str:
    """Replace ODBC-wrapped driver blobs with class + trailing native error number."""
    from shared.database import (
        _looks_like_odbc_driver_text,
        _redacted_driver_message,
        _sql_server_message_from_odbc,
        extract_sql_error_number,
    )

    num = extract_sql_error_number(text)
    if num == "50000":
        inner = _sql_server_message_from_odbc(text)
        if inner:
            return inner
    tuple_blob = _TUPLE_ODBC_BLOB_RE.search(text)
    if tuple_blob:
        safe = _redacted_driver_message("pyodbc.Error", tuple_blob.group(0))
        return text.replace(tuple_blob.group(0), safe, 1)
    return _redacted_driver_message("pyodbc.Error", text)


def _same_or_new(original: Any, scrubbed: Any) -> Any:
    """Return ``original`` when scrubbing changed nothing, so untouched values keep identity."""
    try:
        return original if scrubbed == original else scrubbed
    except Exception:
        return scrubbed


def _scrub_log_arg(value: Any, depth: int = 0) -> Any:
    try:
        if isinstance(value, BaseException):
            # Only a driver-shaped chain is collapsed; every other exception keeps
            # its message (most `logger.error("...: %s", e)` sites are not DB errors).
            if _exception_chain_needs_redaction(value):
                from shared.database import format_exception_for_log

                return format_exception_for_log(value)
            return value
        if isinstance(value, str):
            return redact_log_text(value)
        if depth >= _SCRUB_NESTED_MAX_DEPTH:
            return value
        if isinstance(value, Mapping):
            return _same_or_new(
                value, {key: _scrub_log_arg(val, depth + 1) for key, val in value.items()}
            )
        if isinstance(value, tuple):
            return _same_or_new(value, tuple(_scrub_log_arg(item, depth + 1) for item in value))
        if isinstance(value, list):
            return _same_or_new(value, [_scrub_log_arg(item, depth + 1) for item in value])
        return value
    except Exception:
        return value


def _driver_shaped_exception(exc: BaseException) -> bool:
    try:
        import pyodbc

        from shared.database import DatabaseError, _looks_like_odbc_driver_text

        if isinstance(exc, pyodbc.Error):
            return True
        if isinstance(exc, DatabaseError):
            original = getattr(exc, "original", None)
            if isinstance(original, str) and _looks_like_odbc_driver_text(original):
                return True
            return _looks_like_odbc_driver_text(str(exc))
    except Exception:
        return True
    return False


def _exception_message_needs_redaction(exc: BaseException) -> bool:
    if _driver_shaped_exception(exc):
        return True
    try:
        from shared.database import _looks_like_odbc_driver_text

        return _looks_like_odbc_driver_text(str(exc))
    except Exception:
        return True


def _linked_exceptions(exc: BaseException) -> list[BaseException]:
    """Every exception a traceback renders with ``exc``: cause, context, group members."""
    linked: list[BaseException] = []
    for link in (exc.__cause__, exc.__context__):
        if link is not None:
            linked.append(link)
    members = getattr(exc, "exceptions", None) if isinstance(exc, BaseExceptionGroup) else None
    if members:
        linked.extend(m for m in members if isinstance(m, BaseException))
    return linked


def _notes_need_redaction(exc: BaseException) -> bool:
    notes = getattr(exc, "__notes__", None)
    if not notes:
        return False
    return any(isinstance(n, str) and redact_log_text(n) != n for n in notes)


def _exception_chain_needs_redaction(exc: BaseException, seen: Optional[set[int]] = None) -> bool:
    if seen is None:
        seen = set()
    if id(exc) in seen:
        return False
    seen.add(id(exc))
    try:
        if _exception_message_needs_redaction(exc) or _notes_need_redaction(exc):
            return True
    except Exception:
        return True
    return any(_exception_chain_needs_redaction(link, seen) for link in _linked_exceptions(exc))


def _init_redacted_exception_copy(copy: BaseException, exc: BaseException) -> None:
    import pyodbc

    from shared.database import (
        DatabaseConcurrencyError,
        DatabaseConnectionError,
        DatabaseConstraintError,
        DatabaseOperationError,
        DatabaseTimeoutError,
        format_exception_for_log,
    )

    safe = format_exception_for_log(exc)
    if isinstance(exc, pyodbc.Error):
        if len(exc.args) >= 2 and isinstance(exc.args[0], str):
            copy.__init__(exc.args[0], safe)
        else:
            copy.__init__(safe)
        return
    if isinstance(exc, DatabaseConstraintError):
        copy.__init__(exc.violation, original=exc.original)
        return
    if isinstance(
        exc,
        (
            DatabaseConnectionError,
            DatabaseTimeoutError,
            DatabaseConcurrencyError,
            DatabaseOperationError,
        ),
    ):
        copy.__init__(safe, original=exc.original)
        return
    copy.__init__(safe)


def _copy_exception_redacted(exc: BaseException, seen: Optional[set[int]] = None) -> BaseException:
    """Build a fresh exception tree with real types and tracebacks; never mutates ``exc``.

    Only driver-shaped links get a redacted message; every other link keeps its
    type, args and attributes (built with ``__new__`` + ``args`` so classes with
    required ``__init__`` arguments, OSError fields included, copy faithfully).
    """
    if seen is None:
        seen = set()
    if id(exc) in seen:
        return _RedactedExcInfo("…")
    seen.add(id(exc))

    from shared.database import format_exception_for_log

    exc_type = type(exc)
    redact_message = _exception_message_needs_redaction(exc)
    copy: BaseException
    if isinstance(exc, BaseExceptionGroup):
        members = [_copy_exception_redacted(m, seen) for m in exc.exceptions]
        try:
            copy = exc.derive(members)
        except Exception:
            copy = _RedactedExcInfo(type(exc).__name__)
    elif redact_message:
        try:
            copy = exc_type.__new__(exc_type)
            _init_redacted_exception_copy(copy, exc)
        except Exception:
            copy = _RedactedExcInfo(format_exception_for_log(exc))
    else:
        try:
            # copy.copy rebuilds through the exception's own __reduce__, which keeps
            # OSError's errno/strerror/filename and other C-level fields.
            copy = copy_module.copy(exc)
            if type(copy) is not exc_type:
                raise TypeError("copy changed type")
        except Exception:
            try:
                copy = exc_type.__new__(exc_type, *exc.args)
                copy.args = exc.args
                copy.__dict__.update(getattr(exc, "__dict__", {}))
            except Exception:
                copy = _RedactedExcInfo(str(exc))

    notes = getattr(exc, "__notes__", None)
    if notes:
        try:
            copy.__notes__ = [redact_log_text(n) if isinstance(n, str) else n for n in notes]
        except Exception:
            pass
    copy.__traceback__ = exc.__traceback__
    copy.__suppress_context__ = exc.__suppress_context__

    cause = exc.__cause__
    context = exc.__context__
    if cause is not None:
        copy.__cause__ = _copy_exception_redacted(cause, seen)
    if context is not None and context is not cause:
        copy.__context__ = _copy_exception_redacted(context, seen)
    return copy


def _scrub_exc_info(exc_info: Any) -> Any:
    if not exc_info or exc_info == (None, None, None):
        return exc_info
    exc_type, exc_val, exc_tb = exc_info
    if exc_val is None:
        return exc_info
    try:
        if not _exception_chain_needs_redaction(exc_val):
            return exc_info
        sanitized = _copy_exception_redacted(exc_val)
        return (exc_type, sanitized, exc_tb)
    except Exception:
        return (exc_type, _RedactedExcInfo(type(exc_val).__name__), exc_tb)


def _scrub_log_record_extras(record: logging.LogRecord) -> None:
    for key, value in list(record.__dict__.items()):
        if key in _LOG_RECORD_STD_KEYS:
            continue
        scrubbed = _scrub_log_arg(value)
        if scrubbed is not value:
            record.__dict__[key] = scrubbed


def _scrub_log_record(record: logging.LogRecord) -> logging.LogRecord:
    """Redact driver-shaped text from a record. A record that needs no redaction
    is left exactly as created (template, args and exc_info untouched)."""
    try:
        msg = record.msg
        if isinstance(msg, BaseException):
            if _exception_chain_needs_redaction(msg):
                from shared.database import format_exception_for_log

                record.msg = format_exception_for_log(msg)
                record.args = ()
        elif not isinstance(msg, str) and msg is not None:
            try:
                text = str(msg)
            except Exception:
                text = None
            if text is not None and redact_log_text(text) != text:
                record.msg = text  # rendered and redacted below

        if record.args:
            original = record.args
            if isinstance(original, Mapping):
                scrubbed = {key: _scrub_log_arg(val) for key, val in original.items()}
            elif isinstance(original, tuple):
                scrubbed = tuple(_scrub_log_arg(a) for a in original)
            else:
                scrubbed = _scrub_log_arg(original)
            try:
                args_changed = scrubbed != original
            except Exception:
                args_changed = True
            template_changed = isinstance(record.msg, str) and redact_log_text(record.msg) != record.msg
            if args_changed or template_changed:
                # Render first, then redact, so a template is never altered while
                # its args remain (that would make formatting raise and lose the record).
                record.args = scrubbed
                try:
                    rendered = record.getMessage()
                except Exception:
                    rendered = str(record.msg)
                record.msg = redact_log_text(rendered)
                record.args = ()
        elif isinstance(record.msg, str):
            record.msg = redact_log_text(record.msg)

        _scrub_log_record_extras(record)

        if record.exc_info and record.exc_info[1] is not None:
            record.exc_info = _scrub_exc_info(record.exc_info)
    except Exception:
        pass
    return record


def _redacting_log_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _BASE_LOG_RECORD_FACTORY(*args, **kwargs)
    return _scrub_log_record(record)


def _redacting_logger_make_record(self: logging.Logger, *args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _BASE_LOGGER_MAKE_RECORD(self, *args, **kwargs)
    return _scrub_log_record(record)


def reset_logging_redaction_for_tests() -> None:
    """Restore default logging hooks (unit tests only)."""
    global _REDACTION_INSTALLED, _PROCESS_LOGGING_CONFIGURED
    logging.setLogRecordFactory(_BASE_LOG_RECORD_FACTORY)
    logging.Logger.makeRecord = _BASE_LOGGER_MAKE_RECORD
    _REDACTION_INSTALLED = False
    _PROCESS_LOGGING_CONFIGURED = False


def install_log_redaction() -> None:
    """Install record-creation redaction (idempotent)."""
    global _REDACTION_INSTALLED
    if _REDACTION_INSTALLED:
        return
    logging.setLogRecordFactory(_redacting_log_record_factory)
    logging.Logger.makeRecord = _redacting_logger_make_record  # type: ignore[method-assign]
    _REDACTION_INSTALLED = True


install_database_log_redaction_filter = install_log_redaction


def silence_pypdf_loggers() -> None:
    """pypdf logs raw document byte excerpts on malformed input — silence for the process."""
    logging.getLogger("pypdf").setLevel(logging.CRITICAL + 1)
    warnings.filterwarnings("ignore", module=_PYPDF_WARNING_MODULE)  # regex module name


def configure_process_logging() -> None:
    """Idempotent startup: redact DB errors in logs + silence pypdf (U-535d)."""
    global _PROCESS_LOGGING_CONFIGURED
    if _PROCESS_LOGGING_CONFIGURED:
        return
    install_log_redaction()
    silence_pypdf_loggers()
    _PROCESS_LOGGING_CONFIGURED = True


def configure_app_insights(
    environ: Optional[dict[str, str]] = None,
    configure: Optional[Callable[..., Any]] = None,
) -> bool:
    configure_process_logging()
    env = os.environ if environ is None else environ
    if not env.get(APP_INSIGHTS_ENV):
        return False
    if configure is None:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure = configure_azure_monitor
    configure(logger_name="", enable_live_metrics=False)
    export_info_loggers()
    return True


def export_info_loggers() -> None:
    for name in EXPORTED_INFO_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)
