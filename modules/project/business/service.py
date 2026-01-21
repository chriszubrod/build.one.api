# Python Standard Library Imports
from typing import Optional

# Third-party Imports

# Local Imports
from modules.project.business.model import Project
from modules.project.persistence.repo import ProjectRepository


class ProjectService:
    """
    Service for Project entity business operations.
    """

    def __init__(self, repo: Optional[ProjectRepository] = None):
        """Initialize the ProjectService."""
        self.repo = repo or ProjectRepository()

    def create(self, *, name: str, description: str, status: str, customer_id: Optional[int] = None, abbreviation: Optional[str] = None) -> Project:
        """
        Create a new project.
        """
        return self.repo.create(name=name, description=description, status=status, customer_id=customer_id, abbreviation=abbreviation)

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

    def update_by_public_id(self, public_id: str, project) -> Optional[Project]:
        """
        Update a project by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            existing.row_version = project.row_version
            existing.name = project.name
            existing.description = project.description
            existing.status = project.status
            existing.customer_id = project.customer_id
            existing.abbreviation = project.abbreviation
        return self.repo.update_by_id(existing)

    def delete_by_public_id(self, public_id: str) -> Optional[Project]:
        """
        Delete a project by public ID.
        """
        existing = self.read_by_public_id(public_id=public_id)
        if existing:
            return self.repo.delete_by_id(existing.id)
        return None
