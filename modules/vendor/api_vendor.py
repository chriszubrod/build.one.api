"""
Module for vendor API.
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
from modules.vendor import bus_vendor


api_vendor_bp = Blueprint('api_vendor', __name__, url_prefix='/api')


@api_vendor_bp.route('/post/vendor', methods=['POST'])
def api_post_vendor_route():
    """
    Endpoint for creating a new vendor.
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

        # vendor name
        raw_vendor_name = data.get('vendorname', '').strip()
        clean_vendor_name = bleach.clean(raw_vendor_name, strip=True)
        vendor_name = html.escape(clean_vendor_name)

        # vendor abbreviation
        raw_abbreviation = data.get('abbreviation', '').strip()
        clean_abbreviation = bleach.clean(raw_abbreviation, strip=True)
        abbreviation = html.escape(clean_abbreviation)

        # vendor tax id number
        raw_tax_id_number = data.get('taxidnumber', '').strip()
        clean_tax_id_number = bleach.clean(raw_tax_id_number, strip=True)
        tax_id_number = html.escape(clean_tax_id_number)

        # vendor is active
        raw_is_active = data.get('isActive', '1')
        clean_is_active = bleach.clean(raw_is_active, strip=True)
        is_active = html.escape(clean_is_active)

        # vendor type
        raw_vendor_type = data.get('vendortype', '').strip()
        clean_vendor_type = bleach.clean(raw_vendor_type, strip=True)
        vendor_type = html.escape(clean_vendor_type)

        # Call the post_vendor function and pass in the data to create a vendor
        vendor_bus_response = bus_vendor.post_vendor(
            submission_datetime=submission_datetime,
            vendor_name=vendor_name,
            abbreviation=abbreviation,
            tax_id_number=tax_id_number,
            is_active=is_active,
            vendor_type=vendor_type
        )

        # Return the response from the post_user_by_guid function
        return jsonify(
            ApiResponse(
                data=vendor_bus_response.data,
                message=vendor_bus_response.message,
                status_code=vendor_bus_response.status_code,
                success=vendor_bus_response.success,
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
