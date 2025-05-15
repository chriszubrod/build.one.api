"""
Module for user web.
"""

# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from modules.contact import bus_contact
from modules.role import bus_role
from modules.user import bus_user


web_user_bp = Blueprint('web_user', __name__, template_folder='templates')


@web_user_bp.route('/users', methods=['GET'])
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


@web_user_bp.route('/user/create', methods=['GET'])
def create_user_route():
    """
    Returns the user new route.
    """
    _roles = []
    get_roles_bus_response = bus_role.get_roles()
    if get_roles_bus_response.success:
        _roles = get_roles_bus_response.data
    else:
        _roles = []

    return render_template('user_create.html', roles=_roles)


@web_user_bp.route('/user/<user_guid>', methods=['GET'])
def view_user_route(user_guid):
    """
    Returns the user by guid route.
    """
    _contact = {}
    _roles = {}
    _user = {}
    get_user_bus_response = bus_user.get_user_by_guid(user_guid)
    if get_user_bus_response.success:
        _user = get_user_bus_response.data
        get_contact_bus_response = bus_contact.get_contact_by_user_id(_user.id)
        if get_contact_bus_response.success:
            _contact = get_contact_bus_response.data
        else:
            _contact = {}
    else:
        _user = {}

    get_roles_bus_response = bus_role.get_roles()
    if get_roles_bus_response.success:
        _roles = get_roles_bus_response.data
    else:
        _roles = []

    return render_template('user_view.html', user=_user, contact=_contact, roles=_roles)


@web_user_bp.route('/user/<user_guid>/edit', methods=['GET'])
def edit_user_route(user_guid):
    """
    Returns the user edit route.
    """
    _contact = {}
    _roles = {}
    _user = {}
    get_user_bus_response = bus_user.get_user_by_guid(user_guid)
    if get_user_bus_response.success:
        _user = get_user_bus_response.data
        get_contact_bus_response = bus_contact.get_contact_by_user_id(_user.id)
        if get_contact_bus_response.success:
            _contact = get_contact_bus_response.data
        else:
            _contact = {}
    else:
        _user = {}

    get_roles_bus_response = bus_role.get_roles()
    if get_roles_bus_response.success:
        _roles = get_roles_bus_response.data
    else:
        _roles = []

    return render_template('user_edit.html', user=_user, contact=_contact, roles=_roles)
