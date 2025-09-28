"""
Module for project API.
"""

# python standard library imports
import html
from datetime import datetime
from dateutil import tz

# third party imports
import bleach
from flask import Blueprint, request, jsonify

# local imports
from shared.response import ApiResponse
from modules.project import bus_project
from integrations.ms.persistence import (
    pers_ms_sharepoint_site,
    pers_ms_sharepoint_workbook,
    pers_ms_sharepoint_worksheet,
)


api_project_bp = Blueprint('api_project', __name__, url_prefix='/api')


@api_project_bp.route('/projects', methods=['GET'])
def api_get_projects_route():
    """
    Retrieves all projects from the database.
    """
    resp = bus_project.get_projects()
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_project_bp.route('/post/project', methods=['POST'])
def api_post_project_route():
    """
    Handles the POST request for saving a project.
    """
    try:
        # If request is not JSON, return 400 error
        if not request.is_json:
            return jsonify(
                ApiResponse(
                    data=None,
                    message='Content type must be application/json',
                    status_code=400,
                    success=False,
                    timestamp=datetime.now(tz.tzlocal())
                ).to_dict()
            )

        # Get the JSON data from the request
        data = request.json

        # project name
        raw_name = data.get('name', '').strip()
        clean_name = bleach.clean(raw_name, strip=True) # Remove any HTML tags
        name = html.escape(clean_name) # Escape any special HTML characters

        # project abbreviation
        raw_abbreviation = data.get('abbreviation', '').strip()
        clean_abbreviation = bleach.clean(raw_abbreviation, strip=True) # Remove any HTML tags
        abbreviation = html.escape(clean_abbreviation) # Escape any special HTML characters

        # project status
        raw_status = data.get('status', '').strip()
        clean_status = bleach.clean(raw_status, strip=True) # Remove any HTML tags
        status = html.escape(clean_status) # Escape any special HTML characters

        # customer guid
        raw_customer_guid = data.get('customerGuid', '').strip()
        clean_customer_guid = bleach.clean(raw_customer_guid, strip=True)
        customer_guid = html.escape(clean_customer_guid)

        # Call business layer to create a project
        project_bus_response = bus_project.post_project(
            name=name,
            abbreviation=abbreviation,
            status=status,
            customer_guid=customer_guid
        )

        # Return the response from the post_module function
        return jsonify(
            ApiResponse(
                data=project_bus_response.data,
                message=project_bus_response.message,
                status_code=project_bus_response.status_code,
                success=project_bus_response.success,
                timestamp=datetime.now(tz.tzlocal())
            ).to_dict()
        )

    # Handle any exceptions
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(
            ApiResponse(
                data=None,
                message=str(e),
                status_code=500,
                success=False,
                timestamp=datetime.now(tz.tzlocal())
            ).to_dict()
        )


@api_project_bp.route('/project/<guid>', methods=['PATCH'])
def api_patch_project_route(guid):
    """
    Handles the PATCH request for updating a project.
    """
    try:
        # If request is not JSON, return 400 error
        if not request.is_json:
            return jsonify(
                ApiResponse(
                    data=None,
                    message='Content type must be application/json',
                    status_code=400,
                    success=False,
                    timestamp=datetime.now(tz.tzlocal())
                ).to_dict()
            )

        # Get the JSON data from the request
        data = request.json

        # project name
        raw_name = data.get('name', '').strip()
        clean_name = bleach.clean(raw_name, strip=True) # Remove any HTML tags
        name = html.escape(clean_name) # Escape any special HTML characters

        # project abbreviation
        raw_abbreviation = data.get('abbreviation', '').strip()
        clean_abbreviation = bleach.clean(raw_abbreviation, strip=True) # Remove any HTML tags
        abbreviation = html.escape(clean_abbreviation) # Escape any special HTML characters

        # project status
        raw_status = data.get('status', '').strip()
        clean_status = bleach.clean(raw_status, strip=True) # Remove any HTML tags
        status = html.escape(clean_status) # Escape any special HTML characters

        # customer guid
        raw_customer_guid = data.get('customerGuid', '').strip()
        clean_customer_guid = bleach.clean(raw_customer_guid, strip=True)
        customer_guid = html.escape(clean_customer_guid)

        # Call the patch_project_by_guid function and pass in the data to update a project
        project_bus_response = bus_project.patch_project_by_guid(
            guid=guid,
            name=name,
            abbreviation=abbreviation,
            status=status,
            customer_guid=customer_guid
        )

        # Return the response from the patch_project_by_guid function
        return jsonify(
            ApiResponse(
                data=project_bus_response.data,
                message=project_bus_response.message,
                status_code=project_bus_response.status_code,
                success=project_bus_response.success,
                timestamp=datetime.now(tz.tzlocal())
            ).to_dict()
        )

    # Handle any exceptions
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(
            ApiResponse(
                data=None,
                message=str(e),
                status_code=500,
                success=False,
                timestamp=datetime.now(tz.tzlocal())
            ).to_dict()
        )

@api_project_bp.route('/project/<guid>', methods=['GET'])
def api_get_project_by_guid_route(guid):
    resp = bus_project.get_project_by_guid(guid)
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_project_bp.route('/project/<int:id>', methods=['DELETE'])
def api_delete_project_by_id_route(id):
    resp = bus_project.delete_project_by_id(id)
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_project_bp.route('/post/map/project-intuit', methods=['POST'])
def api_post_map_project_intuit_customer_by_guid_route():
    """Creates a mapping using GUIDs only (project GUID and Intuit customer GUID)."""
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        data = request.json
        project_guid = str(data.get('projectGuid', '')).strip()
        intuit_customer_guid = str(data.get('intuitCustomerGuid', '')).strip()
        if not project_guid or not intuit_customer_guid:
            return jsonify(ApiResponse(data=None, message='Missing GUIDs for mapping', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        resp = bus_project.map_project_to_intuit_customer(project_guid=project_guid, intuit_customer_guid=intuit_customer_guid)
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_project_bp.route('/post/map/project-sharepoint-folder', methods=['POST'])
def api_post_map_project_sharepoint_folder_route():
    """Creates or updates a mapping for Project to MS SharePoint Folder by module slug."""
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        data = request.json
        project_guid = str(data.get('projectGuid', '')).strip()
        module_slug = str(data.get('moduleSlug', '')).strip()
        folder_id = int(data.get('folderId', 0))
        if not project_guid or not module_slug or not folder_id:
            return jsonify(ApiResponse(data=None, message='Missing fields for mapping', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        resp = bus_project.map_project_to_ms_sharepoint_folder(project_guid=project_guid, module_slug=module_slug, ms_sharepoint_folder_id=folder_id)
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_project_bp.route('/post/map/project-sharepoint-folder-select', methods=['POST'])
def api_post_map_project_sharepoint_folder_select_route():
    """Maps a Project to a SharePoint folder by resolving/creating the folder via provided details (from picker)."""
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        data = request.json
        project_guid = str(data.get('projectGuid', '')).strip()
        module_slug = str(data.get('moduleSlug', '')).strip()
        folder = data.get('folder', {})
        name = folder.get('name')
        web_url = folder.get('web_url')
        ms_id = folder.get('ms_id') or folder.get('item_id')
        c_tag = folder.get('c_tag')
        e_tag = folder.get('e_tag')
        ms_created_datetime = folder.get('created_datetime')
        last_modified_datetime = folder.get('last_modified_datetime')
        size = folder.get('size')
        ms_parent_id = folder.get('ms_parent_id')
        shared_scope = folder.get('shared_scope')

        if not project_guid or not module_slug or not web_url:
            return jsonify(ApiResponse(data=None, message='Missing required fields', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        resp = bus_project.map_project_to_ms_sharepoint_folder_by_details(
            project_guid=project_guid,
            module_slug=module_slug,
            name=name,
            web_url=web_url,
            ms_id=ms_id,
            c_tag=c_tag,
            e_tag=e_tag,
            ms_created_datetime=ms_created_datetime,
            last_modified_datetime=last_modified_datetime,
            size=size,
            ms_parent_id=ms_parent_id,
            shared_scope=shared_scope
        )
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


# Workbook mappings
@api_project_bp.route('/project/<project_id>/sharepoint/workbooks', methods=['GET'])
def api_get_project_sharepoint_workbooks_route(project_id):
    try:
        pid = int(project_id)
        resp = bus_project.get_ms_sharepoint_workbooks_by_project_id(pid)
        return jsonify(ApiResponse(data=[{'mapping': vars(item['mapping']), 'workbook': vars(item['workbook']) if item['workbook'] else None} for item in (resp.data or [])], message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_project_bp.route('/post/map/project-sharepoint-workbook', methods=['POST'])
def api_post_map_project_sharepoint_workbook_route():
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())
        data = request.json
        project_guid = str(data.get('projectGuid', '')).strip()
        workbook_id = int(data.get('workbookId', 0))
        if not project_guid or not workbook_id:
            return jsonify(ApiResponse(data=None, message='Missing fields for mapping', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())
        resp = bus_project.post_map_project_to_ms_sharepoint_workbook(project_guid, workbook_id)
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

@api_project_bp.route('/patch/map/project-sharepoint-workbook', methods=['PATCH'])
def api_patch_map_project_sharepoint_workbook_route():
    return jsonify(ApiResponse(data=None, message='Update not supported: missing stored procedure', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

@api_project_bp.route('/delete/map/project-sharepoint-workbook/<int:id>', methods=['DELETE'])
def api_delete_map_project_sharepoint_workbook_route(id):
    return jsonify(ApiResponse(data=None, message='Delete not supported: missing stored procedure', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


# Worksheet mappings
@api_project_bp.route('/project/<project_id>/sharepoint/worksheets', methods=['GET'])
def api_get_project_sharepoint_worksheets_route(project_id):
    try:
        pid = int(project_id)
        resp = bus_project.get_ms_sharepoint_worksheets_by_project_id(pid)
        return jsonify(ApiResponse(data=[{'mapping': vars(item['mapping']), 'worksheet': vars(item['worksheet']) if item['worksheet'] else None} for item in (resp.data or [])], message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_project_bp.route('/project/<int:project_id>/sharepoint/workbook/context', methods=['GET'])
def api_get_project_sharepoint_workbook_context_route(project_id: int):
    """Returns the siteId and workbook itemId (MsId) for the mapped workbook of a project."""
    try:
        # Mapped workbook for project
        wb_resp = bus_project.get_ms_sharepoint_workbooks_by_project_id(project_id)
        workbooks = wb_resp.data or []
        if not workbooks:
            return jsonify(ApiResponse(data=None, message='No mapped SharePoint workbook for project', status_code=404, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        wb = workbooks[0].get('workbook') if isinstance(workbooks[0], dict) else None
        if not wb or not getattr(wb, 'workbook_ms_id', None):
            return jsonify(ApiResponse(data=None, message='Mapped workbook is missing MsId', status_code=404, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        # Site Id (assumes single site persisted; take the first)
        site_resp = pers_ms_sharepoint_site.read_sharepoint_sites()
        if not getattr(site_resp, 'success', False) or not site_resp.data:
            return jsonify(ApiResponse(data=None, message='SharePoint site not found', status_code=404, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())
        site = site_resp.data[0]

        data = {
            'siteId': getattr(site, 'site_sharepoint_id', None),
            'itemId': getattr(wb, 'workbook_ms_id', None),
            'workbook': {
                'id': getattr(wb, 'workbook_id', None),
                'ms_id': getattr(wb, 'workbook_ms_id', None),
                'name': getattr(wb, 'workbook_name', None),
                'web_url': getattr(wb, 'workbook_web_url', None)
            }
        }
        return jsonify(ApiResponse(data=data, message='Workbook context found', status_code=200, success=True, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_project_bp.route('/post/map/project-sharepoint-worksheet', methods=['POST'])
def api_post_map_project_sharepoint_worksheet_route():
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())
        data = request.json
        project_guid = str(data.get('projectGuid', '')).strip()
        worksheet_id = int(data.get('worksheetId', 0))
        if not project_guid or not worksheet_id:
            return jsonify(ApiResponse(data=None, message='Missing fields for mapping', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())
        resp = bus_project.post_map_project_to_ms_sharepoint_worksheet(project_guid, worksheet_id)
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_project_bp.route('/post/map/project-sharepoint-workbook-select', methods=['POST'])
def api_post_map_project_sharepoint_workbook_select_route():
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())
        data = request.json
        project_guid = str(data.get('projectGuid', '')).strip()
        wb = data.get('workbook', {})
        resp = bus_project.map_project_to_ms_sharepoint_workbook_by_details(
            project_guid=project_guid,
            name=wb.get('name'),
            web_url=wb.get('web_url'),
            ms_id=wb.get('ms_id') or wb.get('item_id'),
            c_tag=wb.get('c_tag'),
            e_tag=wb.get('e_tag'),
            ms_created_datetime=wb.get('created_datetime'),
            last_modified_datetime=wb.get('last_modified_datetime'),
            size=wb.get('size'),
            ms_parent_id=wb.get('ms_parent_id'),
            shared_scope=wb.get('shared_scope'),
            ms_graph_download_url=wb.get('ms_graph_download_url'),
            file_mime_type=wb.get('file_mime_type'),
            file_hash_quick_xor_hash=wb.get('file_hash_quick_xor_hash')
        )
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_project_bp.route('/post/map/project-sharepoint-worksheet-select', methods=['POST'])
def api_post_map_project_sharepoint_worksheet_select_route():
    try:
        if not request.is_json:
            return jsonify(
                ApiResponse(
                    data=None,
                    message='Content type must be application/json',
                    status_code=400,
                    success=False,
                    timestamp=datetime.now(tz.tzlocal())
                ).to_dict()
            )
        
        data = request.json
        #print(data)
        project_guid = str(data.get('projectGuid', '')).strip()
        #print(f"Project GUID: {project_guid}")
        worksheet_data = data.get('worksheet', {})
        ms_id = worksheet_data.get('ms_id')
        #print(f"MS ID: {ms_id}")
        

        resp = bus_project.map_project_to_ms_sharepoint_worksheet_by_details(
            project_guid=project_guid,
            ms_id=ws.get('ms_id'),
            name=ws.get('name'),
            ms_o_data_id=ws.get('ms_o_data_id'),
            position=ws.get('position'),
            visibility=ws.get('visibility')
        )
        return jsonify(ApiResponse(data=None, message='Not implemented', status_code=200, success=True, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


# Patch/Delete for ms objects
@api_project_bp.route('/patch/ms/sharepoint/workbook/<int:workbook_id>', methods=['PATCH'])
def api_patch_ms_sharepoint_workbook_route(workbook_id):
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())
        data = request.json
        from integrations.ms.persistence import pers_ms_sharepoint_workbook
        wb = pers_ms_sharepoint_workbook.SharePointWorkbook(
            workbook_id=workbook_id,
            workbook_ms_graph_download_url=data.get('msGraphDownloadUrl'),
            workbook_c_tag=data.get('cTag'),
            workbook_ms_created_datetime=data.get('msCreatedDatetime'),
            workbook_e_tag=data.get('eTag'),
            workbook_file_hash_quick_x_or_hash=data.get('fileHashQuickXorHash'),
            workbook_file_mime_type=data.get('fileMimeType'),
            workbook_ms_id=data.get('msId'),
            workbook_last_modified_datetime=data.get('lastModifiedDatetime'),
            workbook_name=data.get('name'),
            workbook_ms_parent_id=data.get('msParentId'),
            workbook_shared_scope=data.get('sharedScope'),
            workbook_size=data.get('size'),
            workbook_web_url=data.get('webUrl')
        )
        resp = pers_ms_sharepoint_workbook.update_sharepoint_workbook_by_workbook_id(wb)
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_project_bp.route('/delete/ms/sharepoint/workbook/<int:workbook_id>', methods=['DELETE'])
def api_delete_ms_sharepoint_workbook_route(workbook_id):
    try:
        from integrations.ms.persistence import pers_ms_sharepoint_workbook
        resp = pers_ms_sharepoint_workbook.delete_sharepoint_workbook_by_id(workbook_id)
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_project_bp.route('/patch/ms/sharepoint/worksheet/<int:worksheet_id>', methods=['PATCH'])
def api_patch_ms_sharepoint_worksheet_route(worksheet_id):
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())
        data = request.json
        from integrations.ms.persistence import pers_ms_sharepoint_worksheet
        ws = pers_ms_sharepoint_worksheet.SharePointWorksheet(
            worksheet_id=worksheet_id,
            worksheet_ms_o_data_id=data.get('msODataId'),
            worksheet_ms_id=data.get('msId'),
            worksheet_name=data.get('name'),
            worksheet_position=data.get('position'),
            worksheet_visibility=data.get('visibility')
        )
        resp = pers_ms_sharepoint_worksheet.update_sharepoint_worksheet_by_worksheet_id(ws)
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_project_bp.route('/delete/ms/sharepoint/worksheet/<int:worksheet_id>', methods=['DELETE'])
def api_delete_ms_sharepoint_worksheet_route(worksheet_id):
    try:
        from integrations.ms.persistence import pers_ms_sharepoint_worksheet
        resp = pers_ms_sharepoint_worksheet.delete_sharepoint_worksheet_by_id(worksheet_id)
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except Exception as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

@api_project_bp.route('/patch/map/project-sharepoint-worksheet', methods=['PATCH'])
def api_patch_map_project_sharepoint_worksheet_route():
    return jsonify(ApiResponse(data=None, message='Update not supported: missing stored procedure', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

@api_project_bp.route('/delete/map/project-sharepoint-worksheet/<int:id>', methods=['DELETE'])
def api_delete_map_project_sharepoint_worksheet_route(id):
    return jsonify(ApiResponse(data=None, message='Delete not supported: missing stored procedure', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())
