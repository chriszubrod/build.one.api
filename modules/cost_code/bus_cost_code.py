"""
Module for cost code business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business.bus_response import BusinessResponse
from modules.cost_code import pers_cost_code


def post_cost_code(
        created_datetime: datetime,
        modified_datetime: datetime,
        number: str,
        name: str,
        description: str
    ) -> BusinessResponse:
    """
    Posts a cost code.
    """

    # validate number
    if not number:
        return BusinessResponse(
            data=None,
            message='Missing Cost Code number.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate name
    if not name:
        return BusinessResponse(
            data=None,
            message='Missing Cost Code name.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # check if number already exists
    read_cost_code_pers_response = pers_cost_code.read_cost_code_by_number(number)
    if read_cost_code_pers_response.success:
        return BusinessResponse(
            data=read_cost_code_pers_response.data,
            message='Cost Code number already exists.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # check if name already exists
    read_cost_code_pers_response = pers_cost_code.read_cost_code_by_name(name)
    if read_cost_code_pers_response.success:
        return BusinessResponse(
            data=read_cost_code_pers_response.data,
            message='Cost Code name already exists.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # create cost code object instance
    _cost_code = pers_cost_code.CostCode(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        number=number,
        name=name,
        description=description
    )

    # create cost code in database
    post_cost_code_pers_response = pers_cost_code.create_cost_code(_cost_code)

    return BusinessResponse(
        data=post_cost_code_pers_response.data,
        message=post_cost_code_pers_response.message,
        status_code=post_cost_code_pers_response.status_code,
        success=post_cost_code_pers_response.success,
        timestamp=post_cost_code_pers_response.timestamp
    )


def get_cost_codes() -> BusinessResponse:
    """
    Retrieves all cost codes.
    """
    read_cost_codes_pers_response = pers_cost_code.read_cost_codes()
    return BusinessResponse(
        data=read_cost_codes_pers_response.data,
        message=read_cost_codes_pers_response.message,
        status_code=read_cost_codes_pers_response.status_code,
        success=read_cost_codes_pers_response.success,
        timestamp=read_cost_codes_pers_response.timestamp
    )


def get_cost_code_by_guid(cost_code_guid: str) -> BusinessResponse:
    """
    Retrieves a cost code by guid.
    """
    read_cost_code_pers_response = pers_cost_code.read_cost_code_by_guid(cost_code_guid)
    return BusinessResponse(
        data=read_cost_code_pers_response.data,
        message=read_cost_code_pers_response.message,
        status_code=read_cost_code_pers_response.status_code,
        success=read_cost_code_pers_response.success,
        timestamp=read_cost_code_pers_response.timestamp
    )


def get_cost_code_by_id(cost_code_id: int) -> BusinessResponse:
    """
    Retrieves a cost code by id.
    """
    read_cost_code_pers_response = pers_cost_code.read_cost_code_by_id(cost_code_id)
    return BusinessResponse(
        data=read_cost_code_pers_response.data,
        message=read_cost_code_pers_response.message,
        status_code=read_cost_code_pers_response.status_code,
        success=read_cost_code_pers_response.success,
        timestamp=read_cost_code_pers_response.timestamp
    )


def get_cost_code_by_number(cost_code_number: int) -> BusinessResponse:
    """
    Retrieves a cost code by number.
    """
    read_cost_code_pers_response = pers_cost_code.read_cost_code_by_number(cost_code_number)
    return BusinessResponse(
        data=read_cost_code_pers_response.data,
        message=read_cost_code_pers_response.message,
        status_code=read_cost_code_pers_response.status_code,
        success=read_cost_code_pers_response.success,
        timestamp=read_cost_code_pers_response.timestamp
    )


def get_cost_code_by_name(cost_code_name: str) -> BusinessResponse:
    """
    Retrieves a cost code by name.
    """
    read_cost_code_pers_response = pers_cost_code.read_cost_code_by_name(cost_code_name)
    return BusinessResponse(
        data=read_cost_code_pers_response.data,
        message=read_cost_code_pers_response.message,
        status_code=read_cost_code_pers_response.status_code,
        success=read_cost_code_pers_response.success,
        timestamp=read_cost_code_pers_response.timestamp
    )
