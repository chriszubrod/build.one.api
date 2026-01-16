# Python Standard Library Imports
import json
import logging
from typing import Optional

# Third-party Imports
import requests

# Local Imports
from integrations.ms.auth.business.service import MsAuthService

logger = logging.getLogger(__name__)

# Microsoft Graph API base URL
GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"


def _get_auth_headers() -> Optional[dict]:
    """
    Get authorization headers with a valid access token.
    Returns None if no valid token is available.
    """
    ms_auth_service = MsAuthService()
    ms_auth = ms_auth_service.ensure_valid_token()
    
    if not ms_auth or not ms_auth.access_token:
        logger.error("No valid MS access token available")
        return None
    
    return {
        "Authorization": f"Bearer {ms_auth.access_token}",
        "Content-Type": "application/json"
    }


def search_sites(query: str) -> dict:
    """
    Search for SharePoint sites using MS Graph API.
    
    Args:
        query: Search query string
    
    Returns:
        Dict with status_code, message, and sites list
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "sites": []
            }
        
        endpoint = f"{GRAPH_API_BASE}/sites?search={query}"
        
        logger.info(f"Searching SharePoint sites with query: {query}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            sites = data.get("value", [])
            
            formatted_sites = []
            for site in sites:
                formatted_sites.append({
                    "site_id": site.get("id"),
                    "display_name": site.get("displayName"),
                    "web_url": site.get("webUrl"),
                    "hostname": site.get("siteCollection", {}).get("hostname", "")
                })
            
            return {
                "message": f"Found {len(formatted_sites)} sites",
                "status_code": 200,
                "sites": formatted_sites
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "sites": []
            }
        else:
            logger.error(f"Graph API search sites failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "sites": []
            }
    except Exception as e:
        logger.exception("Error searching SharePoint sites")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "sites": []
        }


def get_my_drive() -> dict:
    """
    Get the current user's OneDrive.
    Uses the /me/drive endpoint (user-delegated auth required).
    
    Returns:
        Dict with status_code, message, and drive data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "drive": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/drive"
        
        logger.info("Getting current user's OneDrive")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            drive = resp.json()
            
            formatted_drive = {
                "drive_id": drive.get("id"),
                "name": drive.get("name"),
                "web_url": drive.get("webUrl"),
                "drive_type": drive.get("driveType"),
                "description": drive.get("description", ""),
                "owner": drive.get("owner", {}),
            }
            
            return {
                "message": "OneDrive retrieved successfully",
                "status_code": 200,
                "drive": formatted_drive
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "drive": None
            }
        else:
            logger.error(f"Graph API get my drive failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "drive": None
            }
    except Exception as e:
        logger.exception("Error getting user's OneDrive")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "drive": None
        }


def get_followed_sites() -> dict:
    """
    Get SharePoint sites that the current user follows.
    Uses the /me/followedSites endpoint (user-delegated auth required).
    
    Returns:
        Dict with status_code, message, and sites list
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "sites": []
            }
        
        endpoint = f"{GRAPH_API_BASE}/me/followedSites"
        
        logger.info("Getting followed SharePoint sites for current user")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            sites = data.get("value", [])
            
            formatted_sites = []
            for site in sites:
                formatted_sites.append({
                    "site_id": site.get("id"),
                    "display_name": site.get("displayName"),
                    "web_url": site.get("webUrl"),
                    "hostname": site.get("siteCollection", {}).get("hostname", "")
                })
            
            return {
                "message": f"Found {len(formatted_sites)} followed sites",
                "status_code": 200,
                "sites": formatted_sites
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "sites": []
            }
        else:
            logger.error(f"Graph API get followed sites failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "sites": []
            }
    except Exception as e:
        logger.exception("Error getting followed SharePoint sites")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "sites": []
        }


def get_site_by_id(site_id: str) -> dict:
    """
    Get a SharePoint site by its MS Graph ID.
    
    Args:
        site_id: The MS Graph site ID
    
    Returns:
        Dict with status_code, message, and site data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "site": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/sites/{site_id}"
        
        logger.info(f"Getting SharePoint site by ID: {site_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            site = resp.json()
            
            formatted_site = {
                "site_id": site.get("id"),
                "display_name": site.get("displayName"),
                "web_url": site.get("webUrl"),
                "hostname": site.get("siteCollection", {}).get("hostname", "")
            }
            
            return {
                "message": "Site retrieved successfully",
                "status_code": 200,
                "site": formatted_site
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "site": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Site not found: {site_id}",
                "status_code": 404,
                "site": None
            }
        else:
            logger.error(f"Graph API get site failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "site": None
            }
    except Exception as e:
        logger.exception("Error getting SharePoint site by ID")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "site": None
        }


def get_site_by_path(hostname: str, site_path: str) -> dict:
    """
    Get a SharePoint site by its hostname and path.
    
    Args:
        hostname: The SharePoint hostname (e.g., "contoso.sharepoint.com")
        site_path: The site path (e.g., "/sites/marketing")
    
    Returns:
        Dict with status_code, message, and site data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "site": None
            }
        
        # Ensure site_path starts with /
        if not site_path.startswith("/"):
            site_path = f"/{site_path}"
        
        endpoint = f"{GRAPH_API_BASE}/sites/{hostname}:{site_path}"
        
        logger.info(f"Getting SharePoint site by path: {hostname}{site_path}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            site = resp.json()
            
            formatted_site = {
                "site_id": site.get("id"),
                "display_name": site.get("displayName"),
                "web_url": site.get("webUrl"),
                "hostname": site.get("siteCollection", {}).get("hostname", hostname)
            }
            
            return {
                "message": "Site retrieved successfully",
                "status_code": 200,
                "site": formatted_site
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "site": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Site not found: {hostname}{site_path}",
                "status_code": 404,
                "site": None
            }
        else:
            logger.error(f"Graph API get site by path failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "site": None
            }
    except Exception as e:
        logger.exception("Error getting SharePoint site by path")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "site": None
        }


def list_site_drives(site_id: str) -> dict:
    """
    List all drives (document libraries) for a SharePoint site.
    
    Args:
        site_id: The MS Graph site ID
    
    Returns:
        Dict with status_code, message, and drives list
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "drives": []
            }
        
        endpoint = f"{GRAPH_API_BASE}/sites/{site_id}/drives"
        
        logger.info(f"Listing drives for site: {site_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            drives = data.get("value", [])
            
            formatted_drives = []
            for drive in drives:
                formatted_drives.append({
                    "drive_id": drive.get("id"),
                    "name": drive.get("name"),
                    "web_url": drive.get("webUrl"),
                    "drive_type": drive.get("driveType"),
                    "description": drive.get("description", ""),
                    "created_datetime": drive.get("createdDateTime"),
                })
            
            return {
                "message": f"Found {len(formatted_drives)} drives",
                "status_code": 200,
                "drives": formatted_drives
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "drives": []
            }
        elif resp.status_code == 404:
            return {
                "message": f"Site not found: {site_id}",
                "status_code": 404,
                "drives": []
            }
        else:
            logger.error(f"Graph API list drives failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "drives": []
            }
    except Exception as e:
        logger.exception("Error listing drives for site")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "drives": []
        }


def get_drive_by_id(drive_id: str) -> dict:
    """
    Get a drive by its MS Graph ID.
    
    Args:
        drive_id: The MS Graph drive ID
    
    Returns:
        Dict with status_code, message, and drive data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "drive": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}"
        
        logger.info(f"Getting drive by ID: {drive_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            drive = resp.json()
            
            formatted_drive = {
                "drive_id": drive.get("id"),
                "name": drive.get("name"),
                "web_url": drive.get("webUrl"),
                "drive_type": drive.get("driveType"),
                "description": drive.get("description", ""),
                "created_datetime": drive.get("createdDateTime"),
            }
            
            return {
                "message": "Drive retrieved successfully",
                "status_code": 200,
                "drive": formatted_drive
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "drive": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Drive not found: {drive_id}",
                "status_code": 404,
                "drive": None
            }
        else:
            logger.error(f"Graph API get drive failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "drive": None
            }
    except Exception as e:
        logger.exception("Error getting drive by ID")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "drive": None
        }


def _format_drive_item(item: dict) -> dict:
    """
    Format a DriveItem from MS Graph API response.
    Determines if it's a file or folder based on facets.
    """
    # Determine item type based on facets
    item_type = "folder" if "folder" in item else "file"
    
    # Get size and mime type for files
    size = item.get("size") if item_type == "file" else None
    mime_type = item.get("file", {}).get("mimeType") if item_type == "file" else None
    
    return {
        "item_id": item.get("id"),
        "name": item.get("name"),
        "item_type": item_type,
        "size": size,
        "mime_type": mime_type,
        "web_url": item.get("webUrl"),
        "parent_item_id": item.get("parentReference", {}).get("id"),
        "graph_created_datetime": item.get("createdDateTime"),
        "graph_modified_datetime": item.get("lastModifiedDateTime"),
        "child_count": item.get("folder", {}).get("childCount") if item_type == "folder" else None,
    }


def list_drive_root_children(drive_id: str) -> dict:
    """
    List items at the root of a drive.
    
    Args:
        drive_id: The MS Graph drive ID
    
    Returns:
        Dict with status_code, message, and items list
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "items": []
            }
        
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/root/children"
        
        logger.info(f"Listing root children for drive: {drive_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("value", [])
            
            formatted_items = [_format_drive_item(item) for item in items]
            
            return {
                "message": f"Found {len(formatted_items)} items",
                "status_code": 200,
                "items": formatted_items
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "items": []
            }
        elif resp.status_code == 404:
            return {
                "message": f"Drive not found: {drive_id}",
                "status_code": 404,
                "items": []
            }
        else:
            logger.error(f"Graph API list drive root children failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "items": []
            }
    except Exception as e:
        logger.exception("Error listing drive root children")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "items": []
        }


def list_drive_item_children(drive_id: str, item_id: str) -> dict:
    """
    List children of a specific folder in a drive.
    
    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID (folder)
    
    Returns:
        Dict with status_code, message, and items list
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "items": []
            }
        
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/children"
        
        logger.info(f"Listing children for item: {item_id} in drive: {drive_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("value", [])
            
            formatted_items = [_format_drive_item(item) for item in items]
            
            return {
                "message": f"Found {len(formatted_items)} items",
                "status_code": 200,
                "items": formatted_items
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "items": []
            }
        elif resp.status_code == 404:
            return {
                "message": f"Item not found: {item_id}",
                "status_code": 404,
                "items": []
            }
        else:
            logger.error(f"Graph API list drive item children failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "items": []
            }
    except Exception as e:
        logger.exception("Error listing drive item children")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "items": []
        }


def get_drive_item(drive_id: str, item_id: str) -> dict:
    """
    Get metadata for a specific item in a drive.
    
    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID
    
    Returns:
        Dict with status_code, message, and item data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "item": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}"
        
        logger.info(f"Getting item: {item_id} from drive: {drive_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            item = resp.json()
            formatted_item = _format_drive_item(item)
            
            return {
                "message": "Item retrieved successfully",
                "status_code": 200,
                "item": formatted_item
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "item": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Item not found: {item_id}",
                "status_code": 404,
                "item": None
            }
        else:
            logger.error(f"Graph API get drive item failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "item": None
            }
    except Exception as e:
        logger.exception("Error getting drive item")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "item": None
        }


def get_drive_item_content(drive_id: str, item_id: str) -> dict:
    """
    Get the content (download) of a file in a drive.
    
    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID (must be a file)
    
    Returns:
        Dict with status_code, message, content (bytes), and content_type
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "content": None,
                "content_type": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/content"
        
        logger.info(f"Downloading content for item: {item_id} from drive: {drive_id}")
        resp = requests.get(url=endpoint, headers=headers, allow_redirects=True)
        
        if resp.status_code == 200:
            return {
                "message": "Content retrieved successfully",
                "status_code": 200,
                "content": resp.content,
                "content_type": resp.headers.get("Content-Type", "application/octet-stream")
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "content": None,
                "content_type": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Item not found: {item_id}",
                "status_code": 404,
                "content": None,
                "content_type": None
            }
        else:
            logger.error(f"Graph API get drive item content failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "content": None,
                "content_type": None
            }
    except Exception as e:
        logger.exception("Error getting drive item content")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "content": None,
            "content_type": None
        }


def upload_small_file(drive_id: str, parent_item_id: str, filename: str, content: bytes, content_type: str = "application/octet-stream") -> dict:
    """
    Upload a small file (up to 4MB) to a folder in a drive.
    
    Args:
        drive_id: The MS Graph drive ID
        parent_item_id: The MS Graph item ID of the parent folder
        filename: The name for the new file
        content: The file content as bytes
        content_type: The MIME type of the file
    
    Returns:
        Dict with status_code, message, and created item data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "item": None
            }
        
        # Update content type for upload
        headers["Content-Type"] = content_type
        
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{parent_item_id}:/{filename}:/content"
        
        logger.info(f"Uploading file: {filename} to folder: {parent_item_id} in drive: {drive_id}")
        resp = requests.put(url=endpoint, headers=headers, data=content)
        
        if resp.status_code in [200, 201]:
            item = resp.json()
            formatted_item = _format_drive_item(item)
            
            return {
                "message": "File uploaded successfully",
                "status_code": resp.status_code,
                "item": formatted_item
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "item": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Parent folder not found: {parent_item_id}",
                "status_code": 404,
                "item": None
            }
        else:
            logger.error(f"Graph API upload file failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "item": None
            }
    except Exception as e:
        logger.exception("Error uploading file")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "item": None
        }


def create_folder(drive_id: str, parent_item_id: str, folder_name: str) -> dict:
    """
    Create a new folder in a drive.
    
    Args:
        drive_id: The MS Graph drive ID
        parent_item_id: The MS Graph item ID of the parent folder
        folder_name: The name for the new folder
    
    Returns:
        Dict with status_code, message, and created folder data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "item": None
            }
        
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{parent_item_id}/children"
        
        payload = {
            "name": folder_name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "fail"
        }
        
        logger.info(f"Creating folder: {folder_name} in parent: {parent_item_id} in drive: {drive_id}")
        resp = requests.post(url=endpoint, headers=headers, json=payload)
        
        if resp.status_code == 201:
            item = resp.json()
            formatted_item = _format_drive_item(item)
            
            return {
                "message": "Folder created successfully",
                "status_code": 201,
                "item": formatted_item
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "item": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Parent folder not found: {parent_item_id}",
                "status_code": 404,
                "item": None
            }
        elif resp.status_code == 409:
            return {
                "message": f"A folder with name '{folder_name}' already exists",
                "status_code": 409,
                "item": None
            }
        else:
            logger.error(f"Graph API create folder failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "item": None
            }
    except Exception as e:
        logger.exception("Error creating folder")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "item": None
        }
