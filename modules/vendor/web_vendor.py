"""
Module for vendor web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from modules.vendor import bus_vendor
from utils.auth_help import requires_auth


web_vendor_bp = Blueprint('web_vendor', __name__, template_folder='templates')


@web_vendor_bp.route('/vendors', methods=['GET'])
@requires_auth()
def list_vendors_route():
    """
    Returns the route for the vendors page.
    """
    _vendors = []
    get_vendors_bus_response = bus_vendor.get_vendors()
    if get_vendors_bus_response.success:
        _vendors = get_vendors_bus_response.data

    return render_template('vendor_list.html', vendors=_vendors)


@web_vendor_bp.route('/vendor/create', methods=['GET'])
@requires_auth()
def create_vendor_route():
    """
    Returns the vendor create route for the application.
    """
    return render_template('vendor_create.html')


@web_vendor_bp.route('/vendor/<vendor_guid>', methods=['GET'])
@requires_auth()
def view_vendor_route(vendor_guid):
    """
    Returns the vendor by guid route.
    """
    _vendor = {}
    get_vendor_bus_response = bus_vendor.get_vendor_by_guid(vendor_guid)
    if get_vendor_bus_response.success:
        _vendor = get_vendor_bus_response.data
    else:
        _vendor = {}

    return render_template('vendor_view.html', vendor=_vendor)


@web_vendor_bp.route('/vendor/<vendor_guid>/edit', methods=['GET'])
@requires_auth()
def edit_vendor_route(vendor_guid):
    """
    Returns the vendor edit route.
    """
    _vendor = {}
    get_vendor_bus_response = bus_vendor.get_vendor_by_guid(vendor_guid)
    if get_vendor_bus_response.success:
        _vendor = get_vendor_bus_response.data
    else:
        _vendor = {}

    return render_template('vendor_edit.html', vendor=_vendor)
