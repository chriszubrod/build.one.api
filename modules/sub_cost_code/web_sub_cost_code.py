"""
Module for sub cost code web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from modules.cost_code import bus_cost_code
from modules.sub_cost_code import bus_sub_cost_code


web_sub_cost_code_bp = Blueprint('web_sub_cost_code', __name__, template_folder='templates')


@web_sub_cost_code_bp.route('/sub-cost-codes', methods=['GET'])
def list_sub_cost_codes_route():
    """
    Returns the route for the sub cost codes page.
    """

    _cost_codes = []
    get_cost_codes_bus_response = bus_cost_code.get_cost_codes()
    if get_cost_codes_bus_response.success:
        _cost_codes = get_cost_codes_bus_response.data

    _sub_cost_codes = []
    get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
    if get_sub_cost_codes_bus_response.success:
        _sub_cost_codes = get_sub_cost_codes_bus_response.data

    return render_template('sub_cost_code_list.html', cost_codes=_cost_codes, sub_cost_codes=_sub_cost_codes)


@web_sub_cost_code_bp.route('/sub-cost-code/create', methods=['GET'])
def create_sub_cost_code_route():
    """
    Returns the sub cost code create route for the application.
    """

    _cost_codes = []
    get_cost_codes_bus_response = bus_cost_code.get_cost_codes()
    if get_cost_codes_bus_response.success:
        _cost_codes = get_cost_codes_bus_response.data

    return render_template('sub_cost_code_create.html', cost_codes=_cost_codes)


@web_sub_cost_code_bp.route('/sub-cost-code/<sub_cost_code_guid>', methods=['GET'])
def view_sub_cost_code_route(sub_cost_code_guid):
    """
    Returns the sub cost code by guid route.
    """

    _cost_codes = []
    get_cost_codes_bus_response = bus_cost_code.get_cost_codes()
    if get_cost_codes_bus_response.success:
        _cost_codes = get_cost_codes_bus_response.data


    _sub_cost_code = {}
    get_sub_cost_code_bus_response = bus_sub_cost_code.get_sub_cost_code_by_guid(sub_cost_code_guid)
    if get_sub_cost_code_bus_response.success:
        _sub_cost_code = get_sub_cost_code_bus_response.data
    else:
        _sub_cost_code = {}

    return render_template('sub_cost_code_view.html', cost_codes=_cost_codes, sub_cost_code=_sub_cost_code)


@web_sub_cost_code_bp.route('/sub-cost-code/<sub_cost_code_guid>/edit', methods=['GET'])
def edit_sub_cost_code_route(sub_cost_code_guid):
    """
    Returns the sub cost code edit route.
    """

    _cost_codes = []
    get_cost_codes_bus_response = bus_cost_code.get_cost_codes()
    if get_cost_codes_bus_response.success:
        _cost_codes = get_cost_codes_bus_response.data

    _sub_cost_code = {}
    get_sub_cost_code_bus_response = bus_sub_cost_code.get_sub_cost_code_by_guid(sub_cost_code_guid)
    if get_sub_cost_code_bus_response.success:
        _sub_cost_code = get_sub_cost_code_bus_response.data
    else:
        _sub_cost_code = {}

    return render_template('sub_cost_code_edit.html', cost_codes=_cost_codes, sub_cost_code=_sub_cost_code)
