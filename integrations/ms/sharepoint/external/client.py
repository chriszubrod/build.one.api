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


def upload_large_file(drive_id: str, parent_item_id: str, filename: str, content: bytes, content_type: str = "application/octet-stream") -> dict:
    """
    Upload a file larger than 4MB using the MS Graph upload session (resumable upload) API.
    Uploads in 5MB chunks.
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {"message": "No valid MS access token available.", "status_code": 401, "item": None}

        # 1. Create upload session
        session_url = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{parent_item_id}:/{filename}:/createUploadSession"
        session_resp = requests.post(
            session_url,
            headers={**headers, "Content-Type": "application/json"},
            json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": filename}},
        )
        if session_resp.status_code not in (200, 201):
            logger.error(f"Failed to create upload session: {session_resp.text}")
            return {"message": f"Failed to create upload session: {session_resp.text}", "status_code": session_resp.status_code, "item": None}

        upload_url = session_resp.json().get("uploadUrl")
        if not upload_url:
            return {"message": "Upload session did not return an uploadUrl", "status_code": 500, "item": None}

        # 2. Upload in 5MB chunks
        chunk_size = 5 * 1024 * 1024
        total_size = len(content)
        offset = 0
        last_response = None

        while offset < total_size:
            chunk = content[offset: offset + chunk_size]
            chunk_len = len(chunk)
            chunk_headers = {
                "Content-Length": str(chunk_len),
                "Content-Range": f"bytes {offset}-{offset + chunk_len - 1}/{total_size}",
                "Content-Type": content_type,
            }
            chunk_resp = requests.put(upload_url, headers=chunk_headers, data=chunk)
            if chunk_resp.status_code not in (200, 201, 202):
                logger.error(f"Chunk upload failed at offset {offset}: {chunk_resp.text}")
                return {"message": f"Chunk upload failed: {chunk_resp.text}", "status_code": chunk_resp.status_code, "item": None}
            last_response = chunk_resp
            offset += chunk_len

        # Final response (200/201) contains the item
        if last_response and last_response.status_code in (200, 201):
            return {"message": "File uploaded successfully", "status_code": last_response.status_code, "item": _format_drive_item(last_response.json())}

        return {"message": "Upload completed", "status_code": 200, "item": None}

    except Exception as e:
        logger.exception("Error uploading large file")
        return {"message": f"An error occurred: {str(e)}", "status_code": 500, "item": None}


def move_item(drive_id: str, item_id: str, new_parent_id: str, new_name: str = None) -> dict:
    """
    Move a file or folder to a different parent folder within the same drive.

    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID of the item to move
        new_parent_id: The MS Graph item ID of the destination folder
        new_name: Optional new name for the item after moving

    Returns:
        Dict with status_code, message, and moved item data
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

        body = {
            "parentReference": {"id": new_parent_id}
        }
        if new_name:
            body["name"] = new_name

        logger.info(f"Moving item {item_id} to parent {new_parent_id} in drive {drive_id}")
        resp = requests.patch(url=endpoint, headers=headers, json=body)

        if resp.status_code == 200:
            item = resp.json()
            formatted_item = _format_drive_item(item)
            return {
                "message": "Item moved successfully",
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
                "message": f"Item or destination folder not found",
                "status_code": 404,
                "item": None
            }
        else:
            logger.error(f"Graph API move item failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "item": None
            }
    except Exception as e:
        logger.exception("Error moving item")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "item": None
        }


def delete_item(drive_id: str, item_id: str) -> dict:
    """
    Delete a file or folder from a drive.

    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID of the item to delete

    Returns:
        Dict with status_code and message
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
            }

        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}"

        logger.info(f"Deleting item {item_id} from drive {drive_id}")
        resp = requests.delete(url=endpoint, headers=headers)

        if resp.status_code == 204:
            return {
                "message": "Item deleted successfully",
                "status_code": 204,
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
            }
        elif resp.status_code == 404:
            return {
                "message": f"Item not found: {item_id}",
                "status_code": 404,
            }
        else:
            logger.error(f"Graph API delete item failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
            }
    except Exception as e:
        logger.exception("Error deleting item")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
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
            # Folder already exists — fetch it by path so caller has item_id
            path_resp = requests.get(
                f"{GRAPH_API_BASE}/drives/{drive_id}/items/{parent_item_id}:/{folder_name}",
                headers=headers,
            )
            if path_resp.status_code == 200:
                return {
                    "message": f"Folder '{folder_name}' already exists",
                    "status_code": 200,
                    "item": _format_drive_item(path_resp.json()),
                }
            return {
                "message": f"A folder with name '{folder_name}' already exists",
                "status_code": 409,
                "item": None,
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


# =============================================================================
# Excel Workbook API Functions
# =============================================================================


def get_excel_worksheets(drive_id: str, item_id: str) -> dict:
    """
    Get list of worksheets in an Excel workbook.
    
    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID of the Excel workbook (.xlsx file)
    
    Returns:
        Dict with status_code, message, and worksheets list
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "worksheets": []
            }
        
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets"
        
        logger.info(f"Getting worksheets for Excel workbook: {item_id} in drive: {drive_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            data = resp.json()
            worksheets = data.get("value", [])
            
            formatted_worksheets = []
            for ws in worksheets:
                formatted_worksheets.append({
                    "id": ws.get("id"),
                    "name": ws.get("name"),
                    "position": ws.get("position"),
                    "visibility": ws.get("visibility")
                })
            
            return {
                "message": f"Found {len(formatted_worksheets)} worksheets",
                "status_code": 200,
                "worksheets": formatted_worksheets
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "worksheets": []
            }
        elif resp.status_code == 404:
            return {
                "message": f"Workbook not found: {item_id}",
                "status_code": 404,
                "worksheets": []
            }
        else:
            logger.error(f"Graph API get excel worksheets failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "worksheets": []
            }
    except Exception as e:
        logger.exception("Error getting excel worksheets")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "worksheets": []
        }


def get_excel_worksheet(drive_id: str, item_id: str, worksheet_name: str) -> dict:
    """
    Get a specific worksheet by name from an Excel workbook.
    
    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID of the Excel workbook
        worksheet_name: The name of the worksheet
    
    Returns:
        Dict with status_code, message, and worksheet data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "worksheet": None
            }
        
        # URL encode worksheet name
        import urllib.parse
        encoded_name = urllib.parse.quote(worksheet_name)
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets/{encoded_name}"
        
        logger.info(f"Getting worksheet '{worksheet_name}' from workbook: {item_id} in drive: {drive_id}")
        resp = requests.get(url=endpoint, headers=headers)
        
        if resp.status_code == 200:
            worksheet = resp.json()
            return {
                "message": "Worksheet retrieved successfully",
                "status_code": 200,
                "worksheet": {
                    "id": worksheet.get("id"),
                    "name": worksheet.get("name"),
                    "position": worksheet.get("position"),
                    "visibility": worksheet.get("visibility")
                }
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "worksheet": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Worksheet '{worksheet_name}' not found in workbook",
                "status_code": 404,
                "worksheet": None
            }
        else:
            logger.error(f"Graph API get excel worksheet failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "worksheet": None
            }
    except Exception as e:
        logger.exception("Error getting excel worksheet")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "worksheet": None
        }


def update_excel_range(drive_id: str, item_id: str, worksheet_name: str, range_address: str, values: list, max_retries: int = 3) -> dict:
    """
    Update a range of cells in an Excel worksheet.
    Includes retry logic with exponential backoff for transient Graph API errors.
    
    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID of the Excel workbook
        worksheet_name: The name of the worksheet
        range_address: Excel range address (e.g., "A1:D4")
        values: 2D array of values [[row1], [row2], ...]
        max_retries: Maximum number of retry attempts for transient errors (default 3)
    
    Returns:
        Dict with status_code, message, and updated range data
    """
    import time
    import urllib.parse
    
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "range": None
            }
        
        # URL encode worksheet name and range address
        encoded_name = urllib.parse.quote(worksheet_name)
        encoded_range = urllib.parse.quote(range_address)
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets/{encoded_name}/range(address='{encoded_range}')"
        
        payload = {
            "values": values
        }
        
        logger.info(f"Updating range '{range_address}' in worksheet '{worksheet_name}' of workbook: {item_id}")
        
        # Retry loop for transient errors
        last_error = None
        for attempt in range(max_retries):
            resp = requests.patch(url=endpoint, headers=headers, json=payload)
            
            if resp.status_code == 200:
                range_data = resp.json()
                return {
                    "message": "Range updated successfully",
                    "status_code": 200,
                    "range": {
                        "address": range_data.get("address"),
                        "addressLocal": range_data.get("addressLocal"),
                        "cellCount": range_data.get("cellCount"),
                        "columnCount": range_data.get("columnCount"),
                        "rowCount": range_data.get("rowCount"),
                        "values": range_data.get("values")
                    }
                }
            elif resp.status_code == 401:
                return {
                    "message": "Access token expired or invalid. Try refreshing the token.",
                    "status_code": 401,
                    "range": None
                }
            elif resp.status_code == 404:
                return {
                    "message": f"Worksheet '{worksheet_name}' or range '{range_address}' not found",
                    "status_code": 404,
                    "range": None
                }
            else:
                # Check if this is a retryable error
                is_retryable = (
                    resp.status_code >= 500 or 
                    "GeneralException" in resp.text or
                    "ServiceUnavailable" in resp.text or
                    "TooManyRequests" in resp.text
                )
                
                if is_retryable and attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 1  # Exponential backoff: 2s, 3s, 5s
                    logger.warning(f"Graph API update failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {resp.text[:200]}")
                    time.sleep(wait_time)
                    last_error = resp.text
                    continue
                else:
                    logger.error(f"Graph API update excel range failed after {attempt + 1} attempts: {resp.text}")
                    return {
                        "message": f"Graph API call failed: {resp.text}",
                        "status_code": resp.status_code,
                        "range": None
                    }
        
        # All retries exhausted
        logger.error(f"Graph API update excel range failed after {max_retries} retries")
        return {
            "message": f"Graph API call failed after {max_retries} retries: {last_error}",
            "status_code": 500,
            "range": None
        }
        
    except Exception as e:
        logger.exception("Error updating excel range")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "range": None
        }


def append_excel_rows(drive_id: str, item_id: str, worksheet_name: str, values: list) -> dict:
    """
    Append rows to an Excel worksheet. Appends to the first empty row after existing data.
    
    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID of the Excel workbook
        worksheet_name: The name of the worksheet
        values: 2D array of values to append [[row1], [row2], ...]
    
    Returns:
        Dict with status_code, message, and appended range data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "range": None
            }
        
        # URL encode worksheet name
        import urllib.parse
        encoded_name = urllib.parse.quote(worksheet_name)
        # Use usedRange to find the end, then append after it
        # First, get the used range to find where to append
        get_range_endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets/{encoded_name}/usedRange"
        
        logger.info(f"Getting used range for worksheet '{worksheet_name}' to determine append location")
        get_resp = requests.get(url=get_range_endpoint, headers=headers)

        if get_resp.status_code != 200:
            error_msg = f"Failed to read usedRange (status {get_resp.status_code}). Aborting append to prevent data loss."
            logger.error(error_msg)
            return {
                "message": error_msg,
                "status_code": get_resp.status_code,
                "range": None
            }

        used_range = get_resp.json()
        address = used_range.get("address", "")
        start_row = None
        if "!" in address:
            range_part = address.split("!")[1]
            if ":" in range_part:
                end_cell = range_part.split(":")[1]
                import re
                match = re.search(r'(\d+)$', end_cell)
                if match:
                    start_row = int(match.group(1)) + 1

        if start_row is None:
            error_msg = f"Could not determine last row from usedRange address '{address}'. Aborting append to prevent data loss."
            logger.error(error_msg)
            return {
                "message": error_msg,
                "status_code": 500,
                "range": None
            }
        
        # Now append the data starting at the calculated row.
        # Always pad each row to exactly 26 columns (A:Z) so the range
        # covers the full spreadsheet width regardless of how many values
        # the caller provides.
        num_rows = len(values)
        padded_values = [
            (row + [""] * 26)[:26] for row in values
        ]
        range_address = f"A{start_row}:Z{start_row + num_rows - 1}"
        
        # Use update_excel_range to append
        return update_excel_range(drive_id, item_id, worksheet_name, range_address, padded_values)
        
    except Exception as e:
        logger.exception("Error appending excel rows")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "range": None
        }


def get_excel_used_range_values(drive_id: str, item_id: str, worksheet_name: str) -> dict:
    """
    Get the used range of a worksheet with all cell values.
    
    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID of the Excel workbook
        worksheet_name: The name of the worksheet
    
    Returns:
        Dict with status_code, message, and range data including:
        - values: 2D array of cell values
        - address: Range address (e.g., "Sheet1!A1:N50")
        - row_count: Number of rows
        - column_count: Number of columns
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "range": None
            }
        
        # URL encode worksheet name
        import urllib.parse, re
        encoded_name = urllib.parse.quote(worksheet_name)

        # Step 1: call usedRange to find the last row number
        used_endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets/{encoded_name}/usedRange"
        logger.info(f"Getting used range for worksheet '{worksheet_name}' to find last row")
        used_resp = requests.get(url=used_endpoint, headers=headers)
        if used_resp.status_code != 200:
            logger.error(f"Graph API get excel used range failed: {used_resp.text}")
            return {
                "message": f"Graph API call failed: {used_resp.text}",
                "status_code": used_resp.status_code,
                "range": None
            }
        used_data = used_resp.json()
        address = used_data.get("address", "")
        last_row = used_data.get("rowCount", 1)
        if "!" in address:
            match = re.search(r':([A-Z]+)(\d+)$', address.split("!")[1])
            if match:
                last_row = int(match.group(2))

        # Step 2: fetch the full A1:Z{lastRow} range so col A is always included
        full_range = f"A1:Z{last_row}"
        encoded_range = urllib.parse.quote(full_range)
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets/{encoded_name}/range(address='{encoded_range}')"
        logger.info(f"Getting full range '{full_range}' for worksheet '{worksheet_name}' in workbook: {item_id}")
        resp = requests.get(url=endpoint, headers=headers)

        if resp.status_code == 200:
            range_data = resp.json()
            return {
                "message": "Used range retrieved successfully",
                "status_code": 200,
                "range": {
                    "address": range_data.get("address"),
                    "values": range_data.get("values", []),
                    "row_count": range_data.get("rowCount", 0),
                    "column_count": range_data.get("columnCount", 0)
                }
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "range": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Worksheet '{worksheet_name}' not found",
                "status_code": 404,
                "range": None
            }
        else:
            logger.error(f"Graph API get excel used range failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "range": None
            }
    except Exception as e:
        logger.exception("Error getting excel used range")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "range": None
        }


def insert_excel_rows(drive_id: str, item_id: str, worksheet_name: str, row_index: int, values: list, max_retries: int = 3) -> dict:
    """
    Insert rows at a specific position in a worksheet, shifting existing rows down.
    Includes retry logic with exponential backoff for transient Graph API errors.
    
    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID of the Excel workbook
        worksheet_name: The name of the worksheet
        row_index: The 1-based row number where new rows should be inserted
        values: 2D array of values to insert [[row1], [row2], ...]
        max_retries: Maximum number of retry attempts for transient errors (default 3)
    
    Returns:
        Dict with status_code, message, and inserted range data
    """
    import time
    import urllib.parse
    
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "range": None
            }
        
        encoded_name = urllib.parse.quote(worksheet_name)
        
        num_rows = len(values)
        num_cols = len(values[0]) if values else 1
        
        # Convert column number to letter
        def col_num_to_letter(n):
            result = ""
            while n > 0:
                n -= 1
                result = chr(65 + (n % 26)) + result
                n //= 26
            return result
        
        end_col = col_num_to_letter(num_cols)
        end_row = row_index + num_rows - 1
        
        # Range to insert (will shift down)
        insert_range = f"A{row_index}:{end_col}{end_row}"
        encoded_range = urllib.parse.quote(insert_range)
        
        # Step 1: Insert blank rows at the position (shift down)
        insert_endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets/{encoded_name}/range(address='{encoded_range}')/insert"
        
        insert_payload = {
            "shift": "Down"
        }
        
        logger.info(f"Inserting {num_rows} row(s) at row {row_index} in worksheet '{worksheet_name}'")
        
        # Retry loop for transient errors
        last_error = None
        for attempt in range(max_retries):
            insert_resp = requests.post(url=insert_endpoint, headers=headers, json=insert_payload)
            
            if insert_resp.status_code in [200, 201]:
                # Success - proceed to Step 2
                break
            elif insert_resp.status_code == 401:
                return {
                    "message": "Access token expired or invalid. Try refreshing the token.",
                    "status_code": 401,
                    "range": None
                }
            elif insert_resp.status_code == 404:
                return {
                    "message": f"Worksheet '{worksheet_name}' not found",
                    "status_code": 404,
                    "range": None
                }
            else:
                # Check if this is a retryable error (GeneralException, 5xx, etc.)
                is_retryable = (
                    insert_resp.status_code >= 500 or 
                    "GeneralException" in insert_resp.text or
                    "ServiceUnavailable" in insert_resp.text or
                    "TooManyRequests" in insert_resp.text
                )
                
                if is_retryable and attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 1  # Exponential backoff: 2s, 3s, 5s
                    logger.warning(f"Graph API insert failed (attempt {attempt + 1}/{max_retries}), retrying in {wait_time}s: {insert_resp.text[:200]}")
                    time.sleep(wait_time)
                    last_error = insert_resp.text
                    continue
                else:
                    logger.error(f"Graph API insert rows failed after {attempt + 1} attempts: {insert_resp.text}")
                    return {
                        "message": f"Graph API insert failed: {insert_resp.text}",
                        "status_code": insert_resp.status_code,
                        "range": None
                    }
        else:
            # All retries exhausted
            logger.error(f"Graph API insert rows failed after {max_retries} retries")
            return {
                "message": f"Graph API insert failed after {max_retries} retries: {last_error}",
                "status_code": 500,
                "range": None
            }
        
        # Step 2: Update the inserted rows with values
        return update_excel_range(drive_id, item_id, worksheet_name, insert_range, values)
        
    except Exception as e:
        logger.exception("Error inserting excel rows")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "range": None
        }


def clear_excel_range(drive_id: str, item_id: str, worksheet_name: str, range_address: str) -> dict:
    """
    Clear a range of cells in an Excel worksheet.
    
    Args:
        drive_id: The MS Graph drive ID
        item_id: The MS Graph item ID of the Excel workbook
        worksheet_name: The name of the worksheet
        range_address: Excel range address (e.g., "A1:D4")
    
    Returns:
        Dict with status_code, message, and cleared range data
    """
    try:
        headers = _get_auth_headers()
        if not headers:
            return {
                "message": "No valid MS access token available. Please authenticate first.",
                "status_code": 401,
                "range": None
            }
        
        # URL encode worksheet name and range address
        import urllib.parse
        encoded_name = urllib.parse.quote(worksheet_name)
        encoded_range = urllib.parse.quote(range_address)
        endpoint = f"{GRAPH_API_BASE}/drives/{drive_id}/items/{item_id}/workbook/worksheets/{encoded_name}/range(address='{encoded_range}')/clear"
        
        payload = {
            "applyTo": "All"  # Options: "All", "Formats", "Contents"
        }
        
        logger.info(f"Clearing range '{range_address}' in worksheet '{worksheet_name}' of workbook: {item_id}")
        resp = requests.post(url=endpoint, headers=headers, json=payload)
        
        if resp.status_code == 200:
            range_data = resp.json()
            return {
                "message": "Range cleared successfully",
                "status_code": 200,
                "range": {
                    "address": range_data.get("address"),
                    "addressLocal": range_data.get("addressLocal")
                }
            }
        elif resp.status_code == 401:
            return {
                "message": "Access token expired or invalid. Try refreshing the token.",
                "status_code": 401,
                "range": None
            }
        elif resp.status_code == 404:
            return {
                "message": f"Worksheet '{worksheet_name}' or range '{range_address}' not found",
                "status_code": 404,
                "range": None
            }
        else:
            logger.error(f"Graph API clear excel range failed: {resp.text}")
            return {
                "message": f"Graph API call failed: {resp.text}",
                "status_code": resp.status_code,
                "range": None
            }
    except Exception as e:
        logger.exception("Error clearing excel range")
        return {
            "message": f"An error occurred: {str(e)}",
            "status_code": 500,
            "range": None
        }
