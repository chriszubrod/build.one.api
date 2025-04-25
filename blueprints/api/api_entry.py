"""
Module for entry API.
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
from business import bus_bill


entry_api_bp = Blueprint('entry_api', __name__, url_prefix='/api')


@entry_api_bp.route('/entry/<entry_type>/post', methods=['POST'])
def api_post_entry_route(entry_type):
    """
    Handles the POST request for creating a new entry.
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
        #print('api_post_entry_route')
        #print('--------------------------------')
        #print(f'Request data: {data}')
        #print('--------------------------------')

        # Get the submission datetime
        submission_datetime = datetime.now(tz.tzlocal()).strftime('%Y-%m-%d %H:%M:%S%z')
        submission_datetime = submission_datetime[:-2] + ':' + submission_datetime[-2:]

        # Get the vendor guid, strip whitespace, clean with bleach and html.escape
        raw_vendor_guid = data.get('vendor', '').strip()
        clean_vendor_guid = bleach.clean(raw_vendor_guid, strip=True)
        vendor_guid = html.escape(clean_vendor_guid)

        # Get the number, strip whitespace, clean with bleach and html.escape
        raw_number = data.get('number', '').strip()
        clean_number = bleach.clean(raw_number, strip=True)
        number = html.escape(clean_number)

        # Get the entry date, strip whitespace, clean with bleach and html.escape
        raw_entry_date = data.get('date', '').strip()
        clean_entry_date = bleach.clean(raw_entry_date, strip=True)
        entry_date = html.escape(clean_entry_date)

        # Get the line items
        entry_line_items = data.get('line_items', [])

        # Get the files
        entry_files = data.get('files')

        # Call the post_entry function and pass in the data to create an entry
        entry_bus_response = bus_bill.post_entry_with_line_items_and_attachment(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            vendor_guid=vendor_guid,
            number=number,
            date=entry_date,
            entry_type=entry_type,
            line_items=entry_line_items,
            files=entry_files
        )

        print(f'Entry bus response: {entry_bus_response}')
        # Return the response from the post_entry function
        return jsonify(
            ApiResponse(
                data=entry_bus_response.data,
                message=entry_bus_response.message,
                status_code=entry_bus_response.status_code,
                success=entry_bus_response.success,
                timestamp=entry_bus_response.timestamp
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
