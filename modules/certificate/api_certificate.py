"""
API routes for Certificate of Insurance (COI).
"""

# python standard library imports
from datetime import datetime

# third party imports
from flask import Blueprint, request, jsonify

# local imports
from shared.response import ApiResponse
from modules.certificate import bus_certificate as bus_coi


api_certificate_bp = Blueprint('api_certificate', __name__, url_prefix='/api')


@api_certificate_bp.route('/post/certificate', methods=['POST'])
def api_post_certificate():
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now()).to_dict())

        data = request.json

        resp = bus_coi.post_certificate(
            certificate_type_guid=str(data.get('certificateTypeGuid', '')).strip(),
            policy_number=str(data.get('policyNumber', '')).strip(),
            policy_eff_date=data.get('policyEffDate'),
            policy_exp_date=data.get('policyExpDate'),
            certificate_attachment_guid=str(data.get('certificateAttachmentGuid', '')).strip(),
            vendor_guid=str(data.get('vendorGuid', '')).strip()
        )

        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())

    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now()).to_dict())


@api_certificate_bp.route('/certificates', methods=['GET'])
def api_get_certificates():
    resp = bus_coi.get_certificates()
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_certificate_bp.route('/certificate/<guid>', methods=['GET'])
def api_get_certificate_by_guid(guid):
    resp = bus_coi.get_certificate_by_guid(guid)
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_certificate_bp.route('/certificate/<guid>', methods=['PATCH'])
def api_patch_certificate_by_guid(guid):
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now()).to_dict())

        data = request.json
        resp = bus_coi.patch_certificate_by_guid(
            guid=guid,
            certificate_type_guid=str(data.get('certificateTypeGuid', '')).strip(),
            policy_number=str(data.get('policyNumber', '')).strip(),
            policy_eff_date=data.get('policyEffDate'),
            policy_exp_date=data.get('policyExpDate'),
            certificate_attachment_guid=str(data.get('certificateAttachmentGuid', '')).strip(),
            vendor_guid=str(data.get('vendorGuid', '')).strip()
        )
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now()).to_dict())


@api_certificate_bp.route('/certificate/<int:id>', methods=['DELETE'])
def api_delete_certificate_by_id(id):
    resp = bus_coi.delete_certificate_by_id(id)
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())
