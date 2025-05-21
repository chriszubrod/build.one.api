"""
Module for auth web.
"""

# python standard library imports


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
from modules.module import bus_module
from utils.auth_help import requires_auth


web_auth_bp = Blueprint('web_auth', __name__, template_folder='templates')


@web_auth_bp.route('/register', methods=['GET'])
def register_route():
    """Returns the register route."""
    return render_template('register.html')


@web_auth_bp.route('/login', methods=['GET', 'POST'])
def login_route():
    """
    Handle web session setup after API login
    or
    Returns the login route
    """
    # If request method is POST, handle web session setup
    if request.method == 'POST':
        try:
            # Get data from the form
            token = request.form.get('token')
            username = request.form.get('username')
            user_id = request.form.get('user_id')

            if not all([token, username, user_id]):
                return redirect(url_for('auth_web.login_route'))

            # Set up session
            session.clear()

            # Add session variables
            session['logged_in'] = True
            session['token'] = token
            session['username'] = username
            session['user_id'] = user_id

            return redirect(url_for('web_dashboard.dashboard_route'))

        except Exception:
            return redirect(url_for('web_auth.login_route'))

    # If request method is GET, return the login route
    return render_template('login.html')


@web_auth_bp.route('/logout', methods=['GET'])
@requires_auth()
def logout_route():
    """Logs out the user."""
    session.clear()
    return redirect(url_for('web_auth.login_route'))
