
from typing import Dict, Any
import base64
import io
import requests


def upload_file_to_sharepoint(access_token: str, site_id: str, folder_id: str, file: Dict, conflict_behavior: str = 'replace') -> Dict[str, Any]:
    """
    Uploads a base64 encoded file to SharePoint.
    
    Args:
        access_token: SharePoint access token
        site_id: SharePoint site ID
        folder_id: Destination folder ID
        file: Dictionary containing file details (name, type, size, data)
        conflict_behavior: How to handle naming conflicts
    """
    try:
        _file = file[0]
        # Extract file information
        file_name = _file.get('name')
        file_size = _file.get('size')
        print(f"file_name: {file_name}")
        print(f"file_size: {file_size}")
        
        if not file_name:
            return {
                'status_code': 404,
                'message': "File name is required"
            }

        # Convert base64 to binary
        try:
            base64_data = _file['data'].split('base64,')[1]
            file_content = base64.b64decode(base64_data)
        except (KeyError, IndexError, base64.binascii.Error) as e:
            return {
                'status_code': 400,
                'message': f"Invalid base64 data: {str(e)}"
            }

        # Create file-like object from binary data
        file_obj = io.BytesIO(file_content)
        file_obj.seek(0)

        if file_size > 4 * 1024 * 1024:
            return large_file_upload(
                access_token,
                site_id,
                folder_id,
                file_obj,
                file_name,
                len(file_content),
                conflict_behavior
            )

        return simple_upload(
            access_token,
            site_id,
            folder_id,
            file_obj,
            file_name,
            conflict_behavior
        )

    except Exception as e:
        return {
            'status_code': 500,
            'message': f"Error uploading file: {str(e)}"
        }


def simple_upload(access_token: str, site_id: str, folder_id: str, file_obj: io.BytesIO, 
                 file_name: str, conflict_behavior: str) -> Dict[str, Any]:
    """Handles file upload for files smaller than 4MB."""
    url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{folder_id}:/{file_name}:/content"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/octet-stream',
        'Prefer': f'respond-async,conflictBehavior={conflict_behavior}'
    }

    response = requests.put(url, headers=headers, data=file_obj, timeout=30)
    
    if response.status_code == 201:
        return response.json()
    
    return {
        'status_code': response.status_code,
        'message': f"Upload failed: {response.text}"
    }


def large_file_upload(access_token: str, site_id: str, folder_id: str, file_obj: io.BytesIO, 
                     file_name: str, file_size: int, conflict_behavior: str) -> Dict[str, Any]:
    """Handles file upload for files larger than 4MB using upload sessions."""
    session_url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{folder_id}:/{file_name}:/createUploadSession"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Prefer': f'respond-async,conflictBehavior={conflict_behavior}'
    }

    session_response = requests.post(session_url, headers=headers, timeout=30)
    if session_response.status_code != 200:
        return {
            'status_code': session_response.status_code,
            'message': f"Failed to create upload session: {session_response.text}"
        }

    upload_url = session_response.json().get('uploadUrl')
    chunk_size = 320 * 1024  # 320 KB

    for start in range(0, file_size, chunk_size):
        chunk = file_obj.read(chunk_size)
        chunk_end = min(start + len(chunk) - 1, file_size - 1)

        headers = {
            'Content-Length': str(len(chunk)),
            'Content-Range': f'bytes {start}-{chunk_end}/{file_size}'
        }

        chunk_response = requests.put(upload_url, headers=headers, data=chunk, timeout=30)
        
        if chunk_response.status_code not in (201, 202):
            return {
                'status_code': chunk_response.status_code,
                'message': f"Failed to upload chunk: {chunk_response.text}"
            }

        if chunk_response.status_code == 201:
            return chunk_response.json()

    return {
        'status_code': 500,
        'message': 'File upload did not complete successfully'
    }
