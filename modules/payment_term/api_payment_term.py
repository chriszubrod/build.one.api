"""
Module for payment term API.
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
from modules.payment_term import bus_payment_term


api_payment_term_bp = Blueprint('api_payment_term', __name__, url_prefix='/api')


@api_payment_term_bp.route('/post/payment-term', methods=['POST'])
def api_post_payment_term_route():
    """
    Handles the POST request for creating a payment term.
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


        # Get the payment term name, strip whitespace, clean with bleach and html.escape
        raw_payment_term_name = data.get('name', '').strip()
        clean_payment_term_name = bleach.clean(raw_payment_term_name, strip=True)
        payment_term_name = html.escape(clean_payment_term_name)

        # Get the payment term value, strip whitespace, clean with bleach and html.escape
        raw_payment_term_value = data.get('value', '').strip()
        clean_payment_term_value = bleach.clean(raw_payment_term_value, strip=True)
        payment_term_value = html.escape(clean_payment_term_value)

        # Call the post_payment_term function and pass in the data to create a payment term
        payment_term_bus_response = bus_payment_term.post_payment_term(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            name=payment_term_name,
            value=payment_term_value
        )

        # Return the response from the post_module function
        return jsonify(
            ApiResponse(
                data=payment_term_bus_response.data,
                message=payment_term_bus_response.message,
                status_code=payment_term_bus_response.status_code,
                success=payment_term_bus_response.success,
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

