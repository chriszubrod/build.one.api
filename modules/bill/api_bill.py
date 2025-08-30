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
from shared.response import ApiResponse
from . import bus_bill


api_bill_bp = Blueprint('api_bill', __name__, url_prefix='/api')


@api_bill_bp.route('/bill/post', methods=['POST'])
def api_post_bill_route():
    """
    Handles the POST request for creating a new bill.
    """
    print(request.json)
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
        #print('api_post_bill_route')
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
        raw_number = data.get('bill-number', '').strip()
        clean_number = bleach.clean(raw_number, strip=True)
        number = html.escape(clean_number)

        # Get the bill date, strip whitespace, clean with bleach and html.escape
        raw_bill_date = data.get('bill-date', '').strip()
        clean_bill_date = bleach.clean(raw_bill_date, strip=True)
        bill_date = html.escape(clean_bill_date)
        bill_date = datetime.strptime(bill_date, '%Y-%m-%d')

        # Get the line items
        bill_line_items = data.get('line-items', [])
        print(f'Line items received: {len(bill_line_items)}')
        for i, line_item in enumerate(bill_line_items):
            print(f'Line item {i}: {line_item}')
            print(f'Line item {i} keys: {line_item.keys()}')
            if 'attachment' in line_item:
                print(f'Line item {i} has attachment: {line_item["attachment"]}')
            else:
                print(f'Line item {i} has no attachment')

        # Get the files
        bill_files = []
        for line_item in bill_line_items:
            if 'attachment' in line_item:
                bill_files.append(line_item['attachment'])

        print(f'Bill files: {bill_files}')
        # Call the post_entry function and pass in the data to create an entry
        bill_bus_response = bus_bill.post_bill_with_line_items_and_attachments(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            vendor_guid=vendor_guid,
            number=number,
            date=bill_date,
            line_items=bill_line_items,
            files=bill_files
        )

        print(f'Bill bus response: {bill_bus_response}')
        # Return the response from the post_entry function
        return jsonify(
            ApiResponse(
                data=bill_bus_response.data,
                message=bill_bus_response.message,
                status_code=bill_bus_response.status_code,
                success=bill_bus_response.success,
                timestamp=bill_bus_response.timestamp
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


@api_bill_bp.route('/bill/patch', methods=['PATCH'])
def api_patch_bill_route():
    """
    Handles the PATCH request for updating a bill.
    """
    print(request.json)
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
        #print('api_post_bill_route')
        #print('--------------------------------')
        #print(f'Request data: {data}')
        #print('--------------------------------')

        # Get the bill guid, strip whitespace, clean with bleach and html.escape
        raw_bill_guid = data.get('bill-guid', '').strip()
        clean_bill_guid = bleach.clean(raw_bill_guid, strip=True)
        bill_guid = html.escape(clean_bill_guid)

        # Get the vendor guid, strip whitespace, clean with bleach and html.escape
        raw_vendor_guid = data.get('vendor', '').strip()
        clean_vendor_guid = bleach.clean(raw_vendor_guid, strip=True)
        vendor_guid = html.escape(clean_vendor_guid)

        # Get the number, strip whitespace, clean with bleach and html.escape
        raw_number = data.get('bill-number', '').strip()
        clean_number = bleach.clean(raw_number, strip=True)
        number = html.escape(clean_number)

        # Get the bill date, strip whitespace, clean with bleach and html.escape
        raw_bill_date = data.get('bill-date', '').strip()
        clean_bill_date = bleach.clean(raw_bill_date, strip=True)
        bill_date = html.escape(clean_bill_date)
        bill_date = datetime.strptime(bill_date, '%Y-%m-%d')

        # Get the line items
        bill_line_items = data.get('line-items', [])
        print(f'Line items received: {len(bill_line_items)}')
        for i, line_item in enumerate(bill_line_items):
            print(f'Line item {i}: {line_item}')
            print(f'Line item {i} keys: {line_item.keys()}')
            if 'attachment' in line_item:
                print(f'Line item {i} has attachment: {line_item["attachment"]}')
            else:
                print(f'Line item {i} has no attachment')

        # Get the files
        bill_files = []
        for line_item in bill_line_items:
            if 'attachment' in line_item:
                bill_files.append(line_item['attachment'])

        print(f'Bill files: {bill_files}')
        # Call the patch_bill_with_line_items_and_attachments function and pass in the data to update a bill
        bill_bus_response = bus_bill.patch_bill_with_line_items_and_attachments(
            bill_guid=bill_guid,
            vendor_guid=vendor_guid,
            number=number,
            date=bill_date,
            line_items=bill_line_items,
            files=bill_files
        )

        print(f'Bill bus response: {bill_bus_response}')
        # Return the response from the patch_bill_with_line_items_and_attachments function
        return jsonify(
            ApiResponse(
                data=bill_bus_response.data,
                message=bill_bus_response.message,
                status_code=bill_bus_response.status_code,
                success=bill_bus_response.success,
                timestamp=bill_bus_response.timestamp
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
