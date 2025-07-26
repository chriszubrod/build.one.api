"""
Module for bill web.
"""
# python standard library imports
import base64
import mimetypes

# third party imports
from flask import Blueprint, render_template, flash


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


web_bill_bp = Blueprint(
    'web_bill',
    __name__
)


@web_bill_bp.route('/bills', methods=['GET'])
#@requires_auth()
def list_bills_route():
    """
    Returns the bills route for the application.
    """
    try:
        get_bills_bus_response = bus_bill.get_bills()
        if get_bills_bus_response.success:
            bills = get_bills_bus_response.data
        else:
            bills = []
            flash(get_bills_bus_response.message, 'error')
            print(f'\nError: {get_bills_bus_response.message}\n')

        get_vendors_bus_response = bus_vendor.get_vendors()
        if get_vendors_bus_response.success:
            vendors = get_vendors_bus_response.data
        else:
            vendors = []
            flash(get_vendors_bus_response.message, 'error')
            print(f'\nError: {get_vendors_bus_response.message}\n')

        print(f'\nbills: {bills}\n')
        print(f'\nvendors: {vendors}\n')

        return render_template('bill/bill_list.html', bills=bills, vendors=vendors)

    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return f'Error: {e}', 500


@web_bill_bp.route('/bill/create', methods=['GET'])
#@requires_auth()
def create_bill_route():
    """
    Returns the bill create route for the application.
    """
    try:
        get_vendors_bus_response = bus_vendor.get_vendors()
        if get_vendors_bus_response.success:
            vendors = get_vendors_bus_response.data
        else:
            vendors = []
            flash(get_vendors_bus_response.message, 'error')
            print(f'\nError: {get_vendors_bus_response.message}\n')

        get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
        if get_sub_cost_codes_bus_response.success:
            sub_cost_codes = get_sub_cost_codes_bus_response.data
        else:
            sub_cost_codes = []
            flash(get_sub_cost_codes_bus_response.message, 'error')
            print(f'\nError: {get_sub_cost_codes_bus_response.message}\n')

        get_projects_bus_response = bus_project.get_projects()
        if get_projects_bus_response.success:
            projects = get_projects_bus_response.data
        else:
            projects = []
            flash(get_projects_bus_response.message, 'error')
            print(f'\nError: {get_projects_bus_response.message}\n')

        print(f'\nvendors: {vendors}\n')
        print(f'\nsub_cost_codes: {sub_cost_codes}\n')
        print(f'\nprojects: {projects}\n')

        return render_template(
            'bill/bill_create.html',
            vendors=vendors,
            sub_cost_codes=sub_cost_codes,
            projects=projects,
            bill_line_items=[]
        )

    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return f'Error: {e}', 500


@web_bill_bp.route('/bill/<bill_guid>', methods=['GET'])
#@requires_auth()
def view_bill_route(bill_guid):
    """
    Returns the bill by guid route.
    """
    try:
        get_bill_bus_response = bus_bill.get_bill_by_guid(bill_guid)
        if get_bill_bus_response.success:
            bill = get_bill_bus_response.data
        else:
            flash(get_bill_bus_response.message, 'error')
            print(f'\nError: {get_bill_bus_response.message}\n')
            return render_template('shared/layout/error.html', error=get_bill_bus_response.message), 404

        get_bill_line_items_bus_response = bus_bill_line_item.\
            get_bill_line_item_by_bill_id(bill.id)
        if get_bill_line_items_bus_response.success:
            bill_line_items = get_bill_line_items_bus_response.data
        else:
            bill_line_items = []
            flash(get_bill_line_items_bus_response.message, 'error')
            print(f'\nError: {get_bill_line_items_bus_response.message}\n')

        bill_line_item_attachments = []

        for bill_line_item in bill_line_items:
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

                bill_line_item_attachments.append(
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

        get_vendors_bus_response = bus_vendor.get_vendors()
        if get_vendors_bus_response.success:
            vendors = get_vendors_bus_response.data
        else:
            vendors = []
            flash(get_vendors_bus_response.message, 'error')
            print(f'\nError: {get_vendors_bus_response.message}\n')

        get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
        if get_sub_cost_codes_bus_response.success:
            sub_cost_codes = get_sub_cost_codes_bus_response.data
        else:
            sub_cost_codes = []
            flash(get_sub_cost_codes_bus_response.message, 'error')
            print(f'\nError: {get_sub_cost_codes_bus_response.message}\n')

        get_projects_bus_response = bus_project.get_projects()
        if get_projects_bus_response.success:
            projects = get_projects_bus_response.data
        else:
            projects = []
            flash(get_projects_bus_response.message, 'error')
            print(f'\nError: {get_projects_bus_response.message}\n')

        print(f'\nbill: {bill}\n')
        print(f'\nbill_line_items: {bill_line_items}\n')
        print(f'\nbill_line_item_attachments: {bill_line_item_attachments}\n')
        print(f'\nvendors: {vendors}\n')
        print(f'\nsub_cost_codes: {sub_cost_codes}\n')
        print(f'\nprojects: {projects}\n')

        return render_template(
            'bill/bill_view.html',
            bill=bill,
            bill_line_items=bill_line_items,
            bill_line_item_attachments=bill_line_item_attachments,
            vendors=vendors,
            projects=projects,
            sub_cost_codes=sub_cost_codes
        )

    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return render_template('shared/layout/error.html', error=str(e)), 500


@web_bill_bp.route('/bill/<bill_guid>/edit', methods=['GET'])
#@requires_auth()
def edit_bill_route(bill_guid):
    """
    Returns the bill edit route.
    """
    try:
        get_bill_bus_response = bus_bill.get_bill_by_guid(bill_guid)
        if get_bill_bus_response.success:
            bill = get_bill_bus_response.data
        else:
            flash(get_bill_bus_response.message, 'error')
            print(f'\nError: {get_bill_bus_response.message}\n')
            return render_template('shared/layout/error.html', error=get_bill_bus_response.message), 404

        get_bill_line_items_bus_response = bus_bill_line_item.\
            get_bill_line_item_by_bill_id(bill.id)
        if get_bill_line_items_bus_response.success:
            bill_line_items = get_bill_line_items_bus_response.data
        else:
            bill_line_items = []
            flash(get_bill_line_items_bus_response.message, 'error')
            print(f'\nError: {get_bill_line_items_bus_response.message}\n')

        bill_line_item_attachments = []

        for bill_line_item in bill_line_items:
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

                bill_line_item_attachments.append(
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







        get_vendors_bus_response = bus_vendor.get_vendors()
        if get_vendors_bus_response.success:
            vendors = get_vendors_bus_response.data
        else:
            vendors = []
            flash(get_vendors_bus_response.message, 'error')
            print(f'\nError: {get_vendors_bus_response.message}\n')

        get_sub_cost_codes_bus_response = bus_sub_cost_code.get_sub_cost_codes()
        if get_sub_cost_codes_bus_response.success:
            sub_cost_codes = get_sub_cost_codes_bus_response.data
        else:
            sub_cost_codes = []
            flash(get_sub_cost_codes_bus_response.message, 'error')
            print(f'\nError: {get_sub_cost_codes_bus_response.message}\n')

        get_projects_bus_response = bus_project.get_projects()
        if get_projects_bus_response.success:
            projects = get_projects_bus_response.data
        else:
            projects = []
            flash(get_projects_bus_response.message, 'error')
            print(f'\nError: {get_projects_bus_response.message}\n')

        print(f'\nbill: {bill}\n')
        print(f'\nbill_line_items: {bill_line_items}\n')
        print(f'\nbill_line_item_attachments: {bill_line_item_attachments}\n')
        print(f'\nvendors: {vendors}\n')
        print(f'\nsub_cost_codes: {sub_cost_codes}\n')
        print(f'\nprojects: {projects}\n')

        return render_template(
            'bill/bill_edit.html',
            bill=bill,
            bill_line_items=bill_line_items,
            bill_line_item_attachments=bill_line_item_attachments,
            vendors=vendors,
            projects=projects,
            sub_cost_codes=sub_cost_codes
        )

    except Exception as e:
        flash(str(e), 'error')
        print(f'\nError: {e}\n')
        return render_template('shared/layout/error.html', error=str(e)), 500
