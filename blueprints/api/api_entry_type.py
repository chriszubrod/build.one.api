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
from business import bus_entry_type


entry_type_api_bp = Blueprint('entry_type_api', __name__, url_prefix='/api')





@entry_type_api_bp.route('/post/entry-type', methods=['POST'])
def api_post_entry_type_route():
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

        # Get the entry type name, strip whitespace, clean with bleach and html.escape
        raw_entry_type_name = data.get('name', '').strip()
        clean_entry_type_name = bleach.clean(raw_entry_type_name, strip=True)
        entry_type_name = html.escape(clean_entry_type_name)

        # Get the entry type description, strip whitespace, clean with bleach and html.escape
        raw_entry_type_description = data.get('description', '').strip()
        clean_entry_type_description = bleach.clean(raw_entry_type_description, strip=True)
        entry_type_description = html.escape(clean_entry_type_description)

        # Call the post_entry_type function and pass in the data to create an entry type
        entry_type_bus_response = bus_entry_type.post_entry_type(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            entry_type_name=entry_type_name,
            entry_type_description=entry_type_description
        )

        # Return the response from the post_entry_type function
        return jsonify(
            ApiResponse(
                data=entry_type_bus_response.data,
                message=entry_type_bus_response.message,
                status_code=entry_type_bus_response.status_code,
                success=entry_type_bus_response.success,
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
