"""
Module for address web.
"""

# python standard library imports


# third party imports
from flask import Blueprint, render_template


# local imports
from business import bus_address


address_web_bp = Blueprint('address_web', __name__)


@address_web_bp.route('/addresses', methods=['GET'])
def list_addresses_route():
    """
    Retrieves the addresses route.
    """
    _addresses = []
    get_addresses_bus_response = bus_address.get_addresses()
    if get_addresses_bus_response.success:
        _addresses = get_addresses_bus_response.data

    return render_template('address/list.html', addresses=_addresses)


@address_web_bp.route('/address/create', methods=['GET'])
def create_address_route():
    """
    Retrieves the create address route.
    """
    return render_template('address/create.html')


@address_web_bp.route('/address/<address_guid>', methods=['GET'])
def view_address_route(address_guid):
    """
    Returns the address by guid route.
    """
    _address = {}
    get_address_bus_response = bus_address.get_address_by_guid(address_guid)
    if get_address_bus_response.success:
        _address = get_address_bus_response.data
    else:
        _address = {}

    return render_template('address/view.html', address=_address)


@address_web_bp.route('/address/<address_guid>/edit', methods=['GET'])
def edit_address_route(address_guid):
    """
    Returns the address edit route.
    """
    _address = {}
    get_address_bus_response = bus_address.get_address_by_guid(address_guid)
    if get_address_bus_response.success:
        _address = get_address_bus_response.data
    else:
        _address = {}

    return render_template('address/edit.html', address=_address)
