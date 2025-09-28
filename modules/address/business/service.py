"""
Module for address business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from shared.response import BusinessResponse
from utils import function_help as fhp
from modules.address import pers_address


def post_address(
        created_datetime: datetime,
        modified_datetime: datetime,
        street_one: str,
        street_two: str,
        city: str,
        state: str,
        zip_code: str
    ) -> BusinessResponse:
    """
    Posts an address.
    """

    # validate street_one
    if not street_one or street_one == "" or street_one is None:
        return BusinessResponse(
            data=None,
            message='Invalid or missing street one.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate city
    if not city or city == "" or city is None:
        return BusinessResponse(
            data=None,
            message='Invalid or missing city.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate state
    if not state or state == "" or state is None:
        return BusinessResponse(
            data=None,
            message='Invalid or missing state.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate zip_code
    if not zip_code or zip_code == "" or zip_code is None:
        return BusinessResponse(
            data=None,
            message='Invalid or missing zip code.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # create address object instance
    _address = pers_address.Address(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        street_one=street_one,
        street_two=street_two,
        city=city,
        state=state,
        zip_code=zip_code
    )

    # create address in database
    post_address_pers_response = pers_address.create_address(_address)

    return BusinessResponse(
        data=post_address_pers_response.data,
        message=post_address_pers_response.message,
        status_code=post_address_pers_response.status_code,
        success=post_address_pers_response.success,
        timestamp=post_address_pers_response.timestamp
    )


def get_addresses() -> BusinessResponse:
    """
    Retrieves all addresses from the database.
    """
    read_addresses_pers_response = pers_address.read_addresses()
    return BusinessResponse(
        data=read_addresses_pers_response.data,
        success=read_addresses_pers_response.success,
        message=read_addresses_pers_response.message,
        status_code=read_addresses_pers_response.status_code,
        timestamp=read_addresses_pers_response.timestamp
    )


def get_address_by_guid(address_guid: str) -> BusinessResponse:
    """
    Retrieves an address by guid from the database.
    """
    # read address by guid
    pers_read_address_response = pers_address.read_address_by_guid(address_guid)
    return BusinessResponse(
        data=pers_read_address_response.data,
        message=pers_read_address_response.message,
        status_code=pers_read_address_response.status_code,
        success=pers_read_address_response.success,
        timestamp=pers_read_address_response.timestamp
    )


def patch_address_by_guid(
        address_guid: str,
        modified_datetime: datetime,
        street_one: str,
        street_two: str,
        city: str,
        state: str,
        zip_code: str
    ) -> BusinessResponse:
    """
    Patches an address.
    """
    # read address by guid
    pers_read_address_response = pers_address.read_address_by_guid(address_guid)

    # if address exists, update instance of address
    if pers_read_address_response.success:
        db_address_data = pers_read_address_response.data
        _address = pers_address.Address(
            id=db_address_data.id,
            guid=db_address_data.guid,
            modified_datetime=modified_datetime,
            street_one=street_one,
            street_two=street_two,
            city=city,
            state=state,
            zip_code=zip_code
        )
        # update address by guid in database
        pers_update_address_response = pers_address.update_address_by_id(_address)
        return BusinessResponse(
            data=pers_update_address_response.data,
            message=pers_update_address_response.message,
            status_code=pers_update_address_response.status_code,
            success=pers_update_address_response.success,
            timestamp=pers_update_address_response.timestamp
        )
    else:
        # if address does not exist, return message
        return BusinessResponse(
            data=None,
            message=pers_read_address_response.message,
            status_code=pers_read_address_response.status_code,
            success=pers_read_address_response.success,
            timestamp=pers_read_address_response.timestamp
        )
