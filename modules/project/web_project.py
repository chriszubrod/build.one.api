"""
Module for project web.
"""
"""
Web routes for Project module aligned with Certificate module patterns.
"""

# python standard library imports


# third party imports
from flask import Blueprint, render_template, flash

# local imports
from modules.customer import bus_customer
from modules.project import bus_project
from utils.auth_help import requires_auth

web_project_bp = Blueprint('web_project', __name__, template_folder='templates')


@web_project_bp.route('/projects', methods=['GET'])
#@requires_auth()
def list_projects_route():
    """
    Returns the route for the projects page.
    """
    _projects = []
    resp = bus_project.get_projects()
    if resp.success:
        _projects = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('project/project_list.html', projects=_projects)


@web_project_bp.route('/project/create', methods=['GET'])
#@requires_auth()
def create_project_route():
    """Renders project create with customers list."""
    customers = []
    try:
        customer_resp = bus_customer.get_customers()
        if customer_resp.success:
            customers = customer_resp.data
    except Exception as e:
        flash(str(e), 'error')
    return render_template('project/project_create.html', customers=customers)


@web_project_bp.route('/project/<project_guid>', methods=['GET'])
#@requires_auth()
def view_project_route(project_guid):
    """Renders project view page."""
    _project = {}
    resp = bus_project.get_project_by_guid(project_guid)
    if resp.success:
        _project = resp.data
    else:
        flash(resp.message, 'error')

    _customer = {}
    if _project and getattr(_project, 'customer_id', None):
        cust_resp = bus_customer.get_customer_by_id(_project.customer_id)
        if cust_resp.success:
            _customer = cust_resp.data
    _intuit_customer = None
    try:
        ic_resp = bus_project.get_intuit_customer_by_project_guid(project_guid)
        if ic_resp.success:
            _intuit_customer = ic_resp.data
    except Exception as e:
        # Non-fatal for view; leave intuit customer as None
        pass
    status_choices = {
        'P': 'Planning',
        'D': 'Design',
        'B': 'Build',
        'C': 'Complete'
    }
    return render_template('project/project_view.html', project=_project, customer=_customer, status_choices=status_choices, intuit_customer=_intuit_customer)


@web_project_bp.route('/project/<project_guid>/edit', methods=['GET', 'POST'])
#@requires_auth()
def edit_project_route(project_guid):
    """Renders project edit page with customers list."""
    _project = {}
    resp = bus_project.get_project_by_guid(project_guid)
    if resp.success:
        _project = resp.data
    else:
        flash(resp.message, 'error')

    customers = []
    cust_resp = bus_customer.get_customers()
    if cust_resp.success:
        customers = cust_resp.data

    # Retrieve mapped Intuit customer for project
    _mapped_intuit_customer = None
    try:
        map_resp = bus_project.get_intuit_customer_by_project_guid(project_guid)
        if getattr(map_resp, 'success', False):
            _mapped_intuit_customer = map_resp.data
    except Exception:
        pass

    # Retrieve Intuit projects for selection
    intuit_projects = []
    try:
        ip_resp = bus_project.get_intuit_projects()
        if getattr(ip_resp, 'success', False):
            intuit_projects = ip_resp.data
    except Exception as e:
        flash(str(e), 'error')

    return render_template('project/project_edit.html', project=_project, customers=customers, mapped_intuit_customer=_mapped_intuit_customer, intuit_projects=intuit_projects)
