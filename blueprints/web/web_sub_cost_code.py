"""
Module for sub cost code web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from business import bus_sub_cost_code


sub_cost_code_web_bp = Blueprint('sub_cost_code_web', __name__)


@sub_cost_code_web_bp.route('/sub-cost-codes', methods=['GET'])
def list_sub_cost_codes_route():
    """
    Returns the route for the sub cost codes page.
    """
    _sub_cost_codes = []
    get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
    if get_sub_cost_codes_bus_response.success:
        _sub_cost_codes = get_sub_cost_codes_bus_response.data

    print(_sub_cost_codes)
    return render_template('sub_cost_code/list.html', sub_cost_codes=_sub_cost_codes)


@sub_cost_code_web_bp.route('/sub-cost-code/create', methods=['GET'])
def create_sub_cost_code_route():
    """
    Returns the sub cost code create route for the application.
    """
    return render_template('sub_cost_code/create.html')


@sub_cost_code_web_bp.route('/sub-cost-code/<sub_cost_code_guid>', methods=['GET'])
def view_sub_cost_code_route(sub_cost_code_guid):
    """
    Returns the sub cost code by guid route.
    """
    _sub_cost_code = {}
    get_sub_cost_code_bus_response = bus_sub_cost_code.get_sub_cost_code_by_guid(sub_cost_code_guid)
    if get_sub_cost_code_bus_response.success:
        _sub_cost_code = get_sub_cost_code_bus_response.data
    else:
        _sub_cost_code = {}

    return render_template('sub_cost_code/view.html', sub_cost_code=_sub_cost_code)


@sub_cost_code_web_bp.route('/sub-cost-code/<sub_cost_code_guid>/edit', methods=['GET'])
def edit_sub_cost_code_route(sub_cost_code_guid):
    """
    Returns the sub cost code edit route.
    """
    _sub_cost_code = {}
    get_sub_cost_code_bus_response = bus_sub_cost_code.get_sub_cost_code_by_guid(sub_cost_code_guid)
    if get_sub_cost_code_bus_response.success:
        _sub_cost_code = get_sub_cost_code_bus_response.data
    else:
        _sub_cost_code = {}

    return render_template('sub_cost_code/edit.html', sub_cost_code=_sub_cost_code)
