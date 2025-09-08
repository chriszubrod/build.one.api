"""
API routes for Customer, aligned with Project/Certificate modules.
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
from modules.customer import bus_customer


api_customer_bp = Blueprint('api_customer', __name__, url_prefix='/api')


@api_customer_bp.route('/customers', methods=['GET'])
def api_get_customers_route():
    resp = bus_customer.get_customers()
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_customer_bp.route('/post/customer', methods=['POST'])
def api_post_customer_route():
    """
    Creates a new customer.
    """
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        data = request.json

        raw_name = str(data.get('name', '')).strip() or str(data.get('customername', '')).strip()
        clean_name = bleach.clean(raw_name, strip=True)
        name = html.escape(clean_name)

        raw_is_active = str(data.get('isActive', '1')).strip()
        clean_is_active = bleach.clean(raw_is_active, strip=True)
        is_active = 1 if html.escape(clean_is_active) in ['1', 'true', 'True', 'on', 'yes'] else 0

        resp = bus_customer.post_customer(
            name=name,
            is_active=is_active
        )

        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())

    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_customer_bp.route('/customer/<guid>', methods=['GET'])
def api_get_customer_by_guid_route(guid):
    resp = bus_customer.get_customer_by_guid(guid)
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_customer_bp.route('/customer/<guid>', methods=['PATCH'])
def api_patch_customer_route(guid):
    """
    Updates a customer by GUID.
    """
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        data = request.json

        raw_name = str(data.get('name', '')).strip() or str(data.get('customername', '')).strip()
        raw_name = raw_name.replace('&', 'and')
        clean_name = bleach.clean(raw_name, strip=True)
        name = html.escape(clean_name)

        raw_is_active = str(data.get('isActive', '')).strip()
        clean_is_active = bleach.clean(raw_is_active, strip=True)
        is_active = None
        if raw_is_active != '':
            is_active = 1 if html.escape(clean_is_active) in ['1', 'true', 'True', 'on', 'yes'] else 0

        resp = bus_customer.patch_customer_by_guid(
            guid=guid,
            name=name,
            is_active=is_active
        )

        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())

    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())


@api_customer_bp.route('/customer/<int:id>', methods=['DELETE'])
def api_delete_customer_by_id_route(id):
    resp = bus_customer.delete_customer_by_id(id)
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_customer_bp.route('/post/map/customer-intuit', methods=['POST'])
def api_post_map_customer_intuit_customer_by_guid_route():
    """Creates a mapping using GUIDs only (customer GUID and Intuit customer GUID)."""
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        data = request.json
        customer_guid = str(data.get('customerGuid', '')).strip()
        intuit_customer_guid = str(data.get('intuitCustomerGuid', '')).strip()
        if not customer_guid or not intuit_customer_guid:
            return jsonify(ApiResponse(data=None, message='Missing GUIDs for mapping', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())

        resp = bus_customer.map_customer_to_intuit_customer(customer_guid=customer_guid, intuit_customer_guid=intuit_customer_guid)
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=datetime.now(tz.tzlocal())).to_dict())
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now(tz.tzlocal())).to_dict())
