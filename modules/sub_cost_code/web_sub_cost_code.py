"""
Module for sub cost code web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template, flash

# local imports
from modules.cost_code import bus_cost_code
from modules.sub_cost_code import bus_sub_cost_code
from integrations.intuit.services import bus_intuit_item
from utils.auth_help import requires_auth

web_sub_cost_code_bp = Blueprint('web_sub_cost_code', __name__)


@web_sub_cost_code_bp.route('/sub-cost-codes', methods=['GET'])
#@requires_auth()
def list_sub_cost_codes_route():
    """
    Returns the route for the sub cost codes page.
    """
    try:
        get_cost_codes_bus_response = bus_cost_code.get_cost_codes()
        if get_cost_codes_bus_response.success:
            cost_codes = get_cost_codes_bus_response.data
        else:
            cost_codes = []
            flash(get_cost_codes_bus_response.message, 'error')
            print(f'\nError: {get_cost_codes_bus_response.message}')

        get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
        if get_sub_cost_codes_bus_response.success:
            sub_cost_codes = get_sub_cost_codes_bus_response.data
        else:
            sub_cost_codes = []
            flash(get_sub_cost_codes_bus_response.message, 'error')
            print(f'\nError: {get_sub_cost_codes_bus_response.message}')

        return render_template('sub_cost_code/sub_cost_code_list.html', cost_codes=cost_codes, sub_cost_codes=sub_cost_codes)
    
    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}')
        return f'Error: {e}', 500


@web_sub_cost_code_bp.route('/sub-cost-code/create', methods=['GET'])
#@requires_auth()
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
#@requires_auth()
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

    _mapped_intuit_item = {}
    get_mapped_intuit_item_bus_response = bus_sub_cost_code.\
        get_mapped_intuit_item_by_sub_cost_code_id(
            sub_cost_code_id=_sub_cost_code.id
        )

    if get_mapped_intuit_item_bus_response.success:
        get_intuit_item_bus_response = bus_intuit_item.get_intuit_item_by_id(
            intuit_item_id=get_mapped_intuit_item_bus_response.data.intuit_item_id
        )
        if get_intuit_item_bus_response.success:
            _mapped_intuit_item = get_intuit_item_bus_response.data
        else:
            _mapped_intuit_item = {}
    else:
        _mapped_intuit_item = {}

    return render_template(
        'sub_cost_code/sub_cost_code_view.html',
        cost_codes=_cost_codes,
        sub_cost_code=_sub_cost_code,
        mapped_intuit_item=_mapped_intuit_item
    )


@web_sub_cost_code_bp.route('/sub-cost-code/<sub_cost_code_guid>/edit', methods=['GET'])
#@requires_auth()
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
