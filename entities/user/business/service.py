# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from entities.user.business.model import User
from entities.user.persistence.repo import UserRepository
from shared.authz import current_user_id


class UserService:
    """
    Service for User entity business operations.
    """

    def __init__(self, repo: Optional[UserRepository] = None):
        """Initialize the UserService."""
        self.repo = repo or UserRepository()

    def read_workers(self) -> list[User]:
        """Curated worker list for the time-entry picker."""
        return self.repo.read_workers()

    def create(
        self,
        *,
        tenant_id: int = None,
        firstname: str,
        lastname: str,
        created_by_user_id: Optional[int] = None,
    ) -> User:
        """
        Create a new user. CreatedByUserId / ModifiedByUserId are pulled
        from the per-request ContextVar when not supplied. Signup paths
        (no authenticated actor) leave them NULL.
        """
        actor = created_by_user_id if created_by_user_id is not None else current_user_id.get()
        return self.repo.create(
            firstname=firstname,
            lastname=lastname,
            created_by_user_id=actor,
            modified_by_user_id=actor,
        )

    def read_all(self, *, include_agents: bool = False) -> list[User]:
        """
        Read users. By default agent users (IsAgent=1) are hidden;
        pass include_agents=True for an admin Agents tab.
        """
        return self.repo.read_all(include_agents=include_agents)

    def read_by_id(self, id: str) -> Optional[User]:
        """
        Read a user by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[User]:
        """
        Read a user by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_firstname(self, firstname: str) -> Optional[User]:
        """
        Read a user by firstname.
        """
        return self.repo.read_by_firstname(firstname)

    def read_by_lastname(self, lastname: str) -> Optional[User]:
        """
        Read a user by lastname.
        """
        return self.repo.read_by_lastname(lastname)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        firstname: str = None,
        lastname: str = None,
        modified_by_user_id: Optional[int] = None,
    ) -> Optional[User]:
        """
        Update a user by public ID. ModifiedByUserId pulled from
        ContextVar when not supplied.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if firstname is not None:
                existing.firstname = firstname
            if lastname is not None:
                existing.lastname = lastname
            existing.modified_by_user_id = (
                modified_by_user_id
                if modified_by_user_id is not None
                else current_user_id.get()
            )
        return self.repo.update_by_id(existing)

    def set_worker_link(
        self,
        public_id: str,
        *,
        row_version: str,
        worker_type: Optional[str],
        worker_public_id: Optional[str],
    ) -> Optional[User]:
        """Set the User's worker linkage.

        worker_type ∈ {'employee', 'vendor', None}:
            - 'employee' — resolves worker_public_id via EmployeeService, sets User.EmployeeId, clears VendorId.
            - 'vendor'   — resolves worker_public_id via VendorService, sets User.VendorId, clears EmployeeId.
            - None / '' — clears both (User is no longer a billable worker).

        Service-layer XOR is the primary guard; the sproc has a defense-in-depth
        check too. ValueError on bad inputs propagates to a 400.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if not existing:
            return None

        wt = (worker_type or "").strip().lower() or None
        if wt not in (None, "employee", "vendor"):
            raise ValueError(
                f"worker_type must be 'employee', 'vendor', or null — got {worker_type!r}."
            )

        employee_id: Optional[int] = None
        vendor_id: Optional[int] = None

        if wt is None:
            pass  # both stay None — clears link
        elif wt == "employee":
            if not worker_public_id:
                raise ValueError("worker_public_id is required when worker_type='employee'.")
            # Lazy import to avoid pulling Employee/Vendor entity packages at module
            # import time — keeps test startup + cold start lighter.
            from entities.employee.business.service import EmployeeService
            employee = EmployeeService().read_by_public_id(public_id=worker_public_id)
            if not employee:
                raise ValueError(f"Employee with public_id {worker_public_id!r} not found.")
            employee_id = int(employee.id)
        else:  # 'vendor'
            if not worker_public_id:
                raise ValueError("worker_public_id is required when worker_type='vendor'.")
            from entities.vendor.business.service import VendorService
            vendor = VendorService().read_by_public_id(public_id=worker_public_id)
            if not vendor:
                raise ValueError(f"Vendor with public_id {worker_public_id!r} not found.")
            vendor_id = int(vendor.id)

        # Use caller-provided row_version (optimistic concurrency); fall back
        # to the existing row's version so a stale React state doesn't 409.
        if row_version is not None:
            existing.row_version = row_version

        return self.repo.update_worker_link(
            id=existing.id,
            row_version_bytes=existing.row_version_bytes,
            employee_id=employee_id,
            vendor_id=vendor_id,
        )

    def set_last_company_id(self, *, user_id: int, last_company_id: int) -> None:
        """
        Persist the active Company a user last switched to so their next
        login defaults `cid` to it.
        """
        self.repo.set_last_company_id(user_id=user_id, last_company_id=last_company_id)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[User]:
        """
        Delete a user by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
