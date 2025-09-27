"""
Web routes for Certificate of Insurance (COI).
"""

# python standard library imports


# third party imports
from flask import Blueprint, render_template, flash

# local imports
from modules.certificate import bus_certificate as bus_coi
from modules.vendor import bus_vendor
from modules.certificate_type import bus_certificate_type
from utils.auth_help import requires_auth


web_certificate_bp = Blueprint('web_certificate', __name__, template_folder='templates')


@web_certificate_bp.route('/certificates', methods=['GET'])
#@requires_auth()
def list_certificates_route():
    _certificates = []
    resp = bus_coi.get_certificates()
    if resp.success:
        _certificates = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate/certificate_list.html', certificates=_certificates)


@web_certificate_bp.route('/certificate/create', methods=['GET'])
#@requires_auth()
def create_certificate_route():
    vendors = []
    certificate_types = []

    try:
        get_vendors_bus_response = bus_vendor.get_vendors()
        if get_vendors_bus_response.success:
            vendors = get_vendors_bus_response.data
    except Exception as e:
        flash(str(e), 'error')

    try:
        get_ct_bus_response = bus_certificate_type.get_certificate_types()
        if get_ct_bus_response.success:
            certificate_types = get_ct_bus_response.data
    except Exception as e:
        flash(str(e), 'error')

    return render_template(
        'certificate/certificate_create.html',
        vendors=vendors,
        certificate_types=certificate_types
    )


@web_certificate_bp.route('/certificate/<guid>', methods=['GET'])
#@requires_auth()
def view_certificate_route(guid):
    _certificate = {}
    resp = bus_coi.get_certificate_by_guid(guid)
    if resp.success:
        _certificate = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate/certificate_view.html', certificate=_certificate)


@web_certificate_bp.route('/certificate/<guid>/edit', methods=['GET'])
#@requires_auth()
def edit_certificate_route(guid):
    _certificate = {}
    resp = bus_coi.get_certificate_by_guid(guid)
    if resp.success:
        _certificate = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate/certificate_edit.html', certificate=_certificate)
