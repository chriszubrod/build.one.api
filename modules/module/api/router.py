# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from modules.module.api.schemas import ModuleCreate, ModuleUpdate
from modules.module.business.service import ModuleService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "module"])


@router.post("/create/module")
def create_module_router(body: ModuleCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new module.
    """
    module = ModuleService().create(
        name=body.name,
        route=body.route,
    )
    return module.to_dict()


@router.get("/get/modules")
def get_modules_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all modules.
    """
    modules = ModuleService().read_all()
    return [module.to_dict() for module in modules]


@router.get("/get/module/{public_id}")
def get_module_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a module by public ID.
    """
    module = ModuleService().read_by_public_id(public_id=public_id)
    return module.to_dict()


@router.put("/update/module/{public_id}")
def update_module_by_public_id_router(public_id: str, body: ModuleUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a module by public ID.
    """
    module = ModuleService().update_by_public_id(public_id=public_id, module=body)
    return module.to_dict()


@router.delete("/delete/module/{public_id}")
def delete_module_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a module by public ID.
    """
    module = ModuleService().delete_by_public_id(public_id=public_id)
    return module.to_dict()
