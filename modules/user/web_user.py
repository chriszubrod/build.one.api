"""
Module for user web.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# third party imports
from flask import Blueprint, render_template, redirect, url_for, flash

# local imports
from integrations.ms.auth import bus_ms_auth
from modules.contact import bus_contact
from modules.role import bus_role
from modules.user import bus_user
from utils.auth_help import requires_auth

web_user_bp = Blueprint(
    'web_user',
    __name__
)


@web_user_bp.route('/users', methods=['GET'])
#@requires_auth()
def list_users_route():
    """
    Returns the route for the users page.
    """
    try:
        get_users_bus_response = bus_user.get_users()
        if get_users_bus_response.success:
            users = get_users_bus_response.data
        else:
            users = []
            flash(get_users_bus_response.message, 'error')
            print(f'\nError: {get_users_bus_response.message}\n')

        print(f'\nusers: {users}\n')
        return render_template('user/user_list.html', users=users)

    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return f'Error: {e}', 500


@web_user_bp.route('/user/create', methods=['GET', 'POST'])
#@requires_auth()
def create_user_route():
    """
    Returns the user new route.
    """
    try:
        get_roles_bus_response = bus_role.get_roles()
        if get_roles_bus_response.success:
            roles = get_roles_bus_response.data
        else:
            roles = []
            flash(get_roles_bus_response.message, 'error')
            print(f'\nError loading roles: {get_roles_bus_response.message}\n')
        return render_template('user/user_create.html', roles=roles)

    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return f'Error: {e}', 500


@web_user_bp.route('/user/<user_guid>', methods=['GET'])
#@requires_auth()
def view_user_route(user_guid):
    """
    Returns the user view route.
    """
    try:
        get_user_bus_response = bus_user.get_user_by_guid(
            user_guid=user_guid
        )
        if get_user_bus_response.success:
            user = get_user_bus_response.data
            print(f'\nUser: {user}\n')

            get_roles_bus_response = bus_role.get_roles()
            if get_roles_bus_response.success:
                roles = get_roles_bus_response.data
            else:
                roles = []
                flash(get_roles_bus_response.message, 'error')
                print(f'\nError loading roles: {get_roles_bus_response.message}\n')

            return render_template('user/user_view.html', user=user, roles=roles)
        else:
            flash(get_user_bus_response.message, 'error')
            return render_template('shared/layout/error.html', error=get_user_bus_response.message), 404
    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return f'Error: {e}', 500


@web_user_bp.route('/user/<user_guid>/edit', methods=['GET', 'POST'])
#@requires_auth()
def edit_user_route(user_guid):
    """
    Returns the user edit route.
    """
    try:
        get_user_bus_response = bus_user.get_user_by_guid(
            user_guid=user_guid
        )
        if get_user_bus_response.success:
            user = get_user_bus_response.data
            print(f'\nUser: {user}\n')

            get_roles_bus_response = bus_role.get_roles()
            if get_roles_bus_response.success:
                roles = get_roles_bus_response.data
            else:
                roles = []
                flash(get_roles_bus_response.message, 'error')
                print(f'\nError loading roles: {get_roles_bus_response.message}\n')

            return render_template('user/user_edit.html', user=user, roles=roles)
        else:
            flash(get_user_bus_response.message, 'error')
            return render_template('shared/layout/error.html', error=get_user_bus_response.message), 404
    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return f'Error: {e}', 500
