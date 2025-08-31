"""
API routes for Certificate of Insurance (COI).
"""

# python standard library imports
from datetime import datetime

# third party imports
from flask import Blueprint, request, jsonify

# local imports
from shared.response import ApiResponse
from modules.certificate_of_insurance import bus_certificate_of_insurance as bus_coi


api_certificate_of_insurance_bp = Blueprint('api_certificate_of_insurance', __name__, url_prefix='/api')


@api_certificate_of_insurance_bp.route('/post/certificate-of-insurance', methods=['POST'])
def api_post_certificate_of_insurance_route():
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now()).to_dict())

        data = request.json

        resp = bus_coi.post_certificate_of_insurance(
            type_of_insurance_id=int(data.get('typeOfInsuranceId', 0)),
            policy_number=str(data.get('policyNumber', '')).strip(),
            policy_eff_date=data.get('policyEffDate'),
            policy_exp_date=data.get('policyExpDate'),
            certificate_of_insurance_attachment_id=int(data.get('certificateOfInsuranceAttachmentId', 0)),
            vendor_id=int(data.get('vendorId', 0))
        )

        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())

    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now()).to_dict())


@api_certificate_of_insurance_bp.route('/certificate-of-insurances', methods=['GET'])
def api_get_certificate_of_insurances_route():
    resp = bus_coi.get_certificate_of_insurances()
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_certificate_of_insurance_bp.route('/certificate-of-insurance/<coi_guid>', methods=['GET'])
def api_get_certificate_of_insurance_by_guid_route(coi_guid):
    resp = bus_coi.get_certificate_of_insurance_by_guid(coi_guid)
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())


@api_certificate_of_insurance_bp.route('/certificate-of-insurance/<coi_guid>', methods=['PATCH'])
def api_patch_certificate_of_insurance_by_guid_route(coi_guid):
    try:
        if not request.is_json:
            return jsonify(ApiResponse(data=None, message='Content type must be application/json', status_code=400, success=False, timestamp=datetime.now()).to_dict())

        data = request.json
        resp = bus_coi.patch_certificate_of_insurance_by_guid(
            coi_guid=coi_guid,
            type_of_insurance_id=int(data.get('typeOfInsuranceId', 0)),
            policy_number=str(data.get('policyNumber', '')).strip(),
            policy_eff_date=data.get('policyEffDate'),
            policy_exp_date=data.get('policyExpDate'),
            certificate_of_insurance_attachment_id=int(data.get('certificateOfInsuranceAttachmentId', 0)),
            vendor_id=int(data.get('vendorId', 0))
        )
        return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())
    except (ValueError, TypeError, KeyError) as e:
        return jsonify(ApiResponse(data=None, message=str(e), status_code=500, success=False, timestamp=datetime.now()).to_dict())


@api_certificate_of_insurance_bp.route('/certificate-of-insurance/<int:coi_id>', methods=['DELETE'])
def api_delete_certificate_of_insurance_by_id_route(coi_id):
    resp = bus_coi.delete_certificate_of_insurance_by_id(coi_id)
    return jsonify(ApiResponse(data=resp.data, message=resp.message, status_code=resp.status_code, success=resp.success, timestamp=resp.timestamp).to_dict())
