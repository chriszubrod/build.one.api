"""
Module for customer API.
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
from modules.customer import bus_customer


api_customer_bp = Blueprint('api_customer', __name__, url_prefix='/api')


@api_customer_bp.route('/post/customer', methods=['POST'])
#@tm.token_verification
def api_post_customer_route():
    """
    Handles the POST request for creating a new customer.
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

        # Get the customer name, strip whitespace, clean with bleach and html.escape
        raw_customer_name = data.get('customername', '').strip()
        clean_customer_name = bleach.clean(raw_customer_name, strip=True)
        customer_name = html.escape(clean_customer_name)

        # Get the is active, strip whitespace, clean with bleach and html.escape
        raw_is_active = data.get('isActive', '1').strip()
        clean_is_active = bleach.clean(raw_is_active, strip=True)
        is_active = html.escape(clean_is_active)

        # Call the post_customer function and pass in the data to create a customer
        customer_bus_response = bus_customer.post_customer(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            customer_name=customer_name,
            is_active=is_active
        )

        # Return the response from the post_customer function
        return jsonify(
            ApiResponse(
                data=customer_bus_response.data,
                message=customer_bus_response.message,
                status_code=customer_bus_response.status_code,
                success=customer_bus_response.success,
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
