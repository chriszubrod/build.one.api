"""
Module for company API.
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
from modules.company import bus_company

api_company_bp = Blueprint('api_company', __name__, url_prefix='/api')


@api_company_bp.route('/get/company', methods=['GET'])
def get_company():
    """
    Get company.
    """
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
    
    bus_get_company_response = bus_company.get_company()

    return jsonify(
        ApiResponse(
            data=bus_get_company_response.data,
            message=bus_get_company_response.message,
            status_code=bus_get_company_response.status_code,
            success=bus_get_company_response.success,
            timestamp=bus_get_company_response.timestamp
        ).to_dict()
    )


@api_company_bp.route('/post/company', methods=['POST'])
def post_company():
    """
    Create company.
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

        # Get the company name, strip whitespace, clean with bleach and html.escape
        raw_company_name = data.get('name', '').strip()
        clean_company_name = bleach.clean(raw_company_name, strip=True)
        company_name = html.escape(clean_company_name)

        # Call the post_company function and pass in the data to create a company
        company_bus_response = bus_company.post_company(
            created_datetime=submission_datetime,
            modified_datetime=submission_datetime,
            name=company_name
        )

        # Return the response from the post_company function
        return jsonify(
            ApiResponse(
                data=company_bus_response.data,
                message=company_bus_response.message,
                status_code=company_bus_response.status_code,
                success=company_bus_response.success,
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


@api_company_bp.route('/patch/company', methods=['PATCH'])
def patch_company():
    """
    Update company.
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
        print(data)
        # Get the submission datetime
        submission_datetime = datetime.now(tz.tzlocal()).strftime('%Y-%m-%d %H:%M:%S%z')
        submission_datetime = submission_datetime[:-2] + ':' + submission_datetime[-2:]

        # Get the company guid
        raw_company_guid = data.get('guid', '').strip()
        clean_company_guid = bleach.clean(raw_company_guid, strip=True)
        company_guid = html.escape(clean_company_guid)

        # Get the company name, strip whitespace, clean with bleach and html.escape
        raw_company_name = data.get('name', '').strip()
        clean_company_name = bleach.clean(raw_company_name, strip=True)
        company_name = html.escape(clean_company_name)

        # Call the post_company function and pass in the data to create a company
        company_bus_response = bus_company.patch_company(
            company_guid=company_guid,
            modified_datetime=submission_datetime,
            name=company_name
        )
        print(company_bus_response.data)

        # Return the response from the patch_company function
        return jsonify(
            ApiResponse(
                data=company_bus_response.data,
                message=company_bus_response.message,
                status_code=company_bus_response.status_code,
                success=company_bus_response.success,
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
        
    
@api_company_bp.route('/delete/company', methods=['DELETE'])
def delete_company():
    """
    Delete company.
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

        # Get the company guid
        raw_company_guid = data.get('guid', '').strip()
        clean_company_guid = bleach.clean(raw_company_guid, strip=True)
        company_guid = html.escape(clean_company_guid)

        # Call the delete_company function and pass in the data to delete a company
        company_bus_response = bus_company.delete_company(
            company_guid=company_guid
        )

        # Return the response from the delete_company function
        return jsonify(
            ApiResponse(
                data=company_bus_response.data,
                message=company_bus_response.message,
                status_code=company_bus_response.status_code,
                success=company_bus_response.success,
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
