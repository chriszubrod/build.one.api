# Python Standard Library Imports
import pyodbc
import time
from contextlib import contextmanager

# Third-party Imports

# Local Imports
import config

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
