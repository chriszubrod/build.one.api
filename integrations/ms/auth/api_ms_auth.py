"""
Module for Microsoft Graph API.
"""

# python standard library imports
import json
import os
import requests
import sys
from datetime import datetime
from dateutil import tz

# third party imports
from flask import Blueprint, redirect, request, jsonify, current_app, url_for, session, flash

# local imports
from utils.config_help import get_secrets, update_secrets
from utils.auth_help import requires_auth
from integrations.ms.auth import bus_ms_auth

# At the top of the file
MS_GRAPH_BASE_URL = 'https://graph.microsoft.com/v1.0'
MS_AUTH_BASE_URL = 'https://login.microsoftonline.com'


api_ms_auth_bp = Blueprint('api_ms_auth', __name__, url_prefix='/ms/app', template_folder='templates')

# Add project root to Python path
project_root = os.path.realpath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(project_root)

# Set secrets.json path
os.environ['SECRETS_PATH'] = os.path.join(project_root, 'secrets.json')


@api_ms_auth_bp.route('/oauth2/authorize', methods=['GET'])
def authorization():
    """
    Responds to HTTP GET requests to the "/ms/app/oauth2/authorize" route with a redirect.

    Returns:
    redirect: A Flask Reponse object with a status code of 302 (Redirect) and parameters required
    by the Microsoft Graph API.

    Example:
    >>> authorization()
    <Response 302 Found>
    """
    try:
        secrets = current_app.config['SECRETS']
        client_id = secrets['ms']['client_id']
        tenant = secrets['ms']['tenant']

        next_page = request.args.get('next', url_for('web_dashboard.dashboard_route'))

        endpoint = str(
            f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize?' +
            "client_id=" + client_id +
            "&response_type=code" +
            "&redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fms%2Fapp%2Foauth2%2Fauthorize%2Fcallback" +
            "&response_mode=query" +
            "&scope=offline_access%20user.read%20mail.read%20files.read.all%20files.readwrite.all%20sites.read.all%20sites.selected" +
            f"&state={next_page}"
        )
        return redirect(endpoint)
    except KeyError as e:
        return jsonify({
            "error": f"Missing required configuration: {str(e)}",
            "status_code": 500
        }), 500
    except Exception as e:
        return jsonify({
            "error": str(e),
            "status_code": 500
        }), 500


@api_ms_auth_bp.route('/oauth2/authorize/callback', methods=['GET'])
def authorization_callback():
    """
    Responds to HTTP GET requests to the "/ms/app/oauth2/authorize" route with a JSON response.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API token endpoint.

    Example:
    >>> authorization_callback()
    <Response 200 OK>
    """
    refresh_secrets = current_app.config['SECRETS']
    client_id = refresh_secrets['ms']['client_id']
    tenant = refresh_secrets['ms']['tenant']
    client_secret = refresh_secrets['ms']['client_secret']

    code = request.args.get('code')

    next_page = request.args.get('state', url_for('web_dashboard.dashboard_route'))

    url = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }
    payload = {
        'scope': 'offline_access%20user.read%20mail.read%20files.read.all%20files.readwrite.all%20sites.read.all%20sites.selected'
    }
    data = {
        'client_id': client_id,
        'code': code,
        'client_secret': client_secret,
        'grant_type': 'authorization_code',
        'redirect_uri': 'http://localhost:8000/ms/app/oauth2/authorize/callback'
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

    current_app.update_secrets(refresh_secrets)

    get_bus_ms_auth_response = bus_ms_auth.get_ms_auth_by_user_id(session['user']['id'])
    if get_bus_ms_auth_response.success:
        _ms_auth = get_bus_ms_auth_response.data
        _ms_auth.modified_datetime = datetime.now(tz.tzlocal())
        _ms_auth.client_id = client_id
        _ms_auth.tenant = tenant
        _ms_auth.client_secret = client_secret
        _ms_auth.access_token = access_token
        _ms_auth.expires_in = expires_in
        _ms_auth.ext_expires_in = ext_expires_in
        _ms_auth.refresh_token = token
        _ms_auth.scope = scope
        _ms_auth.token_type = token_type
        # update the ms auth
        bus_patch_ms_auth_response = bus_ms_auth.patch_ms_auth(
            ms_auth=_ms_auth
        )
        if bus_patch_ms_auth_response.success:
            flash('Microsoft Graph API integration updated successfully', 'success')
            return redirect(next_page)
        else:
            flash('Failed to update Microsoft Graph API integration', 'error')
            return redirect(next_page)
    else:
        # create the ms auth
        bus_post_ms_auth_response = bus_ms_auth.post_ms_auth(
            submission_datetime=datetime.now(tz.tzlocal()),
            client_id=client_id,
            tenant=tenant,
            client_secret=client_secret,
            access_token=access_token,
            expires_in=expires_in,
            ext_expires_in=ext_expires_in,
            refresh_token=token,
            scope=scope,
            token_type=token_type,
            user_id=session['user']['id']
        )
        if bus_post_ms_auth_response.success:
            flash('Microsoft Graph API integration created successfully', 'success')
            return redirect(next_page)
        else:
            flash('Failed to create Microsoft Graph API integration', 'error')
            return redirect(next_page)


@api_ms_auth_bp.route('/oauth2/refresh_token', methods=['GET'])
def refresh_token():
    """
    Responds to HTTP POST requests to the "/ms/app/oauth2/refresh_token" route with a JSON response.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API token endpoint.

    Example:
    >>> refresh_token()
    <Response 200 OK>
    """

    refresh_secrets = current_app.config['SECRETS']
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

    current_app.update_secrets(refresh_secrets)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/profile', methods=['GET'])
def get_profile():
    """
    Responds to HTTP GET requests to the "/ms/app/profile" route with a JSON response containing
    the profile information of the user.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API user profile endpoint.

    Example:
    >>> get_profile()
    <Response 200 OK>
    """
    try:
        secrets = current_app.config['SECRETS']
        access_token = secrets['ms']['access_token']
        headers = {
            'Authorization': f'Bearer {access_token}'
        }
        url = 'https://graph.microsoft.com/v1.0/me'
        resp = requests.get(url=url, headers=headers, timeout=10)
        resp.raise_for_status()  # Raise exception for bad status codes
        
        return jsonify({
            "response_json": resp.json()
        })
    except requests.exceptions.RequestException as e:
        return jsonify({
            "error": str(e),
            "status_code": getattr(e.response, 'status_code', 500)
        }), getattr(e.response, 'status_code', 500)


@api_ms_auth_bp.route('/sites', methods=['GET'])
@requires_auth()
def get_sites():
    """
    Responds to HTTP GET requests to the "/ms/app/sites" route with a JSON response containing
    the user's sites information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API sites endpoint.

    Example:
    >>> get_sites()
    <Response 200 OK>
    """
    sites_secrets = current_app.config['SECRETS']

    access_token = sites_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = 'https://graph.microsoft.com/v1.0/sites?search=*'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/sites/<site_id>/drive/root/children', methods=['GET'])
def get_sites_drive_root_children(site_id):
    """
    Responds to HTTP GET requests to the "/ms/app/sites/<site_id>/drive/root/children" route with a JSON response containing
    the user's sites information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API sites endpoint.

    Example:
    >>> get_sites_drive_root_children()
    <Response 200 OK>
    """
    sites_secrets = current_app.config['SECRETS']

    access_token = sites_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/root/children'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/sites/rogersbuildllc', methods=['GET'])
def get_site_by_id():
    """
    Responds to HTTP GET requests to the "/ms/app/sites/" route with a JSON response containing
    the user's site information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API sites endpoint.

    Example:
    >>> get_site_by_id()
    <Response 200 OK>
    """
    sites_secrets = current_app.config['SECRETS']

    access_token = sites_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = f'https://graph.microsoft.com/v1.0/sites/imviokguifqdnyjvkb9idegwrhi.sharepoint.com:/sites/RogersBuildLLC'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/groups', methods=['GET'])
def get_groups():
    """
    Responds to HTTP GET requests to the "/ms/app/groups" route with a JSON response containing
    the user's sites information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API groups endpoint.

    Example:
    >>> get_groups()
    <Response 200 OK>
    """
    groups_secrets = current_app.config['SECRETS']

    access_token = groups_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = 'https://graph.microsoft.com/v1.0/groups'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/drive', methods=['GET'])
def get_drive():
    """
    Responds to HTTP GET requests to the "/ms/app/drive" route with a JSON response containing
    the user's drive information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API drive endpoint.

    Example:
    >>> get_drive()
    <Response 200 OK>
    """
    drive_secrets = current_app.config['SECRETS']

    access_token = drive_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = 'https://graph.microsoft.com/v1.0/me/drive'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/drives', methods=['GET'])
def get_drives():
    """
    Responds to HTTP GET requests to the "/ms/app/drives" route with a JSON response containing
    the user's drives information.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API drives endpoint.

    Example:
    >>> get_drives()
    <Response 200 OK>
    """
    drives_secrets = current_app.config['SECRETS']

    access_token = drives_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = 'https://graph.microsoft.com/v1.0/me/drives'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/drives/<drive_id>', methods=['GET'])
def get_drive_by_id(drive_id):
    """
    Responds to HTTP GET requests to the "/ms/app/drives/<drive_id>" route with a JSON response
    containing the information of a specific drive.

    Args:
    drive_id (str): The ID of the drive.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API drives endpoint.

    Example:
    >>> get_drive_by_id('12345')
    <Response 200 OK>
    """
    drives_secrets = current_app.config['SECRETS']

    access_token = drives_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/drives/<drive_id>/root/children', methods=['GET'])
def get_drive_by_id_root_children(drive_id):
    """
    Responds to HTTP GET requests to the "/ms/app/drive/<drive_id>/root/children" route with a JSON
    response containing the children items of the root drive.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API drive root children endpoint.

    Example:
    >>> get_drive_by_id_root_children()
    <Response 200 OK>
    """
    drive_secrets = current_app.config['SECRETS']

    access_token = drive_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/root/children'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/drives/<drive_id>/items/<item_id>', methods=['GET'])
def get_item_by_id(drive_id, item_id):
    """
    Responds to HTTP GET requests to the "/ms/app/drives/<drive_id>/items/<item_id>" route with a
    JSON response containing the item of the drive id and item id.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API drive item id endpoint.

    Example:
    >>> get_item_by_id()
    <Response 200 OK>
    """
    item_secrets = current_app.config['SECRETS']

    access_token = item_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/drives/<drive_id>/items/<item_id>/children', methods=['GET'])
def get_drive_items_children(drive_id, item_id):
    """Gets children items for a specific drive item."""
    try:
        # Get access token from secrets
        secrets = current_app.config['SECRETS']
        access_token = secrets['ms']['access_token']

        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }

            # Call Microsoft Graph API
        url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children'
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 200:
            return {
                'status': 'success',
                'response_json': response.json()
            }
        else:
            return {
                'status': 'error',
                'message': f"API call failed: {response.text}"
            }

    except Exception as e:
        return {
            'status': 'error',
            'message': str(e)
        }


@api_ms_auth_bp.route('/drives/<drive_id>/items/<item_id>/workbook', methods=['GET'])
def get_workbook(drive_id, item_id):
    """
    Responds to HTTP GET requests to the "/ms/app/drives/<drive_id>/items/<item_id>/workbook"
    route with a JSON response containing the workbook of the specified item.

    Args:
    drive_id (str): The ID of the drive.
    item_id (str): The ID of the item.

    Returns:
    Response: A Flask Response object with a body containing the JSON object from the Microsoft
    Graph API drive item workbook endpoint.

    Example:
    >>> get_workbook('12345', '67890')
    <Response 200 OK>
    """
    workbook_secrets = current_app.config['SECRETS']

    access_token = workbook_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}'
    }
    url = f'https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/workbook'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/sites/<site_id>/drive/items/<item_id>/workbook/createSession', methods=['GET'])
def get_persistent_session(site_id, item_id):

    profile_secrets = current_app.config['SECRETS']

    access_token = profile_secrets['ms']['access_token']
    headers = {
        'Prefer': 'respond-async',
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}'
    }
    data = {
        'persistChanges': 'true'
    }
    url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/createSession'
    resp = requests.post(url=url, headers=headers, data=json.dumps(data), timeout=10)
    resp_json = resp.json()

    if resp_json.get('error', ''):
        return resp_json

    odata_context = resp_json.get('@odata.context', '')
    persist_changes = resp_json.get('persistChanges', '')
    _id = resp_json.get('id', '')
    cluster = ''
    session = ''
    usid = ''

    if _id != '':
        # Split the response by '&' and iterate to find the session part
        parts = _id.split('&')
        for part in parts:
            key, value = part.split('=')
            if key == 'cluster':
                cluster = value
            if key == 'session':
                session = value
            if key == 'usid':
                usid = value

    sesh = {
        'odata_context': odata_context,
        'persist_changes': persist_changes,
        'id': _id,
        'cluster': cluster,
        'session': session,
        'usid': usid
    }

    profile_secrets[item_id] = sesh

    current_app.update_secrets(profile_secrets)

    return jsonify(sesh)


@api_ms_auth_bp.route('/sites/<site_id>/drive/items/<item_id>/workbook/refreshSession', methods=['GET'])
def refresh_session(site_id, item_id):

    profile_secrets = None

    profile_secrets = current_app.config['SECRETS']

    access_token = profile_secrets['ms']['access_token']
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}',
        'Workbook-Session-Id': profile_secrets[item_id]['id']
    }
    url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/refreshSession'
    resp = requests.post(url=url, headers=headers, timeout=10)

    if resp.status_code == 204:
        return jsonify(
            {
                'status_code': '204',
                'response': resp.text,
                'message': 'Session has been refreshed.'
            }
        )

    return resp.json()


@api_ms_auth_bp.route('/drive/items/<item_id>/workbook/closeSession', methods=['GET'])
def close_session(item_id):

    profile_secrets = None

    profile_secrets = current_app.config['SECRETS']

    access_token = profile_secrets['ms']['access_token']
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {access_token}',
        'Workbook-Session-Id': profile_secrets[item_id]['id']
    }
    url = f'https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/workbook/closeSession'
    resp = requests.post(url=url, headers=headers, timeout=10)

    if resp.status_code == 204:
        if item_id in profile_secrets:
            del profile_secrets[item_id]
            current_app.update_secrets(profile_secrets)

        return jsonify(
            {
                'status_code': '204',
                'response': resp.text,
                'message': 'Session has been closed.'
            }
        )

    return resp.json()


@api_ms_auth_bp.route('/sites/<site_id>/drive/items/<item_id>/workbook/worksheets', methods=['GET'])
def get_workbook_worksheets(site_id, item_id):

    profile_secrets = current_app.config['SECRETS']

    access_token = profile_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}',
        'workbook-session-id': profile_secrets[item_id]['id']
    }
    url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/worksheets'

    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "worksheets_response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/sites/<site_id>/drive/items/<item_id>/workbook/worksheets/<worksheet_id>', methods=['GET'])
def get_workbook_worksheet(site_id, item_id, worksheet_id):
    # https://graph.microsoft.com/v1.0/sites/(''imviokguifqdnyjvkb9idegwrhi.sharepoint.com%2C17981139-624e-48b0-b1ca-36a21ab8e963%2C1ae020ca-f72c-4665-98df-5a4a7b397436'')/drive/items(''017ZKYN57RHILAEB2UNJD3OOZWEQ7X4Q5Z'')/workbook/worksheets(%27%7B2E248848-EA5A-4153-B412-738524EBC991%7D%27)
    profile_secrets = current_app.config['SECRETS']

    access_token = profile_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Workbook-Session-Id': profile_secrets[item_id]['id']
    }
    url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/worksheets/{worksheet_id}'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "worksheet_response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/sites/<site_id>/drive/items/<item_id>/workbook/worksheets/<worksheet_id>/usedRange', methods=['GET'])
def get_workbook_worksheet_used_range(site_id, item_id, worksheet_id):

    profile_secrets = current_app.config['SECRETS']

    access_token = profile_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Workbook-Session-Id': profile_secrets[item_id]['id']
    }
    url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/worksheets/{worksheet_id}/usedRange'
    resp = requests.get(url=url, headers=headers, timeout=10)

    return jsonify(
        {
            "worksheet_response_json": resp.json()
        }
    )


@api_ms_auth_bp.route('/sites/<site_id>/drive/items/<item_id>/workbook/worksheets/<worksheet_id>/range/get', methods=['GET'])
def get_workbook_worksheet_range(site_id, item_id, worksheet_id):

    profile_secrets = current_app.config['SECRETS']

    access_token = profile_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Workbook-Session-Id': profile_secrets[item_id]['id']
    }
    url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/worksheets/{worksheet_id}/range(address='A1:B2')"
    resp = requests.get(url=url, headers=headers, timeout=10)

    return resp.json()


@api_ms_auth_bp.route('/sites/<site_id>/drive/items/<item_id>/workbook/worksheets/<worksheet_id>/range/insert', methods=['GET'])
def insert_workbook_worksheet_range(site_id, item_id, worksheet_id):

    profile_secrets = current_app.config['SECRETS']

    access_token = profile_secrets['ms']['access_token']
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Workbook-Session-Id': profile_secrets[item_id]['id']
    }
    data = {
        'shift': 'Down'
    }
    url = f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/worksheets/{worksheet_id}/range(address='A1:N1')/insert"
    resp = requests.post(url=url, data=json.dumps(data), headers=headers, timeout=10)

    return resp.json()
