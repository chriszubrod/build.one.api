"""
Module for entry type API.
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
from business import bus_module


module_api_bp = Blueprint('module_api', __name__, url_prefix='/api')


@module_api_bp.route('/post/module', methods=['POST'])
#@tm.token_verification
def api_post_module_route():
    """
    Handles the POST request for creating a new module.
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

        # Get the module name, strip whitespace, clean with bleach and html.escape
        raw_module_name = data.get('name', '').strip()
        clean_module_name = bleach.clean(raw_module_name, strip=True)
        module_name = html.escape(clean_module_name)

        # Get the module description, strip whitespace, clean with bleach and html.escape
        raw_module_description = data.get('description', '').strip()
        clean_module_description = bleach.clean(raw_module_description, strip=True)
        module_description = html.escape(clean_module_description)

        # Get the module slug, strip whitespace, clean with bleach and html.escape
        raw_module_slug = data.get('slug', '').strip()
        clean_module_slug = bleach.clean(raw_module_slug, strip=True)
        module_slug = html.escape(clean_module_slug)

        # Call the post_module function and pass in the data to create a module
        module_bus_response = bus_module.post_module(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            module_name=module_name,
            module_description=module_description,
            module_slug=module_slug
        )

        # Return the response from the post_module function
        return jsonify(
            ApiResponse(
                data=module_bus_response.data,
                message=module_bus_response.message,
                status_code=module_bus_response.status_code,
                success=module_bus_response.success,
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
