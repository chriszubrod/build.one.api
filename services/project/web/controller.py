# Python Standard Library Imports
from fastapi import APIRouter, Request, Depends
from fastapi.templating import Jinja2Templates

# Third-party Imports

# Local Imports
from services.project.business.service import ProjectService
from services.module.business.service import ModuleService
from services.auth.business.service import get_current_user_web
from integrations.ms.sharepoint.driveitem.connector.project.business.service import DriveItemProjectConnector
from integrations.ms.sharepoint.driveitem.connector.project_module.business.service import DriveItemProjectModuleConnector
from integrations.ms.sharepoint.driveitem.connector.project_excel.business.service import DriveItemProjectExcelConnector
from integrations.ms.sharepoint.drive.business.service import MsDriveService
from integrations.ms.sharepoint.drive.connector.company.business.service import DriveCompanyConnector

router = APIRouter(prefix="/project", tags=["web", "project"])
templates = Jinja2Templates(directory="templates")


@router.get("/list")
async def list_projects(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    List all projects.
    """
    projects = ProjectService().read_all()
    return templates.TemplateResponse(
        "project/list.html",
        {
            "request": request,
            "projects": projects,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/create")
async def create_project(request: Request, current_user: dict = Depends(get_current_user_web)):
    """
    Render create project form.
    """
    return templates.TemplateResponse(
        "project/create.html",
        {
            "request": request,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}")
async def view_project(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    View a project.
    """
    project = ProjectService().read_by_public_id(public_id=public_id)
    
    # Get linked folder if any
    linked_folder = None
    linked_drives = []
    module_folders = {}
    linked_excel = None
    project_root_drive_public_id = None
    
    if project and project.id:
        connector = DriveItemProjectConnector()
        linked_folder = connector.get_driveitem_for_project(project_id=int(project.id))
        
        # Get drive public_id for the project root folder if it exists
        if linked_folder:
            from integrations.ms.sharepoint.drive.persistence.repo import MsDriveRepository
            drive_repo = MsDriveRepository()
            
            # The linked_folder dict should have ms_drive_id from the driveitem
            # If not, we need to look it up from the driveitem
            ms_drive_id = linked_folder.get("ms_drive_id")
            if ms_drive_id:
                drive = drive_repo.read_by_id(ms_drive_id)
                if drive:
                    project_root_drive_public_id = drive.public_id
            else:
                # Fallback: look up by item_id
                from integrations.ms.sharepoint.driveitem.persistence.repo import MsDriveItemRepository
                driveitem_repo = MsDriveItemRepository()
                all_items = driveitem_repo.read_all()
                project_driveitem = next((item for item in all_items if item.item_id == linked_folder.get("item_id")), None)
                if project_driveitem:
                    drive = drive_repo.read_by_id(project_driveitem.ms_drive_id)
                    if drive:
                        project_root_drive_public_id = drive.public_id
        
        # Get module folders
        module_connector = DriveItemProjectModuleConnector()
        module_folders = module_connector.get_all_module_folders(project_id=int(project.id))
        
        # Get linked Excel workbook if any
        excel_connector = DriveItemProjectExcelConnector()
        linked_excel = excel_connector.get_excel_for_project(project_id=int(project.id))
        
        # Get only drives linked to companies the user has access to
        from integrations.ms.sharepoint.drive.connector.company.business.service import DriveCompanyConnector
        drive_connector = DriveCompanyConnector()
        company_ids = [company.get("id") for company in current_user.get("companies", []) if company.get("id")]
        
        linked_drives = []
        for company_id in company_ids:
            drive = drive_connector.get_drive_for_company(company_id=company_id)
            if drive:
                linked_drives.append(drive)
    
    # Get all available modules for the dropdown
    modules = ModuleService().read_all()
    modules_list = [module.to_dict() for module in modules]
    
    return templates.TemplateResponse(
        "project/view.html",
        {
            "request": request,
            "project": project.to_dict(),
            "linked_folder": linked_folder,
            "linked_drives": linked_drives,
            "module_folders": module_folders,
            "linked_excel": linked_excel,
            "project_root_drive_public_id": project_root_drive_public_id,
            "modules": modules_list,
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )


@router.get("/{public_id}/edit")
async def edit_project(request: Request, public_id: str, current_user: dict = Depends(get_current_user_web)):
    """
    Edit a project.
    """
    project = ProjectService().read_by_public_id(public_id=public_id)
    return templates.TemplateResponse(
        "project/edit.html",
        {
            "request": request,
            "project": project.to_dict(),
            "current_user": current_user,
            "current_path": request.url.path,
        },
    )
