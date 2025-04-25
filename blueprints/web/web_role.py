"""
Module for role web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from business import bus_role


role_web_bp = Blueprint('role_web', __name__)


@role_web_bp.route('/roles', methods=['GET'])
def list_roles_route():
    """
    Returns the route for the roles page.
    """
    _roles = []
    get_roles_bus_response = bus_role.get_roles()
    if get_roles_bus_response.success:
        _roles = get_roles_bus_response.data

    print(_roles)
    return render_template('role/list.html', roles=_roles)


@role_web_bp.route('/role/create', methods=['GET'])
def create_role_route():
    """
    Returns the role create route for the application.
    """
    return render_template('role/create.html')


@role_web_bp.route('/role/<role_guid>', methods=['GET'])
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

    return render_template('role/view.html', role=_role)


@role_web_bp.route('/role/<role_guid>/edit', methods=['GET'])
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

    return render_template('role/edit.html', role=_role)
