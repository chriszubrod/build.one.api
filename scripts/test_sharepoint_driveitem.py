# Python Standard Library Imports
import argparse
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Local Imports
from integrations.ms.sharepoint.driveitem.business.service import MsDriveItemService
from integrations.ms.sharepoint.drive.business.service import MsDriveService
from integrations.ms.sharepoint.site.business.service import MsSiteService
from integrations.ms.sharepoint.external.client import (
    list_drive_root_children,
    list_drive_item_children,
    get_drive_item,
)

if __name__ == "__main__":
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Test SharePoint DriveItem Module",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_sharepoint_driveitem.py
  python scripts/test_sharepoint_driveitem.py --drive-public-id "abc-123-def"
        """
    )
    parser.add_argument(
        "-d", "--drive-public-id",
        type=str,
        default="",
        help="Public ID of a linked drive to browse items from"
    )
    parser.add_argument(
        "--skip-link",
        action="store_true",
        help="Skip linking an item (useful if you just want to browse)"
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 80)
    print("TESTING SHAREPOINT DRIVEITEM MODULE")
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
    driveitem_service = MsDriveItemService()
    
    # Test 1: List Linked Drives (to find a drive to work with)
    print("\n📋 Test 1: List Linked Drives")
    print("-" * 40)
    
    drive_public_id = args.drive_public_id
    drive = None
    
    try:
        linked_drives = drive_service.read_all()
        print(f"✅ Found {len(linked_drives)} linked drives")
        
        if linked_drives:
            for i, d in enumerate(linked_drives, 1):
                print(f"\n   [{i}] {d.name}")
                print(f"       Public ID: {d.public_id}")
                print(f"       Drive ID: {d.drive_id[:50] if d.drive_id else 'N/A'}...")
            
            # Use first drive if no drive_public_id provided
            if not drive_public_id:
                drive = linked_drives[0]
                drive_public_id = drive.public_id
                print(f"\n   Using first drive: {drive.name}")
        else:
            print("   (No linked drives found)")
            print("   Please link a drive first using the Drive module")
            print("   Run: python scripts/test_sharepoint_drive.py")
    except Exception as e:
        print(f"❌ ERROR: Could not read linked drives: {str(e)}")
        print("   Make sure the database tables exist")
        linked_drives = []
    
    if not drive_public_id:
        print("\n⚠️  No drive available to test DriveItems. Exiting.")
        sys.exit(1)
    
    # Get drive if not already fetched
    if not drive:
        drive = drive_service.read_by_public_id(drive_public_id)
        if not drive:
            print(f"\n❌ Drive with public_id '{drive_public_id}' not found. Exiting.")
            sys.exit(1)
    
    # Test 2: Browse Drive Root from MS Graph
    print("\n🔍 Test 2: Browse Drive Root from MS Graph")
    print("-" * 40)
    print(f"   Drive: {drive.name}")
    print(f"   Drive ID: {drive.drive_id[:50]}...")
    
    root_items = []
    try:
        result = driveitem_service.browse_drive_root(drive_public_id=drive_public_id)
        
        if result.get("status_code") == 200:
            root_items = result.get("items", [])
            print(f"✅ SUCCESS! Found {len(root_items)} items at root")
            if root_items:
                for i, item in enumerate(root_items[:10], 1):
                    item_type = item.get('item_type', 'unknown')
                    icon = "📁" if item_type == "folder" else "📄"
                    size_str = ""
                    if item_type == "file" and item.get("size"):
                        size_kb = item.get("size", 0) / 1024
                        size_str = f" ({size_kb:.1f} KB)"
                    print(f"\n   [{i}] {icon} {item.get('name', 'Unknown')}{size_str}")
                    item_id = item.get('item_id', 'N/A')
                    print(f"       Item ID: {item_id[:50] if len(item_id) > 50 else item_id}...")
                    print(f"       Type: {item_type}")
                if len(root_items) > 10:
                    print(f"\n   ... and {len(root_items) - 10} more items")
            else:
                print("   (No items found at root)")
        elif result.get("status_code") == 401:
            print(f"❌ AUTHENTICATION FAILED: {result.get('message')}")
            print("   Please authenticate via: /api/v1/ms/auth/request")
        else:
            print(f"❌ FAILED: {result.get('message')}")
            print(f"   Status Code: {result.get('status_code')}")
    except Exception as e:
        print(f"❌ ERROR: {str(e)}")
    
    # Test 3: Browse a Folder (if we found any folders)
    folders = [item for item in root_items if item.get("item_type") == "folder"]
    if folders:
        print("\n📂 Test 3: Browse a Folder")
        print("-" * 40)
        
        first_folder = folders[0]
        folder_id = first_folder.get("item_id")
        folder_name = first_folder.get("name", "Unknown")
        
        print(f"   Browsing folder: {folder_name}")
        print(f"   Item ID: {folder_id[:50]}...")
        
        try:
            result = driveitem_service.browse_folder(
                drive_public_id=drive_public_id,
                item_id=folder_id
            )
            
            if result.get("status_code") == 200:
                folder_items = result.get("items", [])
                print(f"✅ SUCCESS! Found {len(folder_items)} items in folder")
                if folder_items:
                    for i, item in enumerate(folder_items[:5], 1):
                        item_type = item.get('item_type', 'unknown')
                        icon = "📁" if item_type == "folder" else "📄"
                        print(f"\n   [{i}] {icon} {item.get('name', 'Unknown')}")
                    if len(folder_items) > 5:
                        print(f"\n   ... and {len(folder_items) - 5} more items")
                else:
                    print("   (Folder is empty)")
            else:
                print(f"❌ FAILED: {result.get('message')}")
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
    else:
        print("\n⚠️  Test 3: Browse a Folder - SKIPPED (no folders found at root)")
    
    # Test 4: Get Item Metadata
    if root_items:
        print("\n📋 Test 4: Get Item Metadata")
        print("-" * 40)
        
        first_item = root_items[0]
        item_id = first_item.get("item_id")
        item_name = first_item.get("name", "Unknown")
        
        print(f"   Getting metadata for: {item_name}")
        
        try:
            result = driveitem_service.get_item_metadata(
                drive_public_id=drive_public_id,
                item_id=item_id
            )
            
            if result.get("status_code") == 200:
                item = result.get("item", {})
                print(f"✅ SUCCESS!")
                print(f"   Name: {item.get('name')}")
                print(f"   Type: {item.get('item_type')}")
                print(f"   Web URL: {item.get('web_url')}")
                print(f"   Created: {item.get('graph_created_datetime')}")
                print(f"   Modified: {item.get('graph_modified_datetime')}")
            else:
                print(f"❌ FAILED: {result.get('message')}")
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
    else:
        print("\n⚠️  Test 4: Get Item Metadata - SKIPPED (no items found)")
    
    # Test 5: List Linked Items (should be empty initially)
    print("\n📋 Test 5: List Linked DriveItems")
    print("-" * 40)
    
    try:
        linked_items = driveitem_service.read_all()
        print(f"✅ Found {len(linked_items)} linked items in database")
        if linked_items:
            for i, item in enumerate(linked_items[:5], 1):
                item_type = item.item_type or "unknown"
                icon = "📁" if item_type == "folder" else "📄"
                print(f"\n   [{i}] {icon} {item.name}")
                print(f"       Public ID: {item.public_id}")
                print(f"       Item ID: {item.item_id[:50] if item.item_id else 'N/A'}...")
            if len(linked_items) > 5:
                print(f"\n   ... and {len(linked_items) - 5} more items")
        else:
            print("   (No linked items found - this is expected if you haven't linked any yet)")
    except Exception as e:
        print(f"❌ ERROR: Could not read linked items: {str(e)}")
        print("   Make sure the database table ms.DriveItem exists (run ms.driveitem.sql)")
        linked_items = []
    
    # Test 6: Link an Item (if we found any items and not skipping)
    if args.skip_link:
        print("\n⚠️  Test 6: Link an Item - SKIPPED (--skip-link flag set)")
    elif root_items:
        print("\n🔗 Test 6: Link an Item")
        print("-" * 40)
        
        first_item = root_items[0]
        item_id = first_item.get("item_id")
        item_name = first_item.get("name", "Unknown")
        item_type = first_item.get("item_type", "unknown")
        
        icon = "📁" if item_type == "folder" else "📄"
        print(f"   Attempting to link: {icon} {item_name}")
        print(f"   Item ID: {item_id[:50]}...")
        
        try:
            link_result = driveitem_service.link_item(
                drive_public_id=drive_public_id,
                item_id=item_id
            )
            
            if link_result.get("status_code") in [200, 201]:
                print(f"✅ SUCCESS! Item linked")
                linked_item = link_result.get("item", {})
                print(f"   Public ID: {linked_item.get('public_id')}")
                print(f"   Name: {linked_item.get('name')}")
            else:
                print(f"❌ FAILED: {link_result.get('message')}")
                print(f"   Status Code: {link_result.get('status_code')}")
        except Exception as e:
            print(f"❌ ERROR: {str(e)}")
    else:
        print("\n⚠️  Test 6: Link an Item - SKIPPED (no items found to link)")
    
    # Test 7: List Linked Items for Drive
    print("\n📋 Test 7: List Linked Items for Drive")
    print("-" * 40)
    
    try:
        result = driveitem_service.read_by_drive_public_id(drive_public_id=drive_public_id)
        if result.get("status_code") == 200:
            items = result.get("items", [])
            print(f"✅ Found {len(items)} linked items for drive")
            if items:
                for i, item in enumerate(items[:5], 1):
                    item_type = item.get("item_type", "unknown")
                    icon = "📁" if item_type == "folder" else "📄"
                    print(f"\n   [{i}] {icon} {item.get('name')}")
                    print(f"       Public ID: {item.get('public_id')}")
                    print(f"       Created: {item.get('created_datetime')}")
            else:
                print("   (No linked items found for this drive)")
        else:
            print(f"❌ FAILED: {result.get('message')}")
    except Exception as e:
        print(f"❌ ERROR: Could not read linked items: {str(e)}")
    
    print("\n" + "=" * 80)
    print("TESTING COMPLETE")
    print("=" * 80)
    print("\n💡 Tips:")
    print("   - Use --drive-public-id 'uuid' to test with a specific drive")
    print("   - Use --skip-link to test browsing without linking items")
    print("   - Make sure ms.DriveItem table exists (run ms.driveitem.sql)")
    print("   - Ensure MS Auth is configured and authenticated\n")
