# Python Standard Library Imports
import os
import sys

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

# Load environment variables
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

# Local Imports
from shared.database import get_connection


def query_qbo_vendors():
    """Query qbo.Vendor table and provide summary."""
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Total count
        cursor.execute('SELECT COUNT(*) FROM qbo.Vendor')
        total = cursor.fetchone()[0]
        print(f'Total qbo.Vendor records: {total}')
        
        # Active vs Inactive
        cursor.execute('SELECT Active, COUNT(*) FROM qbo.Vendor GROUP BY Active')
        print('\nBy Active status:')
        for row in cursor.fetchall():
            status = 'Active' if row[0] == 1 else ('Inactive' if row[0] == 0 else 'NULL')
            print(f'  {status}: {row[1]}')
        
        # 1099 vendors
        cursor.execute('SELECT Vendor1099, COUNT(*) FROM qbo.Vendor GROUP BY Vendor1099')
        print('\nBy 1099 status:')
        for row in cursor.fetchall():
            status = 'Yes' if row[0] == 1 else ('No' if row[0] == 0 else 'NULL')
            print(f'  {status}: {row[1]}')
        
        # With/without DisplayName
        cursor.execute("""
            SELECT 
                CASE WHEN DisplayName IS NULL OR DisplayName = '' THEN 'Missing' ELSE 'Has Value' END as status,
                COUNT(*) 
            FROM qbo.Vendor 
            GROUP BY CASE WHEN DisplayName IS NULL OR DisplayName = '' THEN 'Missing' ELSE 'Has Value' END
        """)
        print('\nDisplayName:')
        for row in cursor.fetchall():
            print(f'  {row[0]}: {row[1]}')
        
        # With/without CompanyName
        cursor.execute("""
            SELECT 
                CASE WHEN CompanyName IS NULL OR CompanyName = '' THEN 'Missing' ELSE 'Has Value' END as status,
                COUNT(*) 
            FROM qbo.Vendor 
            GROUP BY CASE WHEN CompanyName IS NULL OR CompanyName = '' THEN 'Missing' ELSE 'Has Value' END
        """)
        print('\nCompanyName:')
        for row in cursor.fetchall():
            print(f'  {row[0]}: {row[1]}')
        
        # With/without TaxIdentifier
        cursor.execute("""
            SELECT 
                CASE WHEN TaxIdentifier IS NULL OR TaxIdentifier = '' THEN 'Missing' ELSE 'Has Value' END as status,
                COUNT(*) 
            FROM qbo.Vendor 
            GROUP BY CASE WHEN TaxIdentifier IS NULL OR TaxIdentifier = '' THEN 'Missing' ELSE 'Has Value' END
        """)
        print('\nTaxIdentifier:')
        for row in cursor.fetchall():
            print(f'  {row[0]}: {row[1]}')
        
        # With/without PrimaryEmailAddr
        cursor.execute("""
            SELECT 
                CASE WHEN PrimaryEmailAddr IS NULL OR PrimaryEmailAddr = '' THEN 'Missing' ELSE 'Has Value' END as status,
                COUNT(*) 
            FROM qbo.Vendor 
            GROUP BY CASE WHEN PrimaryEmailAddr IS NULL OR PrimaryEmailAddr = '' THEN 'Missing' ELSE 'Has Value' END
        """)
        print('\nPrimaryEmailAddr:')
        for row in cursor.fetchall():
            print(f'  {row[0]}: {row[1]}')
        
        # With/without PrimaryPhone
        cursor.execute("""
            SELECT 
                CASE WHEN PrimaryPhone IS NULL OR PrimaryPhone = '' THEN 'Missing' ELSE 'Has Value' END as status,
                COUNT(*) 
            FROM qbo.Vendor 
            GROUP BY CASE WHEN PrimaryPhone IS NULL OR PrimaryPhone = '' THEN 'Missing' ELSE 'Has Value' END
        """)
        print('\nPrimaryPhone:')
        for row in cursor.fetchall():
            print(f'  {row[0]}: {row[1]}')
        
        # With balance
        cursor.execute('SELECT COUNT(*) FROM qbo.Vendor WHERE Balance IS NOT NULL AND Balance != 0')
        with_balance = cursor.fetchone()[0]
        cursor.execute('SELECT SUM(Balance) FROM qbo.Vendor WHERE Balance IS NOT NULL AND Balance != 0')
        total_balance = cursor.fetchone()[0] or 0
        print(f'\nVendors with non-zero Balance: {with_balance} (Total: ${total_balance:,.2f})')
        
        # By RealmId
        cursor.execute('SELECT RealmId, COUNT(*) FROM qbo.Vendor GROUP BY RealmId')
        print('\nBy RealmId:')
        for row in cursor.fetchall():
            print(f'  {row[0]}: {row[1]}')
        
        # Duplicate DisplayNames
        cursor.execute("""
            SELECT DisplayName, COUNT(*) as cnt 
            FROM qbo.Vendor 
            WHERE DisplayName IS NOT NULL AND DisplayName != ''
            GROUP BY DisplayName 
            HAVING COUNT(*) > 1
            ORDER BY cnt DESC
        """)
        dupes = cursor.fetchall()
        print(f'\nDuplicate DisplayNames: {len(dupes)}')
        if dupes:
            print('  Top duplicates:')
            for row in dupes[:10]:
                print(f'    "{row[0]}": {row[1]} records')


if __name__ == "__main__":
    query_qbo_vendors()
