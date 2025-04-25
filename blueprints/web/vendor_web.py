"""
Module for vendor web.
"""

# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from business import bus_contact, bus_vendor


vendor_web_bp = Blueprint('vendor_web', __name__)


@vendor_web_bp.route('/vendors', methods=['GET'])
def list_vendors_route():
    """
    Returns the route for the vendors page.
    """
    _vendors = []
    get_vendors_bus_response = bus_vendor.get_vendors()
    if get_vendors_bus_response.success:
        _vendors = get_vendors_bus_response.data
    else:
        _vendors = []

    return render_template('vendor/list.html', vendors=_vendors)


@vendor_web_bp.route('/vendor/create', methods=['GET'])
def create_vendor_route():
    """
    Returns the vendor new route.
    """
    _contacts = []
    get_contacts_bus_response = bus_contact.get_contacts()
    if get_contacts_bus_response.success:
        _contacts = get_contacts_bus_response.data
    else:
        _contacts = []

    return render_template('vendor/create.html', contacts=_contacts)


@vendor_web_bp.route('/vendor/<vendor_guid>', methods=['GET'])
def view_vendor_route(vendor_guid):
    """
    Returns the vendor by guid route.
    """
    _contact = {}
    _vendor = {}
    get_vendor_bus_response = bus_vendor.get_vendor_by_guid(vendor_guid)
    if get_vendor_bus_response.success:
        _vendor = get_vendor_bus_response.data
        get_contact_bus_response = bus_contact.get_contact_by_user_id(_vendor.id)
        if get_contact_bus_response.success:
            _contact = get_contact_bus_response.data
        else:
            _contact = {}
    else:
        _vendor = {}

    get_contacts_bus_response = bus_contact.get_contacts()
    if get_contacts_bus_response.success:
        _contacts = get_contacts_bus_response.data
    else:
        _contacts = []

    return render_template('vendor/view.html', vendor=_vendor, contact=_contact, contacts=_contacts)


@vendor_web_bp.route('/vendor/<vendor_guid>/edit', methods=['GET'])
def edit_vendor_route(vendor_guid):
    """
    Returns the vendor edit route.
    """
    _contact = {}
    _vendor = {}
    get_vendor_bus_response = bus_vendor.get_vendor_by_guid(vendor_guid)
    if get_vendor_bus_response.success:
        _vendor = get_vendor_bus_response.data
        get_contact_bus_response = bus_contact.get_contact_by_user_id(_vendor.id)
        if get_contact_bus_response.success:
            _contact = get_contact_bus_response.data
        else:
            _contact = {}
    else:
        _vendor = {}

    get_contacts_bus_response = bus_contact.get_contacts()
    if get_contacts_bus_response.success:
        _contacts = get_contacts_bus_response.data
    else:
        _contacts = []

    return render_template('vendor/edit.html', vendor=_vendor, contact=_contact, contacts=_contacts)
