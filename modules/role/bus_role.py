"""
Module for role business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from shared.response import BusinessResponse
from modules.role import pers_role


def get_roles() -> BusinessResponse:
    """
    Retrieves all roles from the database.
    """
    read_roles_pers_response = pers_role.read_roles()
    return BusinessResponse(
        data=read_roles_pers_response.data,
        message=read_roles_pers_response.message,
        success=read_roles_pers_response.success,
        status_code=read_roles_pers_response.status_code,
        timestamp=datetime.now(tz.tzlocal())
    )


def get_role_by_id(role_id: int) -> BusinessResponse:
    """
    Retrieves a role by id from the database.
    """
    pers_read_role_response = pers_role.read_role_by_id(role_id)
    return BusinessResponse(
        data=pers_read_role_response.data,
        message=pers_read_role_response.message,
        success=pers_read_role_response.success,
        status_code=pers_read_role_response.status_code,
        timestamp=datetime.now(tz.tzlocal())
    )


def get_role_by_guid(guid: str) -> BusinessResponse:
    """
    Retrieves a role by guid from the database.
    """
    pers_read_role_response = pers_role.read_role_by_guid(guid)
    return BusinessResponse(
        data=pers_read_role_response.data,
        message=pers_read_role_response.message,
        success=pers_read_role_response.success,
        status_code=pers_read_role_response.status_code,
        timestamp=datetime.now(tz.tzlocal())
    )


def get_role_by_name(name: str) -> BusinessResponse:
    """
    Retrieves a role by name from the database.
    """
    pers_read_role_response = pers_role.read_role_by_name(name)
    return BusinessResponse(
        data=pers_read_role_response.data,
        message=pers_read_role_response.message,
        success=pers_read_role_response.success,
        status_code=pers_read_role_response.status_code,
        timestamp=datetime.now(tz.tzlocal())
    )


def post_role(
        created_datetime: datetime,
        modified_datetime: datetime,
        name: str
    ) -> BusinessResponse:
    """
    Create a new role.
    """

    # validate name
    if not name or name == "" or name is None:
        return BusinessResponse(
            data=None,
            message="Missing Role name.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # check if role already exists
    pers_read_role_response = pers_role.read_role_by_name(name)
    if pers_read_role_response.success:
        return BusinessResponse(
            data=None,
            message="Role already exists.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # create role object instance
    _role = pers_role.Role(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        name=name
    )

    # create role in database
    pers_create_role_response = pers_role.create_role(_role)

    return BusinessResponse(
        data=pers_create_role_response.data,
        message=pers_create_role_response.message,
        success=pers_create_role_response.success,
        status_code=pers_create_role_response.status_code,
        timestamp=pers_create_role_response.timestamp
    )
