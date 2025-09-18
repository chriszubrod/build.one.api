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
