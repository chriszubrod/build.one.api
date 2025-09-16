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
from integrations.map import pers_map_project_intuit_customer as pers_map_pic
from integrations.intuit import pers_intuit_customer
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
        customer_id = read_customer_by_guid_pers_response.data.id
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
        created_datetime=datetime.now(tz.tzlocal()),
        modified_datetime=datetime.now(tz.tzlocal()),
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
        guid: str,
        name: str,
        abbreviation: str,
        status: str,
        customer_guid: str
    ) -> BusinessResponse:
    """
    Updates a project.
    """

    # validate guid
    if not guid or guid == "" or guid is None:
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
        customer_id = read_customer_by_guid_pers_response.data.id
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
        read_project_by_guid(guid)
    if read_project_by_guid_pers_response.success:
        _project = read_project_by_guid_pers_response.data

    # construct updated project
    updated_project = pers_project.Project(
        id=_project.id if _project else None,
        guid=_project.guid if _project else guid,
        modified_datetime=datetime.now(tz.tzlocal()),
        name=name,
        abbreviation=abbreviation,
        status=status,
        customer_id=customer_id
    )

    # update project in database
    update_project_pers_response = pers_project.update_project_by_id(
        project=updated_project
    )

    return BusinessResponse(
        data=update_project_pers_response.data,
        message=update_project_pers_response.message,
        status_code=update_project_pers_response.status_code,
        success=update_project_pers_response.success,
        timestamp=update_project_pers_response.timestamp
    )


def delete_project_by_id(id: int) -> BusinessResponse:
    """Deletes a project by Id."""
    if not id:
        return BusinessResponse(
            data=None,
            message="Missing Project id.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    delete_project_pers_response = pers_project.delete_project_by_id(id)

    return BusinessResponse(
        data=delete_project_pers_response.data,
        message=delete_project_pers_response.message,
        status_code=delete_project_pers_response.status_code,
        success=delete_project_pers_response.success,
        timestamp=delete_project_pers_response.timestamp
    )


def get_intuit_customer_by_project_guid(project_guid: str) -> BusinessResponse:
    """Returns the mapped Intuit Customer for a project GUID, if any."""
    if not project_guid:
        return BusinessResponse(
            data=None,
            message="Missing Project guid.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # Read project to get its ID
    proj_resp = pers_project.read_project_by_guid(project_guid)
    if not proj_resp.success or not proj_resp.data:
        return BusinessResponse(
            data=None,
            message=proj_resp.message,
            status_code=proj_resp.status_code,
            success=False,
            timestamp=proj_resp.timestamp
        )

    project_id = proj_resp.data.id

    # Read mapping by project id
    map_resp = pers_map_pic.read_map_project_intuit_customer_by_project_id(project_id)
    if not map_resp.success or not map_resp.data:
        return BusinessResponse(
            data=None,
            message=map_resp.message,
            status_code=map_resp.status_code,
            success=False,
            timestamp=map_resp.timestamp
        )

    intuit_customer_id = str(map_resp.data.intuit_customer_id)

    # Read intuit customer by id
    intuit_resp = pers_intuit_customer.read_intuit_customer_by_id(intuit_customer_id)
    if not intuit_resp.success or not intuit_resp.data:
        return BusinessResponse(
            data=None,
            message=intuit_resp.message,
            status_code=intuit_resp.status_code,
            success=False,
            timestamp=intuit_resp.timestamp
        )

    return BusinessResponse(
        data=intuit_resp.data,
        message="Intuit Customer found",
        status_code=200,
        success=True,
        timestamp=intuit_resp.timestamp
    )


def get_intuit_projects() -> BusinessResponse:
    """Returns Intuit customers that are projects via persistence layer."""
    resp = pers_intuit_customer.read_intuit_projects()
    return BusinessResponse(
        data=resp.data,
        message=resp.message,
        status_code=resp.status_code,
        success=resp.success,
        timestamp=resp.timestamp
    )


def map_project_to_intuit_customer(project_guid: str, intuit_customer_guid: str) -> BusinessResponse:
    """Creates or updates a mapping between dbo.Project and intuit.Customer."""
    if not project_guid or not intuit_customer_guid:
        return BusinessResponse(
            data=None,
            message='Missing GUIDs for mapping',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # Resolve project ID
    read_project = pers_project.read_project_by_guid(project_guid)
    if not read_project.success or not read_project.data:
        return BusinessResponse(
            data=None,
            message='Project not found',
            status_code=404,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )
    project_id = int(read_project.data.id)

    # Resolve Intuit customer ID
    read_intuit = pers_intuit_customer.read_intuit_customer_by_guid(intuit_customer_guid)
    if not read_intuit.success or not read_intuit.data:
        return BusinessResponse(
            data=None,
            message='Intuit customer not found',
            status_code=404,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )
    intuit_customer_id = int(read_intuit.data.id)

    # Create mapping (SP may upsert or require uniqueness; mirrors Customer mapping approach)
    create_resp = pers_map_pic.create_map_project_intuit_customer(project_id, intuit_customer_id)
    return BusinessResponse(
        data=create_resp.data,
        message=create_resp.message,
        status_code=create_resp.status_code,
        success=create_resp.success,
        timestamp=create_resp.timestamp
    )
