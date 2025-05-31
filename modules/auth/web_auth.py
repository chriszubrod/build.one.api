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
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length

# local imports
from modules.auth import bus_auth
from modules.user import bus_user
from utils.auth_help import requires_auth
from utils.token_help import generate_token


web_auth_bp = Blueprint('web_auth', __name__, template_folder='templates')


class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    confirm_password = PasswordField('Confirm Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Login')


@web_auth_bp.route('/register', methods=['GET', 'POST'])
def register_route():
    """
    Handles the user registration.
    Handle the web session setup after API registration.
    or
    Returns the register route.
    """
    form = RegisterForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data
        confirm_password = form.confirm_password.data

        bus_auth_resp = bus_auth.post_auth_registration(username, password, confirm_password)
        if bus_auth_resp.success:

            _token = generate_token()
            if _token.get('success'):
                session['token'] = _token.get('data')

            _user = bus_user.authorize_user(username)
            if _user.success:
                _user_data = _user.data
                session['user'] = _user_data['user']
                print(f"Session: {session}")

                return redirect(url_for('web_dashboard.dashboard_route'))

        return bus_auth_resp.message, bus_auth_resp.status_code

    return render_template('register.html', form=form)


@web_auth_bp.route('/login', methods=['GET', 'POST'])
def login_route():
    """
    Handle web session setup after API login
    or
    Returns the login route
    """
    form = LoginForm()
    if form.validate_on_submit():
        username = form.username.data
        password = form.password.data

        bus_auth_resp = bus_auth.post_auth_login(username, password)
        if bus_auth_resp.success:

            _token = generate_token()
            if _token.get('success'):
                session['token'] = _token.get('data')

            _user = bus_user.authorize_user(username)
            if _user.success:
                _user_data = _user.data
                session['user'] = _user_data['user']
                print(f"Session: {session}")

                return redirect(url_for('web_dashboard.dashboard_route'))

        return bus_auth_resp.message, bus_auth_resp.status_code

    return render_template('login.html', form=form)


@web_auth_bp.route('/logout', methods=['GET'])
def logout_route():
    """
    Logs out the user.
    Clears the session and redirects to the login route.
    """
    session.clear()
    return redirect(url_for('web_auth.login_route'))
