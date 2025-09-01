"""
Web routes for Certificate Type.
"""

# third party imports
from flask import Blueprint, render_template, flash

# local imports
from modules.certificate_type import bus_certificate_type as bus_ct


web_certificate_type_bp = Blueprint('web_certificate_type', __name__, template_folder='templates')


@web_certificate_type_bp.route('/certificate-types', methods=['GET'])
def list_certificate_types_route():
    _items = []
    resp = bus_ct.get_certificate_types()
    if resp.success:
        _items = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate_type/certificate_type_list.html', certificate_types=_items)


@web_certificate_type_bp.route('/certificate-type/create', methods=['GET'])
def create_certificate_type_route():
    return render_template('certificate_type/certificate_type_create.html')


@web_certificate_type_bp.route('/certificate-type/<guid>', methods=['GET'])
def view_certificate_type_route(guid):
    _item = None
    resp = bus_ct.get_certificate_type_by_guid(guid)
    if resp.success:
        _item = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate_type/certificate_type_view.html', certificate_type=_item)


@web_certificate_type_bp.route('/certificate-type/<guid>/edit', methods=['GET'])
def edit_certificate_type_route(guid):
    _item = None
    resp = bus_ct.get_certificate_type_by_guid(guid)
    if resp.success:
        _item = resp.data
    else:
        flash(resp.message, 'error')
    return render_template('certificate_type/certificate_type_edit.html', certificate_type=_item)

