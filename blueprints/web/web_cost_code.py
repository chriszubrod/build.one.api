"""
Module for cost code web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from business import bus_cost_code


cost_code_web_bp = Blueprint('cost_code_web', __name__)


@cost_code_web_bp.route('/cost-codes', methods=['GET'])
def list_cost_codes_route():
    """
    Returns the route for the cost codes page.
    """
    _cost_codes = []
    get_cost_codes_bus_response = bus_cost_code.get_cost_codes()
    if get_cost_codes_bus_response.success:
        _cost_codes = get_cost_codes_bus_response.data

    print(_cost_codes)
    return render_template('cost_code/list.html', cost_codes=_cost_codes)


@cost_code_web_bp.route('/cost-code/create', methods=['GET'])
def create_cost_code_route():
    """
    Returns the cost code create route for the application.
    """
    return render_template('cost_code/create.html')


@cost_code_web_bp.route('/cost-code/<cost_code_guid>', methods=['GET'])
def view_cost_code_route(cost_code_guid):
    """
    Returns the cost code by guid route.
    """
    _cost_code = {}
    get_cost_code_bus_response = bus_cost_code.get_cost_code_by_guid(cost_code_guid)
    if get_cost_code_bus_response.success:
        _cost_code = get_cost_code_bus_response.data
    else:
        _cost_code = {}

    return render_template('cost_code/view.html', cost_code=_cost_code)


@cost_code_web_bp.route('/cost-code/<cost_code_guid>/edit', methods=['GET'])
def edit_cost_code_route(cost_code_guid):
    """
    Returns the cost code edit route.
    """
    _cost_code = {}
    get_cost_code_bus_response = bus_cost_code.get_cost_code_by_guid(cost_code_guid)
    if get_cost_code_bus_response.success:
        _cost_code = get_cost_code_bus_response.data
    else:
        _cost_code = {}

    return render_template('cost_code/edit.html', cost_code=_cost_code)
