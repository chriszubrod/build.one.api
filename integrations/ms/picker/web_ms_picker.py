"""
Module for Microsoft Graph API Picker.
"""
# python standard library imports
from datetime import datetime
from dateutil import tz

# third party imports
from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    url_for
)

# local imports
from integrations.ms.auth import bus_ms_auth, api_ms_auth
from integrations.ms.drives import bus_ms_drives
from integrations.ms.sites import bus_ms_sites
from utils.auth_help import requires_auth
from modules.project import bus_project
from integrations.ms import pers_ms_sharepoint_site
import requests
from flask import current_app

web_ms_picker_bp = Blueprint('web_ms_picker', __name__, url_prefix='/ms/app', template_folder='templates')



@web_ms_picker_bp.route('/site/picker', methods=['GET'])
def site_picker_route():
    """
    Returns the route for the Microsoft Graph API Picker.
    """
    get_ms_sites_bus_response = bus_ms_sites.get_ms_sites()
    if get_ms_sites_bus_response.success:
        _ms_sites = get_ms_sites_bus_response.data
    else:
        pass
        #print(f"Failed to get Microsoft Graph API sites: {get_ms_sites_bus_response.message}")

    return render_template('ms_site_picker.html', ms_sites=_ms_sites)


@web_ms_picker_bp.route('/drive/picker', methods=['GET'])
def drive_picker_route():
    """
    Returns the route for the Microsoft Graph API Picker.
    """
    site_id = request.args.get('site_id')

    get_ms_drives_bus_response = bus_ms_drives.get_ms_drives_from_graph(site_id)
    if get_ms_drives_bus_response.success:
        _ms_drives = get_ms_drives_bus_response.data
    else:
        #print(f"Failed to get Microsoft Graph API drives: {get_ms_drives_bus_response.message}")
        _ms_drives = []  # Initialize with empty list on error

    return render_template('ms_drive_picker.html', ms_drives=_ms_drives)


@web_ms_picker_bp.route('/drive/children/picker', methods=['GET'])
def drive_children_picker_route():
    """
    Returns the route for the Microsoft Graph API Picker.
    """
    drive_id = request.args.get('drive_id')

    get_ms_drives_children_bus_response = bus_ms_drives.get_ms_drives_children_from_graph(drive_id)
    if get_ms_drives_children_bus_response.success:
        _ms_drives_children = get_ms_drives_children_bus_response.data
    else:
        #print(f"Failed to get Microsoft Graph API drives children: {get_ms_drives_children_bus_response.message}")
        _ms_drives_children = []  # Initialize with empty list on error

    return render_template('ms_drive_item_picker.html', ms_drives_items=_ms_drives_children, drive_id=drive_id)


@web_ms_picker_bp.route('/drive/item/children/picker', methods=['GET'])
def drive_item_children_picker_route():
    """
    Returns the route for the Microsoft Graph API Picker.
    """
    drive_id = request.args.get('drive_id')
    drive_item_id = request.args.get('drive_item_id')

    get_ms_drives_items_children_bus_response = bus_ms_drives.get_ms_drives_items_children_from_graph(drive_id, drive_item_id)
    if get_ms_drives_items_children_bus_response.success:
        _ms_drives_items_children = get_ms_drives_items_children_bus_response.data
    else:
        #print(f"Failed to get Microsoft Graph API drives items children: {get_ms_drives_items_children_bus_response.message}")
        _ms_drives_items_children = []  # Initialize with empty list on error
    
    #print(f"ms_drives_items_children: {_ms_drives_items_children}")
    return render_template('ms_drive_item_children_picker.html', ms_drives_items=_ms_drives_items_children, drive_id=drive_id, drive_item_id=drive_item_id)


@web_ms_picker_bp.route('/worksheet/picker', methods=['GET'])
def worksheet_picker_route():
    """Renders available worksheets for the project's mapped workbook into a picker template."""
    project_id = request.args.get('project_id')
    #print(f"DEBUG: project_id: {project_id}")

    # Find mapped workbook for this project
    wb_resp = bus_project.get_ms_sharepoint_workbooks_by_project_id(int(project_id))
    if not getattr(wb_resp, 'success', False) or not wb_resp.data:
        return render_template('ms_worksheet_picker.html', worksheets=[])
    #print(f"DEBUG: wb_resp: {wb_resp.data}")

    wb_item = wb_resp.data[0]
    if not wb_item or not getattr(wb_item, 'workbook_ms_id', None):
        return render_template('ms_worksheet_picker.html', worksheets=[])
    #print(f"DEBUG: wb_item: {wb_item}")
    item_id = wb_item.workbook_ms_id

    # Get site id (assumes one site stored; take first)
    site_resp = pers_ms_sharepoint_site.read_sharepoint_sites()
    if not getattr(site_resp, 'success', False) or not site_resp.data:
        return render_template('ms_worksheet_picker.html', worksheets=[])
    site_id = site_resp.data[0].site_sharepoint_id

    # Refresh access token
    secrets_refresh_resp = api_ms_auth.refresh_token()
    if not secrets_refresh_resp.get('status_code', 0) == 200:
        return render_template('ms_worksheet_picker.html', worksheets=[])

    # Acquire access token
    if 'user' not in session:
        # Use a default user ID for development
        user_id = 2  # or get from environment variable
    else:
        user_id = session['user']['id']
    secrets_resp = bus_ms_auth.get_ms_auth_by_user_id(user_id)
    if not getattr(secrets_resp, 'success', False):
        return render_template('ms_worksheet_picker.html', worksheets=[])
    access_token = secrets_resp.data.access_token
    #print(f"DEBUG: access_token: {access_token}")
 
    # List worksheets
    ws_headers = { 'Authorization': f'Bearer {access_token}' }
    ws_url = f'https://graph.microsoft.com/v1.0/sites/{site_id}/drive/items/{item_id}/workbook/worksheets'
    resp = requests.get(ws_url, headers=ws_headers, timeout=20)
    #print(f"DEBUG: Request Response: {resp.json()}")
    
    values = []
    try:
        values = (resp.json() or {}).get('value', [])
    except Exception:
        values = []
    
    #print(f"DEBUG: web_ms_picker.py Values: {values}")
    worksheets = []
    for w in values:
        #print(f"DEBUG: Individual worksheet: {w}")
        worksheets.append({
            'ms_id': w.get('id'),
            'name': w.get('name'),
            'ms_o_data_id': w.get('@odata.id'),
            'position': w.get('position'),
            'visibility': w.get('visibility')
        })

    #print(f"DEBUG: web_ms_picker.py Worksheets: {worksheets}")
    return render_template('ms_worksheet_picker.html', worksheets=worksheets)
