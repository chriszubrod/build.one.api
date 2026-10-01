"""Privilege-ceiling checks shared by admin-management paths."""
# Python Standard Library Imports

# Third-party Imports

# Local Imports
from shared.authz.context import current_is_system_admin


def assert_actor_can_manage_user(target_user) -> None:
    """Privilege ceiling (U-585): a caller who is not a system admin may never
    manage a system-admin target. Raises PermissionError."""
    if bool(getattr(target_user, "is_system_admin", False)) and not current_is_system_admin.get():
        raise PermissionError(
            "Only a system administrator can set credentials for a system administrator."
        )
