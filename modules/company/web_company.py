"""
Module for company web.
"""
# python standard library imports


# third party imports
from flask import Blueprint, render_template

# local imports
from modules.company import bus_company


web_company_bp = Blueprint('web_company', __name__, template_folder='templates')


@web_company_bp.route('/company', methods=['GET'])
def list_companies_route():
    """
    Returns the route for the company page.
    """
    companies = []
    bus_get_company_resp = bus_company.get_company()
    if bus_get_company_resp.success:
        companies = bus_get_company_resp.data
    return render_template('company_list.html', companies=companies)    


@web_company_bp.route('/company/create', methods=['GET'])
def create_company_route():
    """
    Returns the company create route for the application.
    """
    return render_template('company_create.html')


@web_company_bp.route('/company/<company_guid>', methods=['GET'])
def view_company_route(company_guid):
    """
    Returns the company by guid route.
    """
    company = None
    bus_get_company_resp = bus_company.get_company_by_guid(company_guid)
    if bus_get_company_resp.success:
        company = bus_get_company_resp.data
    return render_template('company_view.html', company=company)


@web_company_bp.route('/company/<company_guid>/edit', methods=['GET'])
def edit_company_route(company_guid):
    """
    Returns the company edit route.
    """
    company = None
    bus_get_company_resp = bus_company.get_company_by_guid(company_guid)
    if bus_get_company_resp.success:
        company = bus_get_company_resp.data
    return render_template('company_edit.html', company=company)

