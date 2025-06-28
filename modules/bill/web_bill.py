"""
Module for bill web.
"""
# python standard library imports
import base64
import mimetypes

# third party imports
from flask import Blueprint, render_template


# local imports
from integrations.map import pers_map_attachment_sharepoint_file
from integrations.ms import pers_ms_sharepoint_file
from modules.bill import (
    bus_bill_line_item_attachment,
    bus_bill_line_item,
    bus_bill
)
from modules.vendor import (
    bus_vendor
)
from modules.project import bus_project
from modules.sub_cost_code import bus_sub_cost_code
from utils.auth_help import requires_auth


web_bill_bp = Blueprint('web_bill', __name__, template_folder='templates')


@web_bill_bp.route('/bills', methods=['GET'])
@requires_auth()
def list_bills_route():
    """
    Returns the bills route for the application.
    """
    _bills = []
    get_bills_bus_response = bus_bill.get_bills()
    if get_bills_bus_response.success:
        _bills = get_bills_bus_response.data
    print(get_bills_bus_response.message)
    _vendors = []
    get_vendors_bus_response = bus_vendor.get_vendors()
    if get_vendors_bus_response.success:
        _vendors = get_vendors_bus_response.data
    print(get_vendors_bus_response.message)

    return render_template('bill_list.html', bills=_bills, vendors=_vendors)


@web_bill_bp.route('/bill/create', methods=['GET'])
@requires_auth()
def create_bill_route():
    """
    Returns the bill create route for the application.
    """
    _vendors = []
    get_vendors_bus_response = bus_vendor.get_vendors()
    if get_vendors_bus_response.success:
        _vendors = get_vendors_bus_response.data
    else:
        _vendors = []

    _sub_cost_codes = []
    get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
    if get_sub_cost_codes_bus_response.success:
        _sub_cost_codes = get_sub_cost_codes_bus_response.data
    else:
        _sub_cost_codes = []

    _projects = []
    get_projects_bus_response = bus_project.get_projects()
    if get_projects_bus_response.success:
        _projects = get_projects_bus_response.data
    else:
        _projects = []

    return render_template(
        'bill_create.html',
        vendors=_vendors,
        sub_cost_codes=_sub_cost_codes,
        projects=_projects,
        bill_line_items=[]
    )


@web_bill_bp.route('/bill/<bill_guid>', methods=['GET'])
@requires_auth()
def view_bill_route(bill_guid):
    """
    Returns the bill by guid route.
    """
    _bill = {}
    get_bill_bus_response = bus_bill.get_bill_by_guid(bill_guid)
    if get_bill_bus_response.success:
        _bill = get_bill_bus_response.data
    else:
        _bill = {}

    _bill_line_items = []
    get_bill_line_items_bus_response = bus_bill_line_item.\
        get_bill_line_item_by_bill_id(_bill.id)
    if get_bill_line_items_bus_response.success:
        _bill_line_items = get_bill_line_items_bus_response.data


    _bill_line_item_attachments = []

    for bill_line_item in _bill_line_items:

        get_bill_line_item_attachments_bus_response = bus_bill_line_item_attachment.\
            get_bill_line_item_attachment_by_bill_line_item_id(bill_line_item.id)

        for attachment in get_bill_line_item_attachments_bus_response.data:
            file_bytes = attachment.content
            file_name = attachment.name
            mime_type, _ = mimetypes.guess_type(file_name)
            mime_type = mime_type or 'application/octet-stream' 
            base64_bytes = base64.b64encode(file_bytes).decode('utf-8')
            data_uri = f'data:{mime_type};base64,{base64_bytes}'

            get_map_attachment_sharepoint_file_response = pers_map_attachment_sharepoint_file.\
                read_map_attachment_sharepoint_file_by_attachment_id(attachment.id)

            ms_sharepoint_file = None
            if get_map_attachment_sharepoint_file_response.success:
                map_attachment_sharepoint_file = get_map_attachment_sharepoint_file_response.data
                
                ms_sharepoint_file_id = map_attachment_sharepoint_file.ms_sharepoint_file_id
                
                read_sharepoint_file_by_id_response = pers_ms_sharepoint_file.\
                    read_sharepoint_file_by_id(ms_sharepoint_file_id)

                if read_sharepoint_file_by_id_response.success:
                    ms_sharepoint_file = read_sharepoint_file_by_id_response.data

            _bill_line_item_attachments.append(
                {
                    'id': attachment.id,
                    'guid': attachment.guid,
                    'created_datetime': attachment.created_datetime,
                    'modified_datetime': attachment.modified_datetime,
                    'name': file_name,
                    'size': attachment.size,
                    'type': mime_type,
                    'content': data_uri,
                    'bill_line_item_id': attachment.bill_line_item_id,
                    'ms_sharepoint_file_web_url': ms_sharepoint_file.file_web_url if ms_sharepoint_file else None
                }
            )

    _vendors = []
    get_vendors_bus_response = bus_vendor.get_vendors()
    if get_vendors_bus_response.success:
        _vendors = get_vendors_bus_response.data
    else:
        _vendors = []

    _sub_cost_codes = []
    get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
    if get_sub_cost_codes_bus_response.success:
        _sub_cost_codes = get_sub_cost_codes_bus_response.data
    else:
        _sub_cost_codes = []

    _projects = []
    get_projects_bus_response = bus_project.get_projects()
    if get_projects_bus_response.success:
        _projects = get_projects_bus_response.data
    else:
        _projects = []

    return render_template(
        'bill_view.html',
        bill=_bill,
        bill_line_items=_bill_line_items,
        bill_line_item_attachments=_bill_line_item_attachments,
        vendors=_vendors,
        projects=_projects,
        sub_cost_codes=_sub_cost_codes
    )


@web_bill_bp.route('/bill/<bill_guid>/edit', methods=['GET'])
@requires_auth()
def edit_bill_route(bill_guid):
    """
    Returns the bill edit route.
    """
    _bill = {}
    get_bill_bus_response = bus_bill.get_bill_by_guid(bill_guid)
    if get_bill_bus_response.success:
        _bill = get_bill_bus_response.data
    else:
        _bill = {}

    _bill_line_items = []
    get_bill_line_items_bus_response = bus_bill_line_item.\
        get_bill_line_item_by_bill_id(_bill.id)
    if get_bill_line_items_bus_response.success:
        _bill_line_items = get_bill_line_items_bus_response.data
    else:
        _bill_line_items = []

    _vendors = []
    get_vendors_bus_response = bus_vendor.get_vendors()
    if get_vendors_bus_response.success:
        _vendors = get_vendors_bus_response.data
    else:
        _vendors = []

    _sub_cost_codes = []
    get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
    if get_sub_cost_codes_bus_response.success:
        _sub_cost_codes = get_sub_cost_codes_bus_response.data
    else:
        _sub_cost_codes = []

    _projects = []
    get_projects_bus_response = bus_project.get_projects()
    if get_projects_bus_response.success:
        _projects = get_projects_bus_response.data
    else:
        _projects = []

    return render_template(
        'bill_edit.html',
        bill=_bill,
        bill_line_items=_bill_line_items,
        vendors=_vendors,
        projects=_projects,
        sub_cost_codes=_sub_cost_codes
    )
