
from typing import Dict, Any
import base64
import io
import requests


def upload_file_to_sharepoint(access_token: str, site_id: str, folder_id: str, file: Dict, conflict_behavior: str = 'replace') -> Dict[str, Any]:
    """
    Uploads a base64 encoded file to SharePoint.
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
                'success': False,
                'status_code': 404,
                'message': "File name is required"
            }

        # Handle both base64 string and bytes
        file_content = _file['data']
        if isinstance(file_content, str):
            # If it's a string, try to decode as base64
            try:
                if 'base64,' in file_content:
                    base64_data = file_content.split('base64,')[1]
                else:
                    base64_data = file_content
                file_content = base64.b64decode(base64_data)
            except (KeyError, IndexError, base64.binascii.Error) as e:
                return {
                    'success': False,
                    'status_code': 400,
                    'message': f"Invalid base64 data: {str(e)}"
                }
        elif isinstance(file_content, bytes):
            # If it's already bytes, use it directly
            pass
        else:
            return {
                'success': False,
                'status_code': 400,
                'message': f"Invalid data type: {type(file_content)}"
            }

        # Create file-like object from binary data
        file_obj = io.BytesIO(file_content)
        file_obj.seek(0)

        if file_size > 4 * 1024 * 1024:
            result = large_file_upload(
                access_token,
                site_id,
                folder_id,
                file_obj,
                file_name,
                len(file_content),
                conflict_behavior
            )
        else:
            result = simple_upload(
                access_token,
                site_id,
                folder_id,
                file_obj,
                file_name,
                conflict_behavior
            )
        
        if 'status_code' in result and result['status_code'] in [200, 201]:
            result['success'] = True
        else:
            result['success'] = False
        
        return result

    except Exception as e:
        return {
            'success': False,
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
        result = response.json()
        result['success'] = True  # Add success flag
        result['status_code'] = 201  # Add status code
        return result
    
    return {
        'success': False,
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
            result = chunk_response.json()
            result['success'] = True  # Add success flag
            result['status_code'] = 201  # Add status code
            return result

    return {
        'success': False,
        'status_code': 500,
        'message': 'File upload did not complete successfully'
    }
