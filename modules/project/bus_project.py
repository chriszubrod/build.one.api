"""
Module for project business.
"""

# python standard library imports
from datetime import datetime
from dateutil import tz
from typing import Optional

# third party imports


# local imports
from integrations.intuit.persistence import pers_intuit_customer
from integrations.map import (
    pers_map_project_intuit_customer,
    pers_map_project_sharepoint_folder
)
from integrations.ms.persistence import pers_ms_sharepoint_folder
from modules.customer import pers_customer
from modules.module import pers_module
from modules.project import pers_project
from shared.response import BusinessResponse
from integrations.map import (
    pers_map_project_sharepoint_workbook,
    pers_map_project_sharepoint_worksheet
)
from integrations.ms.persistence import (
    pers_ms_sharepoint_site,
    pers_ms_sharepoint_workbook,
    pers_ms_sharepoint_worksheet,
)
from integrations.ms.auth import api_ms_auth




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
    map_resp = pers_map_project_intuit_customer.read_map_project_intuit_customer_by_project_id(project_id)
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


def post_map_project_to_intuit_customer(project_guid: str, intuit_customer_guid: str) -> BusinessResponse:
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
    create_resp = pers_map_project_intuit_customer.create_map_project_intuit_customer(project_id, intuit_customer_id)
    return BusinessResponse(
        data=create_resp.data,
        message=create_resp.message,
        status_code=create_resp.status_code,
        success=create_resp.success,
        timestamp=create_resp.timestamp
    )


def get_ms_sharepoint_folders_by_project_id(project_id: int) -> BusinessResponse:
    """Returns all mapped MS SharePoint Folders for a project across modules."""
    if not project_id:
        return BusinessResponse(
            data=None,
            message="Missing Project id.",
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # Read all mappings for project
    map_resp = pers_map_project_sharepoint_folder.read_project_sharepoint_folder_map_by_project_id(project_id)
    if not getattr(map_resp, 'success', False) or map_resp.data is None:
        # Return empty list on not found for clean UI handling
        return BusinessResponse(
            data=[],
            message=getattr(map_resp, 'message', 'No mappings found'),
            status_code=getattr(map_resp, 'status_code', 200 if map_resp.data == [] else 404),
            success=True,
            timestamp=getattr(map_resp, 'timestamp', datetime.now(tz.tzlocal()))
        )
    mappings = map_resp.data
    print(f"Business Layer Mappings: {mappings}")

    # Load modules to enrich mapping with module name/slug
    modules_resp = pers_module.read_modules()
    modules_by_id = {m.id: m for m in (modules_resp.data or [])} if modules_resp.success else {}

    enriched = []
    for m in mappings:
        folder = None
        if getattr(m, 'ms_sharepoint_folder_id', None):
            sp_resp = pers_ms_sharepoint_folder.read_sharepoint_folder_by_folder_id(int(m.ms_sharepoint_folder_id))
            folder = sp_resp.data if sp_resp.success else None
        module_obj = modules_by_id.get(getattr(m, 'module_id', None))
        enriched.append({
            'mapping': m,
            'module': module_obj,
            'folder': folder
        })

    return BusinessResponse(
        data=enriched,
        message="SharePoint mappings found",
        status_code=200,
        success=True,
        timestamp=datetime.now(tz.tzlocal())
    )


def get_ms_sharepoint_workbooks_by_project_id(project_id: int) -> BusinessResponse:
    """Returns mapped SharePoint Workbooks for a project."""
    if not project_id:
        return BusinessResponse(data=None, message="Missing Project id.", status_code=400, success=False, timestamp=datetime.now(tz.tzlocal()))

    # Read all workbook mappings for project
    map_resp = pers_map_project_sharepoint_workbook.read_map_project_sharepoint_workbook_by_project_id(project_id)
    mappings = map_resp.data if getattr(map_resp, 'success', False) else []

    # Read all workbooks
    enriched = []
    for m in mappings or []:
        wb = None
        if getattr(m, 'ms_sharepoint_workbook_id', None):
            wb_resp = pers_ms_sharepoint_workbook.read_sharepoint_workbook_by_workbook_id(int(m.ms_sharepoint_workbook_id))
            wb = wb_resp.data if getattr(wb_resp, 'success', False) else None
        enriched.append(wb)

    return BusinessResponse(
        data=enriched,
        message="SharePoint workbook mappings found" if enriched else "No SharePoint workbook mappings found",
        status_code=200,
        success=True,
        timestamp=datetime.now(tz.tzlocal())
    )


def post_map_project_to_ms_sharepoint_workbook(project_guid: str, ms_sharepoint_workbook_id: int) -> BusinessResponse:
    """Creates a mapping between Project and SharePoint Workbook."""
    if not project_guid or not ms_sharepoint_workbook_id:
        return BusinessResponse(data=None, message='Missing fields for mapping', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal()))

    proj = pers_project.read_project_by_guid(project_guid)
    if not proj.success or not proj.data:
        return BusinessResponse(data=None, message='Project not found', status_code=404, success=False, timestamp=datetime.now(tz.tzlocal()))

    crt = pers_map_project_sharepoint_workbook.create_map_project_sharepoint_workbook(int(proj.data.id), int(ms_sharepoint_workbook_id))
    return BusinessResponse(data=None, message=crt.message, status_code=crt.status_code, success=crt.success, timestamp=crt.timestamp)


def get_db_ms_sharepoint_worksheets_by_project_id(project_id: int) -> BusinessResponse:
    """Returns mapped SharePoint Worksheets for a project from the database."""
    if not project_id:
        return BusinessResponse(data=None, message="Missing Project id.", status_code=400, success=False, timestamp=datetime.now(tz.tzlocal()))

    # Read all workbook mappings for project
    map_wb_resp = pers_map_project_sharepoint_workbook.read_map_project_sharepoint_workbook_by_project_id(project_id)
    wb_mappings = map_wb_resp.data if getattr(map_wb_resp, 'success', False) else []

    # Read all workbooks
    wb_enriched = []
    for m in wb_mappings or []:
        wb = None
        if getattr(m, 'ms_sharepoint_workbook_id', None):
            wb_resp = pers_ms_sharepoint_workbook.read_sharepoint_workbook_by_workbook_id(int(m.ms_sharepoint_workbook_id))
            wb = wb_resp.data if getattr(wb_resp, 'success', False) else None
        wb_enriched.append(wb)
    
    return BusinessResponse(
        data=wb_enriched,
        message="SharePoint workbook mappings found" if wb_enriched else "No SharePoint workbook mappings found",
        status_code=200,
        success=True,
        timestamp=datetime.now(tz.tzlocal())
    )


def get_ms_sharepoint_worksheets_by_project_id(project_id: int) -> BusinessResponse:
    """Returns mapped SharePoint Worksheets for a project."""
    if not project_id:
        return BusinessResponse(data=None, message="Missing Project id.", status_code=400, success=False, timestamp=datetime.now(tz.tzlocal()))

    # Read all workbook mappings for project
    map_wb_resp = pers_map_project_sharepoint_workbook.read_map_project_sharepoint_workbook_by_project_id(project_id)
    wb_mappings = map_wb_resp.data if getattr(map_wb_resp, 'success', False) else []

    # Read all workbooks
    wb_enriched = []
    for m in wb_mappings or []:
        wb = None
        if getattr(m, 'ms_sharepoint_workbook_id', None):
            wb_resp = pers_ms_sharepoint_workbook.read_sharepoint_workbook_by_workbook_id(int(m.ms_sharepoint_workbook_id))
            wb = wb_resp.data if getattr(wb_resp, 'success', False) else None
        wb_enriched.append(wb)

    # Refresh the MS Site Id
    #site_id = ""
    #site_id_resp = pers_ms_sharepoint_site.read_sharepoint_sites()
    #if site_id_resp.success:
    #    site_id = site_id_resp.data[0].site_sharepoint_id
    #print(f"DEBUG: Site id: {site_id}")



    # Refresh MS Auth token
    #refresh_token_resp = api_ms_auth.refresh_token()
    #print(f"DEBUG: Refresh token response: {refresh_token_resp}")

    # Get worksheets from MS Graph API for each workbook
    #ms_ws_resp = api_ms_auth.get_workbook_worksheets(
    #    site_id=site_id,
    #    item_id=wb_enriched[0].workbook_ms_id
    #)
    #print(f"DEBUG: MS Worksheets response: {ms_ws_resp}")
    #worksheets = ms_ws_resp.data
    #print(f"DEBUG: Worksheets: {worksheets}")

    return BusinessResponse(
        data=worksheets,
        message="SharePoint worksheet mappings found" if worksheets else "No SharePoint worksheet mappings found",
        status_code=200,
        success=True,
        timestamp=datetime.now(tz.tzlocal())
    )


def post_map_project_to_ms_sharepoint_worksheet(project_guid: str, ms_sharepoint_worksheet_id: int) -> BusinessResponse:
    """Creates a mapping between Project and SharePoint Worksheet."""
    if not project_guid or not ms_sharepoint_worksheet_id:
        return BusinessResponse(data=None, message='Missing fields for mapping', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal()))

    proj = pers_project.read_project_by_guid(project_guid)
    if not proj.success or not proj.data:
        return BusinessResponse(data=None, message='Project not found', status_code=404, success=False, timestamp=datetime.now(tz.tzlocal()))

    crt = pers_map_project_sharepoint_worksheet.create_map_project_sharepoint_worksheet(int(proj.data.id), int(ms_sharepoint_worksheet_id))
    return BusinessResponse(data=None, message=crt.message, status_code=crt.status_code, success=crt.success, timestamp=crt.timestamp)


def map_project_to_ms_sharepoint_workbook_by_details(
    project_guid: str,
    name: str,
    web_url: str,
    ms_id: Optional[str] = None,
    c_tag: Optional[str] = None,
    e_tag: Optional[str] = None,
    ms_created_datetime: Optional[str] = None,
    last_modified_datetime: Optional[str] = None,
    size: Optional[int] = None,
    ms_parent_id: Optional[str] = None,
    shared_scope: Optional[str] = None,
    ms_graph_download_url: Optional[str] = None,
    file_mime_type: Optional[str] = None,
    file_hash_quick_xor_hash: Optional[str] = None
) -> BusinessResponse:
    """Ensure workbook exists and map it to the project."""
    if not project_guid or not web_url:
        return BusinessResponse(data=None, message='Missing required fields', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal()))

    proj = pers_project.read_project_by_guid(project_guid)
    if not proj.success or not proj.data:
        return BusinessResponse(data=None, message='Project not found', status_code=404, success=False, timestamp=datetime.now(tz.tzlocal()))
    project_id = int(proj.data.id)

    # Lookup workbook by MsId first, then URL
    wb = None
    if ms_id:
        wb = pers_ms_sharepoint_workbook.read_sharepoint_workbook_by_ms_id(ms_id)
        wb = wb if getattr(wb, 'success', False) and wb.data else None
    if wb is None:
        wb_resp = pers_ms_sharepoint_workbook.read_sharepoint_workbook_by_url(web_url)
        wb = wb_resp if getattr(wb_resp, 'success', False) and wb_resp.data else None

    workbook_id = None
    if wb:
        workbook_id = int(wb.data.workbook_id)
    else:
        spw = pers_ms_sharepoint_workbook.SharePointWorkbook(
            workbook_created_datetime=datetime.now(tz.tzlocal()),
            workbook_modified_datetime=datetime.now(tz.tzlocal()),
            workbook_ms_graph_download_url=ms_graph_download_url,
            workbook_c_tag=c_tag,
            workbook_ms_created_datetime=ms_created_datetime,
            workbook_e_tag=e_tag,
            workbook_file_hash_quick_x_or_hash=file_hash_quick_xor_hash,
            workbook_file_mime_type=file_mime_type,
            workbook_ms_id=ms_id,
            workbook_last_modified_datetime=last_modified_datetime,
            workbook_name=name,
            workbook_ms_parent_id=ms_parent_id,
            workbook_shared_scope=shared_scope,
            workbook_size=size,
            workbook_web_url=web_url
        )
        _ = pers_ms_sharepoint_workbook.create_sharepoint_workbook(spw)
        print(f"DEBUG: Created SharePoint workbook: {_.success}, data: {_.data}")
        reread = pers_ms_sharepoint_workbook.read_sharepoint_workbook_by_ms_id(ms_id) if ms_id else pers_ms_sharepoint_workbook.read_sharepoint_workbook_by_url(web_url)
        print(f"DEBUG: Re-read SharePoint workbook: {reread.success}, data: {reread.data}")
        if getattr(reread, 'success', False) and reread.data:
            workbook_id = int(reread.data.workbook_id)
        else:
            return BusinessResponse(data=None, message='Failed to persist SharePoint workbook', status_code=500, success=False, timestamp=datetime.now(tz.tzlocal()))

    crt = pers_map_project_sharepoint_workbook.create_map_project_sharepoint_workbook(project_id, workbook_id)
    return BusinessResponse(data=None, message=crt.message, status_code=crt.status_code, success=crt.success, timestamp=crt.timestamp)


def map_project_to_ms_sharepoint_worksheet_by_details(
    project_guid: str,
    ms_id: str,
    name: str,
    ms_o_data_id: Optional[str] = None,
    position: Optional[int] = None,
    visibility: Optional[str] = None
) -> BusinessResponse:
    if not project_guid or not ms_id or not name:
        return BusinessResponse(data=None, message='Missing required fields', status_code=400, success=False, timestamp=datetime.now(tz.tzlocal()))

    proj = pers_project.read_project_by_guid(project_guid)
    if not proj.success or not proj.data:
        return BusinessResponse(data=None, message='Project not found', status_code=404, success=False, timestamp=datetime.now(tz.tzlocal()))
    project_id = int(proj.data.id)

    ws = pers_ms_sharepoint_worksheet.read_sharepoint_worksheet_by_ms_id(ms_id)
    worksheet_id = None
    if getattr(ws, 'success', False) and ws.data:
        worksheet_id = int(ws.data.worksheet_id)
    else:
        sps = pers_ms_sharepoint_worksheet.SharePointWorksheet(
            worksheet_created_datetime=datetime.now(tz.tzlocal()),
            worksheet_modified_datetime=datetime.now(tz.tzlocal()),
            worksheet_ms_o_data_id=ms_o_data_id,
            worksheet_ms_id=ms_id,
            worksheet_name=name,
            worksheet_position=position,
            worksheet_visibility=visibility
        )
        _ = pers_ms_sharepoint_worksheet.create_sharepoint_worksheet(sps)
        reread = pers_ms_sharepoint_worksheet.read_sharepoint_worksheet_by_ms_id(ms_id)
        if getattr(reread, 'success', False) and reread.data:
            worksheet_id = int(reread.data.worksheet_id)
        else:
            return BusinessResponse(data=None, message='Failed to persist SharePoint worksheet', status_code=500, success=False, timestamp=datetime.now(tz.tzlocal()))

    crt = pers_map_project_sharepoint_worksheet.create_map_project_sharepoint_worksheet(project_id, worksheet_id)
    return BusinessResponse(data=None, message=crt.message, status_code=crt.status_code, success=crt.success, timestamp=crt.timestamp)


def post_map_project_to_ms_sharepoint_folder(project_guid: str, module_slug: str, ms_sharepoint_folder_id: int) -> BusinessResponse:
    """Creates or updates a mapping between dbo.Project and ms.SharePointFolder for a given module slug."""
    if not project_guid or not module_slug or not ms_sharepoint_folder_id:
        return BusinessResponse(
            data=None,
            message='Missing required fields for mapping',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # Resolve project ID
    proj = pers_project.read_project_by_guid(project_guid)
    if not proj.success or not proj.data:
        return BusinessResponse(
            data=None,
            message='Project not found',
            status_code=404,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )
    project_id = int(proj.data.id)

    # Resolve module ID by slug
    mod = pers_module.read_module_by_slug(module_slug)
    if not mod.success or not mod.data:
        return BusinessResponse(
            data=None,
            message='Module not found',
            status_code=404,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )
    module_id = int(mod.data.id)

    # If a mapping exists, update; otherwise create
    existing = pers_map_project_sharepoint_folder.read_map_project_sharepoint_folders_by_project_by_module(project_id, module_id)
    if getattr(existing, 'success', False) and existing.data:
        mapping = existing.data[0]
        mapping.ms_sharepoint_folder_id = int(ms_sharepoint_folder_id)
        upd = pers_map_project_sharepoint_folder.update_map_project_sharepoint_folder(mapping)
        return BusinessResponse(
            data=None,
            message=upd.message,
            status_code=upd.status_code,
            success=upd.success,
            timestamp=upd.timestamp
        )
    else:
        crt = pers_map_project_sharepoint_folder.create_map_project_sharepoint_folder(project_id, module_id, int(ms_sharepoint_folder_id))
        return BusinessResponse(
            data=None,
            message=crt.message,
            status_code=crt.status_code,
            success=crt.success,
            timestamp=crt.timestamp
        )


def map_project_to_ms_sharepoint_folder(project_guid: str, module_slug: str, ms_sharepoint_folder_id: int) -> BusinessResponse:
    """Alias for post_map_project_to_ms_sharepoint_folder to match API usage pattern."""
    return post_map_project_to_ms_sharepoint_folder(project_guid, module_slug, ms_sharepoint_folder_id)


def map_project_to_ms_sharepoint_folder_by_details(
    project_guid: str,
    module_slug: str,
    name: str,
    web_url: str,
    ms_id: Optional[str] = None,
    c_tag: Optional[str] = None,
    e_tag: Optional[str] = None,
    ms_created_datetime: Optional[str] = None,
    last_modified_datetime: Optional[str] = None,
    size: Optional[int] = None,
    ms_parent_id: Optional[str] = None,
    shared_scope: Optional[str] = None
) -> BusinessResponse:
    """Ensure SharePoint folder exists by URL, then map to project/module by slug."""
    print(f"DEBUG: map_project_to_ms_sharepoint_folder_by_details called with:")
    print(f"  project_guid: {project_guid}")
    print(f"  module_slug: {module_slug}")
    print(f"  name: {name}")
    print(f"  web_url: {web_url}")
    print(f"  ms_id: {ms_id}")
    
    if not project_guid or not module_slug or not web_url:
        return BusinessResponse(
            data=None,
            message='Missing required fields',
            status_code=400,
            success=False,
            timestamp=datetime.now(tz.tzlocal())
        )

    # Resolve existing folder by MsId first (stable), fallback to URL
    existing = None
    if ms_id:
        print(f"DEBUG: Looking for existing folder by ms_id: {ms_id}")
        existing = pers_ms_sharepoint_folder.read_sharepoint_folder_by_ms_id(ms_id)
        print(f"DEBUG: Found by ms_id: {existing.success}, data: {existing.data}")
        if not getattr(existing, 'success', False) or not existing.data:
            existing = None
    
    if existing is None:
        print(f"DEBUG: Looking for existing folder by web_url: {web_url}")
        existing = pers_ms_sharepoint_folder.read_sharepoint_folder_by_url(web_url)
        print(f"DEBUG: Found by web_url: {existing.success}, data: {existing.data}")
    
    folder_id = None
    if getattr(existing, 'success', False) and existing.data:
        folder_id = int(existing.data.folder_id)
        print(f"DEBUG: Using existing folder_id: {folder_id}")
    else:
        print(f"DEBUG: Creating new folder record")
        # Create new folder record with provided details
        folder = pers_ms_sharepoint_folder.SharePointFolder(
            folder_c_tag=c_tag,
            folder_ms_created_datetime=ms_created_datetime,
            folder_e_tag=e_tag,
            folder_folder_child_count=None,
            folder_ms_id=ms_id,
            folder_last_modified_datetime=last_modified_datetime,
            folder_name=name,
            folder_ms_parent_id=ms_parent_id,
            folder_shared_scope=shared_scope,
            folder_size=size,
            folder_web_url=web_url
        )
        print(f"DEBUG: Created folder object: {folder}")
        
        create_resp = pers_ms_sharepoint_folder.create_sharepoint_folder(folder)
        print(f"DEBUG: Create folder response: {create_resp.success}, message: {create_resp.message}")
        
        if not getattr(create_resp, 'success', False) or not create_resp.data:
            return BusinessResponse(
                data=None,
                message=create_resp.message or 'Failed to create SharePoint folder',
                status_code=create_resp.status_code or 500,
                success=False,
                timestamp=create_resp.timestamp or datetime.now(tz.tzlocal())
            )
        
        read_resp = pers_ms_sharepoint_folder.read_sharepoint_folder_by_ms_id(ms_id)
        if not getattr(read_resp, 'success', False) or not read_resp.data:
            return BusinessResponse(
                data=None,
                message=read_resp.message or 'Failed to read SharePoint folder',
                status_code=read_resp.status_code or 500,
                success=False,
                timestamp=read_resp.timestamp or datetime.now(tz.tzlocal())
            )
        folder_id = int(read_resp.data.folder_id)
        print(f"DEBUG: Created new folder_id: {folder_id}")
    
    return map_project_to_ms_sharepoint_folder(
        project_guid=project_guid,
        module_slug=module_slug,
        ms_sharepoint_folder_id=folder_id
    )
