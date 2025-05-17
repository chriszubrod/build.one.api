"""
Module for dashboard.
"""

# python standard library imports
from datetime import datetime

# third party imports
from flask import Blueprint, render_template, session, request

# local imports
from modules.module import bus_module
from utils.token_help import generate_token 


web_dashboard_bp = Blueprint('web_dashboard', __name__, template_folder='templates')


@web_dashboard_bp.route('/dashboard', methods=['GET'])
def dashboard_route():
    """
    Returns the dashboard route for the application.
    """

    # Generate a token.
    token = generate_token()

    # Store the new token in the session.
    session['token'] = token

    get_modules_response = bus_module.get_modules()
    if get_modules_response.success:
        _modules = get_modules_response.data
    else:
        _modules = []

    session['modules'] = _modules

    print('Dashboard route session:')
    print(session)
    return render_template('dashboard.html', modules=_modules)



