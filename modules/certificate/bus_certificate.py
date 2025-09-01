"""
Business layer for Certificate of Insurance (COI).
"""

# python standard library imports
from datetime import datetime

# third party imports


# local imports
from shared.response import BusinessResponse
from modules.certificate import pers_certificate as pers_coi
from modules.vendor import pers_vendor
from modules.certificate_type import pers_certificate_type as pers_ct


def post_certificate(
        certificate_type_guid: str,
        policy_number: str,
        policy_eff_date,
        policy_exp_date,
        certificate_attachment_guid: str,
        vendor_guid: str
    ) -> BusinessResponse:
    """Creates a certificate."""

    # basic validation
    if not certificate_type_guid or not str(certificate_type_guid).strip():
        return BusinessResponse(data=None, message="Invalid certificateTypeGuid", status_code=400, success=False, timestamp=datetime.now())
    if not policy_number or not str(policy_number).strip():
        return BusinessResponse(data=None, message="Invalid policyNumber", status_code=400, success=False, timestamp=datetime.now())
    if not policy_eff_date:
        return BusinessResponse(data=None, message="Invalid policyEffDate", status_code=400, success=False, timestamp=datetime.now())
    if not policy_exp_date:
        return BusinessResponse(data=None, message="Invalid policyExpDate", status_code=400, success=False, timestamp=datetime.now())
    # For now, accept attachment GUID but not resolved (table not implemented here)
    if not certificate_attachment_guid or not str(certificate_attachment_guid).strip():
        return BusinessResponse(data=None, message="Invalid certificateAttachmentGuid", status_code=400, success=False, timestamp=datetime.now())
    if not vendor_guid or not str(vendor_guid).strip():
        return BusinessResponse(data=None, message="Invalid vendorGuid", status_code=400, success=False, timestamp=datetime.now())

    # resolve GUIDs to IDs
    vendor_resp = pers_vendor.read_vendor_by_guid(vendor_guid)
    if not vendor_resp.success or not vendor_resp.data:
        return BusinessResponse(data=None, message="Vendor not found", status_code=404, success=False, timestamp=datetime.now())
    vendor_id = vendor_resp.data.id

    ct_resp = pers_ct.read_certificate_type_by_guid(certificate_type_guid)
    if not ct_resp.success or not ct_resp.data:
        return BusinessResponse(data=None, message="Certificate type not found", status_code=404, success=False, timestamp=datetime.now())
    certificate_type_id = ct_resp.data.id

    # TODO: resolve certificate attachment GUID when module exists
    try:
        certificate_attachment_id = int(certificate_attachment_guid)
    except Exception:
        return BusinessResponse(data=None, message="Attachment GUID resolution not implemented", status_code=400, success=False, timestamp=datetime.now())

    _coi = pers_coi.Certificate(
        certificate_type_id=certificate_type_id,
        policy_number=str(policy_number).strip(),
        policy_eff_date=policy_eff_date,
        policy_exp_date=policy_exp_date,
        certificate_attachment_id=certificate_attachment_id,
        vendor_id=vendor_id
    )

    pers_resp = pers_coi.create_certificate(_coi)
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def get_certificates() -> BusinessResponse:
    """Reads all certificates."""
    pers_resp = pers_coi.read_certificates()
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def get_certificate_by_id(id: int) -> BusinessResponse:
    pers_resp = pers_coi.read_certificate_by_id(id)
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def get_certificate_by_guid(guid: str) -> BusinessResponse:
    pers_resp = pers_coi.read_certificate_by_guid(guid)
    return BusinessResponse(
        data=pers_resp.data,
        message=pers_resp.message,
        status_code=pers_resp.status_code,
        success=pers_resp.success,
        timestamp=pers_resp.timestamp
    )


def patch_certificate_by_guid(
        guid: str,
        certificate_type_guid: str,
        policy_number: str,
        policy_eff_date,
        policy_exp_date,
        certificate_attachment_guid: str,
        vendor_guid: str
    ) -> BusinessResponse:
    """Updates a certificate identified by GUID."""
    # read existing
    read_resp = pers_coi.read_certificate_by_guid(guid)
    if not read_resp.success or not read_resp.data:
        return BusinessResponse(
            data=None,
            message=read_resp.message,
            status_code=read_resp.status_code,
            success=False,
            timestamp=read_resp.timestamp
        )

    db_certificate = read_resp.data

    # resolve GUIDs to IDs for update
    vendor_resp = pers_vendor.read_vendor_by_guid(vendor_guid)
    if not vendor_resp.success or not vendor_resp.data:
        return BusinessResponse(data=None, message="Vendor not found", status_code=404, success=False, timestamp=datetime.now())
    vendor_id = vendor_resp.data.id

    ct_resp = pers_ct.read_certificate_type_by_guid(certificate_type_guid)
    if not ct_resp.success or not ct_resp.data:
        return BusinessResponse(data=None, message="Certificate type not found", status_code=404, success=False, timestamp=datetime.now())
    certificate_type_id = ct_resp.data.id

    # TODO: resolve attachment GUID; for now require int-able
    try:
        certificate_attachment_id = int(certificate_attachment_guid)
    except Exception:
        return BusinessResponse(data=None, message="Attachment GUID resolution not implemented", status_code=400, success=False, timestamp=datetime.now())

    _coi = pers_coi.Certificate(
        id=db_certificate.id,
        guid=db_certificate.guid,
        certificate_type_id=certificate_type_id,
        policy_number=str(policy_number).strip(),
        policy_eff_date=policy_eff_date,
        policy_exp_date=policy_exp_date,
        certificate_attachment_id=certificate_attachment_id,
        vendor_id=vendor_id
    )

    update_resp = pers_coi.update_certificate_by_id(_coi)
    return BusinessResponse(
        data=update_resp.data,
        message=update_resp.message,
        status_code=update_resp.status_code,
        success=update_resp.success,
        timestamp=update_resp.timestamp
    )


def delete_certificate_by_id(id: int) -> BusinessResponse:
    del_resp = pers_coi.delete_certificate_by_id(id)
    return BusinessResponse(
        data=del_resp.data,
        message=del_resp.message,
        status_code=del_resp.status_code,
        success=del_resp.success,
        timestamp=del_resp.timestamp
    )
