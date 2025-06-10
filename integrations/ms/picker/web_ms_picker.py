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
from integrations.ms.sites import bus_ms_sites
from utils.auth_help import requires_auth

web_ms_picker_bp = Blueprint('web_ms_picker', __name__, url_prefix='/ms/app', template_folder='templates')



@web_ms_picker_bp.route('/picker', methods=['GET'])
@requires_auth()
def picker_route():
    """
    Returns the route for the Microsoft Graph API Picker.
    """
    secrets = bus_ms_auth.get_ms_auth_by_user_id(session['user']['id'])
    if secrets.success:
        secrets = secrets.data
    else:
        print(f"Failed to get Microsoft Graph API integration: {secrets.message}")

    access_token = secrets.access_token

    get_ms_sites_bus_response = bus_ms_sites.get_ms_sites()
    if get_ms_sites_bus_response.success:
        _ms_sites = get_ms_sites_bus_response.data
    else:
        print(f"Failed to get Microsoft Graph API sites: {get_ms_sites_bus_response.message}")

    return render_template('ms_picker.html', ms_sites=_ms_sites)
