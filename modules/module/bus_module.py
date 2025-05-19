"""
Module for module business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# third party imports


# local imports
from business.bus_response import BusinessResponse
from modules.module import pers_module


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

    # Check if the module slug already exists
    read_module_by_slug_pers = pers_module.read_module_by_slug(slug)
    if read_module_by_slug_pers.success:
        return BusinessResponse(
            data=None,
            message="Module Slug already exists.",
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


def patch_module_by_guid(
        guid: str,
        modified_datetime: datetime,
        name: str,
        description: str,
        slug: str
    ) -> BusinessResponse:
    """
    Posts a module.
    """
    if not guid or guid == "" or guid is None:
        return BusinessResponse(
            data=None,
            message="Missing Module guid.",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

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

    # Check if the module exists
    read_module_by_guid_pers = pers_module.read_module_by_guid(guid)
    if not read_module_by_guid_pers.success:
        return BusinessResponse(
            data=None,
            message="Module does not exist.",
            status_code=400,
            success=False,
            timestamp=datetime.now()
        )

    _module = read_module_by_guid_pers.data
    _module.modified_datetime = modified_datetime
    _module.name = name
    _module.description = description
    _module.slug = slug

    # Update the module in the database
    update_module_pers_reponse = pers_module.update_module(_module)

    return BusinessResponse(
        data=update_module_pers_reponse.data,
        message=update_module_pers_reponse.message,
        status_code=update_module_pers_reponse.status_code,
        success=update_module_pers_reponse.success,
        timestamp=update_module_pers_reponse.timestamp
    )
