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
from blueprints.api.api_response import ApiResponse
from modules.project import bus_project


api_project_bp = Blueprint('api_project', __name__, url_prefix='/api')


@api_project_bp.route('/get/projects', methods=['GET'])
def api_get_projects_route():
    """
    Retrieves all projects from the database.
    """
    projects = bus_project.get_projects()
    return jsonify(projects)


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

        # Get the submission datetime
        submission_datetime = datetime.now(tz.tzlocal()).strftime('%Y-%m-%d %H:%M:%S%z')
        submission_datetime = submission_datetime[:-2] + ':' + submission_datetime[-2:]

        # project guid
        raw_guid = data.get('projectGuid', '').strip()
        clean_guid = bleach.clean(raw_guid, strip=True) # Remove any HTML tags
        project_guid = html.escape(clean_guid) # Escape any special HTML characters

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

        # Call the post_project_by_guid function and pass in the data to create a project
        project_bus_response = bus_project.post_project_by_guid(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            project_guid=project_guid,
            name=name,
            abbreviation=abbreviation,
            status=status
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


@api_project_bp.route('/patch/project', methods=['PATCH'])
def api_patch_project_route():
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

        # Get the submission datetime
        submission_datetime = datetime.now(tz.tzlocal()).strftime('%Y-%m-%d %H:%M:%S%z')
        submission_datetime = submission_datetime[:-2] + ':' + submission_datetime[-2:]

        # project guid
        raw_guid = data.get('projectGuid', '').strip()
        clean_guid = bleach.clean(raw_guid, strip=True) # Remove any HTML tags
        project_guid = html.escape(clean_guid) # Escape any special HTML characters

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

        # Call the patch_project_by_guid function and pass in the data to update a project
        project_bus_response = bus_project.patch_project_by_guid(
            modified_datetime=submission_datetime,
            project_guid=project_guid,
            name=name,
            abbreviation=abbreviation,
            status=status
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


@api_project_bp.route('/delete/project', methods=['DELETE'])
def api_delete_project_route():
    """
    Handles the DELETE request for deleting a project.
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

        # project guid
        raw_guid = data.get('projectGuid', '').strip()
        clean_guid = bleach.clean(raw_guid, strip=True) # Remove any HTML tags
        project_guid = html.escape(clean_guid) # Escape any special HTML characters

        # Call the delete_project_by_guid function and pass in the project guid
        project_bus_response = bus_project.delete_project_by_guid(project_guid)

        # Return the response from the delete_project_by_guid function
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
