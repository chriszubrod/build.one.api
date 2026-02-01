# Python Standard Library Imports
import logging
import os
import sys
import time

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

# Load environment variables
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

# Third-party Imports

# Local Imports
from shared.database import get_connection
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.vendor.external.client import QboVendorClient
from integrations.intuit.qbo.vendor.external.schemas import QboVendorUpdate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BATCH_SIZE = 10
BATCH_DELAY = 1.0  # Delay between batches to avoid rate limiting
MAX_RETRIES = 3


def get_vendors_missing_company_name():
    """
    Query qbo.Vendor records where CompanyName is missing but DisplayName exists.
    
    Returns:
        List of dicts with vendor info needed for update
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 
                Id, QboId, SyncToken, DisplayName, CompanyName
            FROM qbo.Vendor 
            WHERE (CompanyName IS NULL OR CompanyName = '')
              AND DisplayName IS NOT NULL 
              AND DisplayName != ''
            ORDER BY DisplayName
        """)
        rows = cursor.fetchall()
        
        vendors = []
        for row in rows:
            vendors.append({
                'id': row.Id,
                'qbo_id': row.QboId,
                'sync_token': row.SyncToken,
                'display_name': row.DisplayName,
                'company_name': row.CompanyName,
            })
        
        return vendors


def update_vendor_in_qbo(client: QboVendorClient, vendor: dict) -> bool:
    """
    Update a vendor in QuickBooks to set CompanyName = DisplayName.
    
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
            company_name=current_vendor.display_name,  # Set CompanyName = DisplayName
            sparse=True,  # Use sparse update to preserve other fields
        )
        
        # Update in QBO
        result = client.update_vendor(update)
        
        logger.info(f"Updated QBO vendor {vendor['qbo_id']}: CompanyName set to '{current_vendor.display_name}'")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update QBO vendor {vendor['qbo_id']}: {e}")
        return False


def fix_qbo_vendor_company_names(dry_run: bool = True, limit: int = None) -> dict:
    """
    Fix QBO vendors missing CompanyName by setting it to DisplayName.
    
    This updates vendors in QuickBooks Online, then syncs back to local database.
    
    Args:
        dry_run: If True, only report what would be changed
    
    Returns:
        dict: Results including counts
    """
    logger.info(f"Starting QBO vendor CompanyName fix (dry_run={dry_run})")
    
    # Get vendors missing CompanyName
    vendors = get_vendors_missing_company_name()
    logger.info(f"Found {len(vendors)} vendors missing CompanyName")
    
    # Apply limit if specified
    if limit and limit > 0:
        vendors = vendors[:limit]
        logger.info(f"Limiting to first {limit} vendor(s)")
    
    results = {
        "total_missing": len(vendors),
        "updated_in_qbo": 0,
        "failed": 0,
        "synced_to_local": 0,
        "errors": [],
    }
    
    if not vendors:
        logger.info("No vendors need updating")
        return results
    
    if dry_run:
        logger.info("[DRY RUN] Would update the following vendors:")
        for v in vendors[:20]:  # Show first 20
            logger.info(f"  QBO ID {v['qbo_id']}: DisplayName='{v['display_name']}' -> CompanyName='{v['display_name']}'")
        if len(vendors) > 20:
            logger.info(f"  ... and {len(vendors) - 20} more")
        return results
    
    # Get QBO auth token
    auth_service = QboAuthService()
    qbo_auth = auth_service.ensure_valid_token()
    
    if not qbo_auth:
        error = "Failed to get valid QBO auth token"
        logger.error(error)
        results["errors"].append(error)
        return results
    
    logger.info(f"Using realm_id: {qbo_auth.realm_id}")
    
    # Create QBO client
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
            
            # Batch delay to avoid rate limiting
            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(vendors):
                logger.debug(f"Processed {i + 1}/{len(vendors)} vendors, pausing...")
                time.sleep(BATCH_DELAY)
    
    logger.info(f"QBO updates complete. Updated: {results['updated_in_qbo']}, Failed: {results['failed']}")
    
    # Update local qbo.Vendor records directly (faster than full sync)
    if results["updated_in_qbo"] > 0:
        logger.info("Updating local qbo.Vendor records...")
        
        try:
            with get_connection() as conn:
                cursor = conn.cursor()
                for v in vendors:
                    if v.get('updated'):
                        cursor.execute("""
                            UPDATE qbo.Vendor 
                            SET CompanyName = ?, ModifiedDatetime = SYSUTCDATETIME()
                            WHERE QboId = ?
                        """, (v['display_name'], v['qbo_id']))
                        results["synced_to_local"] += 1
                conn.commit()
            
            logger.info(f"Updated {results['synced_to_local']} local qbo.Vendor records")
            
        except Exception as e:
            error = f"Error updating local records: {e}"
            logger.error(error)
            results["errors"].append(error)
    
    return results


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Fix QBO vendors missing CompanyName by setting it to DisplayName"
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
    
    result = fix_qbo_vendor_company_names(dry_run=dry_run, limit=args.limit)
    
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Total vendors missing CompanyName: {result['total_missing']}")
    if dry_run:
        print("(Dry run - no changes made)")
    else:
        print(f"Updated in QBO: {result['updated_in_qbo']}")
        print(f"Failed: {result['failed']}")
        print(f"Synced to local database: {result['synced_to_local']}")
        if result['errors']:
            print(f"Errors: {result['errors']}")
