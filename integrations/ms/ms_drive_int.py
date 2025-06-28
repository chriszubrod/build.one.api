import os
import json
import requests

from datetime import datetime
from flask import jsonify

from persistence.pers_response import DatabaseError, SuccessResponse, PersistenceResponse
from integrations.ms import pers_ms_sharepoint_site


def refresh_token(SECRETS_URL):
    """
    Responds to HTTP POST requests to the "/ms/app/oauth2/refresh_token" route with a JSON response.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API token endpoint.

    Example:
    >>> refresh_token()
    <Response 200 OK>
    """

    refresh_secrets = hp.read_profile_secrets(url=SECRETS_URL)
    client_id = refresh_secrets['ms']['client_id']
    tenant = refresh_secrets['ms']['tenant']
    client_secret = refresh_secrets['ms']['client_secret']
    refresh_token = refresh_secrets['ms']['refresh_token']

    url = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    payload = {
        'scope': 'offline_access%20user.read%20mail.read%20files.read.all%20files.readwrite.all%20sites.read.all%20sites.selected'
    }
    data = {
        'client_id': client_id,
        'refresh_token': refresh_token,
        'grant_type': 'refresh_token',
        'client_secret': client_secret
    }
    resp = requests.post(url=url, data=data, params=payload, headers=headers, timeout=10)
    access_token = resp.json()['access_token']
    expires_in = resp.json()['expires_in']
    ext_expires_in = resp.json()['ext_expires_in']
    token = resp.json()['refresh_token']
    scope = resp.json()['scope']
    token_type = resp.json()['token_type']

    refresh_secrets['ms']['access_token'] = access_token
    refresh_secrets['ms']['expires_in'] = expires_in
    refresh_secrets['ms']['ext_expires_in'] = ext_expires_in
    refresh_secrets['ms']['refresh_token'] = token
    refresh_secrets['ms']['scope'] = scope
    refresh_secrets['ms']['token_type'] = token_type

    hp.write_profile_secrets(url=SECRETS_URL, secrets=refresh_secrets)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


def get_items(site_id: str = None, item_id: str = None, access_token: str = None) -> dict:
    """Recursively gets all items in a folder."""
    # For SharePoint site drive
    #site_id = "imviokguifqdnyjvkb9idegwrhi.sharepoint.com,17981139-624e-48b0-b1ca-36a21ab8e963,1ae020ca-f72c-4665-98df-5a4a7b397436"
    #item_id = "017ZKYN57RHILAEB2UNJD3OOZWEQ7X4Q5Z"

    if item_id:
        url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/children'
    else:
        url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root/children'

    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }

    all_items = []
    next_link = None
    page_count = 0

    try:
        params = {
            '$top': 200
        }

        while True:
            page_count += 1
            if next_link:
                resp = requests.get(url=next_link, headers=headers, timeout=30)
            else:
                resp = requests.get(url, headers=headers, params=params, timeout=30)

            if resp.status_code != 200:
                raise Exception(f"API call failed: {resp.text}")

            data = resp.json()
            items = data.get('value', [])

            for item in items:
                item_data = {
                    'msGraphDownloadUrl': item.get('@microsoft.graph.downloadUrl'),
                    'msCreatedDatetime': item.get('createdDateTime'),
                    'eTag': item.get('eTag'),
                    'id': item.get('id'),
                    'lastModifiedDateTime': item.get('lastModifiedDateTime'),
                    'name': item.get('name'),
                    'webUrl': item.get('webUrl'),
                    'cTag': item.get('cTag'),
                    'hashQuickHash': item['file']['hashes']['quickXorHash'],
                    'mimeType': item['file']['mimeType'],
                    'parentId': item['parentReference']['id'],
                    'sharedScope': item['shared']['scope'],
                    'size': item.get('size')
                }
                all_items.append(item_data)
            
            next_link = data.get('@odata.nextLink')
            if not next_link:
                break

        return all_items
    except Exception as e:
        print(f"Error getting items: {str(e)}")
        return None


def get_item(site_id: str = None, item_id: str = None, access_token: str = None) -> dict:
    """
    Download SharePoint file content using site-specific endpoint
    """
    try:
        # Use the site-specific endpoint
        url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/content"
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/octet-stream'
        }
        
        print(f"Downloading file with ID: {item_id} from site: {site_id}")
        response = requests.get(url, headers=headers, timeout=60)
        
        if response.status_code == 200:
            file_content = response.content
            print(f"Successfully downloaded {len(file_content)} bytes")
            return file_content
        else:
            print(f"Failed to download file: {response.status_code} - {response.text}")
            return None
            
    except Exception as e:
        print(f"Error downloading file: {str(e)}")
        return None


def get_site():
    pass


# TODO: Try to move code from below to get_site()
# TODO: Then update run_map_process() to use get_site() and get_items()


def run_map_process(SECRETS_URL):
    try:
        # Get secrets and access token
        secrets = hp.read_profile_secrets(url=SECRETS_URL)
        access_token = secrets['ms']['access_token']

        headers = {
            'Authorization': f'Bearer {access_token}'
        }

        url = f'https://graph.microsoft.com/v1.0/sites/imviokguifqdnyjvkb9idegwrhi.sharepoint.com:/sites/RogersBuildLLC'
        resp = requests.get(url=url, headers=headers, timeout=10)

        if resp.json().get('error'):
            code = resp.json().get('error').get('code')
            message = resp.json().get('error').get('message')
            if code == 'InvalidAuthenticationToken' and message == 'Lifetime validation failed, the token is expired.':
                refresh_token(SECRETS_URL)
                secrets = hp.read_profile_secrets(url=SECRETS_URL)
                resp = requests.get(url=url, headers=headers, timeout=10)
                if resp.status_code == 200:
                    resp_json = resp.json()

                    if resp_json.get('root') == {}:
                        site_root = ''
                    else:
                        site_root = resp_json.get('root')

                    site_collection = resp_json.get('siteCollection')
                    host_name = site_collection.get('hostname')

                    _site = pers_ms_sharepoint_site.SharePointSite(
                        site_created_datetime=resp_json.get('createdDateTime'),
                        site_modified_datetime=resp_json.get('lastModifiedDateTime'),
                        site_o_data_context=resp_json.get('@odata.context'),
                        site_description=resp_json.get('description'),
                        site_display_name=resp_json.get('displayName'),
                        site_sharepoint_id=resp_json.get('id'),
                        site_last_modified_datetime=resp_json.get('lastModifiedDateTime'),
                        site_name=resp_json.get('name'),
                        site_root=site_root,
                        site_collection_host_name=host_name,
                        site_web_url=resp_json.get('webUrl'),
                    )

                    pers_read_ms_sharepoint_site_by_site_id_resp = pers_ms_sharepoint_site.\
                        read_sharepoint_site_by_site_id(_site.site_sharepoint_id)

                    if isinstance(pers_read_ms_sharepoint_site_by_site_id_resp, SuccessResponse):
                        pers_update_ms_sharepoint_site_resp = pers_ms_sharepoint_site.\
                            update_sharepoint_site_by_site_id(_site)
                        if isinstance(pers_update_ms_sharepoint_site_resp, SuccessResponse):
                            return {
                                'status': pers_update_ms_sharepoint_site_resp.status_code,
                                'message': pers_update_ms_sharepoint_site_resp.message,
                            }
                        else:
                            return {
                                'status': pers_update_ms_sharepoint_site_resp.status_code,
                                'message': pers_update_ms_sharepoint_site_resp.message,
                            }   
                    else:
                        pers_ms_sharepoint_site_resp = pers_ms_sharepoint_site.\
                            create_sharepoint_site(_site)
                        if isinstance(pers_ms_sharepoint_site_resp, SuccessResponse):
                            return {
                                'status': pers_ms_sharepoint_site_resp.status_code,
                                'message': pers_ms_sharepoint_site_resp.message,
                            }
                        else:
                            return {
                                'status': pers_ms_sharepoint_site_resp.status_code,
                                'message': pers_ms_sharepoint_site_resp.message,
                            }

        if resp.status_code == 200:
            resp_json = resp.json()

            if resp_json.get('root') == {}:
                site_root = ''
            else:
                site_root = resp_json.get('root')

            site_collection = resp_json.get('siteCollection')
            host_name = site_collection.get('hostname')

            _site = pers_ms_sharepoint_site.SharePointSite(
                site_created_datetime=resp_json.get('createdDateTime'),
                site_modified_datetime=resp_json.get('lastModifiedDateTime'),
                site_o_data_context=resp_json.get('@odata.context'),
                site_description=resp_json.get('description'),
                site_display_name=resp_json.get('displayName'),
                site_sharepoint_id=resp_json.get('id'),
                site_last_modified_datetime=resp_json.get('lastModifiedDateTime'),
                site_name=resp_json.get('name'),
                site_root=site_root,
                site_collection_host_name=host_name,
                site_web_url=resp_json.get('webUrl'),
            )

            pers_read_ms_sharepoint_site_by_site_id_resp = pers_ms_sharepoint_site.\
                read_sharepoint_site_by_site_id(_site.site_sharepoint_id)

            if isinstance(pers_read_ms_sharepoint_site_by_site_id_resp, SuccessResponse):
                pers_update_ms_sharepoint_site_resp = pers_ms_sharepoint_site.\
                    update_sharepoint_site_by_site_id(_site)
                if isinstance(pers_update_ms_sharepoint_site_resp, SuccessResponse):
                    return {
                        'status': pers_update_ms_sharepoint_site_resp.status_code,
                        'message': pers_update_ms_sharepoint_site_resp.message,
                    }
                else:
                    return {
                        'status': pers_update_ms_sharepoint_site_resp.status_code,
                        'message': pers_update_ms_sharepoint_site_resp.message,
                    }   
            else:
                pers_ms_sharepoint_site_resp = pers_ms_sharepoint_site.\
                    create_sharepoint_site(_site)
                if isinstance(pers_ms_sharepoint_site_resp, SuccessResponse):
                    return {
                        'status': pers_ms_sharepoint_site_resp.status_code,
                        'message': pers_ms_sharepoint_site_resp.message,
                    }
                else:
                    return {
                        'status': pers_ms_sharepoint_site_resp.status_code,
                        'message': pers_ms_sharepoint_site_resp.message,
                    }

        return resp.json()

    except Exception as e:
        return {
            'status': 'error',
            'message': str(e)
        }


        '''
        # Start mapping from root
        drive_map = get_items()

        # Save to file
        output_file = os.path.join(SITE_ROOT, "drive_map.json")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump({
                'status': 'success',
                'drive_map': drive_map
            }, f, indent=4, ensure_ascii=False)

        return jsonify({
            'status': 'success',
            'message': 'Drive map saved to drive_map.json',
            'drive_map': drive_map
        }), 200

    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
        '''
