"""
Module for entry type business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business.bus_response import BusinessResponse
from persistence import pers_entry_type


def get_entry_types() -> BusinessResponse:
    """
    Retrieves all entry types from the database.
    """
    pers_entry_types_resp = pers_entry_type.read_entry_types()
    return BusinessResponse(
        data=pers_entry_types_resp.data,
        message=pers_entry_types_resp.message,
        status_code=pers_entry_types_resp.status_code,
        success=pers_entry_types_resp.success,
        timestamp=pers_entry_types_resp.timestamp
    )


def get_entry_type_by_id(entry_type_id: int) -> BusinessResponse:
    """
    Retrieves an entry type by its ID.
    """
    pers_entry_type_resp = pers_entry_type.read_entry_type_by_id(entry_type_id)
    return BusinessResponse(
        data=pers_entry_type_resp.data,
        message=pers_entry_type_resp.message,
        status_code=pers_entry_type_resp.status_code,
        success=pers_entry_type_resp.success,
        timestamp=pers_entry_type_resp.timestamp
    )


def post_entry_type(
        created_datetime: datetime,
        modified_datetime: datetime,
        name: str,
        description: str
    ) -> BusinessResponse:
    """
    Posts an entry type to the database.
    """

    # validate name
    if not name or name is None or name == '':
        return BusinessResponse(
            data=None,
            message='Missing Entry Type Name.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate description
    if not description or description is None or description == '':
        return BusinessResponse(
            data=None,
            message='Missing Entry Type Description.',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # create entry type object instance
    _entry_type = pers_entry_type.EntryType(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        name=name,
        description=description
    )

    # create entry type in database
    post_entry_type_pers_response = pers_entry_type.create_entry_type(_entry_type)

    return BusinessResponse(
        data=post_entry_type_pers_response.data,
        message=post_entry_type_pers_response.message,
        status_code=post_entry_type_pers_response.status_code,
        success=post_entry_type_pers_response.success,
        timestamp=post_entry_type_pers_response.timestamp
    )
