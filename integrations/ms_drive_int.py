import os
import json
import requests

from datetime import datetime
from flask import jsonify
from helper import function_help as hp
from persistence.pers_response import DatabaseError, SuccessResponse, PersistenceResponse
from persistence import pers_ms_sharepoint_site


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


def get_items(item_id: str = None) -> dict:
    """Recursively gets all items in a folder."""
    # For SharePoint site drive
    site_id = "imviokguifqdnyjvkb9idegwrhi.sharepoint.com,17981139-624e-48b0-b1ca-36a21ab8e963,1ae020ca-f72c-4665-98df-5a4a7b397436"

    if item_id:
        url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/children'
    else:
        url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root/children'

    response = requests.get(url, headers=headers, timeout=10)
    if response.status_code != 200:
        raise Exception(f"API call failed: {response.text}")

    items = response.json().get('value', [])
    result = []

    for item in items:
        item_data = {
            'name': item.get('name'),
            'id': item.get('id'),
            'type': 'folder' if item.get('folder') else 'file',
            'web_url': item.get('webUrl')
        }

        if item.get('folder'):
            item_data['children'] = get_items(item['id'])

        result.append(item_data)

    return result


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
