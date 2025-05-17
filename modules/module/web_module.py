"""
Module for system module web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from modules.module import bus_module


web_module_bp = Blueprint('web_module', __name__, template_folder='templates')


@web_module_bp.route('/modules', methods=['GET'])
def list_modules_route():
    """
    Returns the route for the modules page.
    """
    _modules = []
    get_modules_bus_response = bus_module.get_modules()
    if get_modules_bus_response.success:
        _modules = get_modules_bus_response.data

    print(_modules)
    return render_template('module_list.html', modules=_modules)


@web_module_bp.route('/module/create', methods=['GET'])
def create_module_route():
    """
    Returns the module create route for the application.
    """
    return render_template('module_create.html')


@web_module_bp.route('/module/<module_guid>', methods=['GET'])
def view_module_route(module_guid):
    """
    Returns the module by guid route.
    """
    _module = {}
    get_module_bus_response = bus_module.get_module_by_guid(module_guid)
    if get_module_bus_response.success:
        _module = get_module_bus_response.data
    else:
        _module = {}

    return render_template('module_view.html', module=_module)


@web_module_bp.route('/module/<module_guid>/edit', methods=['GET'])
def edit_module_route(module_guid):
    """
    Returns the module edit route.
    """
    _module = {}
    get_module_bus_response = bus_module.get_module_by_guid(module_guid)
    if get_module_bus_response.success:
        _module = get_module_bus_response.data
    else:
        _module = {}

    return render_template('module_edit.html', module=_module)
