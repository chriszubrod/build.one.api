#!/usr/bin/env python3
"""
Script to run QBO Purchase SQL migrations.
Creates tables and stored procedures for the Purchase integration module.
"""

import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyodbc
import config


def get_connection():
    """Get database connection."""
    settings = config.Settings()
    return pyodbc.connect(
        driver=settings.db_driver,
        server=settings.db_server,
        database=settings.db_name,
        uid=settings.db_user,
        pwd=settings.db_password,
        encrypt=settings.db_encrypt,
        timeout=30,
        autocommit=True  # Required for DDL statements
    )


def execute_sql_file(conn, filepath: str):
    """Execute a SQL file, splitting on GO statements."""
    print(f"\nExecuting: {filepath}")
    
    with open(filepath, 'r') as f:
        sql_content = f.read()
    
    # Split on GO statements (case-insensitive, on its own line)
    import re
    statements = re.split(r'^\s*GO\s*$', sql_content, flags=re.MULTILINE | re.IGNORECASE)
    
    cursor = conn.cursor()
    executed = 0
    
    for statement in statements:
        statement = statement.strip()
        if not statement:
            continue
        
        # Skip SELECT statements (they're just for debugging)
        if statement.upper().startswith('SELECT') or statement.upper().startswith('UPDATE'):
            continue
            
        try:
            cursor.execute(statement)
            executed += 1
        except pyodbc.Error as e:
            print(f"  Error executing statement: {e}")
            print(f"  Statement: {statement[:100]}...")
            raise
    
    cursor.close()
    print(f"  Executed {executed} statements successfully")


def main():
    """Run all Purchase SQL migrations."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    purchase_dir = os.path.join(base_dir, 'integrations', 'intuit', 'qbo', 'purchase')
    
    sql_files = [
        os.path.join(purchase_dir, 'sql', 'qbo.purchase.sql'),
        os.path.join(purchase_dir, 'connector', 'expense', 'sql', 'qbo.purchase_expense.sql'),
        os.path.join(purchase_dir, 'connector', 'expense_line_item', 'sql', 'qbo.purchase_line_expense_line_item.sql'),
    ]
    
    # Verify all files exist
    for sql_file in sql_files:
        if not os.path.exists(sql_file):
            print(f"Error: SQL file not found: {sql_file}")
            sys.exit(1)
    
    print("Connecting to database...")
    conn = get_connection()
    print("Connected successfully")
    
    try:
        for sql_file in sql_files:
            execute_sql_file(conn, sql_file)
        
        print("\n" + "="*50)
        print("All SQL migrations completed successfully!")
        print("="*50)
        
        # Verify tables were created
        cursor = conn.cursor()
        cursor.execute("""
            SELECT TABLE_SCHEMA, TABLE_NAME 
            FROM INFORMATION_SCHEMA.TABLES 
            WHERE TABLE_NAME LIKE '%Purchase%'
            ORDER BY TABLE_SCHEMA, TABLE_NAME
        """)
        tables = cursor.fetchall()
        
        print("\nCreated tables:")
        for table in tables:
            print(f"  [{table.TABLE_SCHEMA}].[{table.TABLE_NAME}]")
        
        cursor.close()
        
    finally:
        conn.close()


if __name__ == '__main__':
    main()
