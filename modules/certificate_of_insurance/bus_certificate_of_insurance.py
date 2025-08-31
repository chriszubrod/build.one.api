"""
Business layer for Certificate of Insurance (COI).
"""

# python standard library imports
from datetime import datetime

# third party imports


# local imports
from shared.response import BusinessResponse
from modules.certificate_of_insurance import pers_certificate_of_insurance as pers_coi


def post_certificate_of_insurance(
        type_of_insurance_id: int,
        policy_number: str,
        policy_eff_date,
        policy_exp_date,
        certificate_of_insurance_attachment_id: int,
        vendor_id: int
    ) -> BusinessResponse:
    """Creates a certificate of insurance."""

    # basic validation
    if not isinstance(type_of_insurance_id, int) or type_of_insurance_id <= 0:
        return BusinessResponse(data=None, message="Invalid typeOfInsuranceId", status_code=400, success=False, timestamp=datetime.now())
    if not policy_number or not str(policy_number).strip():
        return BusinessResponse(data=None, message="Invalid policyNumber", status_code=400, success=False, timestamp=datetime.now())
    if not policy_eff_date:
        return BusinessResponse(data=None, message="Invalid policyEffDate", status_code=400, success=False, timestamp=datetime.now())
    if not policy_exp_date:
        return BusinessResponse(data=None, message="Invalid policyExpDate", status_code=400, success=False, timestamp=datetime.now())
    if not isinstance(certificate_of_insurance_attachment_id, int) or certificate_of_insurance_attachment_id <= 0:
        return BusinessResponse(data=None, message="Invalid certificateOfInsuranceAttachmentId", status_code=400, success=False, timestamp=datetime.now())
    if not isinstance(vendor_id, int) or vendor_id <= 0:
        return BusinessResponse(data=None, message="Invalid vendorId", status_code=400, success=False, timestamp=datetime.now())

    _coi = pers_coi.CertificateOfInsurance(
        type_of_insurance_id=type_of_insurance_id,
        policy_number=str(policy_number).strip(),
        policy_eff_date=policy_eff_date,
        policy_exp_date=policy_exp_date,
        certificate_of_insurance_attachment_id=certificate_of_insurance_attachment_id,
        vendor_id=vendor_id
    )

    pers_resp = pers_coi.create_certificate_of_insurance(_coi)
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def get_certificate_of_insurances() -> BusinessResponse:
    """Reads all certificates of insurance."""
    pers_resp = pers_coi.read_certificate_of_insurances()
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def get_certificate_of_insurance_by_id(coi_id: int) -> BusinessResponse:
    pers_resp = pers_coi.read_certificate_of_insurance_by_id(coi_id)
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def get_certificate_of_insurance_by_guid(coi_guid: str) -> BusinessResponse:
    pers_resp = pers_coi.read_certificate_of_insurance_by_guid(coi_guid)
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def patch_certificate_of_insurance_by_guid(
        coi_guid: str,
        type_of_insurance_id: int,
        policy_number: str,
        policy_eff_date,
        policy_exp_date,
        certificate_of_insurance_attachment_id: int,
        vendor_id: int
    ) -> BusinessResponse:
    """Updates a COI identified by GUID."""
    # read existing
    read_resp = pers_coi.read_certificate_of_insurance_by_guid(coi_guid)
    if not read_resp.success or not read_resp.data:
        return BusinessResponse(
            data=None,
            message=read_resp.message,
            status_code=read_resp.status_code,
            success=False,
            timestamp=read_resp.timestamp
        )

    db_coi = read_resp.data
    _coi = pers_coi.CertificateOfInsurance(
        id=db_coi.id,
        guid=db_coi.guid,
        type_of_insurance_id=type_of_insurance_id,
        policy_number=str(policy_number).strip(),
        policy_eff_date=policy_eff_date,
        policy_exp_date=policy_exp_date,
        certificate_of_insurance_attachment_id=certificate_of_insurance_attachment_id,
        vendor_id=vendor_id
    )

    update_resp = pers_coi.update_certificate_of_insurance_by_id(_coi)
    return BusinessResponse(
        data=update_resp.data,
        message=update_resp.message,
        status_code=update_resp.status_code,
        success=update_resp.success,
        timestamp=update_resp.timestamp
    )


def delete_certificate_of_insurance_by_id(coi_id: int) -> BusinessResponse:
    del_resp = pers_coi.delete_certificate_of_insurance_by_id(coi_id)
    return BusinessResponse(
        data=del_resp.data,
        message=del_resp.message,
        status_code=del_resp.status_code,
        success=del_resp.success,
        timestamp=del_resp.timestamp
    )

