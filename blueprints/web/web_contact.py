"""
Module for contact web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from business import bus_contact


contact_web_bp = Blueprint('contact_web', __name__)


@contact_web_bp.route('/contacts', methods=['GET'])
def list_contacts_route():
    """
    Returns the route for the contacts page.
    """
    _contacts = []
    get_contacts_bus_response = bus_contact.get_contacts()
    if get_contacts_bus_response.success:
        _contacts = get_contacts_bus_response.data

    print(_contacts)
    return render_template('contact/list.html', contacts=_contacts)


@contact_web_bp.route('/contact/create', methods=['GET'])
def create_contact_route():
    """
    Returns the contact create route for the application.
    """
    return render_template('contact/create.html')


@contact_web_bp.route('/contact/<contact_guid>', methods=['GET'])
def view_contact_route(contact_guid):
    """
    Returns the contact by guid route.
    """
    _contact = {}
    get_contact_bus_response = bus_contact.get_contact_by_guid(contact_guid)
    if get_contact_bus_response.success:
        _contact = get_contact_bus_response.data
    else:
        _contact = {}

    return render_template('contact/view.html', contact=_contact)


@contact_web_bp.route('/contact/<contact_guid>/edit', methods=['GET'])
def edit_contact_route(contact_guid):
    """
    Returns the contact edit route.
    """
    _contact = {}
    get_contact_bus_response = bus_contact.get_contact_by_guid(contact_guid)
    if get_contact_bus_response.success:
        _contact = get_contact_bus_response.data
    else:
        _contact = {}

    return render_template('contact/edit.html', contact=_contact)
