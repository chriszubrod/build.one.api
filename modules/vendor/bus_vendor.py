"""
Module for vendor business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business.bus_response import BusinessResponse
from modules.vendor import pers_vendor

# Constants
MAX_NAME_LENGTH = 255
MIN_NAME_LENGTH = 1
MAX_ABBR_LENGTH = 10
MIN_ABBR_LENGTH = 1


def validate_vendor_name(name: str) -> tuple[bool, str]:
    """
    Validates the vendor name.
    """
    if not name or not name.strip():
        return False, "Vendor name is required"

    name = name.strip()

    if len(name) > MAX_NAME_LENGTH:
        return False, f"Vendor name must be less than {MAX_NAME_LENGTH} characters"

    if len(name) < MIN_NAME_LENGTH:
        return False, f"Vendor name must be at least {MIN_NAME_LENGTH} characters"

    if any(char in name for char in ['<', '>', '&', '"', "'"]):
        return False, "Vendor name cannot contain invalid characters"

    return True, name


def validate_vendor_abbreviation(abbreviation: str) -> tuple[bool, str]:
    """
    Validates the vendor abbreviation.
    """
    if not abbreviation or not abbreviation.strip():
        return False, "Vendor abbreviation is required"

    abbreviation = abbreviation.strip()

    if len(abbreviation) > MAX_ABBR_LENGTH:
        return False, f"Vendor abbreviation must be less than {MAX_ABBR_LENGTH} characters"

    if len(abbreviation) < MIN_ABBR_LENGTH:
        return False, f"Vendor abbreviation must be at least {MIN_ABBR_LENGTH} characters"

    if any(char in abbreviation for char in ['<', '>', '&', '"', "'"]):
        return False, "Vendor abbreviation cannot contain invalid characters"

    return True, abbreviation


def post_vendor(
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
    is_valid, name_result = validate_vendor_name(
        name=name
    )
    if not is_valid:
        return BusinessResponse(
            data=None,
            message=name_result,
            success=False,
            status_code=400,
            timestamp=datetime.now()
        )

    vendor_name = name_result


    # validate abbreviation
    is_valid, abbreviation_result = validate_vendor_abbreviation(
        abbreviation=abbreviation
    )
    if not is_valid:
        return BusinessResponse(
            data=None,
            message=abbreviation_result,
            success=False,
            status_code=400,
            timestamp=datetime.now()
        )

    vendor_abbreviation = abbreviation_result

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
        name=vendor_name,
        abbreviation=vendor_abbreviation,
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


def get_vendors() -> BusinessResponse:
    """
    Retrieves all vendors from the database.
    """
    try:
        pers_buildone_vendors_resp = pers_vendor.read_vendors()
        return BusinessResponse(
            data=pers_buildone_vendors_resp.data,
            message=pers_buildone_vendors_resp.message,
            success=pers_buildone_vendors_resp.success,
            status_code=pers_buildone_vendors_resp.status_code,
            timestamp=pers_buildone_vendors_resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to read vendors: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )


def get_vendor_by_id(vendor_id: int) -> BusinessResponse:
    """
    Retrieves a vendor by its ID.
    """
    try:
        pers_buildone_vendor_resp = pers_vendor.read_vendor_by_id(vendor_id)
        return BusinessResponse(
            data=pers_buildone_vendor_resp.data,
            message=pers_buildone_vendor_resp.message,
            success=pers_buildone_vendor_resp.success,
            status_code=pers_buildone_vendor_resp.status_code,
            timestamp=pers_buildone_vendor_resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to read vendor by id: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )


def get_vendor_by_guid(vendor_guid: str) -> BusinessResponse:
    """
    Retrieves a vendor by its GUID.
    """
    try:
        pers_buildone_vendor_resp = pers_vendor.read_vendor_by_guid(vendor_guid)
        return BusinessResponse(
            data=pers_buildone_vendor_resp.data,
            message=pers_buildone_vendor_resp.message,
            success=pers_buildone_vendor_resp.success,
            status_code=pers_buildone_vendor_resp.status_code,
            timestamp=pers_buildone_vendor_resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to read vendor by guid: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )

