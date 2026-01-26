# Python Standard Library Imports
import os
import re
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Local Imports
from shared.database import get_connection

# SQL files to run in order (dependencies first)
SQL_FILES = [
    "agents/persistence/sql/agents.Workflow.sql",
    "agents/persistence/sql/agents.WorkflowEvent.sql",
    "agents/persistence/sql/seed.WorkflowInboxModule.sql",
]


def run_sql_file(cursor, filepath: str) -> bool:
    """Run a SQL file, splitting on GO statements."""
    print(f"\n{'='*60}")
    print(f"Running: {filepath}")
    print('='*60)
    
    try:
        with open(filepath, 'r') as f:
            sql_content = f.read()
        
        # Split on GO statements (SQL Server batch separator)
        batches = re.split(r'^\s*GO\s*$', sql_content, flags=re.MULTILINE | re.IGNORECASE)
        
        success_count = 0
        error_count = 0
        
        for i, batch in enumerate(batches, 1):
            batch = batch.strip()
            if not batch:
                continue
            
            try:
                cursor.execute(batch)
                success_count += 1
            except Exception as e:
                error_str = str(e)
                # Ignore "already exists" errors for idempotent scripts
                if "already exists" in error_str.lower() or "duplicate" in error_str.lower():
                    print(f"  [SKIP] Batch {i}: Object already exists")
                else:
                    print(f"  [ERROR] Batch {i}: {error_str[:100]}")
                    error_count += 1
        
        print(f"  Completed: {success_count} batches executed, {error_count} errors")
        return error_count == 0
        
    except FileNotFoundError:
        print(f"  [ERROR] File not found: {filepath}")
        return False
    except Exception as e:
        print(f"  [ERROR] {e}")
        return False


def main():
    print("\n" + "="*60)
    print("AGENTS SCHEMA SQL MIGRATIONS")
    print("="*60)
    
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            
            all_success = True
            for sql_file in SQL_FILES:
                filepath = os.path.join(project_root, sql_file)
                if not run_sql_file(cursor, filepath):
                    all_success = False
            
            # Commit is handled by context manager
            
            print("\n" + "="*60)
            if all_success:
                print("ALL MIGRATIONS COMPLETED SUCCESSFULLY")
            else:
                print("MIGRATIONS COMPLETED WITH SOME ERRORS")
            print("="*60 + "\n")
            
    except Exception as e:
        print(f"\n[FATAL ERROR] Database connection failed: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
