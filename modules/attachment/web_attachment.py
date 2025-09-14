"""
Module for attachment web.
"""

# python standard library imports


# third party imports
from flask import Blueprint, render_template, redirect, url_for, flash


# local imports
from modules.attachment import bus_attachment
from utils.auth_help import requires_auth


web_attachment_bp = Blueprint('web_attachment', __name__, template_folder='templates')


@web_attachment_bp.route('/attachments', methods=['GET'])
#@requires_auth()
def list_attachments_route():
    """
    Retrieves the attachments route.
    """
    _attachments = []
    get_attachments_bus_response = bus_attachment.get_attachments()
    if get_attachments_bus_response.success:
        _attachments = get_attachments_bus_response.data

    return render_template('attachment/attachment_list.html', attachments=_attachments)



@web_attachment_bp.route('/attachment/create', methods=['GET'])
#@requires_auth()
def create_attachment_route():
    """
    Retrieves the create attachment route.
    """
    return render_template('attachment/attachment_create.html')




@web_attachment_bp.route('/attachment/<guid>', methods=['GET'])
#@requires_auth()
def view_attachment_route(guid):
    """
    Retrieves the view attachment route.
    """
    _attachment = None
    get_attachment_bus_response = bus_attachment.get_attachment_by_guid(guid)
    if get_attachment_bus_response.success:
        _attachment = get_attachment_bus_response.data
    else:
        _attachment = None

    return render_template('attachment/attachment_view.html', attachment=_attachment)



@web_attachment_bp.route('/attachment/<guid>/edit', methods=['GET'])
#@requires_auth()
def edit_attachment_route(guid):
    """
    Retrieves the edit attachment route.
    """
    _attachment = None
    get_attachment_bus_response = bus_attachment.get_attachment_by_guid(guid)
    if get_attachment_bus_response.success:
        _attachment = get_attachment_bus_response.data
    else:
        _attachment = None
    return render_template('attachment/attachment_edit.html', attachment=_attachment)



@web_attachment_bp.route('/attachment/<guid>/delete', methods=['GET'])
#@requires_auth()
def delete_attachment_route(guid):
    """
    Retrieves the delete attachment route.
    """
    delete_attachment_bus_response = bus_attachment.delete_attachment_by_guid(guid)
    if delete_attachment_bus_response.success:
        return redirect(url_for('web_attachment.list_attachments_route'))
    else:
        flash(delete_attachment_bus_response.message, 'error')
        return redirect(url_for('web_attachment.list_attachments_route'))
