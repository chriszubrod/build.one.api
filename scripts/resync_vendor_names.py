# Python Standard Library Imports
import logging
import os
import sys
import time

# Add project root to path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

# Load environment variables before any other imports
from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

# Third-party Imports

# Local Imports
from shared.database import with_retry
from integrations.intuit.qbo.vendor.connector.vendor.persistence.repo import VendorVendorRepository
from integrations.intuit.qbo.vendor.persistence.repo import QboVendorRepository
from services.vendor.persistence.repo import VendorRepository

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
BATCH_SIZE = 10
BATCH_DELAY = 0.5
MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 2.0


def resync_vendor_names(dry_run: bool = True) -> dict:
    """
    Re-sync all mapped vendors with updated field mapping logic.
    
    New mapping:
    - dbo.Vendor.Name <- qbo.Vendor.DisplayName (direct, no fallback)
    - dbo.Vendor.Abbreviation <- None (not mapped)
    
    Args:
        dry_run: If True, only report changes without applying them
    
    Returns:
        dict: Results including counts and details
    """
    logger.info(f"Starting vendor name re-sync (dry_run={dry_run})")
    
    # Initialize repositories
    mapping_repo = VendorVendorRepository()
    qbo_vendor_repo = QboVendorRepository()
    vendor_repo = VendorRepository()
    
    # Get all QBO vendors and find those with mappings
    qbo_vendors = qbo_vendor_repo.read_all()
    logger.info(f"Found {len(qbo_vendors)} QBO vendors")
    
    # Build list of mappings by checking each QBO vendor
    mappings = []
    for qbo_vendor in qbo_vendors:
        mapping = mapping_repo.read_by_qbo_vendor_id(qbo_vendor.id)
        if mapping:
            mappings.append((mapping, qbo_vendor))
    
    logger.info(f"Found {len(mappings)} vendor mappings")
    
    results = {
        "total_mappings": len(mappings),
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "changes": [],
    }
    
    for i, (mapping, qbo_vendor) in enumerate(mappings):
        try:
            # Get Vendor
            vendor = vendor_repo.read_by_id(mapping.vendor_id)
            if not vendor:
                logger.warning(f"Vendor {mapping.vendor_id} not found for mapping {mapping.id}")
                results["errors"] += 1
                continue
            
            # Calculate new values
            new_name = qbo_vendor.display_name
            new_abbreviation = None
            
            # Check if changes are needed
            name_changed = vendor.name != new_name
            abbrev_changed = vendor.abbreviation is not None
            
            if not name_changed and not abbrev_changed:
                logger.debug(f"Vendor {vendor.id} already up to date")
                results["skipped"] += 1
                continue
            
            change_record = {
                "vendor_id": vendor.id,
                "qbo_vendor_id": qbo_vendor.id,
                "old_name": vendor.name,
                "new_name": new_name,
                "old_abbreviation": vendor.abbreviation,
                "new_abbreviation": new_abbreviation,
            }
            results["changes"].append(change_record)
            
            if dry_run:
                logger.info(
                    f"[DRY RUN] Would update Vendor {vendor.id}: "
                    f"name='{vendor.name}' -> '{new_name}', "
                    f"abbreviation='{vendor.abbreviation}' -> None"
                )
            else:
                # Apply changes
                vendor.name = new_name
                vendor.abbreviation = new_abbreviation
                
                updated_vendor = with_retry(
                    vendor_repo.update_by_id,
                    vendor,
                    max_retries=MAX_RETRIES,
                    initial_delay=INITIAL_RETRY_DELAY,
                )
                
                if updated_vendor:
                    logger.info(f"Updated Vendor {vendor.id}: name='{new_name}'")
                    results["updated"] += 1
                else:
                    logger.error(f"Failed to update Vendor {vendor.id} (no result returned)")
                    results["errors"] += 1
            
            # Batch delay
            if (i + 1) % BATCH_SIZE == 0 and i + 1 < len(mappings):
                logger.debug(f"Processed {i + 1}/{len(mappings)} mappings, pausing...")
                time.sleep(BATCH_DELAY)
                
        except Exception as e:
            logger.error(f"Error processing mapping {mapping.id}: {e}")
            results["errors"] += 1
    
    # Summary
    if dry_run:
        logger.info(
            f"[DRY RUN] Complete. Would update {len(results['changes'])} vendors, "
            f"skip {results['skipped']}, errors: {results['errors']}"
        )
    else:
        logger.info(
            f"Re-sync complete. Updated: {results['updated']}, "
            f"skipped: {results['skipped']}, errors: {results['errors']}"
        )
    
    return results


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Re-sync vendor names from QBO DisplayName")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run mode)"
    )
    args = parser.parse_args()
    
    dry_run = not args.apply
    
    if not dry_run:
        print("=" * 60)
        print("WARNING: This will modify vendor records in the database!")
        print("=" * 60)
        confirm = input("Type 'yes' to continue: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)
    
    result = resync_vendor_names(dry_run=dry_run)
    
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Total mappings: {result['total_mappings']}")
    if dry_run:
        print(f"Would update: {len(result['changes'])}")
    else:
        print(f"Updated: {result['updated']}")
    print(f"Skipped (no changes): {result['skipped']}")
    print(f"Errors: {result['errors']}")
    
    if result['changes'] and dry_run:
        print("\nChanges to be applied:")
        for change in result['changes'][:10]:  # Show first 10
            print(f"  Vendor {change['vendor_id']}: '{change['old_name']}' -> '{change['new_name']}'")
        if len(result['changes']) > 10:
            print(f"  ... and {len(result['changes']) - 10} more")
