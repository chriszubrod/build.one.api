"""
Module for vendor business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business.bus_response import BusinessResponse
from persistence import pers_vendor


def get_vendors() -> BusinessResponse:
    """
    Retrieves all vendors from the database.
    """
    pers_buildone_vendors_resp = pers_vendor.read_vendors()
    return BusinessResponse(
        data=pers_buildone_vendors_resp.data,
        message=pers_buildone_vendors_resp.message,
        success=pers_buildone_vendors_resp.success,
        status_code=pers_buildone_vendors_resp.status_code,
        timestamp=pers_buildone_vendors_resp.timestamp
    )


def get_vendor_by_id(vendor_id: int) -> BusinessResponse:
    """
    Retrieves a vendor by its ID.
    """
    pers_buildone_vendor_resp = pers_vendor.read_vendor_by_id(vendor_id)
    return BusinessResponse(
        data=pers_buildone_vendor_resp.data,
        message=pers_buildone_vendor_resp.message,
        success=pers_buildone_vendor_resp.success,
        status_code=pers_buildone_vendor_resp.status_code,
        timestamp=pers_buildone_vendor_resp.timestamp
    )


def get_vendor_by_guid(vendor_guid: str) -> BusinessResponse:
    """
    Retrieves a vendor by its GUID.
    """
    pers_buildone_vendor_resp = pers_vendor.read_vendor_by_guid(vendor_guid)
    return BusinessResponse(
        data=pers_buildone_vendor_resp.data,
        message=pers_buildone_vendor_resp.message,
        success=pers_buildone_vendor_resp.success,
        status_code=pers_buildone_vendor_resp.status_code,
        timestamp=pers_buildone_vendor_resp.timestamp
    )


def post_vendor(
        created_datetime: datetime,
        modified_datetime: datetime,
        name: str,
        abbreviation: str,
        tax_id_number: str,
        is_active: int,
        vendor_type: str
    ) -> BusinessResponse:
    """
    Posts a vendor.
    """

    # validate name
    if not name or name == "" or name is None:
        return BusinessResponse(
            data=None,
            message="Missing Vendor name.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate abbreviation
    if not abbreviation or abbreviation == "" or abbreviation is None:
        return BusinessResponse(
            data=None,
            message="Missing Vendor abbreviation.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate tax_id_number
    if not tax_id_number or tax_id_number == "" or tax_id_number is None:
        return BusinessResponse(
            data=None,
            message="Missing Vendor tax id number.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate is_active
    if not is_active or is_active is None:
        return BusinessResponse(
            data=None,
            message="Missing Vendor is active.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate vendor_type
    if not vendor_type or vendor_type == "" or vendor_type is None:
        return BusinessResponse(
            data=None,
            message="Missing Vendor type.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    read_vendor_by_name_pers_resp = pers_vendor.read_vendor_by_name(name)
    if read_vendor_by_name_pers_resp.success:
        return BusinessResponse(
            data=None,
            message="Vendor already exists.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    # create vendor object instance
    _vendor = pers_vendor.Vendor(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        name=name,
        abbreviation=abbreviation,
        tax_id_number=tax_id_number,
        is_active=is_active,
        type=vendor_type
    )

    # create vendor
    pers_create_vendor_resp = pers_vendor.create_vendor(_vendor)

    return BusinessResponse(
        data=pers_create_vendor_resp.data,
        message=pers_create_vendor_resp.message,
        success=pers_create_vendor_resp.success,
        status_code=pers_create_vendor_resp.status_code,
        timestamp=pers_create_vendor_resp.timestamp
    )
