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
    attempt = 0
    conn = None
    while True:
        try:
            conn = _connect()
            try:
                yield conn
                conn.commit()
            except:
                conn.rollback()
                raise
            break
        except pyodbc.Error as e:
            if attempt >= retries:
                raise

            time.sleep(backoff ** (2 ** attempt))
            attempt += 1
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass


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
