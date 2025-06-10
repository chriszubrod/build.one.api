"""
Module for project web.
"""
# python standard library imports
from datetime import datetime
from dateutil import tz

# third party imports
from flask import (
    Blueprint,
    redirect,
    render_template,
    request,
    session,
    url_for
)
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length

# local imports
from modules.customer import bus_customer
from modules.project import bus_project
from utils.auth_help import requires_auth

web_project_bp = Blueprint('web_project', __name__, template_folder='templates')


class ProjectForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired(), Length(min=3)])
    abbreviation = StringField('Abbreviation', validators=[DataRequired(), Length(min=3)])
    status = SelectField('Status')
    customer_guid = SelectField('Customer')
    submit = SubmitField('Submit')


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
    return render_template('project_list.html', projects=_projects)


@web_project_bp.route('/project/create', methods=['GET'])
@requires_auth()
def create_project_route():
    """
    Returns the project create route for the application.
    """
    form = ProjectForm()
    submission_datetime = datetime.now(tz.tzlocal()).strftime('%Y-%m-%d %H:%M:%S%z')
    submission_datetime = submission_datetime[:-2] + ':' + submission_datetime[-2:]
    
    if form.validate_on_submit():
        name = form.name.data
        abbreviation = form.abbreviation.data
        status = form.status.data
        customer_guid = form.customer_guid.data

        bus_post_project_response = bus_project.post_project(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            name=name,
            abbreviation=abbreviation,
            status=status,
            customer_guid=customer_guid
        )

        if bus_post_project_response.success:
            return redirect(url_for('web_project.list_projects_route'))

        return bus_post_project_response.message, bus_post_project_response.status_code

    return render_template('project_create.html', form=form)


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

    # Set status choices for display
    status_choices = {
        'P': 'Planning',
        'D': 'Design',
        'B': 'Build',
        'C': 'Complete'
    }

    return render_template('project_view.html', project=_project, customer=_customer, status_choices=status_choices)


@web_project_bp.route('/project/<project_guid>/edit', methods=['GET', 'POST'])
@requires_auth()
def edit_project_route(project_guid):
    """
    Returns the project edit route.
    """
    form = ProjectForm()
    
    # Get project data
    get_project_bus_response = bus_project.get_project_by_guid(project_guid)
    if not get_project_bus_response.success:
        return render_template('project_edit.html', form=None)
    
    project = get_project_bus_response.data
    
    # Get customer data for the select field
    get_customers_bus_response = bus_customer.get_customers()
    customers = get_customers_bus_response.data if get_customers_bus_response.success else []
    
    # Set customer choices
    form.customer_guid.choices = [(customer.guid, customer.name) for customer in customers]

    # Set status choices
    form.status.choices = [
        ('P', 'Planning'),
        ('D', 'Design'),
        ('B', 'Build'),
        ('C', 'Complete')
    ]
    
    if request.method == 'GET':
        # Populate form with existing data
        form.name.data = project.name
        form.abbreviation.data = project.abbreviation
        form.status.data = project.status
        form.customer_guid.data = next((customer.guid for customer in customers if customer.id == project.customer_id), None)
    
    if form.validate_on_submit():
        submission_datetime = datetime.now(tz.tzlocal()).strftime('%Y-%m-%d %H:%M:%S%z')
        submission_datetime = submission_datetime[:-2] + ':' + submission_datetime[-2:]
        
        # Update project
        bus_put_project_response = bus_project.put_project(
            project_guid=project_guid,
            modified_datetime=submission_datetime,
            name=form.name.data,
            abbreviation=form.abbreviation.data,
            status=form.status.data,
            customer_guid=form.customer_guid.data
        )
        
        if bus_put_project_response.success:
            return redirect(url_for('web_project.view_project_route', project_guid=project_guid))
        
        return bus_put_project_response.message, bus_put_project_response.status_code
    
    return render_template('project_edit.html', form=form, project=project)
