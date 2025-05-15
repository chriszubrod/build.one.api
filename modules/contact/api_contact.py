"""
Module for contact API.
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
from modules.contact import bus_contact


api_contact_bp = Blueprint('api_contact', __name__, url_prefix='/api')





@api_contact_bp.route('/post/contact', methods=['POST'])
def api_post_contact_route():
    """
    Handles the POST request for creating a contact.
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

        # Get the user guid, strip whitespace, clean with bleach and html.escape
        raw_guid = data.get('userGuid', '').strip()
        clean_guid = bleach.clean(raw_guid, strip=True)
        user_guid = html.escape(clean_guid)

        # Get the first name, strip whitespace, clean with bleach and html.escape
        raw_first_name = data.get('firstname', '').strip()
        clean_first_name = bleach.clean(raw_first_name, strip=True)
        first_name = html.escape(clean_first_name)

        # Get the last name, strip whitespace, clean with bleach and html.escape
        raw_last_name = data.get('lastname', '').strip()
        clean_last_name = bleach.clean(raw_last_name, strip=True)
        last_name = html.escape(clean_last_name)

        # Get the email, convert to lowercase, strip whitespace, clean with bleach and html.escape
        raw_email = data.get('email', '').lower().strip()
        clean_email = bleach.clean(raw_email, strip=True)
        email = html.escape(clean_email)

        # Get the phone number, strip whitespace, clean with bleach and html.escape
        raw_phone = data.get('phone', '').strip()
        clean_phone = bleach.clean(raw_phone, strip=True)
        phone = html.escape(clean_phone)

        # Get the customer guid, strip whitespace, clean with bleach and html.escape
        raw_customer_guid = data.get('customerGuid', '').strip()
        clean_customer_guid = bleach.clean(raw_customer_guid, strip=True)
        customer_guid = html.escape(clean_customer_guid)

        # Call the post_contact function and pass in the data to create a contact
        contact_bus_response = bus_contact.post_contact(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            firstname=first_name,
            lastname=last_name,
            email=email,
            phone=phone,
            customer_guid=customer_guid,
            user_guid=user_guid
        )

        # Return the response from the post_contact function
        return jsonify(
            ApiResponse(
                data=contact_bus_response.data,
                message=contact_bus_response.message,
                status_code=contact_bus_response.status_code,
                success=contact_bus_response.success,
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
