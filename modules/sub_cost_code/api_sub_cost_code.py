"""
Module for sub cost code API.
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
from modules.sub_cost_code import bus_sub_cost_code


api_sub_cost_code_bp = Blueprint('api_sub_cost_code', __name__, url_prefix='/api')


@api_sub_cost_code_bp.route('/post/sub-cost-code', methods=['POST'])
def api_post_sub_cost_code_route():

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

        # sub cost code number
        raw_number = data.get('number', '').strip()
        clean_number = bleach.clean(raw_number, strip=True) # Remove any HTML tags
        number = html.escape(clean_number) # Escape any special HTML characters

        # sub cost code name
        raw_name = data.get('name', '').strip()
        clean_name = bleach.clean(raw_name, strip=True) # Remove any HTML tags
        name = html.escape(clean_name) # Escape any special HTML characters

        # sub cost code description
        raw_desc = data.get('desc', '').strip()
        clean_desc = bleach.clean(raw_desc, strip=True) # Remove any HTML tags
        desc = html.escape(clean_desc) # Escape any special HTML characters

        # cost code guid
        raw_cost_code_guid = data.get('cost-code', '').strip()
        clean_cost_code_guid = bleach.clean(raw_cost_code_guid, strip=True) # Remove any HTML tags
        cost_code_guid = html.escape(clean_cost_code_guid) # Escape any special HTML characters

        # Call the post_sub_cost_code function and pass in the data to create a sub cost code
        sub_cost_code_bus_response = bus_sub_cost_code.post_sub_cost_code(
            number=number,
            name=name,
            desc=desc,
            cost_code_guid=cost_code_guid
        )

        # Return the response from the post_module function
        return jsonify(
            ApiResponse(
                data=sub_cost_code_bus_response.data,
                message=sub_cost_code_bus_response.message,
                status_code=sub_cost_code_bus_response.status_code,
                success=sub_cost_code_bus_response.success,
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


@api_sub_cost_code_bp.route('/get/sub-cost-codes', methods=['GET'])
def api_get_sub_cost_codes_route():
    """
    Retrieves all sub cost codes from the database.

    Returns:
        A JSON response containing the list of sub cost codes.
    """
    sub_cost_code_bus_response = bus_sub_cost_code.get_sub_cost_codes()
    sub_cost_codes_list = []
    for code in sub_cost_code_bus_response.data:
        sub_cost_code_dict = {
            'guid': code.guid,
            'number': code.number,
            'name': code.name
        }
        sub_cost_codes_list.append(sub_cost_code_dict)
    return jsonify(sub_cost_codes_list)
