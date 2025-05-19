"""
Module for authentication API endpoints.
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
from modules.auth import bus_auth

api_auth_bp = Blueprint('api_auth', __name__, url_prefix='/api')


@api_auth_bp.route('/post/auth/register', methods=['POST'])
def api_post_auth_register_route():
    """
    Handles the POST request for registering a new user.
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
        clean_username = bleach.clean(raw_username, strip=True)
        username = html.escape(clean_username)

        # Get the user name, strip whitespace, clean with bleach and html.escape
        raw_password = data.get('password', '').strip()
        clean_password = bleach.clean(raw_password, strip=True)
        password = html.escape(clean_password)

        # Get the user name, strip whitespace, clean with bleach and html.escape
        raw_confirm_password = data.get('confirmPassword', '').strip()
        clean_confirm_password = bleach.clean(raw_confirm_password, strip=True)
        confirm_password = html.escape(clean_confirm_password)

        post_auth_registration_resp = bus_auth.post_auth_registration(
            username=username,
            password=password,
            confirm_password=confirm_password
        )

        return jsonify(
            ApiResponse(
                data=post_auth_registration_resp.data,
                message=post_auth_registration_resp.message,
                status_code=post_auth_registration_resp.status_code,
                success=post_auth_registration_resp.success,
                timestamp=post_auth_registration_resp.timestamp
            ).to_dict()
        )

    except (ValueError, TypeError, KeyError) as e:
        # Return is exception.
        return jsonify(
            ApiResponse(
                data=None,
                message=str(e),
                status_code=500,
                success=False,
                timestamp=datetime.now()
            ).to_dict()
        )


@api_auth_bp.route('/post/auth/login', methods=['POST'])
def api_post_auth_login_route():
    """
    Handles the POST request for logging in a user.
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
        clean_username = bleach.clean(raw_username, strip=True)
        username = html.escape(clean_username)

        # Get the user name, strip whitespace, clean with bleach and html.escape
        raw_password = data.get('password', '').strip()
        clean_password = bleach.clean(raw_password, strip=True)
        password = html.escape(clean_password)


        post_auth_login_resp = bus_auth.post_auth_login(
            username=username,
            password=password
        )

        # Always define response_data
        if post_auth_login_resp.success:
            response_data = post_auth_login_resp.data
            response_data['redirect_url'] = '/dashboard' # absolute path
        else:
            response_data = None

        return jsonify(
            ApiResponse(
                data=response_data,
                message=post_auth_login_resp.message,
                status_code=post_auth_login_resp.status_code,
                success=post_auth_login_resp.success,
                timestamp=post_auth_login_resp.timestamp
            ).to_dict()
        )

    except (ValueError, TypeError, KeyError) as e:
        # Return is exception.
        return jsonify(
            ApiResponse(
                data=None,
                message=str(e),
                status_code=500,
                success=False,
                timestamp=datetime.now()
            ).to_dict()
        )
