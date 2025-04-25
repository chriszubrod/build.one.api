"""
Module for bill web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template


# local imports
from business import (
    bus_bill_line_item,
    bus_bill,
    bus_entry_type,
    bus_project,
    bus_sub_cost_code,
    bus_vendor
)


web_bill_bp = Blueprint('web_bill', __name__)


@web_bill_bp.route('/bills', methods=['GET'])
def list_bills_route():
    """
    Returns the bills route for the application.
    """
    _bills = []
    get_bills_bus_response = bus_bill.get_bills()
    if get_bills_bus_response.success:
        _bills = get_bills_bus_response.data
    print(get_bills_bus_response.message)
    return render_template('bill/list.html', bills=_bills)


@web_bill_bp.route('/bill/create', methods=['GET'])
def create_bill_route():
    """
    Returns the bill create route for the application.
    """
    _vendors = []
    get_vendors_bus_response = bus_vendor.get_vendors()
    if get_vendors_bus_response.success:
        _vendors = get_vendors_bus_response.data
    else:
        _vendors = []

    _sub_cost_codes = []
    get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
    if get_sub_cost_codes_bus_response.success:
        _sub_cost_codes = get_sub_cost_codes_bus_response.data
    else:
        _sub_cost_codes = []

    _projects = []
    get_projects_bus_response = bus_project.get_projects()
    if get_projects_bus_response.success:
        _projects = get_projects_bus_response.data
    else:
        _projects = []

    return render_template(
        'bill/create.html',
        vendors=_vendors,
        sub_cost_codes=_sub_cost_codes,
        projects=_projects,
        bill_line_items=[]
    )


@web_bill_bp.route('/bill/<bill_guid>', methods=['GET'])
def view_bill_route(bill_guid):
    """
    Returns the bill by guid route.
    """
    _bill = {}
    get_bill_bus_response = bus_bill.get_bill_by_guid(bill_guid)
    if get_bill_bus_response.success:
        _bill = get_bill_bus_response.data
    else:
        _bill = {}

    _bill_line_items = []
    get_bill_line_items_bus_response = bus_bill_line_item.\
        get_bill_line_item_by_bill_id(_bill.id)
    if get_bill_line_items_bus_response.success:
        _bill_line_items = get_bill_line_items_bus_response.data
    else:
        _bill_line_items = []

    _vendors = []
    get_vendors_bus_response = bus_vendor.get_vendors()
    if get_vendors_bus_response.success:
        _vendors = get_vendors_bus_response.data
    else:
        _vendors = []

    _sub_cost_codes = []
    get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
    if get_sub_cost_codes_bus_response.success:
        _sub_cost_codes = get_sub_cost_codes_bus_response.data
    else:
        _sub_cost_codes = []

    _projects = []
    get_projects_bus_response = bus_project.get_projects()
    if get_projects_bus_response.success:
        _projects = get_projects_bus_response.data
    else:
        _projects = []

    return render_template(
        'bill/view.html',
        bill=_bill,
        bill_line_items=_bill_line_items,
        vendors=_vendors,
        projects=_projects,
        sub_cost_codes=_sub_cost_codes
    )


@web_bill_bp.route('/bill/<bill_guid>/edit', methods=['GET'])
def edit_bill_route(bill_guid):
    """
    Returns the bill edit route.
    """
    _bill = {}
    get_bill_bus_response = bus_bill.get_bill_by_guid(bill_guid)
    if get_bill_bus_response.success:
        _bill = get_bill_bus_response.data
    else:
        _bill = {}

    _bill_line_items = []
    get_bill_line_items_bus_response = bus_bill_line_item.\
        get_bill_line_item_by_bill_id(_bill.id)
    if get_bill_line_items_bus_response.success:
        _bill_line_items = get_bill_line_items_bus_response.data
    else:
        _bill_line_items = []

    _vendors = []
    get_vendors_bus_response = bus_vendor.get_vendors()
    if get_vendors_bus_response.success:
        _vendors = get_vendors_bus_response.data
    else:
        _vendors = []

    _sub_cost_codes = []
    get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
    if get_sub_cost_codes_bus_response.success:
        _sub_cost_codes = get_sub_cost_codes_bus_response.data
    else:
        _sub_cost_codes = []

    _projects = []
    get_projects_bus_response = bus_project.get_projects()
    if get_projects_bus_response.success:
        _projects = get_projects_bus_response.data
    else:
        _projects = []

    return render_template(
        'bill/edit.html',
        bill=_bill,
        bill_line_items=_bill_line_items,
        vendors=_vendors,
        projects=_projects,
        sub_cost_codes=_sub_cost_codes
    )
