#!/usr/bin/env python3
"""
Script to restore CompanyName and BillAddr for vendors affected by previous updates.
Uses sparse updates to only set the fields we want to restore.
"""

import argparse
import logging
import os
import sys
import time

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

from shared.database import get_connection
from integrations.intuit.qbo.auth.business.service import QboAuthService
from integrations.intuit.qbo.vendor.external.client import QboVendorClient
import httpx

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_vendors_to_restore() -> list[dict]:
    """
    Get vendors that need data restored.
    These are the 41 vendors from the 1099 script that lost CompanyName and BillAddr.
    """
    affected_names = [
        'Advantage Shutters', 'All Service Propane', 'Ann Sacks Tile & Stone Inc.',
        'B&E Irrigation & Landscaping, LLC', 'BugOut Termite and Pest Control, Inc.',
        'Castro Welding & Maintenance, LLC', 'Chim Chimney', 'Clearline Networks, LLC',
        'ConcreteCraft Nashville', 'Dale, Inc.', 'Daniels & Associates, Inc.',
        'Dusty Baker', 'Eco-Lyfe LLC', 'Emaus Restoration', 'Esteban Diaz Landscaping',
        'Francisco A Jimenez', 'Frank Stafford', 'Gladiator Roofing and More, LLC',
        'Glass Doctor of Nashville', 'Goat Turf, LLC', 'Gregory L Cashion',
        'Groove Construction', 'Haywood Burgess', 'Homestead Building, LLC',
        'Innovative Cutting, Inc.', 'Insulation Solutions of TN', 'J&J Mechanical Services, Inc.',
        'Jarrett Fire Protection, LLC', 'Omero Valenciano Aparicio', 'Palazzo Tile And Stone Inc',
        'Robi Decking', 'Rock Solutions', 'SEC of Nashville, Inc.',
        'Sneed Builders & Maintenance', 'Superglass Windshield Repair', 'Timber Build, Inc',
        'Timothy Hammonds', 'Tri-Stone & Tile, Inc.', 'TTL, Inc.',
        'W. M. Brooks Plumbing Co., Inc.', 'White Cap, LP'
    ]
    
    vendors = []
    with get_connection() as conn:
        cursor = conn.cursor()
        
        for name in affected_names:
            cursor.execute("""
                SELECT v.QboId, v.DisplayName, v.CompanyName, v.BillAddrId,
                       a.Line1, a.Line2, a.City, a.CountrySubDivisionCode, a.PostalCode
                FROM qbo.Vendor v
                LEFT JOIN qbo.PhysicalAddress a ON v.BillAddrId = a.Id
                WHERE v.DisplayName = ?
            """, (name,))
            row = cursor.fetchone()
            if row:
                vendors.append({
                    'qbo_id': str(row[0]),
                    'display_name': row[1],
                    'company_name': row[2],
                    'bill_addr_id': row[3],
                    'addr_line1': row[4],
                    'addr_line2': row[5],
                    'addr_city': row[6],
                    'addr_state': row[7],
                    'addr_postal': row[8],
                })
    
    return vendors


def restore_vendor_in_qbo(client: QboVendorClient, vendor: dict, access_token: str, realm_id: str) -> bool:
    """
    Restore CompanyName and BillAddr for a vendor using sparse update.
    """
    try:
        # Get current vendor from QBO for SyncToken
        current = client.get_vendor(vendor['qbo_id'])
        if not current:
            logger.error(f"Could not fetch vendor {vendor['qbo_id']}")
            return False
        
        # Build sparse update payload
        payload = {
            'Id': current.id,
            'SyncToken': current.sync_token,
            'sparse': True,
        }
        
        # Set CompanyName = DisplayName (this was our intended fix)
        payload['CompanyName'] = vendor['display_name']
        
        # Add BillAddr if we have address data locally
        if vendor['addr_line1']:
            payload['BillAddr'] = {
                'Line1': vendor['addr_line1'],
                'City': vendor['addr_city'],
                'CountrySubDivisionCode': vendor['addr_state'],
                'PostalCode': vendor['addr_postal'],
            }
            if vendor['addr_line2']:
                payload['BillAddr']['Line2'] = vendor['addr_line2']
        
        # Make the update
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        }
        url = f'https://quickbooks.api.intuit.com/v3/company/{realm_id}/vendor?minorversion=65'
        
        resp = httpx.post(url, headers=headers, json=payload, timeout=30.0)
        
        if resp.status_code == 200:
            result = resp.json()['Vendor']
            logger.info(f"Restored vendor {vendor['qbo_id']}: CompanyName={result.get('CompanyName')}, BillAddr={result.get('BillAddr') is not None}")
            return True
        else:
            logger.error(f"Failed to restore vendor {vendor['qbo_id']}: {resp.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error restoring vendor {vendor['qbo_id']}: {e}")
        return False


def restore_vendors(dry_run: bool = True, limit: int = None) -> dict:
    """
    Restore CompanyName and BillAddr for affected vendors.
    """
    results = {
        "total": 0,
        "restored": 0,
        "failed": 0,
        "errors": [],
    }
    
    logger.info(f"Starting vendor data restoration (dry_run={dry_run})")
    
    vendors = get_vendors_to_restore()
    logger.info(f"Found {len(vendors)} vendors to restore")
    
    if limit and limit > 0:
        vendors = vendors[:limit]
        logger.info(f"Limiting to first {limit} vendor(s)")
    
    results["total"] = len(vendors)
    
    if dry_run:
        logger.info("DRY RUN - No changes will be made")
        print("\nVendors that would be restored:")
        print(f"{'QBO ID':<10} {'DisplayName':<35} {'CompanyName':<30} {'Has Addr'}")
        print("-" * 85)
        for v in vendors:
            has_addr = 'Yes' if v['addr_line1'] else 'No'
            company = (v['company_name'] or '')[:28]
            print(f"{v['qbo_id']:<10} {v['display_name'][:33]:<35} {company:<30} {has_addr}")
        return results
    
    # Get QBO auth
    auth_service = QboAuthService()
    qbo_auth = auth_service.ensure_valid_token()
    
    if not qbo_auth:
        logger.error("Failed to get QBO authentication")
        return results
    
    logger.info(f"Using realm_id: {qbo_auth.realm_id}")
    
    batch_size = 10
    batch_delay = 1.0
    
    with QboVendorClient(
        access_token=qbo_auth.access_token,
        realm_id=qbo_auth.realm_id,
    ) as client:
        for i, vendor in enumerate(vendors):
            success = restore_vendor_in_qbo(
                client, vendor, 
                qbo_auth.access_token, 
                qbo_auth.realm_id
            )
            
            if success:
                results["restored"] += 1
            else:
                results["failed"] += 1
            
            if (i + 1) % batch_size == 0 and i < len(vendors) - 1:
                time.sleep(batch_delay)
    
    logger.info(f"Restoration complete. Restored: {results['restored']}, Failed: {results['failed']}")
    
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Restore CompanyName and BillAddr for affected vendors"
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
        help="Limit number of vendors to process"
    )
    args = parser.parse_args()
    
    dry_run = not args.apply
    
    if not dry_run:
        print("=" * 70)
        print("This will restore CompanyName and BillAddr in QuickBooks Online")
        print("=" * 70)
        confirm = input("Type 'yes' to continue: ")
        if confirm.lower() != 'yes':
            print("Aborted.")
            sys.exit(0)
    
    result = restore_vendors(dry_run=dry_run, limit=args.limit)
    
    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Total vendors: {result['total']}")
    
    if not dry_run:
        print(f"Restored: {result['restored']}")
        print(f"Failed: {result['failed']}")
