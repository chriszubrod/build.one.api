# Python Standard Library Imports

# Third-party Imports
from fastapi import APIRouter, Depends

# Local Imports
from modules.role_module.api.schemas import RoleModuleCreate, RoleModuleUpdate
from modules.role_module.business.service import RoleModuleService
from modules.auth.business.service import get_current_user_api

router = APIRouter(prefix="/api/v1", tags=["api", "role_module"])


@router.post("/create/role_module")
def create_role_module_router(body: RoleModuleCreate, current_user: dict = Depends(get_current_user_api)):
    """
    Create a new role module.
    """
    role_module = RoleModuleService().create(
        role_id=body.role_id,
        module_id=body.module_id
    )
    return role_module.to_dict()


@router.get("/get/role_modules")
def get_role_modules_router(current_user: dict = Depends(get_current_user_api)):
    """
    Read all role modules.
    """
    role_modules = RoleModuleService().read_all()
    return [role_module.to_dict() for role_module in role_modules]


@router.get("/get/role_module/{public_id}")
def get_role_module_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Read a role module by public ID.
    """
    role_module = RoleModuleService().read_by_public_id(public_id=public_id)
    return role_module.to_dict()


@router.put("/update/role_module/{public_id}")
def update_role_module_by_public_id_router(public_id: str, body: RoleModuleUpdate, current_user: dict = Depends(get_current_user_api)):
    """
    Update a role module by public ID.
    """
    role_module = RoleModuleService().update_by_public_id(public_id=public_id, role_module=body)
    return role_module.to_dict()


@router.delete("/delete/role_module/{public_id}")
def delete_role_module_by_public_id_router(public_id: str, current_user: dict = Depends(get_current_user_api)):
    """
    Delete a role module by public ID.
    """
    role_module = RoleModuleService().delete_by_public_id(public_id=public_id)
    return role_module.to_dict()
