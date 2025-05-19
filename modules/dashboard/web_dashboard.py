"""
Module for dashboard.
"""

# python standard library imports
from datetime import datetime

# third party imports
from flask import Blueprint, render_template, session, request

# local imports
from modules.module import bus_module
from utils.auth_help import requires_auth


web_dashboard_bp = Blueprint('web_dashboard', __name__, template_folder='templates')


@web_dashboard_bp.route('/dashboard', methods=['GET'])
@requires_auth()
def dashboard_route():
    """
    Returns the dashboard route for the application.
    """
    
    # TODO: This is a temporary solution to get the modules.
    # TODO: Need to update to retrieve modules authorized for the user.
    get_modules_response = bus_module.get_modules()
    if get_modules_response.success:
        _modules = get_modules_response.data
    else:
        _modules = []

    return render_template('dashboard_view.html', modules=_modules)



