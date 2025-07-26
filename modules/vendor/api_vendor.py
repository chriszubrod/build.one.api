"""
Module for vendor API.
"""

# python standard library imports
import html
from datetime import datetime

# third party imports
import bleach
from flask import Blueprint, request, jsonify, session

# local imports
from blueprints.api.api_response import ApiResponse
from modules.vendor import bus_vendor


api_vendor_bp = Blueprint(
    'api_vendor',
    __name__,
    url_prefix='/api'
)


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
                    timestamp=datetime.now()
                ).to_dict()
            )

        # Get the JSON data from the request
        data = request.json

        # print the data
        print(f'\nData: {data}\n')

        # vendor name
        vendor_name = bleach.clean(data.get('name', ''), strip=True)

        # vendor abbreviation
        vendor_abbreviation = bleach.clean(data.get('abbreviation', ''), strip=True)

        # vendor tax id number
        vendor_tax_id_number = bleach.clean(data.get('taxidnumber', ''), strip=True)

        # vendor is active
        vendor_is_active = bleach.clean(data.get('isActive', '1'), strip=True)

        # vendor type
        vendor_type = bleach.clean(data.get('vendortype', ''), strip=True)

        # Call the post_vendor function and pass in the data to create a vendor
        vendor_bus_response = bus_vendor.post_vendor(
            name=vendor_name,
            abbreviation=vendor_abbreviation,
            tax_id_number=vendor_tax_id_number,
            is_active=vendor_is_active,
            vendor_type=vendor_type
        )

        # Return the response from the post_user_by_guid function
        return jsonify(
            ApiResponse(
                data=vendor_bus_response.data,
                message=vendor_bus_response.message,
                status_code=vendor_bus_response.status_code,
                success=vendor_bus_response.success,
                timestamp=vendor_bus_response.timestamp
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
                timestamp=datetime.now()
            ).to_dict()
        )
