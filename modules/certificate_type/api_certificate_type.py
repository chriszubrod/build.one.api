"""
API routes for Certificate Type.
"""

# python standard library imports
from datetime import datetime

# third party imports
from flask import Blueprint, request, jsonify

# local imports
from shared.response import ApiResponse
from modules.certificate_type import bus_certificate_type


api_certificate_type_bp = Blueprint('api_certificate_type', __name__, url_prefix='/api')


@api_certificate_type_bp.route('/post/certificate-type', methods=['POST'])
def api_post_certificate_type_route():
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now()).to_dict())
        data = request.json
        resp = bus_certificate_type.post_certificate_type(name=str(data.get('name', '')).strip(), abbreviation=str(data.get('abbreviation', '')).strip(), description=str(data.get('description', '')).strip())
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now()).to_dict())


@api_certificate_type_bp.route('/certificate-types', methods=['GET'])
def api_get_certificate_types_route():
    resp = bus_certificate_type.get_certificate_types()
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_certificate_type_bp.route('/certificate-type/<guid>', methods=['GET'])
def api_get_certificate_type_by_guid_route(guid):
    resp = bus_certificate_type.get_certificate_type_by_guid(guid)
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_certificate_type_bp.route('/patch/certificate-type', methods=['PATCH'])
def api_patch_certificate_type_route():
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now()).to_dict())
        data = request.json
        resp = bus_certificate_type.patch_certificate_type(guid=str(data.get('guid', '')).strip(), name=str(data.get('name', '')).strip(), abbreviation=str(data.get('abbreviation', '')).strip(), description=str(data.get('description', '')).strip())
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now()).to_dict())


@api_certificate_type_bp.route('/delete/certificate-type', methods=['DELETE'])
def api_delete_certificate_type_route():
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now()).to_dict())
        data = request.json
        resp = bus_certificate_type.delete_certificate_type(guid=str(data.get('guid', '')).strip())
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now()).to_dict())

