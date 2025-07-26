"""
Module for vendor type web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template, flash

# local imports
from modules.vendor_type import bus_vendor_type
from utils.auth_help import requires_auth


web_vendor_type_bp = Blueprint(
    'web_vendor_type',
    __name__
)


@web_vendor_type_bp.route('/vendor-types', methods=['GET'])
#@requires_auth()
def list_vendor_types_route():
    """
    Returns the route for the vendor types page.
    """
    try:
        get_vendor_types_bus_response = bus_vendor_type.get_vendor_types()
        if get_vendor_types_bus_response.success:
            vendor_types = get_vendor_types_bus_response.data
        else:
            vendor_types = []
            flash(get_vendor_types_bus_response.message, 'error')
            print(f'\nError: {get_vendor_types_bus_response.message}\n')

        print(f'\nvendor_types: {vendor_types}\n')  

        return render_template('vendor_type/vendor_type_list.html', vendor_types=vendor_types)

    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return f'Error: {e}', 500


@web_vendor_type_bp.route('/vendor-type/create', methods=['GET'])
#@requires_auth()
def create_vendor_type_route():
    """
    Returns the route for the vendor type create page.
    """
    try:
        return render_template('vendor_type/vendor_type_create.html')
    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return f'Error: {e}', 500


@web_vendor_type_bp.route('/vendor-type/<vendor_type_guid>', methods=['GET'])
#@requires_auth()
def view_vendor_type_route(vendor_type_guid):
    """
    Returns the route for the vendor type view page.
    """
    try:
        get_vendor_type_bus_response = bus_vendor_type.get_vendor_type_by_guid(
            vendor_type_guid=vendor_type_guid
        )
        if get_vendor_type_bus_response.success:
            vendor_type = get_vendor_type_bus_response.data
            print(f'\nVendor Type: {vendor_type}\n')
            return render_template('vendor_type/vendor_type_view.html', vendor_type=vendor_type)
        else:
            flash(get_vendor_type_bus_response.message, 'error')
            return render_template('shared/layout/error.html', error=get_vendor_type_bus_response.message), 404
    except Exception as e:
        flash(str(e), 'error')
        return render_template('shared/layout/error.html', error=str(e)), 500


@web_vendor_type_bp.route('/vendor-type/<vendor_type_guid>/edit', methods=['GET'])
#@requires_auth()
def edit_vendor_type_route(vendor_type_guid):
    """
    Returns the vendor type edit route.
    """
    try:
        get_vendor_type_bus_response = bus_vendor_type.get_vendor_type_by_guid(
            vendor_type_guid=vendor_type_guid
        )
        if get_vendor_type_bus_response.success:
            vendor_type = get_vendor_type_bus_response.data
            print(f'\nVendor Type: {vendor_type}\n')
            return render_template('vendor_type/vendor_type_edit.html', vendor_type=vendor_type)
        else:
            print(f'\nError: {get_vendor_type_bus_response.message}\n')
            flash(get_vendor_type_bus_response.message, 'error')
            return render_template('shared/layout/error.html', error=get_vendor_type_bus_response.message), 404
    except Exception as e:
        print(f'\nError: {e}\n')
        flash(str(e), 'error')
        return render_template('shared/layout/error.html', error=str(e)), 500
