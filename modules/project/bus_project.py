"""
Module for project business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz

# third party imports


# local imports
from shared.response import BusinessResponse
from modules.project import pers_project
from modules.customer import pers_customer


def get_projects() -> BusinessResponse:
    """
    Retrieves all projects from the database.
    """
    read_projects_pers_response = pers_project.read_projects()

    return BusinessResponse(
        data=read_projects_pers_response.data,
        message=read_projects_pers_response.message,
        status_code=read_projects_pers_response.status_code,
        success=read_projects_pers_response.success,
        timestamp=read_projects_pers_response.timestamp
    )


def get_project_by_id(project_id: int) -> BusinessResponse:
    """
    Retrieves a project by its ID.
    """
    read_project_by_id_pers_response = pers_project.\
        read_project_by_id(project_id)

    return BusinessResponse(
        data=read_project_by_id_pers_response.data,
        message=read_project_by_id_pers_response.message,
        status_code=read_project_by_id_pers_response.status_code,
        success=read_project_by_id_pers_response.success,
        timestamp=read_project_by_id_pers_response.timestamp
    )


def get_project_by_guid(project_guid: str) -> BusinessResponse:
    """
    Retrieves a project by its GUID.
    """
    read_project_by_guid_pers_response = pers_project.\
        read_project_by_guid(project_guid)

    return BusinessResponse(
        data=read_project_by_guid_pers_response.data,
        message=read_project_by_guid_pers_response.message,
        status_code=read_project_by_guid_pers_response.status_code,
        success=read_project_by_guid_pers_response.success,
        timestamp=read_project_by_guid_pers_response.timestamp
    )


def get_projects_by_customer_id(customer_id: int) -> BusinessResponse:
    """
    Retrieves a project by its customer ID.
    """
    read_projects_by_customer_id_pers_response = pers_project.\
        read_projects_by_customer_id(customer_id)

    print(customer_id)
    print(read_projects_by_customer_id_pers_response.data)

    return BusinessResponse(
        data=read_projects_by_customer_id_pers_response.data,
        message=read_projects_by_customer_id_pers_response.message,
        status_code=read_projects_by_customer_id_pers_response.status_code,
        success=read_projects_by_customer_id_pers_response.success,
        timestamp=read_projects_by_customer_id_pers_response.timestamp
    )


def post_project(
        created_datetime: datetime,
        modified_datetime: datetime,
        name: str,
        abbreviation: str,
        status: str,
        customer_guid: str
    ) -> BusinessResponse:
    """
    Posts a project.
    """

    # validate name
    if not name or name == "" or name is None:
        return BusinessResponse(
            data=None,
            message="Missing Project name.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate abbreviation
    if not abbreviation or abbreviation == "" or abbreviation is None:
        return BusinessResponse(
            data=None,
            message="Missing Project abbreviation.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate status
    if not status or status == "" or status is None:
        return BusinessResponse(
            data=None,
            message="Missing Project status.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate customer_guid
    if not customer_guid or customer_guid == "" or customer_guid is None:
        return BusinessResponse(
            data=None,
            message="Missing Project customer_guid.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # get customer id
    customer_id = None
    read_customer_by_guid_pers_response = pers_customer.\
        read_customer_by_guid(customer_guid)
    if read_customer_by_guid_pers_response.success:
        customer_id = read_customer_by_guid_pers_response.data.customer_id
    else:
        return BusinessResponse(
            data=None,
            message=read_customer_by_guid_pers_response.message,
            status_code=read_customer_by_guid_pers_response.status_code,
            success=read_customer_by_guid_pers_response.success,
            timestamp=read_customer_by_guid_pers_response.timestamp
        )

    # create project object instance
    _project = pers_project.Project(
        created_datetime=created_datetime,
        modified_datetime=modified_datetime,
        name=name,
        abbreviation=abbreviation,
        status=status,
        customer_id=customer_id
    )

    # create project in database
    create_project_pers_response = pers_project.create_project(_project)

    return BusinessResponse(
        data=create_project_pers_response.data,
        message=create_project_pers_response.message,
        status_code=create_project_pers_response.status_code,
        success=create_project_pers_response.success,
        timestamp=create_project_pers_response.timestamp
    )


def patch_project_by_guid(
        modified_datetime: datetime,
        project_guid: str,
        name: str,
        abbreviation: str,
        status: str
    ) -> BusinessResponse:
    """
    Updates a project.
    """

    # validate project_guid
    if not project_guid or project_guid == "" or project_guid is None:
        return BusinessResponse(
            data=None,
            message="Missing Project guid.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate name
    if not name or name == "" or name is None:
        return BusinessResponse(
            data=None,
            message="Missing Project name.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate abbreviation
    if not abbreviation or abbreviation == "" or abbreviation is None:
        return BusinessResponse(
            data=None,
            message="Missing Project abbreviation.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate status
    if not status or status == "" or status is None:
        return BusinessResponse(
            data=None,
            message="Missing Project status.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # validate customer_guid
    if not customer_guid or customer_guid == "" or customer_guid is None:
        return BusinessResponse(
            data=None,
            message="Missing Project customer_guid.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # get customer id
    customer_id = None
    read_customer_by_guid_pers_response = pers_customer.\
        read_customer_by_guid(customer_guid)
    if read_customer_by_guid_pers_response.success:
        customer_id = read_customer_by_guid_pers_response.data.customer_id
    else:
        return BusinessResponse(
            data=None,
            message=read_customer_by_guid_pers_response.message,
            status_code=read_customer_by_guid_pers_response.status_code,
            success=read_customer_by_guid_pers_response.success,
            timestamp=read_customer_by_guid_pers_response.timestamp
        )

    # get project
    _project = None
    read_project_by_guid_pers_response = pers_project.\
        read_project_by_guid(project_guid)
    if read_project_by_guid_pers_response.success:
        _project = read_project_by_guid_pers_response.data

    # update project in database
    update_project_pers_response = pers_project.update_project_by_id(
        project=_project
    )

    return BusinessResponse(
        data=update_project_pers_response.data,
        message=update_project_pers_response.message,
        status_code=update_project_pers_response.status_code,
        success=update_project_pers_response.success,
        timestamp=update_project_pers_response.timestamp
    )


def delete_project_by_guid(
        project_guid: str
    ) -> BusinessResponse:
    """
    Deletes a project.
    """

    # validate project_guid
    if not project_guid or project_guid == "" or project_guid is None:
        return BusinessResponse(
            data=None,
            message="Missing Project guid.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # get project
    _project = None
    read_project_by_guid_pers_response = pers_project.\
        read_project_by_guid(project_guid)
    if read_project_by_guid_pers_response.success:
        _project = read_project_by_guid_pers_response.data

    # delete project in database
    delete_project_pers_response = pers_project.delete_project_by_id(
        project=_project
    )

    return BusinessResponse(
        data=delete_project_pers_response.data,
        message=delete_project_pers_response.message,
        status_code=delete_project_pers_response.status_code,
        success=delete_project_pers_response.success,
        timestamp=delete_project_pers_response.timestamp
    )
