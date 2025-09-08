"""
Module for customer web, aligned with Project/Certificate modules.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template, flash

# local imports
from modules.customer import bus_customer
from modules.project import bus_project
from integrations.intuit import pers_intuit_customer
from datetime import datetime
# from utils.auth_help import requires_auth


web_customer_bp = Blueprint('web_customer', __name__, template_folder='templates')


@web_customer_bp.route('/customers', methods=['GET'])
#@requires_auth()
def list_customers_route():
    """Renders the customers list page."""
    _customers = []
    resp = bus_customer.get_customers()
    if resp.success:
        _customers = resp.data
    else:
        flash(resp.message, 'error')

    return render_template('customer/customer_list.html', customers=_customers)


@web_customer_bp.route('/customer/create', methods=['GET'])
#@requires_auth()
def create_customer_route():
    """Renders the customer create page."""
    _intuit_customers = []
    try:
        iresp = bus_customer.get_available_intuit_customers_for_mapping()
        if getattr(iresp, 'success', False):
            _intuit_customers = iresp.data
    except Exception as e:
        flash(str(e), 'error')
    return render_template('customer/customer_create.html', intuit_customers=_intuit_customers)


@web_customer_bp.route('/customer/<customer_guid>', methods=['GET'])
#@requires_auth()
def view_customer_route(customer_guid):
    """Renders the customer view page."""

    _customer = {}
    resp = bus_customer.get_customer_by_guid(customer_guid)
    if resp.success:
        _customer = resp.data
    else:
        flash(resp.message, 'error')

    _projects = []
    if getattr(_customer, 'id', None):
        proj_resp = bus_project.get_projects_by_customer_id(_customer.id)
        if proj_resp.success:
            _projects = proj_resp.data

    # Get mapped Intuit customer
    _intuit_customer = None
    if getattr(_customer, 'id', None):
        intuit_resp = bus_customer.get_mapped_intuit_customer_by_customer_id(_customer.id)
        print(f"Intuit response: {intuit_resp.message}")
        if intuit_resp.success:
            _intuit_customer = intuit_resp.data

    return render_template('customer/customer_view.html', customer=_customer, projects=_projects, intuit_customer=_intuit_customer)


@web_customer_bp.route('/customer/<customer_guid>/edit', methods=['GET'])
#@requires_auth()
def edit_customer_route(customer_guid):
    """Renders the customer edit page."""
    _customer = {}
    resp = bus_customer.get_customer_by_guid(customer_guid)
    if resp.success:
        _customer = resp.data
    else:
        flash(resp.message, 'error')
    
    # Get mapped Intuit customer
    _intuit_customer = None
    if getattr(_customer, 'id', None):
        intuit_resp = bus_customer.get_mapped_intuit_customer_by_customer_id(_customer.id)
        print(f"Intuit response: {intuit_resp.message}")
        if intuit_resp.success:
            _intuit_customer = intuit_resp.data

    # Get available Intuit customers for mapping
    _intuit_customers = []
    resp = bus_customer.get_available_intuit_customers_for_mapping()
    if resp.success:
        _intuit_customers = resp.data
    else:
        flash(resp.message, 'error')
    print(f"Intuit customers: {_intuit_customers}")
    
    return render_template('customer/customer_edit.html', customer=_customer, mapped_intuit_customer=_intuit_customer, intuit_customers=_intuit_customers)
