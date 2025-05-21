"""
Module for role web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from modules.role import bus_role
from utils.auth_help import requires_auth

web_role_bp = Blueprint('web_role', __name__, template_folder='templates')


@web_role_bp.route('/roles', methods=['GET'])
@requires_auth()
def list_roles_route():
    """
    Returns the route for the roles page.
    """
    _roles = []
    get_roles_bus_response = bus_role.get_roles()
    if get_roles_bus_response.success:
        _roles = get_roles_bus_response.data

    print(_roles)
    return render_template('role_list.html', roles=_roles)


@web_role_bp.route('/role/create', methods=['GET'])
@requires_auth()
def create_role_route():
    """
    Returns the role create route for the application.
    """
    return render_template('role_create.html')


@web_role_bp.route('/role/<role_guid>', methods=['GET'])
@requires_auth()
def view_role_route(role_guid):
    """
    Returns the role by guid route.
    """
    _role = {}
    get_role_bus_response = bus_role.get_role_by_guid(role_guid)
    if get_role_bus_response.success:
        _role = get_role_bus_response.data
    else:
        _role = {}

    return render_template('role_view.html', role=_role)


@web_role_bp.route('/role/<role_guid>/edit', methods=['GET'])
@requires_auth()
def edit_role_route(role_guid):
    """
    Returns the role edit route.
    """
    _role = {}
    get_role_bus_response = bus_role.get_role_by_guid(role_guid)
    if get_role_bus_response.success:
        _role = get_role_bus_response.data
    else:
        _role = {}

    return render_template('role_edit.html', role=_role)
