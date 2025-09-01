"""
Business layer for Certificate Type.
"""

# python standard library imports
from datetime import datetime

# local imports
from shared.response import BusinessResponse
from modules.certificate_type import pers_certificate_type


def post_certificate_type(name: str, abbreviation: str, description: str) -> BusinessResponse:
    name = (name or '').strip()
    if not name:
        return BusinessResponse(data=None, message="Invalid name", status_code=400, success=False, timestamp=datetime.now())

    abbreviation = (abbreviation or '').strip()
    if not abbreviation:
        return BusinessResponse(data=None, message="Invalid abbreviation", status_code=400, success=False, timestamp=datetime.now())

    description = (description or '').strip()
    if not description:
        return BusinessResponse(data=None, message="Invalid description", status_code=400, success=False, timestamp=datetime.now())

    # prevent duplicates by name
    existing = pers_certificate_type.read_certificate_type_by_name(name)
    if existing.success and existing.data:
        return BusinessResponse(data=None, message="Certificate type already exists", status_code=409, success=False, timestamp=datetime.now())

    resp = pers_certificate_type.create_certificate_type(pers_certificate_type.CertificateType(name=name, abbreviation=abbreviation, description=description))
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp
    )


def get_certificate_types() -> BusinessResponse:
    resp = pers_certificate_type.read_certificate_types()
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp
    )


def get_certificate_type_by_id(id: int) -> BusinessResponse:
    resp = pers_certificate_type.read_certificate_type_by_id(id)
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp
    )


def get_certificate_type_by_guid(guid: str) -> BusinessResponse:
    resp = pers_certificate_type.read_certificate_type_by_guid(guid)
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp
    )


def patch_certificate_type(guid: str, name: str, abbreviation: str, description: str) -> BusinessResponse:
    # read existing by guid
    existing = pers_certificate_type.read_certificate_type_by_guid(guid)
    if not existing.success or not existing.data:
        return BusinessResponse(data=None, message="Certificate type not found", status_code=404, success=False, timestamp=datetime.now())

    name = (name or '').strip()
    if not name:
        return BusinessResponse(data=None, message="Invalid name", status_code=400, success=False, timestamp=datetime.now())

    abbreviation = (abbreviation or '').strip()
    if not abbreviation:
        return BusinessResponse(data=None, message="Invalid abbreviation", status_code=400, success=False, timestamp=datetime.now())

    description = (description or '').strip()
    if not description:
        return BusinessResponse(data=None, message="Invalid description", status_code=400, success=False, timestamp=datetime.now())

    # check duplicate name (different guid)
    dupe = pers_certificate_type.read_certificate_type_by_name(name)
    if dupe.success and dupe.data and dupe.data.guid != guid:
        return BusinessResponse(data=None, message="Certificate type name taken", status_code=409, success=False, timestamp=datetime.now())

    upd = pers_certificate_type.update_certificate_type(pers_certificate_type.CertificateType(id=existing.data.id, name=name, abbreviation=abbreviation, description=description))
    return BusinessResponse(
        data=upd.data,
        message=upd.message,
        status_code=upd.status_code,
        success=upd.success,
        timestamp=upd.timestamp
    )


def delete_certificate_type(guid: str) -> BusinessResponse:
    existing = pers_certificate_type.read_certificate_type_by_guid(guid)
    if not existing.success or not existing.data:
        return BusinessResponse(data=None, message="Certificate type not found", status_code=404, success=False, timestamp=datetime.now())
    del_resp = pers_certificate_type.delete_certificate_type(pers_certificate_type.CertificateType(id=existing.data.id))
    return BusinessResponse(
        data=del_resp.data,
        message=del_resp.message,
        status_code=del_resp.status_code,
        success=del_resp.success,
        timestamp=del_resp.timestamp
    )

