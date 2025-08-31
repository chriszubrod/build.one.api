"""
Web routes for Certificate of Insurance (COI).
"""

# python standard library imports


# third party imports
from flask import Blueprint, render_template, flash

# local imports
from modules.certificate_of_insurance import bus_certificate_of_insurance as bus_coi
from modules.vendor import bus_vendor
from modules.type_of_insurance import bus_type_of_insurance
from utils.auth_help import requires_auth


web_certificate_of_insurance_bp = Blueprint('web_certificate_of_insurance', __name__, template_folder='templates')


@web_certificate_of_insurance_bp.route('/certificate-of-insurances', methods=['GET'])
#@requires_auth()
def list_certificate_of_insurances_route():
    _cois = []
    resp = bus_coi.get_certificate_of_insurances()
    if resp.success:
        _cois = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate_of_insurance/certificate_of_insurance_list.html', certificate_of_insurances=_cois)


@web_certificate_of_insurance_bp.route('/certificate-of-insurance/create', methods=['GET'])
#@requires_auth()
def create_certificate_of_insurance_route():
    vendors = []
    type_of_insurances = []

    try:
        get_vendors_bus_response = bus_vendor.get_vendors()
        if get_vendors_bus_response.success:
            vendors = get_vendors_bus_response.data
    except Exception as e:
        flash(str(e), 'error')

    try:
        get_toi_bus_response = bus_type_of_insurance.get_type_of_insurances()
        if get_toi_bus_response.success:
            type_of_insurances = get_toi_bus_response.data
    except Exception as e:
        flash(str(e), 'error')

    return render_template(
        'certificate_of_insurance/certificate_of_insurance_create.html',
        vendors=vendors,
        type_of_insurances=type_of_insurances
    )


@web_certificate_of_insurance_bp.route('/certificate-of-insurance/<coi_guid>', methods=['GET'])
#@requires_auth()
def view_certificate_of_insurance_route(coi_guid):
    _coi = {}
    resp = bus_coi.get_certificate_of_insurance_by_guid(coi_guid)
    if resp.success:
        _coi = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate_of_insurance/certificate_of_insurance_view.html', certificate_of_insurance=_coi)


@web_certificate_of_insurance_bp.route('/certificate-of-insurance/<coi_guid>/edit', methods=['GET'])
#@requires_auth()
def edit_certificate_of_insurance_route(coi_guid):
    _coi = {}
    resp = bus_coi.get_certificate_of_insurance_by_guid(coi_guid)
    if resp.success:
        _coi = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate_of_insurance/certificate_of_insurance_edit.html', certificate_of_insurance=_coi)
