"""
Module for project web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from modules.customer import bus_customer
from modules.project import bus_project
from utils.auth_help import requires_auth

web_project_bp = Blueprint('web_project', __name__, template_folder='templates')


@web_project_bp.route('/projects', methods=['GET'])
@requires_auth()
def list_projects_route():
    """
    Returns the route for the projects page.
    """
    _projects = []
    get_projects_bus_response = bus_project.get_projects()
    if get_projects_bus_response.success:
        _projects = get_projects_bus_response.data

    #print(_projects)
    return render_template('list.html', projects=_projects)


@web_project_bp.route('/project/create', methods=['GET'])
@requires_auth()
def create_project_route():
    """
    Returns the project create route for the application.
    """
    return render_template('create.html')


@web_project_bp.route('/project/<project_guid>', methods=['GET'])
@requires_auth()
def view_project_route(project_guid):
    """
    Returns the project by guid route.
    """
    _project = {}
    get_project_bus_response = bus_project.get_project_by_guid(project_guid)
    if get_project_bus_response.success:
        _project = get_project_bus_response.data
    else:
        _project = {}

    _customer = {}
    get_customer_bus_response = bus_customer.get_customer_by_id(_project.customer_id)
    if get_customer_bus_response.success:
        _customer = get_customer_bus_response.data
    else:
        _customer = {}

    return render_template('view.html', project=_project, customer=_customer)


@web_project_bp.route('/project/<project_guid>/edit', methods=['GET'])
@requires_auth()
def edit_project_route(project_guid):
    """
    Returns the project edit route.
    """
    _project = {}
    get_project_bus_response = bus_project.get_project_by_guid(project_guid)
    if get_project_bus_response.success:
        _project = get_project_bus_response.data
    else:
        _project = {}

    return render_template('edit.html', project=_project)
