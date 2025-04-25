"""
Module for cost code API.
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
from business import bus_cost_code


cost_code_api_bp = Blueprint('cost_code_api', __name__, url_prefix='/api')


@cost_code_api_bp.route('/post/cost-code', methods=['POST'])
def api_post_cost_code_route():
    """
    Handle the POST request for creating a new Cost Code.
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

        # Get the number, strip whitespace, clean with bleach and html.escape
        raw_number = data.get('number', '').strip()
        clean_number = bleach.clean(raw_number, strip=True)
        number = html.escape(clean_number)

        # Get the name, strip whitespace, clean with bleach and html.escape
        raw_name = data.get('name', '').strip()
        clean_name = bleach.clean(raw_name, strip=True)
        name = html.escape(clean_name)

        # Get the description, strip whitespace, clean with bleach and html.escape
        raw_desc = data.get('desc', '').strip()
        clean_desc = bleach.clean(raw_desc, strip=True)
        desc = html.escape(clean_desc)

        # Call the post_cost_code function and pass in the data to create a cost code
        cost_code_bus_response = bus_cost_code.post_cost_code(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            number=number,
            name=name,
            description=desc
        )

        # Return the response from the post_cost_code function
        return jsonify(
            ApiResponse(
                data=cost_code_bus_response.data,
                message=cost_code_bus_response.message,
                status_code=cost_code_bus_response.status_code,
                success=cost_code_bus_response.success,
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
