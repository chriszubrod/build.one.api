# Python Standard Library Imports
import argparse
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Local Imports
from integrations.ms.sharepoint.drive.business.service import MsDriveService
from integrations.ms.sharepoint.site.business.service import MsSiteService
from integrations.ms.sharepoint.external.client import list_site_drives, get_drive_by_id

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Test SharePoint Drive Module",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_sharepoint_drive.py
  python scripts/test_sharepoint_drive.py --site-public-id "abc-123-def"
        """
    )
    parser.add_argument(
        "-s", "--site-public-id",
        type=str,
        default="",
        help="Public ID of a linked site to list drives from"
    )
    parser.add_argument(
        "--skip-link",
        action="store_true",
        help="Skip linking a drive (useful if you just want to list)"
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 80)
    print("TESTING SHAREPOINT DRIVE MODULE")
    print("=" * 80)
    
    # Check if MS Auth is configured
    try:
        from integrations.ms.auth.business.service import MsAuthService
        auth_service = MsAuthService()
        auth = auth_service.ensure_valid_token()
        
        if not auth:
            print("\n⚠️  WARNING: No valid MS Auth token found.")
            print("   Please authenticate first via: /api/v1/ms/auth/request")
            print("   Continuing anyway...\n")
    except Exception as e:
        print(f"\n⚠️  WARNING: Could not check MS Auth: {e}")
        print("   Continuing anyway...\n")
    
    site_service = MsSiteService()
    drive_service = MsDriveService()
    
    # Test 1: List Linked Sites (to find a site to work with)
    print("\n📋 Test 1: List Linked Sites")
    print("-" * 40)
    
    site_public_id = args.site_public_id
    site = None
    
    try:
        linked_sites = site_service.read_all()
        print(f"✅ Found {len(linked_sites)} linked sites")
        
        if linked_sites:
            for i, s in enumerate(linked_sites, 1):
                print(f"\n   [{i}] {s.display_name}")
                print(f"       Public ID: {s.public_id}")
                print(f"       Site ID: {s.site_id[:50] if s.site_id else 'N/A'}...")
            
            # Use first site if no site_public_id provided
            if not site_public_id:
                site = linked_sites[0]
                site_public_id = site.public_id
                print(f"\n   Using first site: {site.display_name}")
        else:
            print("   (No linked sites found)")
            print("   Please link a site first using the Site module")
            print("   Run: python scripts/test_sharepoint_site.py")
    except Exception as e:
        print(f"❌ ERROR: Could not read linked sites: {str(e)}")
        print("   Make sure the database tables exist")
        linked_sites = []
    
    if not site_public_id:
        print("\n⚠️  No site available to test drives. Exiting.")
        sys.exit(1)
    
    # Get site if not already fetched
    if not site:
        site = site_service.read_by_public_id(site_public_id)
        if not site:
            print(f"\n❌ Site with public_id '{site_public_id}' not found. Exiting.")
            sys.exit(1)
    
    # Test 2: List Available Drives from MS Graph
    print("\n🔍 Test 2: List Available Drives from MS Graph")
    print("-" * 40)
    print(f"   Site: {site.display_name}")
    print(f"   Site ID: {site.site_id[:50]}...")
    
    available_drives = []
    try:
        result = drive_service.list_available_drives(site_public_id=site_public_id)
        
        if result.get("status_code") == 200:
            available_drives = result.get("drives", [])
            print(f"✅ SUCCESS! Found {len(available_drives)} drives")
            if available_drives:
                for i, drive in enumerate(available_drives[:5], 1):
                    print(f"\n   [{i}] {drive.get('name', 'Unknown')}")
                    drive_id = drive.get('drive_id', 'N/A')
                    print(f"       Drive ID: {drive_id[:50] if len(drive_id) > 50 else drive_id}...")
                    print(f"       Type: {drive.get('drive_type', 'N/A')}")
                    print(f"       URL: {drive.get('web_url', 'N/A')}")
                if len(available_drives) > 5:
                    print(f"\n   ... and {len(available_drives) - 5} more drives")
            else:
                print("   (No drives found in this site)")
        elif result.get("status_code") == 401:
            print(f"❌ AUTHENTICATION FAILED: {result.get('message')}")
            print("   Please authenticate via: /api/v1/ms/auth/request")
        else:
            print(f"❌ FAILED: {result.get('message')}")
            print(f"   Status Code: {result.get('status_code')}")
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
    
    # Test 3: List Linked Drives (should be empty initially)
    print("\n📋 Test 3: List Linked Drives")
    print("-" * 40)
    
    try:
        linked_drives = drive_service.read_all()
        print(f"✅ Found {len(linked_drives)} linked drives in database")
        if linked_drives:
            for i, drive in enumerate(linked_drives, 1):
                print(f"\n   [{i}] {drive.name}")
                print(f"       Public ID: {drive.public_id}")
                print(f"       Drive ID: {drive.drive_id[:50] if drive.drive_id else 'N/A'}...")
        else:
            print("   (No linked drives found - this is expected if you haven't linked any yet)")
    except Exception as e:
        print(f"❌ ERROR: Could not read linked drives: {str(e)}")
        print("   Make sure the database table ms.Drive exists (run ms.drive.sql)")
        linked_drives = []
    
    # Test 4: Link a Drive (if we found any available)
    if args.skip_link:
        print("\n⚠️  Test 4: Link a Drive - SKIPPED (--skip-link flag set)")
    elif available_drives:
        print("\n🔗 Test 4: Link a Drive")
        print("-" * 40)
        
        first_drive = available_drives[0]
        drive_id = first_drive.get("drive_id")
        drive_name = first_drive.get("name", "Unknown")
        
        print(f"   Attempting to link: {drive_name}")
        print(f"   Drive ID: {drive_id[:50]}...")
        
        try:
            link_result = drive_service.link_drive(
                site_public_id=site_public_id,
                drive_id=drive_id
            )
            
            if link_result.get("status_code") in [200, 201]:
                print(f"✅ SUCCESS! Drive linked")
                linked_drive = link_result.get("drive", {})
                print(f"   Public ID: {linked_drive.get('public_id')}")
                print(f"   Name: {linked_drive.get('name')}")
            else:
                print(f"❌ FAILED: {link_result.get('message')}")
                print(f"   Status Code: {link_result.get('status_code')}")
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
    else:
        print("\n⚠️  Test 4: Link a Drive - SKIPPED (no drives found to link)")
    
    # Test 5: List Linked Drives for Site
    print("\n📋 Test 5: List Linked Drives for Site")
    print("-" * 40)
    
    try:
        result = drive_service.read_by_site_public_id(site_public_id=site_public_id)
        if result.get("status_code") == 200:
            drives = result.get("drives", [])
            print(f"✅ Found {len(drives)} linked drives for site")
            if drives:
                for i, drive in enumerate(drives, 1):
                    print(f"\n   [{i}] {drive.get('name')}")
                    print(f"       Public ID: {drive.get('public_id')}")
                    print(f"       Created: {drive.get('created_datetime')}")
            else:
                print("   (No linked drives found for this site)")
        else:
            print(f"❌ FAILED: {result.get('message')}")
    except Exception as e:
        print(f"❌ ERROR: Could not read linked drives: {str(e)}")
    
    # Test 6: Get Drive by ID (direct Graph API call)
    if available_drives:
        print("\n🔍 Test 6: Get Drive by ID (Direct Graph API)")
        print("-" * 40)
        
        first_drive = available_drives[0]
        drive_id = first_drive.get("drive_id")
        
        if drive_id:
            print(f"   Fetching drive ID: {drive_id[:50]}...")
            
            try:
                get_result = get_drive_by_id(drive_id)
                
                if get_result.get("status_code") == 200:
                    drive = get_result.get("drive", {})
                    print(f"✅ SUCCESS!")
                    print(f"   Name: {drive.get('name')}")
                    print(f"   Web URL: {drive.get('web_url')}")
                    print(f"   Drive Type: {drive.get('drive_type')}")
                else:
                    print(f"❌ FAILED: {get_result.get('message')}")
                    print(f"   Status Code: {get_result.get('status_code')}")
            except Exception as e:
                print(f"❌ ERROR: {str(e)}")
        else:
            print("   ⚠️  SKIPPED (no drive_id found)")
    else:
        print("\n⚠️  Test 6: Get Drive by ID - SKIPPED (no drives found)")
    
    print("\n" + "=" * 80)
    print("TESTING COMPLETE")
    print("=" * 80)
    print("\n💡 Tips:")
    print("   - Use --site-public-id 'uuid' to test with a specific site")
    print("   - Use --skip-link to test listing without linking drives")
    print("   - Make sure ms.Drive table exists (run ms.drive.sql)")
    print("   - Ensure MS Auth is configured and authenticated\n")
