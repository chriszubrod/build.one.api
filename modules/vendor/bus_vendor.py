"""
Module for vendor business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz
import html

# local imports
from shared.response import BusinessResponse
from modules.vendor import pers_vendor
from modules.vendor_type import pers_vendor_type
from integrations.adapters import map_vendor_to_intuit_vendor as pers_map_vendor_intuit_vendor

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

    # Remove potentially dangerous characters instead of rejecting
    sanitized_name = name.replace('<', '').replace('>', '').replace('"', '').replace("'", '')
    
    # Check if any characters were removed and warn if so
    if len(sanitized_name) != len(name):
        print(f"Warning: Removed dangerous characters from vendor name: '{name}' -> '{sanitized_name}'")

    return True, sanitized_name


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

    # Remove potentially dangerous characters instead of rejecting
    sanitized_abbreviation = abbreviation.replace('<', '').replace('>', '').replace('"', '').replace("'", '')
    
    # Check if any characters were removed and warn if so
    if len(sanitized_abbreviation) != len(abbreviation):
        print(f"Warning: Removed dangerous characters from vendor abbreviation: '{abbreviation}' -> '{sanitized_abbreviation}'")

    return True, sanitized_abbreviation


def post_vendor(name: str, abbreviation: str, is_active: int, vendor_type_id: int) -> BusinessResponse:
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
    if not vendor_type_id or vendor_type_id == "" or vendor_type_id is None:
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
        is_active=is_active,
        vendor_type_id=vendor_type_id
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
        if pers_buildone_vendors_resp.success and pers_buildone_vendors_resp.data:
            for vendor in pers_buildone_vendors_resp.data:
                if vendor.name:
                    vendor.name = html.unescape(vendor.name)
                if vendor.abbreviation:
                    vendor.abbreviation = html.unescape(vendor.abbreviation)

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
        if pers_buildone_vendor_resp.success and pers_buildone_vendor_resp.data:
            vendor = pers_buildone_vendor_resp.data
            if vendor.name:
                vendor.name = html.unescape(vendor.name)
            if vendor.abbreviation:
                vendor.abbreviation = html.unescape(vendor.abbreviation)
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
        if pers_buildone_vendor_resp.success and pers_buildone_vendor_resp.data:
            vendor = pers_buildone_vendor_resp.data
            if vendor.name:
                vendor.name = html.unescape(vendor.name)
            if vendor.abbreviation:
                vendor.abbreviation = html.unescape(vendor.abbreviation)
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


def get_mapped_intuit_vendor_by_vendor_id(vendor_id: int) -> BusinessResponse:
    """
    Retrieves a mapped intuit vendor from the database by vendor id.
    """
    pers_read_mapped_intuit_vendor_resp = pers_map_vendor_intuit_vendor.\
        read_map_vendor_to_intuit_vendor_by_vendor_id(vendor_id=vendor_id)
    
    return BusinessResponse(
        data=pers_read_mapped_intuit_vendor_resp.data,
        message=pers_read_mapped_intuit_vendor_resp.message,
        success=pers_read_mapped_intuit_vendor_resp.success,
        status_code=pers_read_mapped_intuit_vendor_resp.status_code,
        timestamp=pers_read_mapped_intuit_vendor_resp.timestamp
    )


def patch_vendor(guid: str, name: str, abbreviation: str, is_active: int, vendor_type_guid: str) -> BusinessResponse:
    """
    Minimal patch to update a vendor's name and is_active by GUID.

    Looks up the vendor by GUID, then calls the persistence layer to update
    using the existing values for all other required fields.
    """
    try:
        # validate name
        is_valid, name_result = validate_vendor_name(name)
        if not is_valid:
            return BusinessResponse(
                data=None,
                message=name_result,
                success=False,
                status_code=400,
                timestamp=datetime.now()
            )

        # normalize is_active to 0/1
        normalized_is_active = 1 if str(is_active) in ['1', 'true', 'True', 'on', 'yes'] else 0

        # read vendor by guid
        read_resp = pers_vendor.read_vendor_by_guid(vendor_guid=guid)
        if not read_resp.success or not read_resp.data:
            return BusinessResponse(
                data=None,
                message="Vendor not found",
                success=False,
                status_code=404,
                timestamp=datetime.now()
            )
        v = read_resp.data

        # read vendor_type by guid
        read_vendor_type_resp = pers_vendor_type.read_vendor_type_by_guid(vendor_type_guid=vendor_type_guid)
        if not read_vendor_type_resp.success or not read_vendor_type_resp.data:
            return BusinessResponse(
                data=None,
                message="Vendor type not found",
                success=False,
                status_code=404,
                timestamp=datetime.now()
            )
        vt = read_vendor_type_resp.data

        
        
        v.name = name_result
        v.abbreviation = abbreviation
        v.is_active = normalized_is_active
        v.vendor_type_id = vt.id

        # call minimal update by id, preserving existing values for other fields
        update_resp = pers_vendor.update_vendor_by_id(
            vendor=v
        )

        return BusinessResponse(
            data=update_resp.data,
            message=update_resp.message,
            success=update_resp.success,
            status_code=update_resp.status_code,
            timestamp=update_resp.timestamp
        )

    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to update vendor: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )
