# Python Standard Library Imports
import functools
import logging
import pyodbc
import time
from contextlib import contextmanager
from typing import Callable, TypeVar, Any

# Third-party Imports

# Local Imports
import config

logger = logging.getLogger(__name__)

T = TypeVar('T')

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


class DatabaseError(Exception):
    pass

class DatabaseConnectionError(DatabaseError):
    pass

class DatabaseOperationError(DatabaseError):
    pass

class DatabaseConcurrencyError(DatabaseError):
    pass

class DatabaseTimeoutError(DatabaseError):
    pass


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
        autocommit=False
    )

@contextmanager
def get_connection(retries: int = 2, backoff: float = 0.5):
    """
    Context manager for database connections with retry logic.
    
    Separates connection establishment (with retries) from the yield/exception handling
    to avoid generator state issues.
    """
    conn = None
    last_error = None
    
    # Connection retry loop (separate from yield)
    for attempt in range(retries + 1):
        try:
            conn = _connect()
            break
        except pyodbc.Error as e:
            last_error = e
            if attempt >= retries:
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
            pass  # Ignore close errors


def call_procedure(cursor: pyodbc.Cursor, name: str, params: dict):
    placeholders = ", ".join([f"@{k}=?" for k in params.keys()])
    sql = f"EXEC dbo.{name} {placeholders}"
    cursor.execute(sql, list(params.values()))
    return cursor


def map_database_error(error: Exception) -> DatabaseError:
    error_message = str(error).lower()

    # Connection-related errors
    if any(keyword in error_message for keyword in [
        'connection', 'connect', 'network', 'timeout', 'unreachable',
        'login failed', 'authentication', 'server not found'
    ]):
        return DatabaseConnectionError(f"Database connection failed: {error}")
    
    # Timeout errors
    if any(keyword in error_message for keyword in [
        'timeout', 'timed out', 'expired'
    ]):
        return DatabaseTimeoutError(f"Database operation timed out: {error}")
    
    # Concurrency errors
    if any(keyword in error_message for keyword in [
        'concurrency', 'version', 'rowversion', 'optimistic', 'conflict',
        'deadlock', 'lock'
    ]):
        return DatabaseConcurrencyError(f"Concurrency violation: {error}")
    
    # Default to general operation error
    return DatabaseOperationError(f"Database operation failed: {error}")


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
]


def is_transient_error(error: Exception) -> bool:
    """Check if an error is transient and safe to retry."""
    error_str = str(error).lower()
    
    # Check error codes
    if hasattr(error, 'args') and len(error.args) >= 1:
        error_code = str(error.args[0]) if error.args else ''
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
                        logger.error(f"Non-transient database error in {func.__name__}: {e}")
                        raise
                    
                    if attempt >= max_retries:
                        logger.error(f"Max retries ({max_retries}) exceeded for {func.__name__}: {e}")
                        raise
                    
                    logger.warning(
                        f"Transient error in {func.__name__} (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                        f"Retrying in {delay:.1f}s..."
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
                logger.error(f"Max retries ({max_retries}) exceeded: {e}")
                raise
            
            logger.warning(
                f"Transient error (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                f"Retrying in {delay:.1f}s..."
            )
            time.sleep(delay)
            delay = min(delay * backoff_factor, max_delay)
    
    if last_error:
        raise last_error
    raise DatabaseError("Unexpected error in retry logic")
