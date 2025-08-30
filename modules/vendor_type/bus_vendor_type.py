"""
Module for vendor type business logic.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from shared.response import BusinessResponse
from modules.vendor_type import pers_vendor_type

# Constants
MAX_NAME_LENGTH = 255
MIN_NAME_LENGTH = 1


def validate_vendor_type_name(name: str) -> tuple[bool, str]:
    """
    Validates the vendor type name.
    """
    if not name or not name.strip():
        return False, "Vendor type name is required"

    name = name.strip()

    if len(name) > MAX_NAME_LENGTH:
        return False, f"Vendor type name must be less than {MAX_NAME_LENGTH} characters"

    if len(name) < MIN_NAME_LENGTH:
        return False, f"Vendor type name must be at least {MIN_NAME_LENGTH} characters"

    if any(char in name for char in ['<', '>', '&', '"', "'"]):
        return False, "Vendor type name cannot contain invalid characters"

    return True, name


def post_vendor_type(
        vendor_type_name: str
    ) -> BusinessResponse:
    """
    Creates a vendor type in the database.
    """
    try:
        # validate name
        is_valid, result = validate_vendor_type_name(
            name=vendor_type_name
        )
        if not is_valid:
            return BusinessResponse(
                data=None,
                message=result,
                success=False,
                status_code=400,
                timestamp=datetime.now()
            )

        vendor_type_name = result

        # check if vendor type name already exists
        pers_buildone_vendor_type_resp = pers_vendor_type.read_vendor_type_by_name(
            vendor_type_name=vendor_type_name
        )
        if pers_buildone_vendor_type_resp.success:
            return BusinessResponse(
                data=None,
                message="Vendor type name already exists",
                success=False,
                status_code=400,
                timestamp=datetime.now()
            )

        # create vendor instance
        _vendor_type = pers_vendor_type.VendorType(
            name=vendor_type_name
        )

        # create vendor type
        pers_buildone_vendor_type_resp = pers_vendor_type.create_vendor_type(
            vendor_type=_vendor_type
        )

        # return response
        return BusinessResponse(
            data=pers_buildone_vendor_type_resp.data,
            message=pers_buildone_vendor_type_resp.message,
            success=pers_buildone_vendor_type_resp.success,
            status_code=pers_buildone_vendor_type_resp.status_code,
            timestamp=pers_buildone_vendor_type_resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to create vendor type: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )


def get_vendor_types() -> BusinessResponse:
    """
    Retrieves all vendor types from the database.
    """
    try:
        pers_buildone_vendor_types_resp = pers_vendor_type.read_vendor_types()
        return BusinessResponse(
            data=pers_buildone_vendor_types_resp.data,
            message=pers_buildone_vendor_types_resp.message,
            success=pers_buildone_vendor_types_resp.success,
            status_code=pers_buildone_vendor_types_resp.status_code,
            timestamp=pers_buildone_vendor_types_resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to read vendor types: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )


def get_vendor_type_by_name(vendor_type_name: str) -> BusinessResponse:
    """
    Retrieves a vendor type by name from the database.
    """
    try:
        # Sanitize the vendor type name
        if vendor_type_name:
            vendor_type_name = vendor_type_name.strip()

        # Read the vendor type by name
        pers_buildone_vendor_type_resp = pers_vendor_type.read_vendor_type_by_name(
            vendor_type_name=vendor_type_name
        )
        return BusinessResponse(
            data=pers_buildone_vendor_type_resp.data,
            message=pers_buildone_vendor_type_resp.message,
            success=pers_buildone_vendor_type_resp.success,
            status_code=pers_buildone_vendor_type_resp.status_code,
            timestamp=pers_buildone_vendor_type_resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to read vendor type by name: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )


def get_vendor_type_by_id(vendor_type_id: int) -> BusinessResponse:
    """
    Retrieves a vendor type by id from the database.
    """
    try:
        pers_buildone_vendor_type_resp = pers_vendor_type.read_vendor_type_by_id(
            vendor_type_id=vendor_type_id
        )
        return BusinessResponse(
            data=pers_buildone_vendor_type_resp.data,
            message=pers_buildone_vendor_type_resp.message,
            success=pers_buildone_vendor_type_resp.success,
            status_code=pers_buildone_vendor_type_resp.status_code,
            timestamp=pers_buildone_vendor_type_resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to read vendor type by id: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )


def get_vendor_type_by_guid(vendor_type_guid: str) -> BusinessResponse:
    """
    Retrieves a vendor type by guid from the database.
    """
    try:
        pers_buildone_vendor_type_resp = pers_vendor_type.read_vendor_type_by_guid(
            vendor_type_guid=vendor_type_guid
        )
        return BusinessResponse(
            data=pers_buildone_vendor_type_resp.data,
            message=pers_buildone_vendor_type_resp.message,
            success=pers_buildone_vendor_type_resp.success,
            status_code=pers_buildone_vendor_type_resp.status_code,
            timestamp=pers_buildone_vendor_type_resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to read vendor type by guid: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )


def patch_vendor_type(
        vendor_type_guid: str,
        vendor_type_name: str
    ) -> BusinessResponse:
    """
    Updates a vendor type by guid in the database.
    """
    try:
        # validate name
        is_valid, result = validate_vendor_type_name(
            name=vendor_type_name
        )
        if not is_valid:
            return BusinessResponse(
                data=None,
                message=result,
                success=False,
                status_code=400,
                timestamp=datetime.now()
            )

        vendor_type_name = result

        # check if vendor type exists
        pers_buildone_vendor_type_by_guid_resp = pers_vendor_type.read_vendor_type_by_guid(
            vendor_type_guid=vendor_type_guid
        )
        # if vendor type does not exist, return error
        if not pers_buildone_vendor_type_by_guid_resp.success:
            return BusinessResponse(
                data=None,
                message="Vendor type not found",
                success=False,
                status_code=404,
                timestamp=datetime.now()
            )

        # check if vendor type name already exists
        pers_buildone_vendor_type_resp = pers_vendor_type.read_vendor_type_by_name(
            vendor_type_name=vendor_type_name
        )
        if pers_buildone_vendor_type_resp.success and pers_buildone_vendor_type_resp.data.guid != vendor_type_guid:
            return BusinessResponse(
                data=None,
                message="Vendor type name already exists",
                success=False,
                status_code=400,
                timestamp=datetime.now()
            )

        # create vendor type instance
        _vendor_type = pers_vendor_type.VendorType(
            id=pers_buildone_vendor_type_by_guid_resp.data.id,
            name=vendor_type_name
        )

        # update vendor type
        pers_buildone_vendor_type_resp = pers_vendor_type.update_vendor_type(
            vendor_type=_vendor_type
        )

        # return response
        return BusinessResponse(
            data=pers_buildone_vendor_type_resp.data,
            message=pers_buildone_vendor_type_resp.message,
            success=pers_buildone_vendor_type_resp.success,
            status_code=pers_buildone_vendor_type_resp.status_code,
            timestamp=pers_buildone_vendor_type_resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to update vendor type: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )


def delete_vendor_type(vendor_type_id: int) -> BusinessResponse:
    """
    Deletes a vendor type by id from the database.
    """
    try:
        # check if vendor type exists
        pers_buildone_vendor_type_resp = pers_vendor_type.read_vendor_type_by_id(
            vendor_type_id=vendor_type_id
        )

        # if vendor type does not exist, return error
        if not pers_buildone_vendor_type_resp.success:
            return BusinessResponse(
                data=None,
                message="Vendor type not found",
                success=False,
                status_code=404,
                timestamp=datetime.now()
            )

        _vendor_type = pers_vendor_type.VendorType(
            id=vendor_type_id
        )

        pers_buildone_vendor_type_resp = pers_vendor_type.delete_vendor_type(
            vendor_type=_vendor_type
        )

        return BusinessResponse(
            data=pers_buildone_vendor_type_resp.data,
            message=pers_buildone_vendor_type_resp.message,
            success=pers_buildone_vendor_type_resp.success,
            status_code=pers_buildone_vendor_type_resp.status_code,
            timestamp=pers_buildone_vendor_type_resp.timestamp
        )
    except Exception as e:
        return BusinessResponse(
            data=None,
            message=f"Failed to delete vendor type: {str(e)}",
            success=False,
            status_code=500,
            timestamp=datetime.now()
        )
