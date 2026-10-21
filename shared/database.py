# Python Standard Library Imports
import functools
import logging
import re
import pyodbc
import time
from contextlib import contextmanager
from typing import Callable, Optional, TypeVar, Any

# Third-party Imports

# Local Imports
import config
from shared.db_constraints import (
    ConstraintViolation,
    classify_constraint_violation,
    looks_like_unique_violation,
)

logger = logging.getLogger(__name__)

T = TypeVar('T')

# Enable ODBC driver-manager connection pooling. Must be set before the first
# pyodbc.connect() call. Pooling is configured in the API Docker image:
# Dockerfile post-install edits /etc/odbcinst.ini — `[ODBC] Pooling=Yes` plus
# `CPTimeout=120` under `[ODBC Driver 18 for SQL Server]` (unixODBC evicts idle
# pooled handles before Azure SQL's longer server-side idle disconnect).
#
# Pooled connections reuse the same SQL Server session (same @@SPID). Never set
# session-scoped state on a pooled checkout — SET options outside sprocs, Session-
# owned sp_getapplock, CONTEXT_INFO, etc. persist across close→connect reuse.
pyodbc.pooling = True

@functools.lru_cache(maxsize=1)
def _get_db_config():
    """Get database configuration from settings."""
    settings = config.Settings()
    return {
        'driver': settings.db_driver,
        'server': settings.db_server,
        'database': settings.db_name,
        'uid': settings.db_user,
        'pwd': settings.db_password,
        'encrypt': settings.db_encrypt,
    }


_SQL_ERROR_NUMBER_RE = re.compile(r"\((\d{3,5})\)")
_SQL_EXEC_DIRECT_TRAILING_RE = re.compile(r"\((\d{3,5})\)\s*\(SQLExecDirectW\)")
_SQL_EXEC_DIRECT_STRIP_RE = re.compile(r"\s*\(\d{3,5}\)\s*\(SQLExecDirectW\).*")

_ODBC_DRIVER_MARKERS = ("[Microsoft]", "ODBC Driver", "[SQL Server]")


def extract_sql_error_number(text: str) -> Optional[str]:
    """Trailing SQL Server native error number in ODBC driver text, e.g. '(547)' before ``(SQLExecDirectW)``."""
    match = _SQL_EXEC_DIRECT_TRAILING_RE.search(text)
    if match:
        return match.group(1)
    matches = list(_SQL_ERROR_NUMBER_RE.finditer(text))
    if not matches:
        return None
    return matches[-1].group(1)


def _looks_like_odbc_driver_text(error_text: str) -> bool:
    """Strong ODBC/SQL Server driver markers only (avoid bracket codes in app text)."""
    if not error_text:
        return False
    return any(marker in error_text for marker in _ODBC_DRIVER_MARKERS)


def _redacted_driver_message(category: str, error_text: str) -> str:
    """Client/log-safe str() for mapped DB errors — never echoes bound parameter values."""
    number = extract_sql_error_number(error_text)
    # User RAISERROR (50000) carries app-owned text without ODBC parameter binding.
    if number == "50000":
        inner = _sql_server_message_from_odbc(error_text)
        if inner:
            return inner
    if number:
        return f"{category} ({number})"
    return category


def _mapped_error_message(category: str, error: Exception, error_text: str) -> str:
    """Redact driver/parameter text; preserve app-owned messages (repo guardrails, etc.)."""
    if isinstance(error, pyodbc.Error) or _looks_like_odbc_driver_text(error_text):
        return _redacted_driver_message(category, error_text)
    if error_text:
        return f"{category}: {error_text}"
    return category


def _sql_server_message_from_odbc(error_text: str) -> Optional[str]:
    marker = "][SQL Server]"
    idx = error_text.find(marker)
    if idx == -1:
        return None
    tail = error_text[idx + len(marker) :].strip()
    if not tail:
        return None
    tail = _SQL_EXEC_DIRECT_STRIP_RE.sub("", tail).strip()
    tail = tail.rstrip("')").rstrip("'").strip()
    return tail or None


def format_exception_for_log(error: Exception) -> str:
    """Safe one-line summary for logs (class + SQL error number when present)."""
    if isinstance(error, DatabaseError):
        from shared.observability import redact_log_text

        return redact_log_text(str(error))
    error_text = str(error)
    if isinstance(error, pyodbc.Error) or _looks_like_odbc_driver_text(error_text):
        return _redacted_driver_message(type(error).__name__, error_text)
    return type(error).__name__


class DatabaseError(Exception):
    pass

class DatabaseConnectionError(DatabaseError):
    def __init__(self, message: str, *, original: Optional[str] = None):
        self.original = original if original is not None else message
        super().__init__(message)


class DatabaseOperationError(DatabaseError):
    """Mapped operation failure; str() is redacted, .original keeps driver text for handlers."""

    def __init__(self, message: str, *, original: Optional[str] = None):
        self.original = original if original is not None else message
        super().__init__(message)

class DatabaseConcurrencyError(DatabaseError):
    def __init__(self, message: str, *, original: Optional[str] = None):
        self.original = original if original is not None else message
        super().__init__(message)


class RowVersionConflictError(DatabaseConcurrencyError):
    """The UPDATE matched no row at the stored RowVersion; the row still exists for this actor — reload and retry."""


class RecordNotFoundError(DatabaseError):
    """The row no longer exists (or is no longer visible to this actor)."""


class DatabaseTimeoutError(DatabaseError):
    def __init__(self, message: str, *, original: Optional[str] = None):
        self.original = original if original is not None else message
        super().__init__(message)


class DatabaseConstraintError(DatabaseError):
    """A SQL constraint violation (FK 547 / unique 2627|2601), classified precisely by error number.

    str() is the CLEAN client-safe message; .original keeps the raw driver text for logs and for
    handlers that still need to sniff it.
    """
    def __init__(self, violation: ConstraintViolation, original: str):
        super().__init__(violation.message)
        self.violation = violation
        self.original = original


def _connect():
    config = _get_db_config()
    return pyodbc.connect(
        driver=config['driver'],
        server=config['server'],
        database=config['database'],
        uid=config['uid'],
        pwd=config['pwd'],
        encrypt=config['encrypt'],
        timeout=15,
        autocommit=False,
        # msodbcsql18 idle connection resiliency: transparent reconnect of a broken
        # idle session on a later execute (ConnectRetryCount/Interval), not only at
        # initial connect. Complements CPTimeout in odbcinst.ini for pooled handles.
        ConnectRetryCount=3,
        ConnectRetryInterval=2,
    )


def _validate_connection(conn: pyodbc.Connection) -> None:
    """Cheap liveness probe — catches dead handles checked out from the ODBC pool."""
    conn.execute("SELECT 1").fetchone()

@contextmanager
def get_connection(retries: int = 2, backoff: float = 0.5):
    """
    Context manager for database connections with retry logic.
    
    Separates connection establishment (with retries) from the yield/exception handling
    to avoid generator state issues.
    """
    conn = None
    last_error = None
    
    # Connection retry loop (separate from yield). With pooling, connect() may reuse
    # a pooled session whose TCP link Azure already dropped — failure shows on first
    # execute (08S01), not on connect; validate once and retry checkout.
    for attempt in range(retries + 1):
        try:
            conn = _connect()
        except pyodbc.Error as e:
            last_error = e
            conn = None
            if attempt >= retries:
                raise
            time.sleep(backoff * (2 ** attempt))
            continue

        try:
            _validate_connection(conn)
            last_error = None
            break
        except pyodbc.Error as e:
            last_error = e
            try:
                conn.close()
            except Exception:
                pass
            conn = None
            if not is_transient_error(e) or attempt >= retries:
                raise
            time.sleep(backoff * (2 ** attempt))

    if conn is None:
        raise last_error or DatabaseConnectionError("Failed to establish database connection")
    
    # Yield with proper exception handling (no while loop)
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass  # Ignore rollback errors
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass  # Ignore close errors; pyodbc rolls back open transactions on close


@contextmanager
def conn_ctx(conn: Optional[pyodbc.Connection] = None):
    """Yield `conn` if provided (no lifecycle management); else open a fresh one.

    The seam that lets a repository method either join a caller's existing
    transaction/connection or stand alone, so a router can batch several repo
    reads onto one connection instead of checking out a new one per call.

    Canonical home for the pattern. `entities/bill/persistence/repo.py` still
    carries a private `_conn_ctx` copy predating this; migrate it here (and the
    hand-rolled variants in `core/workflow{,_event}/persistence/repo.py`)
    rather than adding a fourth.
    """
    if conn is not None:
        yield conn
    else:
        with get_connection() as c:
            yield c


def call_procedure(cursor: pyodbc.Cursor, name: str, params: dict):
    placeholders = ", ".join([f"@{k}=?" for k in params.keys()])
    sql = f"EXEC dbo.{name} {placeholders}"
    cursor.execute(sql, list(params.values()))
    return cursor


# 'conflict' is retained deliberately: it is the only keyword here covering SQL 3960
# (snapshot isolation update conflict). SQL 547 can no longer reach this list — the
# number-first constraint check in map_database_error short-circuits it — which is what
# stopped every FK violation from surfacing as a 409 concurrency error with raw SQL.
# Named rather than inlined so tests/test_database_error_mapping.py can derive the list
# instead of retyping it (a hand-copied duplicate silently stops testing the real one).
CONCURRENCY_KEYWORDS = (
    'concurrency', 'version', 'rowversion', 'optimistic', 'conflict',
    'deadlock', 'lock',
)


def map_database_error(error: Exception) -> DatabaseError:
    if isinstance(error, DatabaseError):
        return error
    error_text = str(error)
    error_message = error_text.lower()

    # Constraint violations are classified by ERROR NUMBER (two-signal) BEFORE the
    # substring blocks below. Precise beats fuzzy: SQL 547's text contains
    # 'conflicted', which the concurrency keyword list's 'conflict' entry matched,
    # so every FK violation used to surface as a 409 concurrency error with raw SQL.
    violation = classify_constraint_violation(error_text)
    if violation is not None:
        return DatabaseConstraintError(violation, original=error_text)

    # Connection-related errors
    if any(keyword in error_message for keyword in [
        'connection', 'connect', 'network', 'timeout', 'unreachable',
        'login failed', 'authentication', 'server not found'
    ]):
        return DatabaseConnectionError(
            _mapped_error_message("Database connection failed", error, error_text),
            original=error_text,
        )

    # Timeout errors
    if any(keyword in error_message for keyword in [
        'timeout', 'timed out', 'expired'
    ]):
        return DatabaseTimeoutError(
            _mapped_error_message("Database operation timed out", error, error_text),
            original=error_text,
        )

    # Concurrency errors — phrase-only unique violations (no error number) can false-match
    # 'lock' in index names; keep ConcurrencyError type but preserve str() text for classify.
    if any(keyword in error_message for keyword in CONCURRENCY_KEYWORDS):
        msg = _mapped_error_message("Concurrency violation", error, error_text)
        if looks_like_unique_violation(error_text) and not _looks_like_odbc_driver_text(
            error_text
        ):
            msg = f"Concurrency violation: {error_text}"
        return DatabaseConcurrencyError(msg, original=error_text)

    # Default to general operation error
    return DatabaseOperationError(
        _mapped_error_message("Database operation failed", error, error_text),
        original=error_text,
    )


# Transient error codes that are safe to retry
TRANSIENT_ERROR_CODES = [
    '08S01',  # Communication link failure
    '08001',  # Unable to establish connection
    '08004',  # Server rejected connection
    '40001',  # Deadlock
    '40613',  # Database unavailable (Azure SQL)
    '49918',  # Not enough resources (Azure SQL)
    '49919',  # Too many requests (Azure SQL)
    '49920',  # Too many requests (Azure SQL)
    'HYT00',  # Query timeout (ODBC)
    'HYT01',  # Connection timeout (ODBC)
]

TRANSIENT_ERROR_MESSAGES = [
    'communication link failure',
    'tcp provider',
    'connection timed out',
    'network-related',
    'connection was closed',
    'connection reset',
    'broken pipe',
    'error code 0x274c',
    '10060',  # Connection timed out
    '10054',  # Connection reset by peer
    'query timeout expired',
    'timeout expired',
    'login timeout expired',
]


def is_transient_error(error: Exception) -> bool:
    """Check if an error is transient and safe to retry."""
    error_str = str(error).lower()
    
    # Check error codes
    if hasattr(error, 'args') and len(error.args) >= 1:
        error_code = str(error.args[0]).upper() if error.args else ''
        if error_code in TRANSIENT_ERROR_CODES:
            return True
    
    # Check error messages
    for msg in TRANSIENT_ERROR_MESSAGES:
        if msg in error_str:
            return True
    
    return False


def retry_on_transient(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
) -> Callable:
    """
    Decorator that retries a function on transient database errors.
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        backoff_factor: Multiply delay by this factor after each retry
        max_delay: Maximum delay between retries
    
    Returns:
        Decorated function with retry logic
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_error = None
            delay = initial_delay
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (pyodbc.Error, DatabaseError) as e:
                    last_error = e
                    
                    if not is_transient_error(e):
                        # Non-transient error, don't retry
                        logger.error(
                            "Non-transient database error in %s: %s",
                            func.__name__,
                            format_exception_for_log(e),
                        )
                        raise
                    
                    if attempt >= max_retries:
                        logger.error(
                            "Max retries (%s) exceeded for %s: %s",
                            max_retries,
                            func.__name__,
                            format_exception_for_log(e),
                        )
                        raise
                    
                    logger.warning(
                        "Transient error in %s (attempt %s/%s): %s. Retrying in %.1fs...",
                        func.__name__,
                        attempt + 1,
                        max_retries + 1,
                        format_exception_for_log(e),
                        delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * backoff_factor, max_delay)
            
            # Should not reach here, but just in case
            if last_error:
                raise last_error
            raise DatabaseError("Unexpected error in retry logic")
        
        return wrapper
    return decorator


def with_retry(
    func: Callable[..., T],
    *args: Any,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
    **kwargs: Any,
) -> T:
    """
    Execute a function with retry logic for transient database errors.
    
    This is a helper for calling functions that aren't decorated with @retry_on_transient.
    
    Args:
        func: Function to execute
        *args: Positional arguments to pass to func
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries in seconds
        backoff_factor: Multiply delay by this factor after each retry
        max_delay: Maximum delay between retries
        **kwargs: Keyword arguments to pass to func
    
    Returns:
        Result of func(*args, **kwargs)
    """
    last_error = None
    delay = initial_delay
    
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except (pyodbc.Error, DatabaseError, Exception) as e:
            last_error = e
            
            if not is_transient_error(e):
                # Non-transient error, don't retry
                raise
            
            if attempt >= max_retries:
                logger.error(
                    "Max retries (%s) exceeded: %s",
                    max_retries,
                    format_exception_for_log(e),
                )
                raise
            
            logger.warning(
                "Transient error (attempt %s/%s): %s. Retrying in %.1fs...",
                attempt + 1,
                max_retries + 1,
                format_exception_for_log(e),
                delay,
            )
            time.sleep(delay)
            delay = min(delay * backoff_factor, max_delay)
    
    if last_error:
        raise last_error
    raise DatabaseError("Unexpected error in retry logic")
