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
from modules.module import bus_module
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


    _intuit_customer = {}
    try:
        ic_resp = bus_project.get_intuit_customer_by_project_guid(project_guid)
        if ic_resp.success:
            _intuit_customer = ic_resp.data
    except Exception as e:
        # Non-fatal for view; leave intuit customer as None
        pass

    # Retrieve mapped MS SharePoint folders for this project (all modules)
    _sharepoint_mappings = []
    _sp_workbook_mappings = []
    _sp_worksheet_mappings = []
    try:
        if _project and getattr(_project, 'id', None):
            sp_resp = bus_project.get_ms_sharepoint_folders_by_project_id(_project.id)
            if getattr(sp_resp, 'success', False):
                _sharepoint_mappings = sp_resp.data or []
            wb_resp = bus_project.get_ms_sharepoint_workbooks_by_project_id(_project.id)
            if getattr(wb_resp, 'success', False):
                _sp_workbook_mappings = wb_resp.data or []
            ws_resp = bus_project.get_ms_sharepoint_worksheets_by_project_id(_project.id)
            if getattr(ws_resp, 'success', False):
                _sp_worksheet_mappings = ws_resp.data or []
    except Exception:
        pass
    #print(f"sharepoint_mappings: {_sharepoint_mappings}")

    status_choices = {
        'P': 'Planning',
        'D': 'Design',
        'B': 'Build',
        'C': 'Complete'
    }
    return render_template(
        'project/project_view.html',
        project=_project,
        customer=_customer,
        status_choices=status_choices,
        intuit_customer=_intuit_customer,
        sharepoint_mappings=_sharepoint_mappings,
        sp_workbook_mappings=_sp_workbook_mappings,
        sp_worksheet_mappings=_sp_worksheet_mappings
    )


@web_project_bp.route('/project/<project_guid>/edit', methods=['GET', 'POST'])
#@requires_auth()
def edit_project_route(project_guid):
    """Renders project edit page with customers list."""
    
    # Get project
    _project = {}
    resp = bus_project.get_project_by_guid(project_guid)
    if resp.success:
        _project = resp.data
    else:
        flash(resp.message, 'error')


    # Get customers
    customers = []
    cust_resp = bus_customer.get_customers()
    if cust_resp.success:
        customers = cust_resp.data


    # Get intuit customer
    _mapped_intuit_customer = {}
    try:
        map_resp = bus_project.get_intuit_customer_by_project_guid(project_guid)
        if getattr(map_resp, 'success', False):
            _mapped_intuit_customer = map_resp.data
    except Exception:
        pass

    # Get intuit projects
    intuit_projects = []
    try:
        ip_resp = bus_project.get_intuit_projects()
        if getattr(ip_resp, 'success', False):
            intuit_projects = ip_resp.data
    except Exception as e:
        flash(str(e), 'error')

    # Get modules
    modules = []
    mod_resp = bus_module.get_modules()
    if mod_resp.success:
        modules = mod_resp.data
    else:
        flash(mod_resp.message, 'error')


    # Get SharePoint mappings (folders) and mapped workbook/worksheet entities for editing
    sharepoint_mappings = []
    sharepoint_workbooks = []
    sharepoint_worksheets = []
    
    sharepoint_folders_resp = bus_project.get_ms_sharepoint_folders_by_project_id(_project.id)
    if sharepoint_folders_resp.success:
        sharepoint_mappings = sharepoint_folders_resp.data or []
    else:
        flash(sharepoint_folders_resp.message, 'error')

    sharepoint_workbooks_resp = bus_project.get_ms_sharepoint_workbooks_by_project_id(_project.id)
    if sharepoint_workbooks_resp.success:
        # Response already returns a list of workbook entities (or empty)
        sharepoint_workbooks = [wb for wb in (sharepoint_workbooks_resp.data) if wb]
    else:
        flash(sharepoint_workbooks_resp.message, 'error')

    sharepoint_worksheets_resp = bus_project.get_db_ms_sharepoint_worksheets_by_project_id(_project.id)
    if sharepoint_worksheets_resp.success:
        # Response returns a list of worksheet entities from Graph (or empty)
        sharepoint_worksheets = [ws for ws in (sharepoint_worksheets_resp.data) if ws]
    else:
        flash(sharepoint_worksheets_resp.message, 'error')


    return render_template(
        'project/project_edit.html',
        project=_project,
        customers=customers,
        mapped_intuit_customer=_mapped_intuit_customer,
        intuit_projects=intuit_projects,
        modules=modules,
        sharepoint_mappings=sharepoint_mappings,
        sharepoint_workbooks=sharepoint_workbooks,
        sharepoint_worksheets=sharepoint_worksheets
    )
