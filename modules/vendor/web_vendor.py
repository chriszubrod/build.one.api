"""
Module for vendor web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template, flash

# local imports
from modules.vendor import bus_vendor
from utils.auth_help import requires_auth


web_vendor_bp = Blueprint(
    'web_vendor',
    __name__
)


@web_vendor_bp.route('/vendors', methods=['GET'])
#@requires_auth()
def list_vendors_route():
    try:
        get_vendors_bus_response = bus_vendor.get_vendors()
        if get_vendors_bus_response.success:
            vendors = get_vendors_bus_response.data
        else:
            vendors = []
            flash(get_vendors_bus_response.message, 'error')
            print(f'\nError: {get_vendors_bus_response.message}\n')

        print(f'\nvendors: {vendors}\n')  

        return render_template('vendor/vendor_list.html', vendors=vendors)

    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return f'Error: {e}', 500


@web_vendor_bp.route('/vendor/create', methods=['GET'])
#@requires_auth()
def create_vendor_route():
    """
    Returns the vendor create route for the application.
    """
    try:
        return render_template('vendor/vendor_create.html')
    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return f'Error: {e}', 500


@web_vendor_bp.route('/vendor/<vendor_guid>', methods=['GET'])
#@requires_auth()
def view_vendor_route(vendor_guid):
    """
    Returns the vendor by guid route.
    """
    try:
        get_vendor_bus_response = bus_vendor.get_vendor_by_guid(
            vendor_guid=vendor_guid
        )
        if get_vendor_bus_response.success:
            vendor = get_vendor_bus_response.data
            print(f'\nVendor: {vendor}\n')
            return render_template('vendor/vendor_view.html', vendor=vendor)
        else:
            flash(get_vendor_bus_response.message, 'error')
            return render_template('shared/layout/error.html', error=get_vendor_bus_response.message), 404
    except Exception as e:
        flash(str(e), 'error')
        return render_template('shared/layout/error.html', error=str(e)), 500


@web_vendor_bp.route('/vendor/<vendor_guid>/edit', methods=['GET'])
#@requires_auth()
def edit_vendor_route(vendor_guid):
    """
    Returns the vendor edit route.
    """
    try:
        get_vendor_bus_response = bus_vendor.get_vendor_by_guid(
            vendor_guid=vendor_guid
        )
        if get_vendor_bus_response.success:
            vendor = get_vendor_bus_response.data
            print(f'\nVendor: {vendor}\n')
            return render_template('vendor/vendor_edit.html', vendor=vendor)
        else:
            print(f'\nError: {get_vendor_bus_response.message}\n')
            flash(get_vendor_bus_response.message, 'error')
            return render_template('shared/layout/error.html', error=get_vendor_bus_response.message), 404
    except Exception as e:
        print(f'\nError: {e}\n')
        flash(str(e), 'error')
        return render_template('shared/layout/error.html', error=str(e)), 500
