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
from business import bus_module


auth_web_bp = Blueprint('auth_web', __name__)


@auth_web_bp.route('/register', methods=['GET'])
def register_route():
    """Returns the register route."""
    return render_template('auth/register.html')


@auth_web_bp.route('/login', methods=['GET', 'POST'])
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

            # Add modules
            get_modules_bus_response = bus_module.get_modules()
            if get_modules_bus_response.success:
                session['modules'] = get_modules_bus_response.data

            #print(f'Session: {session}')

            # Redirect to the dashboard
            return redirect(url_for('dashboard_web.dashboard_route'))

        except Exception:
            return redirect(url_for('auth_web.login_route'))

    # If request method is GET, return the login route
    return render_template('auth/login.html')


@auth_web_bp.route('/logout', methods=['GET'])
def logout_route():
    """Logs out the user."""
    session.clear()
    return redirect(url_for('auth_web.login_route'))


@auth_web_bp.route('/unauthorized', methods=['GET'])
def unauthorized_route():
    """Returns the unauthorized route."""
    return render_template('auth/unauthorized.html'), 401
