"""
Module for user API.
"""

# python standard library imports
import html
from datetime import datetime
from dateutil import tz

# third party imports
import bleach
from flask import Blueprint, request, jsonify, session

# local imports
from blueprints.api.api_response import ApiResponse
from business import bus_user


user_api_bp = Blueprint('user_api', __name__, url_prefix='/api')



@user_api_bp.route('/post/user', methods=['POST'])
def api_post_user_route():
    """
    Handles the POST request for saving a user.
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

        # Get the user name, strip whitespace, clean with bleach and html.escape
        raw_username = data.get('username', '').strip()
        clean_username = bleach.clean(raw_username, strip=True) # Remove any HTML tags
        username = html.escape(clean_username) # Escape any special HTML characters

        # Get the user password, strip whitespace, clean with bleach and html.escape
        raw_password = data.get('password', '').strip()
        clean_password = bleach.clean(raw_password, strip=True) # Remove any HTML tags
        password = html.escape(clean_password) # Escape any special HTML characters

        # Get the role guid, strip whitespace, clean with bleach and html.escape
        raw_role_guid = data.get('roleGuid', '').strip()
        clean_role_guid = bleach.clean(raw_role_guid, strip=True) # Remove any HTML tags
        role_guid = html.escape(clean_role_guid) # Escape any special HTML characters

        # Get the user is active, default to False
        raw_is_active = data.get('isActive', False)
        is_active = True if raw_is_active == 'on' else False

        # Call the post_user function and pass in the data to create a user
        user_bus_response = bus_user.post_user(
            submission_datetime=submission_datetime,
            username=username,
            password=password,
            role_guid=role_guid,
            is_active=is_active
        )

        # Return the response from the post_user_by_guid function
        return jsonify(
            ApiResponse(
                data=user_bus_response.data,
                message=user_bus_response.message,
                status_code=user_bus_response.status_code,
                success=user_bus_response.success,
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
