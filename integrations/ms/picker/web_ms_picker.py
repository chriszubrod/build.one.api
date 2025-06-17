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
from integrations.ms.auth import bus_ms_auth
from integrations.ms.drives import bus_ms_drives
from integrations.ms.sites import bus_ms_sites
from utils.auth_help import requires_auth

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
        print(f"Failed to get Microsoft Graph API sites: {get_ms_sites_bus_response.message}")

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
        print(f"Failed to get Microsoft Graph API drives: {get_ms_drives_bus_response.message}")

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
        print(f"Failed to get Microsoft Graph API drives children: {get_ms_drives_children_bus_response.message}")

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
        print(f"Failed to get Microsoft Graph API drives items children: {get_ms_drives_items_children_bus_response.message}")
    print(f"ms_drives_items_children: {_ms_drives_items_children}")
    return render_template('ms_drive_item_children_picker.html', ms_drives_items=_ms_drives_items_children, drive_id=drive_id, drive_item_id=drive_item_id)
