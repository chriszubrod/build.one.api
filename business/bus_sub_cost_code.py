"""
Module for sub cost code business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business.bus_response import BusinessResponse
from modules.cost_code import pers_cost_code
from persistence import pers_sub_cost_code


def get_sub_cost_codes() -> BusinessResponse:
    """
    Retrieves all sub cost codes from the database.
    """
    read_sub_cost_codes_pers_response = pers_sub_cost_code.read_sub_cost_codes()

    return BusinessResponse(
        data=read_sub_cost_codes_pers_response.data,
        message=read_sub_cost_codes_pers_response.message,
        success=read_sub_cost_codes_pers_response.success,
        status_code=read_sub_cost_codes_pers_response.status_code,
        timestamp=read_sub_cost_codes_pers_response.timestamp
    )


def get_sub_cost_code_by_name(sub_cost_code_name: str) -> BusinessResponse:
    """
    Retrieves a sub cost code from the database by name.
    """
    pers_read_sub_cost_code_resp = pers_sub_cost_code.\
        read_sub_cost_code_by_name(name=sub_cost_code_name)

    return BusinessResponse(
        data=pers_read_sub_cost_code_resp.data,
        message=pers_read_sub_cost_code_resp.message,
        success=pers_read_sub_cost_code_resp.success,
        status_code=pers_read_sub_cost_code_resp.status_code,
        timestamp=pers_read_sub_cost_code_resp.timestamp
    )


def post_sub_cost_code(
        created_datetime: datetime,
        modified_datetime: datetime,
        number: str,
        name: str,
        description: str,
        cost_code_guid: str
    ) -> BusinessResponse:
    """
    Creates a new sub cost code.
    """

    # validate number
    if not number or number == "" or number is None:
        return BusinessResponse(
            data=None,
            message="Missing Sub Cost Code number.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate name
    if not name or name == "" or name is None:
        return BusinessResponse(
            data=None,
            message="Missing Sub Cost Code name.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate description
    if not description or description == "" or description is None:
        return BusinessResponse(
            data=None,
            message="Missing Sub Cost Code description.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate cost_code_guid
    if not cost_code_guid or cost_code_guid == "" or cost_code_guid is None:
        return BusinessResponse(
            data=None,
            message="Missing Sub Cost Code cost code guid.",
            success=False,
            status_code=400,
            timestamp=datetime.now(tz.tzlocal())
        )

    # get cost code id
    cost_code_id = None
    pers_read_cost_code_resp = pers_cost_code.read_cost_code_by_guid(cost_code_guid)
    if pers_read_cost_code_resp.success:
        cost_code_id = pers_read_cost_code_resp.data.cost_code_id
    else:
        return BusinessResponse(
            data=None,
            message=pers_read_cost_code_resp.message,
            success=pers_read_cost_code_resp.success,
            status_code=pers_read_cost_code_resp.status_code,
            timestamp=pers_read_cost_code_resp.timestamp
        )

    # create sub cost code object instance
    _sub_cost_code = pers_sub_cost_code.SubCostCode(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        number=number,
        name=name,
        description=description,
        cost_code_id=cost_code_id
    )

    # create sub cost code
    pers_create_sub_cost_code_resp = pers_sub_cost_code.\
        create_sub_cost_code(_sub_cost_code)

    return BusinessResponse(
        data=pers_create_sub_cost_code_resp.data,
        message=pers_create_sub_cost_code_resp.message,
        success=pers_create_sub_cost_code_resp.success,
        status_code=pers_create_sub_cost_code_resp.status_code,
        timestamp=pers_create_sub_cost_code_resp.timestamp
    )
