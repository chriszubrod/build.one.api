"""
Business layer for Certificate Type.
"""

# python standard library imports
from datetime import datetime

# local imports
from shared.response import BusinessResponse
from modules.certificate_type import pers_certificate_type as pers_ct


def post_certificate_type(name: str) -> BusinessResponse:
    name = (name or '').strip()
    if not name:
        return BusinessResponse(data=None, message="Invalid name", status_code=400, success=False, timestamp=datetime.now())

    # prevent duplicates by name
    existing = pers_ct.read_certificate_type_by_name(name)
    if existing.success and existing.data:
        return BusinessResponse(data=None, message="Certificate type already exists", status_code=409, success=False, timestamp=datetime.now())

    resp = pers_ct.create_certificate_type(pers_ct.CertificateType(name=name))
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp
    )


def get_certificate_types() -> BusinessResponse:
    resp = pers_ct.read_certificate_types()
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp
    )


def get_certificate_type_by_id(id: int) -> BusinessResponse:
    resp = pers_ct.read_certificate_type_by_id(id)
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp
    )


def get_certificate_type_by_guid(guid: str) -> BusinessResponse:
    resp = pers_ct.read_certificate_type_by_guid(guid)
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp
    )


def patch_certificate_type(guid: str, name: str) -> BusinessResponse:
    # read existing by guid
    existing = pers_ct.read_certificate_type_by_guid(guid)
    if not existing.success or not existing.data:
        return BusinessResponse(data=None, message="Certificate type not found", status_code=404, success=False, timestamp=datetime.now())

    name = (name or '').strip()
    if not name:
        return BusinessResponse(data=None, message="Invalid name", status_code=400, success=False, timestamp=datetime.now())

    # check duplicate name (different guid)
    dupe = pers_ct.read_certificate_type_by_name(name)
    if dupe.success and dupe.data and dupe.data.guid != guid:
        return BusinessResponse(data=None, message="Certificate type name taken", status_code=409, success=False, timestamp=datetime.now())

    upd = pers_ct.update_certificate_type(pers_ct.CertificateType(id=existing.data.id, name=name))
    return BusinessResponse(
        data=upd.data,
        message=upd.message,
        status_code=upd.status_code,
        success=upd.success,
        timestamp=upd.timestamp
    )


def delete_certificate_type(guid: str) -> BusinessResponse:
    existing = pers_ct.read_certificate_type_by_guid(guid)
    if not existing.success or not existing.data:
        return BusinessResponse(data=None, message="Certificate type not found", status_code=404, success=False, timestamp=datetime.now())
    del_resp = pers_ct.delete_certificate_type(pers_ct.CertificateType(id=existing.data.id))
    return BusinessResponse(
        data=del_resp.data,
        message=del_resp.message,
        status_code=del_resp.status_code,
        success=del_resp.success,
        timestamp=del_resp.timestamp
    )

