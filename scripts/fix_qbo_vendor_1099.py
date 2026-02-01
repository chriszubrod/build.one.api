#!/usr/bin/env python3
"""
Script to fix QBO Vendor records where TaxIdentifier exists but Vendor1099 is False.
Sets Vendor1099 = True in QuickBooks Online and syncs back to local database.
"""

# Python Standard Library Imports
import argparse
import logging
import os
import sys
import time

# Setup path and load environment before other imports
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

# Third-party imports
# (none)

# Local imports
from shared.database import get_connection
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.vendor.external.client import QboVendorClient
from integrations.intuit.qbo.vendor.external.schemas import QboVendorUpdate

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_vendors_needing_1099_fix() -> list[dict]:
    """
    Get all QBO vendors that have TaxIdentifier but Vendor1099 is False/NULL.
    
    Returns:
        List of dicts with vendor info
    """
    vendors = []
    
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT QboId, DisplayName, TaxIdentifier, Vendor1099, SyncToken
            FROM qbo.Vendor
            WHERE TaxIdentifier IS NOT NULL 
            AND TaxIdentifier != ''
            AND (Vendor1099 = 0 OR Vendor1099 IS NULL)
            ORDER BY DisplayName
        """)
        
        for row in cursor.fetchall():
            vendors.append({
                'qbo_id': str(row[0]),
                'display_name': row[1],
                'tax_identifier': row[2],
                'vendor_1099': row[3],
                'sync_token': str(row[4]) if row[4] else '0',
            })
    
    return vendors


def update_vendor_in_qbo(client: QboVendorClient, vendor: dict) -> bool:
    """
    Update a vendor in QuickBooks to set Vendor1099 = True.
    
    Args:
        client: QboVendorClient instance
        vendor: Dict with vendor info
    
    Returns:
        True if update succeeded, False otherwise
    """
    try:
        # First, fetch the current vendor from QBO to get latest SyncToken
        current_vendor = client.get_vendor(vendor['qbo_id'])
        
        if not current_vendor:
            logger.error(f"Could not fetch QBO vendor {vendor['qbo_id']}")
            return False
        
        # Create sparse update payload - only updates specified fields
        update = QboVendorUpdate(
            id=current_vendor.id,
            sync_token=current_vendor.sync_token,
            display_name=current_vendor.display_name,
            vendor_1099=True,  # Set Vendor1099 = True
            sparse=True,  # Use sparse update to preserve other fields
        )
        
        # Update in QBO
        result = client.update_vendor(update)
        
        logger.info(f"Updated QBO vendor {vendor['qbo_id']}: Vendor1099 set to True for '{vendor['display_name']}'")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update QBO vendor {vendor['qbo_id']}: {e}")
        return False


def fix_qbo_vendor_1099(dry_run: bool = True, limit: int = None) -> dict:
    """
    Fix vendors with TaxIdentifier but Vendor1099 = False.
    
    Args:
        dry_run: If True, only show what would be done without making changes
        limit: Optional limit on number of vendors to process
    
    Returns:
        Dict with results summary
    """
    results = {
        "total": 0,
        "updated_in_qbo": 0,
        "failed": 0,
        "synced_to_local": 0,
        "errors": [],
    }
    
    logger.info(f"Starting QBO vendor 1099 fix (dry_run={dry_run})")
    
    # Get vendors needing fix
    vendors = get_vendors_needing_1099_fix()
    logger.info(f"Found {len(vendors)} vendors with TaxIdentifier but Vendor1099=False")
    
    # Apply limit if specified
    if limit and limit > 0:
        vendors = vendors[:limit]
        logger.info(f"Limiting to first {limit} vendor(s)")
    
    results["total"] = len(vendors)
    
    if dry_run:
        logger.info("DRY RUN - No changes will be made")
        print("\nVendors that would be updated:")
        print(f"{'QBO ID':<10} {'DisplayName':<40} {'TaxIdentifier':<15}")
        print("-" * 70)
        for v in vendors[:20]:
            tax_id = v['tax_identifier'][:4] + '****' if v['tax_identifier'] and len(v['tax_identifier']) > 4 else v['tax_identifier']
            print(f"{v['qbo_id']:<10} {v['display_name'][:38]:<40} {tax_id:<15}")
        if len(vendors) > 20:
            print(f"... and {len(vendors) - 20} more")
        return results
    
    # Get QBO auth
    auth_service = QboAuthService()
    qbo_auth = auth_service.ensure_valid_token()
    
    if not qbo_auth:
        error = "Failed to get QBO authentication"
        logger.error(error)
        results["errors"].append(error)
        return results
    
    logger.info(f"Using realm_id: {qbo_auth.realm_id}")
    
    # Process vendors in batches
    batch_size = 10
    batch_delay = 1.0  # seconds between batches
    
    with QboVendorClient(
        access_token=qbo_auth.access_token,
        realm_id=qbo_auth.realm_id,
    ) as client:
        for i, vendor in enumerate(vendors):
            success = update_vendor_in_qbo(client, vendor)
            
            if success:
                results["updated_in_qbo"] += 1
                vendor['updated'] = True
            else:
                results["failed"] += 1
                vendor['updated'] = False
            
            # Batch delay
            if (i + 1) % batch_size == 0 and i < len(vendors) - 1:
                time.sleep(batch_delay)
    
    logger.info(f"QBO updates complete. Updated: {results['updated_in_qbo']}, Failed: {results['failed']}")
    
    # Update local qbo.Vendor records directly
    if results["updated_in_qbo"] > 0:
        logger.info("Updating local qbo.Vendor records...")
        
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                for v in vendors:
                    if v.get('updated'):
                        cursor.execute("""
                            UPDATE qbo.Vendor 
                            SET Vendor1099 = 1, ModifiedDatetime = SYSUTCDATETIME()
                            WHERE QboId = ?
                        """, (v['qbo_id'],))
                        results["synced_to_local"] += 1
                conn.commit()
            
            logger.info(f"Updated {results['synced_to_local']} local qbo.Vendor records")
            
        except Exception as e:
            error = f"Error updating local records: {e}"
            logger.error(error)
            results["errors"].append(error)
    
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fix QBO vendors with TaxIdentifier but Vendor1099=False"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run mode)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of vendors to process (for testing)"
    )
    args = parser.parse_args()
    
    dry_run = not args.apply
    
    if not dry_run:
        print("=" * 70)
        print("WARNING: This will modify vendor records in QuickBooks Online!")
        print("=" * 70)
        confirm = input("Type 'yes' to continue: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)
    
    result = fix_qbo_vendor_1099(dry_run=dry_run, limit=args.limit)
    
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Total vendors with TaxIdentifier but Vendor1099=False: {result['total']}")
    
    if not dry_run:
        print(f"Updated in QBO: {result['updated_in_qbo']}")
        print(f"Failed: {result['failed']}")
        print(f"Synced to local database: {result['synced_to_local']}")
    
    if result["errors"]:
        print("\nErrors:")
        for error in result["errors"]:
            print(f"  - {error}")
