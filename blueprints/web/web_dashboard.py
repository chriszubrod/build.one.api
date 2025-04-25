"""
Module for dashboard web.
"""

# python standard library imports


# third party imports
from flask import Blueprint, render_template, session


# local imports
from helper.auth_help import requires_auth


dashboard_web_bp = Blueprint('dashboard_web', __name__)


@dashboard_web_bp.route('/dashboard', methods=['GET'])
@requires_auth()
def dashboard_route():
    """
    Retrieves the dashboard route.
    """
    return render_template('shared/dashboard.html', modules=session.get('modules', []))
