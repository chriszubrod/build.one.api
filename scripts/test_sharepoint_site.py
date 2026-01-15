# Python Standard Library Imports
import argparse
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Local Imports
from integrations.ms.sharepoint.site.business.service import MsSiteService
from integrations.ms.sharepoint.external.client import search_sites, get_site_by_id

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Test SharePoint Site Module",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_sharepoint_site.py
  python scripts/test_sharepoint_site.py --query "contoso"
  python scripts/test_sharepoint_site.py -q "team site"
        """
    )
    parser.add_argument(
        "-q", "--query",
        type=str,
        default="",
        help="Search query for SharePoint sites (default: searches for all sites)"
    )
    parser.add_argument(
        "--skip-link",
        action="store_true",
        help="Skip linking a site (useful if you just want to search)"
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 80)
    print("TESTING SHAREPOINT SITE MODULE")
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
    
    service = MsSiteService()
    
    # Test 1: Search Sites
    print("\n🔍 Test 1: Search SharePoint Sites")
    print("-" * 40)
    
    search_query = args.query if args.query else "*"  # Use * to search all sites if no query provided
    print(f"   Searching for: '{search_query}'")
    if not args.query:
        print("   (Note: Use --query 'your-tenant-name' to search for specific sites)")
    
    try:
        result = service.search_sites(query=search_query)
        
        if result.get("status_code") == 200:
            sites = result.get("sites", [])
            print(f"✅ SUCCESS! Found {len(sites)} sites")
            if sites:
                for i, site in enumerate(sites[:5], 1):  # Show first 5
                    print(f"\n   [{i}] {site.get('display_name', 'Unknown')}")
                    site_id = site.get('site_id', 'N/A')
                    print(f"       Site ID: {site_id[:50] if len(site_id) > 50 else site_id}...")
                    print(f"       URL: {site.get('web_url', 'N/A')}")
                if len(sites) > 5:
                    print(f"\n   ... and {len(sites) - 5} more sites")
            else:
                print("   (No sites found matching the query)")
        elif result.get("status_code") == 401:
            print(f"❌ AUTHENTICATION FAILED: {result.get('message')}")
            print("   Please authenticate via: /api/v1/ms/auth/request")
        else:
            print(f"❌ FAILED: {result.get('message')}")
            print(f"   Status Code: {result.get('status_code')}")
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
        result = {"status_code": 500, "sites": []}
    
    # Test 2: List Linked Sites (should be empty initially)
    print("\n📋 Test 2: List Linked Sites")
    print("-" * 40)
    
    try:
        linked_sites = service.read_all()
        print(f"✅ Found {len(linked_sites)} linked sites in database")
        if linked_sites:
            for i, site in enumerate(linked_sites, 1):
                print(f"\n   [{i}] {site.display_name}")
                print(f"       Public ID: {site.public_id}")
                print(f"       Site ID: {site.site_id[:50] if site.site_id else 'N/A'}...")
        else:
            print("   (No linked sites found - this is expected if you haven't linked any yet)")
    except Exception as e:
        print(f"❌ ERROR: Could not read linked sites: {str(e)}")
        print("   Make sure the database table ms.Site exists (run ms.site.sql)")
        linked_sites = []
    
    # Test 3: Link a Site (if we found any in search)
    if args.skip_link:
        print("\n⚠️  Test 3: Link a Site - SKIPPED (--skip-link flag set)")
    elif result.get("status_code") == 200:
        sites = result.get("sites", [])
        if sites:
            print("\n🔗 Test 3: Link a Site")
            print("-" * 40)
            
            first_site = sites[0]
            site_id = first_site.get("site_id")
            site_name = first_site.get("display_name", "Unknown")
            
            print(f"   Attempting to link: {site_name}")
            print(f"   Site ID: {site_id[:50]}...")
            
            try:
                link_result = service.link_site(site_id=site_id)
                
                if link_result.get("status_code") in [200, 201]:
                    print(f"✅ SUCCESS! Site linked")
                    linked_site = link_result.get("site", {})
                    print(f"   Public ID: {linked_site.get('public_id')}")
                    print(f"   Display Name: {linked_site.get('display_name')}")
                else:
                    print(f"❌ FAILED: {link_result.get('message')}")
                    print(f"   Status Code: {link_result.get('status_code')}")
            except Exception as e:
                print(f"❌ ERROR: {str(e)}")
        else:
            print("\n⚠️  Test 3: Link a Site - SKIPPED (no sites found to link)")
    else:
        print("\n⚠️  Test 3: Link a Site - SKIPPED (search failed)")
    
    # Test 4: List Linked Sites Again
    print("\n📋 Test 4: List Linked Sites (After Linking)")
    print("-" * 40)
    
    try:
        linked_sites = service.read_all()
        print(f"✅ Found {len(linked_sites)} linked sites")
        if linked_sites:
            for i, site in enumerate(linked_sites, 1):
                print(f"\n   [{i}] {site.display_name}")
                print(f"       Public ID: {site.public_id}")
                print(f"       Created: {site.created_datetime}")
        else:
            print("   (No linked sites found)")
    except Exception as e:
        print(f"❌ ERROR: Could not read linked sites: {str(e)}")
    
    # Test 5: Get Site by ID (direct Graph API call)
    if result.get("status_code") == 200:
        sites = result.get("sites", [])
        if sites:
            print("\n🔍 Test 5: Get Site by ID (Direct Graph API)")
            print("-" * 40)
            
            first_site = sites[0]
            site_id = first_site.get("site_id")
            
            if site_id:
                print(f"   Fetching site ID: {site_id[:50]}...")
                
                try:
                    get_result = get_site_by_id(site_id)
                    
                    if get_result.get("status_code") == 200:
                        site = get_result.get("site", {})
                        print(f"✅ SUCCESS!")
                        print(f"   Display Name: {site.get('display_name')}")
                        print(f"   Web URL: {site.get('web_url')}")
                        print(f"   Hostname: {site.get('hostname')}")
                    else:
                        print(f"❌ FAILED: {get_result.get('message')}")
                        print(f"   Status Code: {get_result.get('status_code')}")
                except Exception as e:
                    print(f"❌ ERROR: {str(e)}")
            else:
                print("   ⚠️  SKIPPED (no site_id found)")
        else:
            print("\n⚠️  Test 5: Get Site by ID - SKIPPED (no sites found)")
    else:
        print("\n⚠️  Test 5: Get Site by ID - SKIPPED (search failed)")
    
    print("\n" + "=" * 80)
    print("TESTING COMPLETE")
    print("=" * 80)
    print("\n💡 Tips:")
    print("   - Use --query 'your-tenant-name' to search for specific sites")
    print("   - Use --skip-link to test search without linking sites")
    print("   - Make sure ms.Site table exists (run ms.site.sql)")
    print("   - Ensure MS Auth is configured and authenticated\n")
