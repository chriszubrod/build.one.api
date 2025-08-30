"""
Module for vendor type API.
"""

# python standard library imports
import html
from datetime import datetime

# third party imports
import bleach
from flask import Blueprint, request, jsonify, session

# local imports
from shared.response import ApiResponse
from modules.vendor_type import bus_vendor_type


api_vendor_type_bp = Blueprint(
    'api_vendor_type',
    __name__,
    url_prefix='/api'
)


@api_vendor_type_bp.route('/post/vendor-type', methods=['POST'])
def api_post_vendor_type_route():
    """
    Endpoint for creating a new vendor type.
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

        # vendor type name
        vendor_type_name = bleach.clean(data.get('name', ''), strip=True)

        # Call the post_vendor_type function and pass in the data to create a vendor type
        vendor_type_bus_response = bus_vendor_type.post_vendor_type(
            vendor_type_name=vendor_type_name
        )

        # Return the response from the post_vendor_type function
        return jsonify(
            ApiResponse(
                data=vendor_type_bus_response.data,
                message=vendor_type_bus_response.message,
                status_code=vendor_type_bus_response.status_code,
                success=vendor_type_bus_response.success,
                timestamp=vendor_type_bus_response.timestamp
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


@api_vendor_type_bp.route('/get/vendor-types', methods=['GET'])
def api_get_vendor_types_route():
    """
    Endpoint for retrieving all vendor types.
    """
    try:
        # Call the get_vendor_types function and pass in the data to retrieve all vendor types
        vendor_type_bus_response = bus_vendor_type.get_vendor_types()

        # Return the response from the get_vendor_types function 
        return jsonify(
            ApiResponse(
                data=vendor_type_bus_response.data,
                message=vendor_type_bus_response.message,
                status_code=vendor_type_bus_response.status_code,
                success=vendor_type_bus_response.success,
                timestamp=vendor_type_bus_response.timestamp
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


@api_vendor_type_bp.route('/get/vendor-type-by-name', methods=['GET'])
def api_get_vendor_type_by_name_route():
    """
    Endpoint for retrieving a vendor type by name.
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

        # vendor type name
        vendor_type_name = bleach.clean(data.get('name', ''), strip=True)

        # Call the get_vendor_type_by_name function and pass in the data to retrieve a vendor type by name
        vendor_type_bus_response = bus_vendor_type.get_vendor_type_by_name(
            vendor_type_name=vendor_type_name
        )

        # Return the response from the get_vendor_type_by_name function
        return jsonify(
            ApiResponse(
                data=vendor_type_bus_response.data,
                message=vendor_type_bus_response.message,
                status_code=vendor_type_bus_response.status_code,
                success=vendor_type_bus_response.success,
                timestamp=vendor_type_bus_response.timestamp
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


@api_vendor_type_bp.route('/get/vendor-type-by-id', methods=['GET'])
def api_get_vendor_type_by_id_route():
    """
    Endpoint for retrieving a vendor type by id.
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

        # Convert ID to integer
        try:
            # vendor type id
            vendor_type_id = int(data.get('id', 0))
        except (ValueError, TypeError):
            return jsonify(
                ApiResponse(
                    data=None,
                    message='Invalid vendor type ID',
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                ).to_dict()
            )

        # Call the get_vendor_type_by_id function and pass in the data to retrieve a vendor type by id
        vendor_type_bus_response = bus_vendor_type.get_vendor_type_by_id(
            vendor_type_id=vendor_type_id
        )

        # Return the response from the get_vendor_type_by_id function
        return jsonify(
            ApiResponse(
                data=vendor_type_bus_response.data,
                message=vendor_type_bus_response.message,
                status_code=vendor_type_bus_response.status_code,
                success=vendor_type_bus_response.success,
                timestamp=vendor_type_bus_response.timestamp
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


@api_vendor_type_bp.route('/get/vendor-type-by-guid', methods=['GET'])
def api_get_vendor_type_by_guid_route():
    """
    Endpoint for retrieving a vendor type by guid.
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

        # vendor type guid
        vendor_type_guid = bleach.clean(data.get('guid', ''), strip=True)

        # Call the get_vendor_type_by_guid function and pass in the data to retrieve a vendor type by guid
        vendor_type_bus_response = bus_vendor_type.get_vendor_type_by_guid(
            vendor_type_guid=vendor_type_guid
        )

        # Return the response from the get_vendor_type_by_guid function
        return jsonify(
            ApiResponse(
                data=vendor_type_bus_response.data,
                message=vendor_type_bus_response.message,
                status_code=vendor_type_bus_response.status_code,
                success=vendor_type_bus_response.success,
                timestamp=vendor_type_bus_response.timestamp
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


@api_vendor_type_bp.route('/patch/vendor-type', methods=['PATCH'])
def api_patch_vendor_type_route():
    """
    Endpoint for updating a vendor type.
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
        print(f'\nAPI Data: {data}\n')

        # Convert ID to integer
        try:
            # vendor type guid
            vendor_type_guid = bleach.clean(data.get('guid', ''), strip=True)
            print(f'\nAPI Vendor Type GUID: {vendor_type_guid}\n')
        except (ValueError, TypeError):
            return jsonify(
                ApiResponse(
                    data=None,
                    message='Invalid vendor type ID',
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                ).to_dict()
            )

        # vendor type name
        vendor_type_name = bleach.clean(data.get('name', ''), strip=True)

        # Call the patch_vendor_type function and pass in the data to update a vendor type
        vendor_type_bus_response = bus_vendor_type.patch_vendor_type(
            vendor_type_guid=vendor_type_guid,
            vendor_type_name=vendor_type_name
        )

        # Return the response from the patch_vendor_type function
        return jsonify(
            ApiResponse(
                data=vendor_type_bus_response.data,
                message=vendor_type_bus_response.message,
                status_code=vendor_type_bus_response.status_code,
                success=vendor_type_bus_response.success,
                timestamp=vendor_type_bus_response.timestamp
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


@api_vendor_type_bp.route('/delete/vendor-type', methods=['DELETE'])
def api_delete_vendor_type_route():
    """
    Endpoint for deleting a vendor type.
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

        # Convert ID to integer
        try:
            # vendor type id
            vendor_type_id = int(data.get('id', 0))
        except (ValueError, TypeError):
            return jsonify(
                ApiResponse(
                    data=None,
                    message='Invalid vendor type ID',
                    status_code=400,
                    success=False,
                    timestamp=datetime.now()
                ).to_dict()
            )

        # Call the delete_vendor_type function and pass in the data to delete a vendor type
        vendor_type_bus_response = bus_vendor_type.delete_vendor_type(
            vendor_type_id=vendor_type_id
        )

        # Return the response from the delete_vendor_type function
        return jsonify(
            ApiResponse(
                data=vendor_type_bus_response.data,
                message=vendor_type_bus_response.message,
                status_code=vendor_type_bus_response.status_code,
                success=vendor_type_bus_response.success,
                timestamp=vendor_type_bus_response.timestamp
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
