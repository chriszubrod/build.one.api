"""
Module for vendor web.
"""
# python standard library imports
import html

# third party imports
from flask import Blueprint, render_template, flash

# local imports
from modules.vendor import bus_vendor
from modules.vendor_type import bus_vendor_type
from utils.auth_help import requires_auth


web_vendor_bp = Blueprint('web_vendor', __name__, template_folder='templates')


@web_vendor_bp.route('/vendors', methods=['GET'])
#@requires_auth()
def list_vendors_route():
    try:
        get_vendors_bus_response = bus_vendor.get_vendors()
        if get_vendors_bus_response.success:
            vendors = get_vendors_bus_response.data
            for vendor in vendors:
                if vendor.name:
                    vendor.name = html.unescape(vendor.name)
                if vendor.abbreviation:
                    vendor.abbreviation = html.unescape(vendor.abbreviation)
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
        vendor = None
        get_vendor_bus_response = bus_vendor.get_vendor_by_guid(
            vendor_guid=vendor_guid
        )
        if get_vendor_bus_response.success:
            vendor = get_vendor_bus_response.data
        
        vendor_type = None
        get_vendor_type_bus_response = bus_vendor_type.get_vendor_type_by_id(vendor_type_id=vendor.vendor_type_id)
        if get_vendor_type_bus_response.success:
            vendor_type = get_vendor_type_bus_response.data
        
        return render_template('vendor/vendor_view.html', vendor=vendor, vendor_type=vendor_type)

    except Exception as e:
        print(f'\nError: {e}\n')
        flash(str(e), 'error')
        return render_template('shared/layout/error.html', error=str(e)), 500


@web_vendor_bp.route('/vendor/<vendor_guid>/edit', methods=['GET'])
#@requires_auth()
def edit_vendor_route(vendor_guid):
    """
    Returns the vendor edit route.
    """
    try:
        vendor = None
        get_vendor_bus_response = bus_vendor.get_vendor_by_guid(
            vendor_guid=vendor_guid
        )
        if get_vendor_bus_response.success:
            vendor = get_vendor_bus_response.data

        
        vendor_types = None
        get_vendor_types_bus_response = bus_vendor_type.get_vendor_types()
        if get_vendor_types_bus_response.success:
            vendor_types = get_vendor_types_bus_response.data
        
        return render_template('vendor/vendor_edit.html', vendor=vendor, vendor_types=vendor_types)

    except Exception as e:
        print(f'\nError: {e}\n')
        flash(str(e), 'error')
        return render_template('shared/layout/error.html', error=str(e)), 500
