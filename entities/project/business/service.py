# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from services.project.business.model import Project
from services.project.persistence.repo import ProjectRepository


class ProjectService:
    """
    Service for Project entity business operations.
    """

    def __init__(self, repo: Optional[ProjectRepository] = None):
        """Initialize the ProjectService."""
        self.repo = repo or ProjectRepository()

    def create(self, *, tenant_id: int = 1, name: str, description: str, status: str, customer_id: Optional[int] = None, abbreviation: Optional[str] = None) -> Project:
        """
        Create a new project.
        
        Args:
            tenant_id: Tenant ID for multi-tenant isolation (default: 1)
            name: Project name
            description: Project description
            status: Project status
            customer_id: Optional customer ID
            abbreviation: Optional project abbreviation
        """
        return self.repo.create(tenant_id=tenant_id, name=name, description=description, status=status, customer_id=customer_id, abbreviation=abbreviation)

    def read_all(self) -> list[Project]:
        """
        Read all projects.
        """
        return self.repo.read_all()

    def read_by_id(self, id: int) -> Optional[Project]:
        """
        Read a project by ID.
        """
        return self.repo.read_by_id(id)

    def read_by_public_id(self, public_id: str) -> Optional[Project]:
        """
        Read a project by public ID.
        """
        return self.repo.read_by_public_id(public_id)

    def read_by_name(self, name: str) -> Optional[Project]:
        """
        Read a project by name.
        """
        return self.repo.read_by_name(name)

    def update_by_public_id(
        self,
        public_id: str,
        *,
        tenant_id: int = None,
        row_version: str,
        name: str = None,
        description: str = None,
        status: str = None,
        customer_id: int = None,
        abbreviation: str = None,
    ) -> Optional[Project]:
        """
        Update a project by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = row_version
            if name is not None:
                existing.name = name
            if description is not None:
                existing.description = description
            if status is not None:
                existing.status = status
            if customer_id is not None:
                existing.customer_id = customer_id
            if abbreviation is not None:
                existing.abbreviation = abbreviation
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str, *, tenant_id: int = None) -> Optional[Project]:
        """
        Delete a project by public ID.
        """
        # TODO: In Phase 10, validate tenant_id matches record's tenant
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
