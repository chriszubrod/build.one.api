"""
Module for module business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# local imports
from business.bus_response import BusinessResponse
from persistence import pers_module


def get_modules():
    """
    Retrieves all modules from the database.
    """

    pers_read_modules_response = pers_module.read_modules()

    return BusinessResponse(
        data=pers_read_modules_response.data,
        message=pers_read_modules_response.message,
        status_code=pers_read_modules_response.status_code,
        success=pers_read_modules_response.success,
        timestamp=pers_read_modules_response.timestamp
    )


def get_module_by_guid(module_guid):
    """
    Retrieves a module by its GUID.
    """
    pers_read_module_response = pers_module.read_module_by_guid(module_guid)

    return BusinessResponse(
        data=pers_read_module_response.data,
        message=pers_read_module_response.message,
        status_code=pers_read_module_response.status_code,
        success=pers_read_module_response.success,
        timestamp=pers_read_module_response.timestamp
    )


def post_module(
        created_datetime: datetime,
        modified_datetime: datetime,
        name: str,
        description: str,
        slug: str
    ) -> BusinessResponse:
    """
    Posts a module.
    """
    if not name or name == "" or name is None:
        return BusinessResponse(
            data=None,
            message="Missing Module name.",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    if not description or description == "" or description is None:
        return BusinessResponse(
            data=None,
            message="Missing Module description.",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    if not slug or slug == "" or slug is None:
        return BusinessResponse(
            data=None,
            message="Missing Module slug.",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # Check if the module name already exists
    read_module_by_name_pers = pers_module.read_module_by_name(name)
    if read_module_by_name_pers.success:
        return BusinessResponse(
            data=None,
            message="Module Name already exists.",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    # Create the module
    module = pers_module.Module(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        name=name,
        description=description,
        slug=slug
    )

    # Create the module in the database
    create_module_pers_reponse = pers_module.create_module(module)

    return BusinessResponse(
        data=create_module_pers_reponse.data,
        message=create_module_pers_reponse.message,
        status_code=create_module_pers_reponse.status_code,
        success=create_module_pers_reponse.success,
        timestamp=create_module_pers_reponse.timestamp
    )
