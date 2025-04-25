"""
Module for entry type web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from business import bus_entry_type


entry_type_web_bp = Blueprint('entry_type_web', __name__)


@entry_type_web_bp.route('/entry-types', methods=['GET'])
def list_entry_types_route():
    """
    Returns the route for the entry types page.
    """
    _entry_types = []
    get_entry_types_bus_response = bus_entry_type.get_entry_types()
    if get_entry_types_bus_response.success:
        _entry_types = get_entry_types_bus_response.data

    print(_entry_types)
    return render_template('entry_type/list.html', entry_types=_entry_types)


@entry_type_web_bp.route('/entry-type/create', methods=['GET'])
def create_entry_type_route():
    """
    Returns the entry type create route for the application.
    """
    return render_template('entry_type/create.html')


@entry_type_web_bp.route('/entry-type/<entry_type_guid>', methods=['GET'])
def view_entry_type_route(entry_type_guid):
    """
    Returns the entry type by guid route.
    """
    _entry_type = {}
    get_entry_type_bus_response = bus_entry_type.get_entry_type_by_guid(entry_type_guid)
    if get_entry_type_bus_response.success:
        _entry_type = get_entry_type_bus_response.data
    else:
        _entry_type = {}

    return render_template('entry_type/view.html', entry_type=_entry_type)


@entry_type_web_bp.route('/entry-type/<entry_type_guid>/edit', methods=['GET'])
def edit_entry_type_route(entry_type_guid):
    """
    Returns the entry type edit route.
    """
    _entry_type = {}
    get_entry_type_bus_response = bus_entry_type.get_entry_type_by_guid(entry_type_guid)
    if get_entry_type_bus_response.success:
        _entry_type = get_entry_type_bus_response.data
    else:
        _entry_type = {}

    return render_template('entry_type/edit.html', entry_type=_entry_type)
