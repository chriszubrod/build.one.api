"""
Module for role API.
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
from modules.role import bus_role


api_role_bp = Blueprint('api_role', __name__, url_prefix='/api')


@api_role_bp.route('/post/role', methods=['POST'])
def api_post_role_route():
    """
    Handles the POST request for saving a role.
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

        # role guid
        raw_guid = data.get('guid', '').strip()
        clean_guid = bleach.clean(raw_guid, strip=True) # Remove any HTML tags
        role_guid = html.escape(clean_guid) # Escape any special HTML characters


        # role name
        raw_name = data.get('name', '').strip()
        clean_name = bleach.clean(raw_name, strip=True) # Remove any HTML tags
        name = html.escape(clean_name) # Escape any special HTML characters

        # Call the post_role_by_guid function and pass in the data to create a role
        role_bus_response = bus_role.post_role_by_guid(
            guid=role_guid,
            name=name
        )

        # Return the response from the post_module function
        return jsonify(
            ApiResponse(
                data=role_bus_response.data,
                message=role_bus_response.message,
                status_code=role_bus_response.status_code,
                success=role_bus_response.success,
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
