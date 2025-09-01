"""
Web routes for Certificate Type.
"""

# third party imports
from flask import Blueprint, render_template, flash

# local imports
from modules.certificate_type import bus_certificate_type


web_certificate_type_bp = Blueprint('web_certificate_type', __name__, template_folder='templates')


@web_certificate_type_bp.route('/certificate-types', methods=['GET'])
def list_certificate_types_route():
    _certificate_types = []
    resp = bus_certificate_type.get_certificate_types()
    if resp.success:
        _certificate_types = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate_type/certificate_type_list.html', certificate_types=_certificate_types)


@web_certificate_type_bp.route('/certificate-type/create', methods=['GET'])
def create_certificate_type_route():
    return render_template('certificate_type/certificate_type_create.html')


@web_certificate_type_bp.route('/certificate-type/<guid>', methods=['GET'])
def view_certificate_type_route(guid):
    _certificate_type = None
    resp = bus_certificate_type.get_certificate_type_by_guid(guid)
    if resp.success:
        _certificate_type = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate_type/certificate_type_view.html', certificate_type=_certificate_type)


@web_certificate_type_bp.route('/certificate-type/<guid>/edit', methods=['GET'])
def edit_certificate_type_route(guid):
    _certificate_type = None
    resp = bus_certificate_type.get_certificate_type_by_guid(guid)
    if resp.success:
        _certificate_type = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate_type/certificate_type_edit.html', certificate_type=_certificate_type)

