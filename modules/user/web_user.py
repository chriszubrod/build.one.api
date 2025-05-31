"""
Module for user web.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# third party imports
from flask import Blueprint, render_template, redirect, url_for, flash
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SelectField, SubmitField
from wtforms.validators import DataRequired, Email, Length

# local imports
from integrations.ms.auth import bus_ms_auth
from modules.contact import bus_contact
from modules.role import bus_role
from modules.user import bus_user
from utils.auth_help import requires_auth

web_user_bp = Blueprint('web_user', __name__, template_folder='templates')


class UserCreateForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    is_active = BooleanField('Active', validators=[DataRequired()])
    role = SelectField('Role', coerce=str, validators=[DataRequired()])
    submit = SubmitField('Create User')


class UserEditForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3)])
    role = SelectField('Role', coerce=str, validators=[DataRequired()])
    submit = SubmitField('Update User')


@web_user_bp.route('/users', methods=['GET'])
@requires_auth()
def list_users_route():
    """
    Returns the route for the users page.
    """
    _users = []
    get_users_bus_response = bus_user.get_users()
    if get_users_bus_response.success:
        _users = get_users_bus_response.data
    else:
        _users = []

    return render_template('user_list.html', users=_users)


@web_user_bp.route('/user/create', methods=['GET', 'POST'])
@requires_auth()
def create_user_route():
    """
    Returns the user new route.
    """
    # Get the submission datetime
    submission_datetime = datetime.now(tz.tzlocal()).strftime('%Y-%m-%d %H:%M:%S%z')
    submission_datetime = submission_datetime[:-2] + ':' + submission_datetime[-2:]

    form = UserCreateForm()

    _roles = []
    get_roles_bus_response = bus_role.get_roles()
    if get_roles_bus_response.success:
        _roles = get_roles_bus_response.data
        form.role.choices = [(role.guid, role.name) for role in _roles]
    else:
        flash('Error loading roles', 'error')
        _roles = []

    if form.validate_on_submit():   
        username = form.username.data
        password = form.password.data
        is_active = form.is_active.data
        role_guid = form.role.data

        bus_user_resp = bus_user.post_user(
            submission_datetime=submission_datetime,
            username=username,
            password=password,
            is_active=is_active,
            role_guid=role_guid
        )
        if bus_user_resp.success:
            flash('User created successfully', 'success')
            return redirect(url_for('web_user.list_users_route'))
        else:
            flash(f'Error creating user: {bus_user_resp.message}', {bus_user_resp.status_code})

    return render_template('user_create.html', form=form, roles=_roles)


@web_user_bp.route('/user/<user_guid>', methods=['GET'])
@requires_auth()
def view_user_route(user_guid):
    """
    Returns the user by guid route.
    """
    _contact = {}
    _roles = {}
    _user = {}
    _ms_auth = {}

    get_user_bus_response = bus_user.get_user_by_guid(user_guid)
    if get_user_bus_response.success:
        _user = get_user_bus_response.data

        get_contact_bus_response = bus_contact.get_contact_by_user_id(_user.id)
        if get_contact_bus_response.success:
            _contact = get_contact_bus_response.data
        else:
            flash('Error loading contact', 'error')
            _contact = {}

        get_ms_auth_bus_response = bus_ms_auth.get_ms_auth_by_user_id(_user.id)
        if get_ms_auth_bus_response.success:
            _ms_auth = get_ms_auth_bus_response.data
        else:
            flash('Error loading ms auth', 'error')
            _ms_auth = {}

    else:
        flash('Error loading user', 'error')
        _user = {}


    get_roles_bus_response = bus_role.get_roles()
    if get_roles_bus_response.success:
        _roles = get_roles_bus_response.data
    else:
        flash('Error loading roles', 'error')
        _roles = []

    return render_template('user_view.html', user=_user, contact=_contact, roles=_roles, ms_auth=_ms_auth)


@web_user_bp.route('/user/<user_guid>/edit', methods=['GET', 'POST'])
@requires_auth()
def edit_user_route(user_guid):
    """
    Returns the user edit route.
    """
    # Get the submission datetime
    submission_datetime = datetime.now(tz.tzlocal()).strftime('%Y-%m-%d %H:%M:%S%z')
    submission_datetime = submission_datetime[:-2] + ':' + submission_datetime[-2:]

    form = UserEditForm()

    _contact = {}
    _roles = {}
    _user = {}

    get_user_bus_response = bus_user.get_user_by_guid(user_guid)
    if get_user_bus_response.success:
        _user = get_user_bus_response.data
        form.username.data = _user.username

        get_contact_bus_response = bus_contact.get_contact_by_user_id(_user.id)
        if get_contact_bus_response.success:
            _contact = get_contact_bus_response.data
        else:
            flash('Error loading contact', 'error')
            _contact = {}
    else:
        flash('Error loading user', 'error')
        _user = {}

    get_roles_bus_response = bus_role.get_roles()
    if get_roles_bus_response.success:
        _roles = get_roles_bus_response.data
        form.role.choices = [(role.guid, role.name) for role in _roles]
    else:
        flash('Error loading roles', 'error')
        _roles = []

    if form.validate_on_submit():
        username = form.username.data
        role_guid = form.role.data

        bus_user_resp = bus_user.patch_user(
            user_guid=user_guid,
            username=username,
            role_guid=role_guid
        )
        if bus_user_resp.success:
            flash('User updated successfully', 'success')
            return redirect(url_for('web_user.view_user_route', user_guid=user_guid))
        else:
            flash(f'Error updating user: {bus_user_resp.message}', {bus_user_resp.status_code})

    form.username.data = _user.username
    form.role.data = _user.guid

    return render_template('user_edit.html', form=form, user=_user, contact=_contact, roles=_roles)
