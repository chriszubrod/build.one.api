"""
Module for customer web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from modules.customer import bus_customer
from modules.project import bus_project
from utils.auth_help import requires_auth


web_customer_bp = Blueprint('web_customer', __name__, template_folder='templates')


@web_customer_bp.route('/customers', methods=['GET'])
@requires_auth()
def list_customers_route():
    """
    Returns the route for the customers page.
    """
    _customers = []
    get_customers_bus_response = bus_customer.get_customers()
    if get_customers_bus_response.success:
        _customers = get_customers_bus_response.data

    print(_customers)
    return render_template('customer_list.html', customers=_customers)


@web_customer_bp.route('/customer/create', methods=['GET'])
@requires_auth()
def create_customer_route():
    """
    Returns the customer create route for the application.
    """
    return render_template('customer_create.html')


@web_customer_bp.route('/customer/<customer_guid>', methods=['GET'])
@requires_auth()
def view_customer_route(customer_guid):
    """
    Returns the customer by guid route.
    """
    _customer = {}
    get_customer_bus_response = bus_customer.get_customer_by_guid(customer_guid)
    if get_customer_bus_response.success:
        _customer = get_customer_bus_response.data
    else:
        _customer = {}

    #print("Customer View:")
    #print(_customer)

    _projects = []
    get_projects_bus_response = bus_project.get_projects_by_customer_id(
        _customer.id
    )
    if get_projects_bus_response.success:
        _projects = get_projects_bus_response.data

    return render_template('customer_view.html', customer=_customer, projects=_projects)


@web_customer_bp.route('/customer/<customer_guid>/edit', methods=['GET'])
@requires_auth()
def edit_customer_route(customer_guid):
    """
    Returns the customer edit route.
    """
    _customer = {}
    get_customer_bus_response = bus_customer.get_customer_by_guid(customer_guid)
    if get_customer_bus_response.success:
        _customer = get_customer_bus_response.data
    else:
        _customer = {}

    return render_template('customer_edit.html', customer=_customer)
